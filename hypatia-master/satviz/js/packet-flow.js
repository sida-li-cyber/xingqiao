/**
 * Packet Flow Animation Module v3 — Phase 5 (packet-flow)
 *
 * Renders lightweight "packet" point entities that glide along active links,
 * giving an intuitive sense of traffic direction and load. This is a purely
 * cosmetic layer:
 *   - It NEVER touches link polyline materials, so the existing color-cache /
 *     metrics-gradient logic in CesiumManager is left completely untouched.
 *   - Packet motion is driven by wall time (not sim time), so it stays smooth
 *     at any playback speed and even while the sim is paused.
 *   - Packet density and speed scale with each link's bandwidth utilization.
 *
 * Integration points (see cesium-manager.js):
 *   - created in CesiumManager.initialize()
 *   - reconciled at the end of CesiumManager.syncLinks()
 *   - cleared in CesiumManager.clearAll()
 *   - dropped per-link in CesiumManager.removeLink()
 *   - pick-guarded in CesiumManager.setupEventHandlers() (type === 'packet')
 */

class PacketFlowManager {
    /**
     * @param {CesiumManager} cm - The owning CesiumManager instance. Used to
     *   reach the viewer, the entity stores, and the node-position sampler.
     */
    constructor(cm) {
        this.cm = cm;

        /** Master on/off switch (UI toggle). */
        this.enabled = true;

        /** Hard cap on simultaneous packet entities (perf guard). */
        this.maxPackets = 250;

        /** linkId -> { srcId, tgtId, count, packets: [entity, ...] } */
        this._byLink = new Map();

        /** Flat list of every live packet entity (for quick total counts). */
        this._all = [];

        /** Reusable scratch vector for per-frame lerp (avoids GC churn). */
        this._scratch = new Cesium.Cartesian3();
    }

    // ======================================================================
    // Public API
    // ======================================================================

    /** Turn the animation on/off. Turning off removes all packet entities. */
    setEnabled(on) {
        this.enabled = !!on;
        if (!this.enabled) {
            this._removeAll();
        } else {
            this.sync();
        }
    }

    /** Remove every packet entity and forget all per-link state. */
    clear() {
        this._removeAll();
    }

    /** Drop all packets belonging to a single link (called on link removal). */
    dropLink(linkId) {
        const rec = this._byLink.get(linkId);
        if (rec) {
            this._removeLinkPackets(rec);
            this._byLink.delete(linkId);
        }
    }

    /**
     * Reconcile packet entities with the current set of active links.
     * Called at the end of CesiumManager.syncLinks() (≈5Hz).
     */
    sync() {
        if (!this.enabled) return;

        const links = this.cm.entities.links;

        // --- Pass 1: compute desired packet count per active link ---------
        const desired = new Map(); // linkId -> {srcId, tgtId, n}
        let total = 0;
        for (const [linkId, link] of links.entries()) {
            const p = link.properties;
            if (!p) continue;

            const isActive = this._gv(p, 'is_active', true) !== false;
            if (!isActive) continue;

            const srcId = this._gv(p, 'source', null);
            const tgtId = this._gv(p, 'target', null);
            if (!srcId || !tgtId) continue;

            const util = Math.max(0, Math.min(1, this._gv(p, 'bandwidth_utilization', 0)));
            // 1..4 packets: busier links carry more visible packets.
            let n = 1 + Math.min(3, Math.floor(util * 4));

            desired.set(linkId, { srcId, tgtId, n });
            total += n;
        }

        // --- Pass 2: enforce the global cap via proportional scaling ------
        if (total > this.maxPackets && total > 0) {
            const factor = this.maxPackets / total;
            for (const rec of desired.values()) {
                rec.n = Math.floor(rec.n * factor); // may drop to 0
            }
        }

        // --- Pass 3: drop packets for links that are gone / scaled to 0 ---
        for (const [linkId, rec] of this._byLink.entries()) {
            const want = desired.get(linkId);
            if (!want || want.n <= 0) {
                this._removeLinkPackets(rec);
                this._byLink.delete(linkId);
            }
        }

        // --- Pass 4: add / rebuild packets to match the desired counts ----
        for (const [linkId, want] of desired.entries()) {
            if (want.n <= 0) continue;

            const existing = this._byLink.get(linkId);
            const needsRebuild =
                !existing ||
                existing.count !== want.n ||
                existing.srcId !== want.srcId ||
                existing.tgtId !== want.tgtId;

            if (needsRebuild) {
                if (existing) {
                    this._removeLinkPackets(existing);
                    this._byLink.delete(linkId);
                }
                this._spawnForLink(linkId, want.srcId, want.tgtId, want.n);
            }
            // else: count & endpoints unchanged — keep existing packets.
        }
    }

    // ======================================================================
    // Internals
    // ======================================================================

    /** Read a (possibly Property-wrapped) value from an entity PropertyBag. */
    _gv(props, key, dflt) {
        const v = props[key];
        if (v === undefined || v === null) return dflt;
        if (typeof v.getValue === 'function') return v.getValue();
        return v;
    }

    /** Create `count` packet entities for a link and record them. */
    _spawnForLink(linkId, srcId, tgtId, count) {
        // Read utilization once for traversal speed (cosmetic only).
        const link = this.cm.entities.links.get(linkId);
        const util = link && link.properties
            ? Math.max(0, Math.min(1, this._gv(link.properties, 'bandwidth_utilization', 0)))
            : 0;

        // Busier links traverse faster: 1200ms (idle) -> 500ms (saturated).
        const traversalMs = 1200 - 700 * util;
        const speed = 1 / traversalMs; // phase-units per ms

        const packets = [];
        for (let i = 0; i < count; i++) {
            if (this._all.length >= this.maxPackets) break;
            const ent = this._spawnPacket(linkId, srcId, tgtId, speed, i / count);
            if (ent) packets.push(ent);
        }

        this._byLink.set(linkId, { srcId, tgtId, count: packets.length, packets });
    }

    /**
     * Create a single packet point entity whose position lerps from the live
     * source-node position to the live target-node position, cycling forever.
     */
    _spawnPacket(linkId, srcId, tgtId, speed, phase0) {
        const cm = this.cm;
        const scratch = this._scratch;

        // Per-packet mutable state captured by the closure.
        const pk = {
            lastPos: new Cesium.Cartesian3(),
            hasPos: false,
        };

        const entity = cm.viewer.entities.add({
            position: new Cesium.CallbackProperty(() => {
                const src = cm._sampleNode(srcId);
                const tgt = cm._sampleNode(tgtId);
                if (!src || !tgt) {
                    // Endpoints not ready — hold the last known position.
                    return pk.hasPos ? pk.lastPos : undefined;
                }
                const now = (typeof performance !== 'undefined')
                    ? performance.now() : Date.now();
                const phase = (now * speed + phase0) % 1;
                Cesium.Cartesian3.lerp(src, tgt, phase, scratch);
                Cesium.Cartesian3.clone(scratch, pk.lastPos);
                pk.hasPos = true;
                return scratch;
            }, false),
            point: {
                pixelSize: 3,
                color: Cesium.Color.WHITE,
                outlineColor: Cesium.Color.CYAN.withAlpha(0.6),
                outlineWidth: 1,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            properties: {
                type: 'packet',
                linkId: linkId,
            },
        });

        this._all.push(entity);
        return entity;
    }

    /** Remove all packet entities for one link record (does not delete map). */
    _removeLinkPackets(rec) {
        for (const ent of rec.packets) {
            this.cm.viewer.entities.remove(ent);
            const idx = this._all.indexOf(ent);
            if (idx !== -1) this._all.splice(idx, 1);
        }
        rec.packets = [];
    }

    /** Remove every packet entity and reset all state. */
    _removeAll() {
        for (const ent of this._all) {
            this.cm.viewer.entities.remove(ent);
        }
        this._all = [];
        this._byLink.clear();
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PacketFlowManager;
}
