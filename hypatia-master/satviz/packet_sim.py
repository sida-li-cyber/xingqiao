"""
Packet-Level Discrete-Event Simulation Engine (Protocol v3, Phase 2).

A lightweight store-and-forward packet simulator that produces *real*
network telemetry (per-link throughput / queue depth / latency / loss, and
per-node packet counters / end-to-end latency / jitter) instead of the
synthetic sine-wave metrics used in Phase 1.

Design
------
- Event-driven core on a `heapq` priority queue. Event kinds:
    GEN  a traffic source injects a packet
    TX   a packet finishes serialising on an output port -> it propagates
    ARR  a packet arrives at a node -> delivered or forwarded
- Topology is supplied externally each tick (the orbital/geometry layer owns
  node positions and link visibility). The engine builds a directed graph,
  runs reverse-Dijkstra from each traffic sink to obtain hop-by-hop forwarding
  tables, and forwards packets store-and-forward through per-port FIFO queues.
- Routing is hop-by-hop (each node forwards toward the destination using a
  forwarding table), refreshed periodically and on topology change, so links
  handing over naturally reroute traffic and occasionally drop in-flight
  packets (realistic handover loss).

The engine is geometry-free: propagation delays and capacities are handed in
with the topology, keeping all Earth/space math in the caller.
"""

import heapq
import math
import random
from collections import deque, defaultdict


# Event kinds (small ints for cheap comparison)
GEN = 0   # a: source node
TX = 1    # a: (u, v) directed link whose transmission completed
ARR = 2   # a: node, b: packet

_INF = float("inf")


class Packet:
    __slots__ = ("pid", "src", "dst", "size", "inject_time", "enq_time",
                 "hops", "alive")

    def __init__(self, pid, src, dst, size, inject_time):
        self.pid = pid
        self.src = src
        self.dst = dst
        self.size = size            # bytes
        self.inject_time = inject_time
        self.enq_time = inject_time  # when enqueued on current port
        self.hops = 0
        self.alive = False           # True once it has entered the network


class Link:
    """A directed edge u -> v with capacity, propagation delay and counters."""
    __slots__ = ("u", "v", "undir", "capacity_bps", "prop_s",
                 "bytes_tx", "prev_bytes_tx",
                 "lat_sum", "lat_cnt", "attempts", "drops")

    def __init__(self, u, v, undir, capacity_bps, prop_s):
        self.u = u
        self.v = v
        self.undir = undir            # frozenset({a, b}) for aggregation
        self.capacity_bps = capacity_bps
        self.prop_s = prop_s
        self.bytes_tx = 0             # cumulative bytes (for windowed rate)
        self.prev_bytes_tx = 0
        self.lat_sum = 0.0            # windowed per-hop latency accumulator (s)
        self.lat_cnt = 0
        self.attempts = 0             # windowed enqueue attempts
        self.drops = 0                # windowed drops on this link


class Port:
    """Output port for a directed link: FIFO queue + single transmitter."""
    __slots__ = ("link", "queue", "in_tx", "busy_until")

    def __init__(self, link):
        self.link = link
        self.queue = deque()
        self.in_tx = None             # packet currently transmitting
        self.busy_until = 0.0


DEFAULT_CONFIG = {
    "packet_size_bytes": 1500,
    "queue_capacity_pkts": 200,
    "default_rate_pps": 200.0,        # Poisson arrival rate per source
    "route_refresh_interval": 5.0,    # seconds
    "link_error_rate": 0.0,           # per-packet corruption probability
    "max_in_flight": 100000,          # backpressure safety valve
    "capacity": {                     # per-direction capacity (bps) by type
        "isl": 1e10,
        "gsl": 1e9,
        "sul": 5e8,
        "ssl": 5e8,
    },
}


class PacketEngine:
    def __init__(self, config=None, seed=None):
        self.cfg = dict(DEFAULT_CONFIG)
        if config:
            self.cfg.update(config)
        self.rng = random.Random(seed)

        self.now = 0.0
        self.events = []              # heap of (time, seq, kind, a, b)
        self._seq = 0

        self.nodes = set()
        self.links = {}               # (u, v) -> Link
        self.ports = {}               # (u, v) -> Port
        self.adj = defaultdict(list)  # u -> [(v, weight=prop_s)]
        self._edge_sig = None
        self._topo_dirty = False

        self.route = defaultdict(dict)   # node -> {sink: next_hop}
        self.source_sink = {}            # source -> sink
        self.flow_rate = {}              # source -> pps
        self.sources = []
        self.sinks = set()
        self._gen_scheduled = set()
        self._last_route_refresh = -_INF

        self._pid = 0
        self._reset_counters()

    # ------------------------------------------------------------------
    # Lifecycle / reset
    # ------------------------------------------------------------------

    def _reset_counters(self):
        self.in_flight = 0
        self.total_delivered = 0
        self.total_dropped = 0
        self.n_generated = defaultdict(int)
        self.n_delivered = defaultdict(int)
        self.n_forwarded = defaultdict(int)
        self.n_dropped = defaultdict(int)
        self.e2e_samples = []                 # windowed, seconds
        self.node_e2e = defaultdict(list)     # windowed, seconds

    def flush(self, until):
        """Hard reset on a time discontinuity (seek / stop / reset)."""
        self.events = []
        self.ports = {k: Port(lk) for k, lk in self.links.items()}
        self._gen_scheduled = set()
        self._last_route_refresh = -_INF
        self._topo_dirty = True
        self._reset_counters()
        for lk in self.links.values():
            lk.bytes_tx = 0
            lk.prev_bytes_tx = 0
            lk.lat_sum = 0.0
            lk.lat_cnt = 0
            lk.attempts = 0
            lk.drops = 0
        self.now = until

    # ------------------------------------------------------------------
    # Topology & flows (fed by the caller each tick)
    # ------------------------------------------------------------------

    def sync_topology(self, nodes, edges):
        """Update the directed graph.

        nodes: iterable of node ids.
        edges: iterable of (a, b, link_type, prop_s) undirected links.
        """
        self.nodes = set(nodes)
        new_sig = frozenset(
            (a, b) if a < b else (b, a) for a, b, _, _ in edges
        )

        old_links = self.links
        changed = new_sig != self._edge_sig
        if changed:
            self.links = {}
            self.ports = {}
            self._edge_sig = new_sig
            self._topo_dirty = True

        cap_by_type = self.cfg["capacity"]
        for a, b, ltype, prop_s in edges:
            cap = cap_by_type.get(ltype, 1e9)
            undir = frozenset((a, b))
            for u, v in ((a, b), (b, a)):
                key = (u, v)
                lk = self.links.get(key)
                if lk is None:
                    lk = Link(u, v, undir, cap, prop_s)
                    prev = old_links.get(key)
                    if prev is not None:
                        lk.bytes_tx = prev.bytes_tx  # rate continuity
                    self.links[key] = lk
                    self.ports[key] = Port(lk)
                else:
                    lk.prop_s = prop_s
                    lk.capacity_bps = cap

        # Rebuild forward adjacency only when the edge set changed. Routing
        # consumes adj at refresh time, which coincides with _topo_dirty, so a
        # changed topology always gets a fresh adj; an unchanged one reuses it.
        if changed:
            self.adj = defaultdict(list)
            for (u, v), lk in self.links.items():
                self.adj[u].append((v, lk.prop_s))

    def sync_flows(self, source_sink, flow_rate=None):
        """Declare traffic sources, their sinks, and per-source rates."""
        self.source_sink = dict(source_sink)
        self.sources = list(source_sink.keys())
        self.sinks = set(source_sink.values())
        if flow_rate is None:
            flow_rate = {s: self.cfg["default_rate_pps"] for s in self.sources}
        self.flow_rate = dict(flow_rate)

        for s in self.sources:
            if s not in self._gen_scheduled and self.flow_rate.get(s, 0) > 0:
                self._schedule_gen(s, self.now)
                self._gen_scheduled.add(s)

    # ------------------------------------------------------------------
    # Routing (reverse-Dijkstra from each sink)
    # ------------------------------------------------------------------

    def _refresh_routes(self):
        radj = defaultdict(list)
        for u in self.adj:
            for v, w in self.adj[u]:
                radj[v].append((u, w))

        self.route = defaultdict(dict)
        for sink in self.sinks:
            dist = {sink: 0.0}
            next_hop = {}
            heap = [(0.0, sink)]
            while heap:
                d, x = heapq.heappop(heap)
                if d > dist.get(x, _INF):
                    continue
                for u, w in radj.get(x, ()):
                    nd = d + w
                    if nd < dist.get(u, _INF):
                        dist[u] = nd
                        next_hop[u] = x
                        heapq.heappush(heap, (nd, u))
            for u, nh in next_hop.items():
                self.route[u][sink] = nh

    # ------------------------------------------------------------------
    # Time advance
    # ------------------------------------------------------------------

    def advance(self, until):
        if until <= self.now and self.now != 0.0:
            self.now = until
            return

        if (self._topo_dirty or
                until - self._last_route_refresh > self.cfg["route_refresh_interval"]):
            self._refresh_routes()
            self._last_route_refresh = until
            self._topo_dirty = False

        events = self.events
        while events and events[0][0] <= until:
            t, _seq, kind, a, b = heapq.heappop(events)
            self.now = t
            if kind == GEN:
                self._on_generate(a, t)
            elif kind == TX:
                self._on_tx_complete(a[0], a[1], t)
            else:  # ARR
                self._on_arrive(a, b, t)
        self.now = until

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _push(self, t, kind, a, b=None):
        self._seq += 1
        heapq.heappush(self.events, (t, self._seq, kind, a, b))

    def _schedule_gen(self, source, t):
        rate = self.flow_rate.get(source, 0)
        if rate <= 0:
            return
        self._push(t + self.rng.expovariate(rate), GEN, source)

    def _on_generate(self, source, t):
        # Reschedule next arrival first (keeps the flow going regardless)
        self._schedule_gen(source, t)

        dst = self.source_sink.get(source)
        if dst is None:
            return
        if self.in_flight >= self.cfg["max_in_flight"]:
            return  # backpressure: skip this injection

        self._pid += 1
        pkt = Packet(self._pid, source, dst,
                     self.cfg["packet_size_bytes"], t)
        self.n_generated[source] += 1

        nh = self.route.get(source, {}).get(dst)
        if nh is None:
            self.n_dropped[source] += 1
            self.total_dropped += 1
            return
        self._enqueue(source, nh, pkt, t)

    def _enqueue(self, u, v, packet, t):
        port = self.ports.get((u, v))
        link = port.link if port else None
        if link is not None:
            link.attempts += 1

        # Drop: link gone or queue overflow
        if port is None or len(port.queue) >= self.cfg["queue_capacity_pkts"]:
            self.n_dropped[u] += 1
            self.total_dropped += 1
            if link is not None:
                link.drops += 1
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
            return

        packet.enq_time = t
        port.queue.append(packet)
        if not packet.alive:
            packet.alive = True
            self.in_flight += 1

        if port.in_tx is None and port.busy_until <= t:
            self._start_tx(port, t)

    def _start_tx(self, port, t):
        packet = port.queue.popleft()
        port.in_tx = packet
        link = port.link

        ser = packet.size * 8.0 / link.capacity_bps
        link.bytes_tx += packet.size

        # Per-hop sojourn latency: queue wait + serialization + propagation
        qwait = t - packet.enq_time
        link.lat_sum += qwait + ser + link.prop_s
        link.lat_cnt += 1

        port.busy_until = t + ser
        self._push(port.busy_until, TX, (link.u, link.v))

    def _on_tx_complete(self, u, v, t):
        port = self.ports.get((u, v))
        if port is None or port.in_tx is None:
            return
        packet = port.in_tx
        port.in_tx = None
        link = port.link
        packet.hops += 1

        err = self.cfg["link_error_rate"]
        if err > 0 and self.rng.random() < err:
            # Corrupted in transit
            self.n_dropped[u] += 1
            self.total_dropped += 1
            link.drops += 1
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
        else:
            self._push(t + link.prop_s, ARR, v, packet)

        if port.queue:
            self._start_tx(port, t)

    def _on_arrive(self, node, packet, t):
        if node == packet.dst:
            self.n_delivered[node] += 1
            self.total_delivered += 1
            e2e = t - packet.inject_time
            self.e2e_samples.append(e2e)
            self.node_e2e[packet.src].append(e2e)
            self.node_e2e[node].append(e2e)
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
            return

        self.n_forwarded[node] += 1
        nh = self.route.get(node, {}).get(packet.dst)
        if nh is None:
            self.n_dropped[node] += 1
            self.total_dropped += 1
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
            return
        self._enqueue(node, nh, packet, t)

    # ------------------------------------------------------------------
    # Snapshot (read metrics + reset windowed accumulators)
    # ------------------------------------------------------------------

    def snapshot(self, dt):
        dt = dt if dt > 0 else 0.0

        # --- Per undirected link metrics (aggregate both directions) ---
        undir_map = defaultdict(list)
        for lk in self.links.values():
            undir_map[lk.undir].append(lk)

        link_metrics = {}
        agg_throughput = 0.0
        qcap = self.cfg["queue_capacity_pkts"]
        for uk, dirs in undir_map.items():
            rates = []
            for d in dirs:
                r = (d.bytes_tx - d.prev_bytes_tx) * 8.0 / dt if dt > 0 else 0.0
                rates.append(r)
            tx_sum = sum(rates)
            tx_max = max(rates) if rates else 0.0
            agg_throughput += tx_sum

            cap = dirs[0].capacity_bps
            qd = 0
            for d in dirs:
                p = self.ports.get((d.u, d.v))
                if p is not None:
                    qd += len(p.queue) + (1 if p.in_tx is not None else 0)

            lat_cnt = sum(d.lat_cnt for d in dirs)
            lat_sum = sum(d.lat_sum for d in dirs)
            lat_ms = (lat_sum / lat_cnt * 1000.0) if lat_cnt else 0.0

            att = sum(d.attempts for d in dirs)
            drp = sum(d.drops for d in dirs)
            loss = (drp / att) if att else 0.0

            link_metrics[uk] = {
                "tx_bps": tx_sum,
                "capacity_bps": cap,
                "utilization": (tx_max / cap) if cap else 0.0,
                "queue_depth": qd,
                "queue_capacity": qcap * len(dirs),
                "propagation_ms": dirs[0].prop_s * 1000.0,
                "latency_ms": lat_ms,
                "loss_rate": loss,
            }

            for d in dirs:
                d.prev_bytes_tx = d.bytes_tx
                d.lat_sum = 0.0
                d.lat_cnt = 0
                d.attempts = 0
                d.drops = 0

        # --- Per-node metrics (counters cumulative; latency windowed) ---
        node_metrics = {}
        for node in self.nodes:
            e2e = self.node_e2e.get(node)
            if e2e:
                avg = sum(e2e) / len(e2e) * 1000.0
                if len(e2e) > 1:
                    mean = sum(e2e) / len(e2e)
                    var = sum((x - mean) ** 2 for x in e2e) / (len(e2e) - 1)
                    jitter = math.sqrt(var) * 1000.0
                else:
                    jitter = 0.0
            else:
                avg = 0.0
                jitter = 0.0
            node_metrics[node] = {
                "pkts_sent": self.n_generated[node],
                "pkts_recv": self.n_delivered[node],
                "pkts_fwd": self.n_forwarded[node],
                "pkts_dropped": self.n_dropped[node],
                "e2e_latency_ms": avg,
                "jitter_ms": jitter,
            }
        self.node_e2e.clear()

        # --- Global summary ---
        if self.e2e_samples:
            avg_e2e = sum(self.e2e_samples) / len(self.e2e_samples) * 1000.0
        else:
            avg_e2e = 0.0
        summary = {
            "pkts_in_flight": self.in_flight,
            "pkts_delivered": self.total_delivered,
            "pkts_dropped": self.total_dropped,
            "avg_e2e_latency_ms": avg_e2e,
            "aggregate_throughput_bps": agg_throughput,
        }
        self.e2e_samples = []

        return {
            "links": link_metrics,
            "nodes": node_metrics,
            "summary": summary,
        }
