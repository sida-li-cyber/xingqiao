"""
Packet-Level Discrete-Event Simulation Engine (Protocol v3, Phase 2 + 3).

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
  handing over naturally reroute traffic.

Phase 3 additions
-----------------
- **Differential topology updates / real handover loss.** `sync_topology`
  diffs the new edge set against the current one. Surviving links keep their
  Link/Port objects (queues, in-flight transmission, byte counters) so traffic
  is not disturbed when unrelated links come and go. Links that disappear are
  drained: every queued packet and the packet in transmission is counted as a
  *handover drop* (per-node, per-link and global counters, and the in-flight
  tally are all updated). This is what produces the observable loss spike when
  an uplink hands over to another satellite.
- **QoS strict-priority scheduling.** Each packet carries a priority level
  (`Packet.prio`, 0 = highest). Each output port holds one FIFO queue per
  priority level and always serves the highest-priority non-empty queue first.
  Under congestion, high-priority flows (e.g. UAV telemetry) therefore see
  lower latency and fewer drops than best-effort flows (e.g. ship bulk data).

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
FGEN = 3  # a: file_id          (Milestone A: paced file-chunk injection)
FRET = 4  # a: (file_id, seq, deadline)  (Milestone A: ARQ retx timeout)

_INF = float("inf")

# QoS priority levels. 0 = highest (strict priority). Keep small.
NUM_PRIO = 2
PRIO_HIGH = 0      # e.g. UAV telemetry / control
PRIO_BEST_EFFORT = 1   # e.g. ship bulk data


class Packet:
    __slots__ = ("pid", "src", "dst", "size", "inject_time", "enq_time",
                 "hops", "alive", "prio", "file_id", "chunk_seq")

    def __init__(self, pid, src, dst, size, inject_time, prio=PRIO_BEST_EFFORT,
                 file_id=None, chunk_seq=-1):
        self.pid = pid
        self.src = src
        self.dst = dst
        self.size = size            # bytes
        self.inject_time = inject_time
        self.enq_time = inject_time  # when enqueued on current port
        self.hops = 0
        self.alive = False           # True once it has entered the network
        self.prio = prio             # QoS priority level (0 = highest)
        # File-transfer identity (Milestone A). Background Poisson packets keep
        # file_id=None and behave exactly as before; file chunks carry the
        # owning transfer id and their 0-based chunk index so the engine can
        # drive selective-repeat ARQ and the data plane can reassemble bytes.
        self.file_id = file_id
        self.chunk_seq = chunk_seq


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
    """Output port for a directed link: per-priority FIFO queues + one transmitter.

    Strict-priority scheduling: `pop_next()` always drains the lowest-numbered
    (highest-priority) non-empty queue first.
    """
    __slots__ = ("link", "queues", "queued", "in_tx", "busy_until")

    def __init__(self, link):
        self.link = link
        self.queues = [deque() for _ in range(NUM_PRIO)]
        self.queued = 0               # total packets across all priority queues
        self.in_tx = None             # packet currently transmitting
        self.busy_until = 0.0

    def append(self, packet):
        self.queues[packet.prio].append(packet)
        self.queued += 1

    def pop_next(self):
        """Pop the highest-priority (lowest index) queued packet, or None."""
        for q in self.queues:
            if q:
                self.queued -= 1
                return q.popleft()
        return None

    def drain_all(self):
        """Yield every queued packet (all priorities) and the in-tx packet,
        resetting the port. Used to account handover drops on link removal."""
        pkts = []
        for q in self.queues:
            while q:
                pkts.append(q.popleft())
        self.queued = 0
        if self.in_tx is not None:
            pkts.append(self.in_tx)
            self.in_tx = None
        return pkts


DEFAULT_CONFIG = {
    "packet_size_bytes": 1500,
    "queue_capacity_pkts": 200,
    "default_rate_pps": 200.0,        # Poisson arrival rate per source
    "default_prio": PRIO_BEST_EFFORT,  # priority when a flow has none set
    "route_refresh_interval": 5.0,    # seconds
    "link_error_rate": 0.0,           # per-packet corruption probability
    "max_in_flight": 100000,          # backpressure safety valve
    # Milestone A: file transfer (control plane)
    "file_window_chunks": 64,         # sliding-window: max outstanding chunks
    "file_rto_s": 0.2,                # ARQ retransmission timeout (seconds)
    "file_default_rate_bps": 5e6,     # injection rate when a transfer has no cap
    "capacity": {                     # per-direction capacity (bps) by type
        "isl": 1e10,
        "gsl": 1e9,
        "sul": 5e8,
        "ssl": 5e8,
    },
}


# File-transfer states (Milestone A control plane).
FT_TRANSFERRING = 0
FT_COMPLETE = 1
FT_CANCELLED = 2


class FileTransfer:
    """One file transfer as seen by the DES control plane.

    The engine models the transfer as a stream of fixed-size abstract chunks
    (Packet.file_id / chunk_seq). It paces injection, routes each chunk like an
    ordinary packet, and drives selective-repeat ARQ purely from timeouts: a
    chunk that is not delivered within ``rto`` seconds is presumed lost and
    re-injected (counted as a freshly generated packet, so the global
    conservation invariant still holds exactly).

    No real payload bytes are held here — the backend data plane stores those
    and reassembles them from the ``file_chunk_delivered`` events the engine
    emits. This keeps the DES process lightweight even for large files.
    """

    __slots__ = ("file_id", "name", "src", "dst", "total_bytes", "chunk_size",
                 "total_chunks", "prio", "rate_cap_bps", "state",
                 "delivered", "pending", "next_seq", "retx_count",
                 "start_time", "complete_time", "rto", "interval")

    def __init__(self, file_id, name, src, dst, total_bytes, chunk_size,
                 prio, rate_cap_bps, rto, now):
        self.file_id = file_id
        self.name = name
        self.src = src
        self.dst = dst
        self.total_bytes = int(total_bytes)
        self.chunk_size = int(chunk_size)
        self.total_chunks = max(1, -(-self.total_bytes // self.chunk_size))  # ceil
        self.prio = prio
        self.rate_cap_bps = rate_cap_bps
        self.state = FT_TRANSFERRING
        self.delivered = set()        # chunk seqs delivered (deduped)
        self.pending = {}             # seq -> retx deadline (outstanding chunks)
        self.next_seq = 0             # next chunk to inject for the first time
        self.retx_count = 0
        self.start_time = now
        self.complete_time = None
        self.rto = rto
        # Pacing interval between first-time chunk injections (seconds).
        bps = rate_cap_bps if rate_cap_bps and rate_cap_bps > 0 else None
        if bps:
            self.interval = (self.chunk_size * 8.0) / bps
        else:
            self.interval = 0.001     # near-line-rate when uncapped

    def chunk_bytes(self, seq):
        """Real byte length of a chunk (the last one may be short)."""
        start = seq * self.chunk_size
        return min(self.chunk_size, max(0, self.total_bytes - start))

    @property
    def delivered_bytes(self):
        return sum(self.chunk_bytes(s) for s in self.delivered)

    @property
    def progress(self):
        return (self.delivered_bytes / self.total_bytes) if self.total_bytes else 1.0


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
        self.transit = None           # nodes allowed to forward in transit
        self._edge_sig = None
        self._topo_dirty = False
        # Phase 7: precomputed undirected aggregation groups for snapshot():
        # (undir_key, capacity_bps, propagation_ms, [(link, port), ...]).
        # Rebuilt whenever the edge set or the port objects change.
        self._snap_groups = []

        self.route = defaultdict(dict)   # node -> {sink: next_hop}
        self.source_sink = {}            # source -> sink
        self.flow_rate = {}              # source -> pps
        self.flow_prio = {}              # source -> priority level
        self.sources = []
        self.sinks = set()
        self._gen_scheduled = set()
        self._last_route_refresh = -_INF

        self._pid = 0
        # Milestone A: file-transfer control plane.
        self.files = {}               # file_id -> FileTransfer
        self.file_events = []         # outbound events for data plane / protocol
        self._reset_counters()

    # ------------------------------------------------------------------
    # Lifecycle / reset
    # ------------------------------------------------------------------

    def _reset_counters(self):
        self.in_flight = 0
        self.total_delivered = 0
        self.total_dropped = 0
        self.total_handover_dropped = 0
        self.n_generated = defaultdict(int)
        self.n_delivered = defaultdict(int)
        self.n_forwarded = defaultdict(int)
        self.n_dropped = defaultdict(int)
        self.n_generated_prio = [0] * NUM_PRIO   # cumulative generated per priority
        self.n_dropped_prio = [0] * NUM_PRIO     # cumulative drops per priority
        self.n_delivered_prio = [0] * NUM_PRIO   # cumulative deliveries per priority
        self.e2e_samples = []                 # windowed, seconds
        self.node_e2e = defaultdict(list)     # windowed, seconds
        # Phase 7: windowed per-node activity markers (cleared each snapshot),
        # used to emit node metrics only for recently-active nodes.
        self.n_fwd_window = defaultdict(int)
        self.n_drop_window = defaultdict(int)

    def flush(self, until):
        """Hard reset on a time discontinuity (seek / stop / reset)."""
        self.events = []
        self.ports = {k: Port(lk) for k, lk in self.links.items()}
        self._rebuild_snap_groups()
        self._gen_scheduled = set()
        self._last_route_refresh = -_INF
        self._topo_dirty = True
        # Milestone A: a time discontinuity abandons in-flight file chunks
        # (their FGEN/FRET events just vanished with the heap).
        self.files = {}
        self.file_events = []
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

    def sync_topology(self, nodes, edges, transit=None):
        """Differentially update the directed graph.

        nodes: iterable of node ids.
        edges: iterable of (a, b, link_type, prop_s) undirected links.
        transit: optional iterable of node ids allowed to act as
            intermediate forwarding hops. None (default) lets every node
            transit. Nodes outside the set still originate and receive
            traffic; routing simply never relaxes *through* them (Phase 7:
            keeps UAVs, ships and ground stations from being used as
            shortcuts between satellites).

        Surviving directed links keep their Link/Port state (queues, in-tx
        packet, byte counters). Removed links are drained and every packet they
        held is counted as a handover drop. Added links get fresh Link/Port
        objects. This keeps traffic on stable links undisturbed while producing
        a real, observable loss spike when a link hands over.
        """
        self.nodes = set(nodes)
        self.transit = set(transit) if transit is not None else None
        cap_by_type = self.cfg["capacity"]

        # Normalise undirected edges into directed (u, v) specs.
        new_specs = {}
        for a, b, ltype, prop_s in edges:
            cap = cap_by_type.get(ltype, 1e9)
            for u, v in ((a, b), (b, a)):
                new_specs[(u, v)] = (cap, prop_s)

        new_sig = frozenset(new_specs.keys())
        if new_sig == self._edge_sig:
            # Edge set unchanged: just refresh per-link propagation/capacity.
            for key, (cap, prop_s) in new_specs.items():
                lk = self.links.get(key)
                if lk is not None:
                    lk.prop_s = prop_s
                    lk.capacity_bps = cap
            return

        old_keys = set(self.links.keys())
        new_keys = set(new_specs.keys())
        removed = old_keys - new_keys
        added = new_keys - old_keys

        # --- Removed links: drain queues + in-tx, count handover drops ---
        for key in removed:
            port = self.ports.get(key)
            lk = self.links.get(key)
            if port is not None:
                for pkt in port.drain_all():
                    if lk is not None:
                        lk.drops += 1
                    self.n_dropped[pkt.src] += 1
                    self.n_drop_window[pkt.src] += 1
                    self.n_dropped_prio[pkt.prio] += 1
                    self.total_dropped += 1
                    self.total_handover_dropped += 1
                    if pkt.alive:
                        self.in_flight -= 1
                        pkt.alive = False
            self.links.pop(key, None)
            self.ports.pop(key, None)

        # --- Surviving links: refresh parameters, keep state ---
        for key in (old_keys & new_keys):
            cap, prop_s = new_specs[key]
            lk = self.links[key]
            lk.prop_s = prop_s
            lk.capacity_bps = cap

        # --- Added links: fresh Link + Port ---
        for key in added:
            cap, prop_s = new_specs[key]
            u, v = key
            lk = Link(u, v, frozenset((u, v)), cap, prop_s)
            self.links[key] = lk
            self.ports[key] = Port(lk)

        self._edge_sig = new_sig
        self._topo_dirty = True

        # Rebuild forward adjacency to match the new edge set.
        self.adj = defaultdict(list)
        for (u, v), lk in self.links.items():
            self.adj[u].append((v, lk.prop_s))

        self._rebuild_snap_groups()

    def _rebuild_snap_groups(self):
        """Precompute the undirected aggregation groups used by snapshot().

        Avoids rebuilding a defaultdict of directed links (and repeated port
        lookups) on every tick — at 1584-satellite scale that map alone cost
        several milliseconds per tick.
        """
        by_undir = {}
        for lk in self.links.values():
            by_undir.setdefault(lk.undir, []).append(lk)
        ports = self.ports
        self._snap_groups = [
            (uk, dirs[0].capacity_bps, dirs[0].prop_s * 1000.0,
             [(d, ports.get((d.u, d.v))) for d in dirs])
            for uk, dirs in by_undir.items()
        ]

    def sync_flows(self, source_sink, flow_rate=None, flow_prio=None):
        """Declare traffic sources, their sinks, per-source rates and priority."""
        self.source_sink = dict(source_sink)
        self.sources = list(source_sink.keys())
        self.sinks = set(source_sink.values())
        if flow_rate is None:
            flow_rate = {s: self.cfg["default_rate_pps"] for s in self.sources}
        self.flow_rate = dict(flow_rate)
        if flow_prio is None:
            flow_prio = {s: self.cfg["default_prio"] for s in self.sources}
        self.flow_prio = dict(flow_prio)

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

        # Route towards background-flow sinks AND every active file-transfer
        # destination. File destinations are included here (not just via
        # self.sinks) because sync_flows rebuilds self.sinks from the
        # background flows each tick, which would otherwise drop a file's
        # destination and leave its chunks unroutable.
        sinks = set(self.sinks)
        for ft in self.files.values():
            if ft.state == FT_TRANSFERRING:
                sinks.add(ft.dst)

        transit = self.transit
        self.route = defaultdict(dict)
        for sink in sinks:
            dist = {sink: 0.0}
            next_hop = {}
            heap = [(0.0, sink)]
            while heap:
                d, x = heapq.heappop(heap)
                if d > dist.get(x, _INF):
                    continue
                # Only nodes allowed to forward in transit (plus the sink
                # itself, for the final downlink hop) may be relaxed through.
                if transit is not None and x != sink and x not in transit:
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

        # Phase 7: refresh routes at most once per route_refresh_interval.
        # Topology churn (e.g. 1 Hz link handovers at thousand-satellite
        # scale) no longer triggers a Dijkstra storm; the first refresh
        # after init / flush is still immediate because _last_route_refresh
        # starts at -inf. Routing on a briefly stale graph is safe: packets
        # aimed at a removed link are dropped and counted in _enqueue.
        if until - self._last_route_refresh > self.cfg["route_refresh_interval"]:
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
            elif kind == FGEN:
                self._on_file_gen(a, t)
            elif kind == FRET:
                self._on_file_retx(a[0], a[1], a[2], t)
            else:  # ARR
                self._on_arrive(a, b, t)
        self.now = until

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _schedule_gen(self, source, t):
        rate = self.flow_rate.get(source, 0)
        if rate <= 0:
            return
        self._seq += 1
        heapq.heappush(self.events,
                       (t + self.rng.expovariate(rate), self._seq, GEN, source, None))

    def _on_generate(self, source, t):
        # Reschedule next arrival first (keeps the flow going regardless)
        self._schedule_gen(source, t)

        dst = self.source_sink.get(source)
        if dst is None:
            return
        if self.in_flight >= self.cfg["max_in_flight"]:
            return  # backpressure: skip this injection

        self._pid += 1
        prio = self.flow_prio.get(source, self.cfg["default_prio"])
        pkt = Packet(self._pid, source, dst,
                     self.cfg["packet_size_bytes"], t, prio)
        self.n_generated[source] += 1
        self.n_generated_prio[prio] += 1

        nh = self.route.get(source, {}).get(dst)
        if nh is None:
            self.n_dropped[source] += 1
            self.n_drop_window[source] += 1
            self.n_dropped_prio[prio] += 1
            self.total_dropped += 1
            return
        self._enqueue(source, nh, pkt, t)

    def _enqueue(self, u, v, packet, t):
        port = self.ports.get((u, v))
        link = port.link if port else None
        if link is not None:
            link.attempts += 1

        # Drop: link gone
        if port is None:
            self.n_dropped[u] += 1
            self.n_drop_window[u] += 1
            self.n_dropped_prio[packet.prio] += 1
            self.total_dropped += 1
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
            return

        # Buffer full: apply the drop policy. A high-priority packet pushes out
        # the lowest-priority queued packet (protecting priority traffic under
        # congestion); otherwise the arriving packet is tail-dropped.
        if port.queued >= self.cfg["queue_capacity_pkts"]:
            victim = None
            if packet.prio == PRIO_HIGH:
                for q in reversed(port.queues):
                    if q:
                        victim = q.pop()
                        port.queued -= 1
                        break
            if victim is None:
                self.n_dropped[u] += 1
                self.n_drop_window[u] += 1
                self.n_dropped_prio[packet.prio] += 1
                self.total_dropped += 1
                if link is not None:
                    link.drops += 1
                if packet.alive:
                    self.in_flight -= 1
                    packet.alive = False
                return
            # Account the pushed-out (lower-priority) packet as the drop.
            self.n_dropped[u] += 1
            self.n_drop_window[u] += 1
            self.n_dropped_prio[victim.prio] += 1
            self.total_dropped += 1
            if link is not None:
                link.drops += 1
            if victim.alive:
                self.in_flight -= 1
                victim.alive = False

        packet.enq_time = t
        port.append(packet)
        if not packet.alive:
            packet.alive = True
            self.in_flight += 1

        if port.in_tx is None and port.busy_until <= t:
            self._start_tx(port, t)

    def _start_tx(self, port, t):
        packet = port.pop_next()
        if packet is None:
            return
        port.in_tx = packet
        link = port.link

        ser = packet.size * 8.0 / link.capacity_bps
        link.bytes_tx += packet.size

        # Per-hop sojourn latency: queue wait + serialization + propagation
        qwait = t - packet.enq_time
        link.lat_sum += qwait + ser + link.prop_s
        link.lat_cnt += 1

        port.busy_until = t + ser
        self._seq += 1
        heapq.heappush(self.events,
                       (port.busy_until, self._seq, TX, (link.u, link.v), None))

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
            self.n_drop_window[u] += 1
            self.n_dropped_prio[packet.prio] += 1
            self.total_dropped += 1
            link.drops += 1
            if packet.alive:
                self.in_flight -= 1
                packet.alive = False
        else:
            self._seq += 1
            heapq.heappush(self.events,
                           (t + link.prop_s, self._seq, ARR, v, packet))

        if port.queued:
            self._start_tx(port, t)

    def _on_arrive(self, node, packet, t):
        # Guard against stale events for packets already dropped (e.g. a link
        # handed over while this packet's propagation event was still pending).
        if not packet.alive:
            return

        if node == packet.dst:
            self.n_delivered[node] += 1
            self.n_delivered_prio[packet.prio] += 1
            self.total_delivered += 1
            e2e = t - packet.inject_time
            self.e2e_samples.append(e2e)
            self.node_e2e[packet.src].append(e2e)
            self.node_e2e[node].append(e2e)
            if packet.file_id is not None:
                self._on_file_chunk_delivered(packet, t)
            self.in_flight -= 1
            packet.alive = False
            return

        self.n_forwarded[node] += 1
        self.n_fwd_window[node] += 1
        nh = self.route.get(node, {}).get(packet.dst)
        if nh is None:
            self.n_dropped[node] += 1
            self.n_drop_window[node] += 1
            self.n_dropped_prio[packet.prio] += 1
            self.total_dropped += 1
            self.in_flight -= 1
            packet.alive = False
            return
        self._enqueue(node, nh, packet, t)

    # ------------------------------------------------------------------
    # File transfer (Milestone A control plane)
    # ------------------------------------------------------------------

    def start_file(self, file_id, name, src, dst, total_bytes,
                   chunk_size=None, prio=None, rate_cap_bps=None):
        """Register a file transfer and begin paced chunk injection.

        The file is modelled as ``ceil(total_bytes/chunk_size)`` abstract
        chunks routed from ``src`` to ``dst`` like ordinary packets, with
        timeout-driven selective-repeat ARQ. No payload bytes are handled
        here — the backend data plane stores those and reassembles them from
        the ``file_chunk_delivered`` events this engine emits.
        """
        if chunk_size is None or chunk_size <= 0:
            chunk_size = 16384
        if prio is None:
            prio = self.cfg["default_prio"]
        if rate_cap_bps is None:
            rate_cap_bps = self.cfg["file_default_rate_bps"]
        ft = FileTransfer(file_id, name, src, dst, total_bytes, chunk_size,
                          prio, rate_cap_bps, self.cfg["file_rto_s"], self.now)
        self.files[file_id] = ft
        # Make sure the destination is routable even if it is not already a
        # background-flow sink, and force a route refresh on the next advance.
        self.sinks.add(dst)
        self._last_route_refresh = -_INF
        self._seq += 1
        heapq.heappush(self.events, (self.now, self._seq, FGEN, file_id, None))
        self.file_events.append({
            "type": "file_started", "file_id": file_id, "name": name,
            "src": src, "dst": dst, "total_bytes": ft.total_bytes,
            "total_chunks": ft.total_chunks, "prio": prio,
        })
        return ft

    def cancel_file(self, file_id):
        ft = self.files.get(file_id)
        if ft is None or ft.state != FT_TRANSFERRING:
            return
        ft.state = FT_CANCELLED
        ft.pending = {}
        self.file_events.append({"type": "file_cancelled", "file_id": file_id})

    def _on_file_gen(self, file_id, t):
        """Sliding-window paced injection of first-time chunks."""
        ft = self.files.get(file_id)
        if ft is None or ft.state != FT_TRANSFERRING:
            return
        window = self.cfg["file_window_chunks"]
        made = False
        if ft.next_seq < ft.total_chunks and len(ft.pending) < window:
            if self._inject_file_chunk(ft, ft.next_seq, t):
                ft.next_seq += 1
                made = True
        # Keep the pump running while first-time chunks remain. When the window
        # is full (or we are backpressured) retry soon so deliveries reopen it.
        if ft.next_seq < ft.total_chunks and ft.state == FT_TRANSFERRING:
            delay = ft.interval if made else min(ft.interval, 0.02)
            self._seq += 1
            heapq.heappush(self.events,
                           (t + delay, self._seq, FGEN, file_id, None))

    def _inject_file_chunk(self, ft, seq, t):
        """Create one chunk packet, arm its ARQ timer, and forward it.

        Returns False only under global backpressure (caller retries later
        without consuming the chunk). A missing route is counted as a drop but
        still returns True — the armed ARQ timer retries once a route appears.
        Every created chunk counts as a generated packet, so conservation holds.
        """
        if self.in_flight >= self.cfg["max_in_flight"]:
            return False
        self._pid += 1
        pkt = Packet(self._pid, ft.src, ft.dst, ft.chunk_bytes(seq), t, ft.prio,
                     file_id=ft.file_id, chunk_seq=seq)
        self.n_generated[ft.src] += 1
        self.n_generated_prio[ft.prio] += 1

        deadline = t + ft.rto
        ft.pending[seq] = deadline
        self._seq += 1
        heapq.heappush(self.events,
                       (deadline, self._seq, FRET,
                        (ft.file_id, seq, deadline), None))

        nh = self.route.get(ft.src, {}).get(ft.dst)
        if nh is None:
            self.n_dropped[ft.src] += 1
            self.n_drop_window[ft.src] += 1
            self.n_dropped_prio[ft.prio] += 1
            self.total_dropped += 1
            return True
        self._enqueue(ft.src, nh, pkt, t)
        return True

    def _on_file_retx(self, file_id, seq, deadline, t):
        """ARQ timeout: re-inject a chunk not delivered within its deadline."""
        ft = self.files.get(file_id)
        if ft is None or ft.state != FT_TRANSFERRING:
            return
        if seq in ft.delivered:
            ft.pending.pop(seq, None)
            return
        if ft.pending.get(seq) != deadline:
            return  # stale timer superseded by a newer injection
        if self._inject_file_chunk(ft, seq, t):
            ft.retx_count += 1
        else:  # backpressured — re-arm a quick retry without losing the chunk
            nd = t + 0.01
            ft.pending[seq] = nd
            self._seq += 1
            heapq.heappush(self.events,
                           (nd, self._seq, FRET, (ft.file_id, seq, nd), None))

    def _on_file_chunk_delivered(self, packet, t):
        ft = self.files.get(packet.file_id)
        if ft is None or ft.state != FT_TRANSFERRING:
            return
        seq = packet.chunk_seq
        if seq in ft.delivered:
            return  # duplicate (late original after a retx) — ignore
        ft.delivered.add(seq)
        ft.pending.pop(seq, None)
        self.file_events.append({
            "type": "file_chunk_delivered", "file_id": ft.file_id, "seq": seq,
            "bytes": ft.chunk_bytes(seq),
        })
        if len(ft.delivered) >= ft.total_chunks:
            ft.state = FT_COMPLETE
            ft.complete_time = t
            ft.pending = {}
            self.file_events.append({
                "type": "file_complete", "file_id": ft.file_id,
                "elapsed_s": t - ft.start_time, "retx": ft.retx_count,
                "total_bytes": ft.total_bytes,
            })

    def _file_route_path(self, ft):
        """Current forwarding chain src -> ... -> dst (best effort)."""
        path = [ft.src]
        node = ft.src
        seen = {ft.src}
        for _ in range(len(self.nodes) + 1):
            nh = self.route.get(node, {}).get(ft.dst)
            if nh is None:
                break
            path.append(nh)
            if nh == ft.dst or nh in seen:
                break
            seen.add(nh)
            node = nh
        return path

    def file_states(self):
        """Snapshot of every transfer for the protocol layer."""
        state_name = {FT_TRANSFERRING: "TRANSFERRING",
                      FT_COMPLETE: "COMPLETE", FT_CANCELLED: "CANCELLED"}
        out = {}
        for fid, ft in self.files.items():
            end = ft.complete_time if ft.complete_time is not None else self.now
            elapsed = end - ft.start_time
            thr_bytes = (ft.delivered_bytes / elapsed) if elapsed > 0 else 0.0
            remaining = ft.total_bytes - ft.delivered_bytes
            eta = (remaining / thr_bytes) if (thr_bytes > 0
                                              and ft.state == FT_TRANSFERRING) else 0.0
            out[fid] = {
                "name": ft.name, "src": ft.src, "dst": ft.dst,
                "state": state_name[ft.state],
                "progress": ft.progress,
                "delivered_bytes": ft.delivered_bytes,
                "total_bytes": ft.total_bytes,
                "eta_s": eta, "throughput_bps": thr_bytes * 8.0,
                "path": self._file_route_path(ft),
                "in_flight": len(ft.pending), "retx": ft.retx_count,
            }
        return out

    def drain_file_events(self):
        """Return and clear queued file events (for the data plane / protocol)."""
        ev = self.file_events
        self.file_events = []
        return ev

    # ------------------------------------------------------------------
    # Snapshot (read metrics + reset windowed accumulators)
    # ------------------------------------------------------------------

    def snapshot(self, dt):
        dt = dt if dt > 0 else 0.0

        # --- Per undirected link metrics (aggregate both directions) ---
        # Phase 7: iterate the precomputed _snap_groups (rebuilt only when
        # the topology changes) instead of rebuilding an undirected map and
        # re-looking-up ports on every tick.
        link_metrics = {}
        agg_throughput = 0.0
        qcap = self.cfg["queue_capacity_pkts"]
        rate_scale = 8.0 / dt if dt > 0 else 0.0
        for uk, cap, prop_ms, dps in self._snap_groups:
            tx_sum = 0.0
            tx_max = 0.0
            qd = 0
            lat_sum = 0.0
            lat_cnt = 0
            att = 0
            drp = 0
            for d, port in dps:
                r = (d.bytes_tx - d.prev_bytes_tx) * rate_scale
                tx_sum += r
                if r > tx_max:
                    tx_max = r
                if port is not None:
                    qd += port.queued + (1 if port.in_tx is not None else 0)
                lat_sum += d.lat_sum
                lat_cnt += d.lat_cnt
                att += d.attempts
                drp += d.drops
            agg_throughput += tx_sum

            loss = (drp / att) if att else 0.0
            lat_ms = (lat_sum / lat_cnt * 1000.0) if lat_cnt else 0.0

            # Phase 7: omit idle links from the per-link metrics dict so
            # thousand-satellite snapshots stay small. Aggregate throughput
            # still accounts every link, and window state is always reset.
            if tx_sum > 0 or qd > 0 or lat_cnt > 0 or att > 0:
                link_metrics[uk] = {
                    "tx_bps": tx_sum,
                    "capacity_bps": cap,
                    "utilization": (tx_max / cap) if cap else 0.0,
                    "queue_depth": qd,
                    "queue_capacity": qcap * len(dps),
                    "propagation_ms": prop_ms,
                    "latency_ms": lat_ms,
                    "loss_rate": loss,
                }

            for d, _port in dps:
                d.prev_bytes_tx = d.bytes_tx
                d.lat_sum = 0.0
                d.lat_cnt = 0
                d.attempts = 0
                d.drops = 0

        # --- Per-node metrics (counters cumulative; latency windowed) ---
        # Phase 7: emit only nodes that matter — traffic sources / sinks
        # (always) plus nodes with forward, drop or e2e activity in this
        # window. At thousand-satellite scale this keeps the payload to the
        # ~100-200 nodes actually carrying traffic instead of the whole graph.
        active_nodes = set(self.sources) | set(self.sinks)
        active_nodes.update(self.n_fwd_window.keys())
        active_nodes.update(self.n_drop_window.keys())
        active_nodes.update(self.node_e2e.keys())

        node_metrics = {}
        for node in active_nodes:
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
        self.n_fwd_window.clear()
        self.n_drop_window.clear()

        # --- Global summary ---
        if self.e2e_samples:
            avg_e2e = sum(self.e2e_samples) / len(self.e2e_samples) * 1000.0
        else:
            avg_e2e = 0.0
        summary = {
            "pkts_in_flight": self.in_flight,
            "pkts_delivered": self.total_delivered,
            "pkts_dropped": self.total_dropped,
            "pkts_handover_dropped": self.total_handover_dropped,
            "avg_e2e_latency_ms": avg_e2e,
            "aggregate_throughput_bps": agg_throughput,
            "qos": {
                str(p): {
                    "generated": self.n_generated_prio[p],
                    "delivered": self.n_delivered_prio[p],
                    "dropped": self.n_dropped_prio[p],
                }
                for p in range(NUM_PRIO)
            },
        }
        self.e2e_samples = []

        return {
            "links": link_metrics,
            "nodes": node_metrics,
            "summary": summary,
        }
