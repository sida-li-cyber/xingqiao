/**
 * Packet Flow Animation Module v4 — Milestone B (pulse rendering)
 *
 * Renders soft-glow "energy pulses" that glide along active links, giving an
 * intuitive sense of traffic direction and load. Milestone B replaces the old
 * hard-edged 3px points (which read as just another node dot) with glowing,
 * throbbing billboards so "data moving along a link" is unmistakable and
 * clearly distinct from the solid node markers.
 *
 * This is a purely cosmetic layer:
 *   - It NEVER touches link polyline materials, so the existing color-cache /
 *     metrics-gradient logic in CesiumManager is left completely untouched.
 *   - Pulse motion is driven by wall time (not sim time), so it stays smooth
 *     at any playback speed and even while the sim is paused.
 *   - Pulse density and speed scale with each link's bandwidth utilization.
 *   - A single shared glow texture (radial-gradient canvas) is cached and
 *     reused for every pulse; per-pulse colour is applied via the billboard
 *     `color` (additive-style white-hot core tints to the set colour).
 *
 * Integration points (see cesium-manager.js):
 *   - created in CesiumManager.initialize()
 *   - reconciled at the end of CesiumManager.syncLinks()
 *   - cleared in CesiumManager.clearAll()
 *   - dropped per-link in CesiumManager.removeLink()
 *   - pick-guarded in CesiumManager.setupEventHandlers() (type === 'packet')
 *
 * Milestone A tie-in:
 *   - setFilePath(nodes) spawns a single file-coloured pulse that travels the
 *     selected transfer's path (reusing the same glow), so "this lit streak is
 *     my file" is visually distinct from the link-typed background pulses.
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

        // --- Milestone B: glow rendering ------------------------------------
        /** colour-hex -> Cesium texture (singleton glow sprite cache). */
        this._texCache = new Map();

        /** Dedicated colour for file-transfer pulses (distinct from traffic). */
        this.filePulseColor = Cesium.Color.fromCssColorString('#FF4DD8');

        /** file pulse state (Milestone A tie-in). */
        this._filePulse = null; // { entity, nodes, color, speed, scratch }
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
            if (this._filePulse) this._respawnFilePulse();
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

    /**
     * Milestone A tie-in: show a file-coloured pulse travelling the given path
     * (node-id list, src -> ... -> dst). Pass null / empty to remove it.
     */
    setFilePath(nodes, color) {
        this._removeFilePulse();
        if (!nodes || nodes.length < 2) return;
        this._filePulse = {
            nodes: nodes.slice(),
            color: color || this.filePulseColor,
            speed: 1 / 1600, // path-traversal phase units per ms
            scratch: new Cesium.Cartesian3(),
            entity: null,
        };
        if (this.enabled) this._respawnFilePulse();
    }

    /** True when a file pulse is active (used to skip the auto route cycle). */
    hasFilePulse() {
        return !!this._filePulse;
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

    /**
     * Singleton radial-gradient glow sprite, cached per colour (few distinct
     * hues). A white-hot core lets the per-entity billboard `color` tint the
     * body while the centre stays bright, producing an "energy pulse" look.
     * Returns an HTMLCanvasElement — Cesium entity billboards accept a canvas
     * as `image` and manage the GPU texture internally, so one cached sprite
     * per colour is reused across all pulses (no per-packet image creation).
     */
    _glowCanvas(color) {
        const hex = color.toCssColorString();
        const cached = this._texCache.get(hex);
        if (cached) return cached;

        const size = 64;
        const cv = document.createElement('canvas');
        cv.width = size;
        cv.height = size;
        const ctx = cv.getContext('2d');
        const g = ctx.createRadialGradient(
            size / 2, size / 2, 0, size / 2, size / 2, size / 2);
        g.addColorStop(0.0, 'rgba(255,255,255,1)');
        g.addColorStop(0.18, 'rgba(255,255,255,0.95)');
        g.addColorStop(0.42, color.toCssHexString());
        g.addColorStop(1.0, 'rgba(0,0,0,0)');
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, size, size);

        this._texCache.set(hex, cv);
        return cv;
    }

    /** Create `count` pulse entities for a link and record them. */
    _spawnForLink(linkId, srcId, tgtId, count) {
        // Read utilization once for traversal speed + glow size (cosmetic).
        const link = this.cm.entities.links.get(linkId);
        const linkType = link && link.properties
            ? this._gv(link.properties, 'linkType', 'isl') : 'isl';
        const util = link && link.properties
            ? Math.max(0, Math.min(1, this._gv(link.properties, 'bandwidth_utilization', 0)))
            : 0;

        // Busier links traverse faster: 1200ms (idle) -> 500ms (saturated).
        const traversalMs = 1200 - 700 * util;
        const speed = 1 / traversalMs; // phase-units per ms

        // Pulse colour follows the link type; glow grows slightly with load.
        const color = this.cm.linkTypeColors[linkType] || Cesium.Color.WHITE;
        const baseScale = 0.55 + 0.5 * util;

        const packets = [];
        for (let i = 0; i < count; i++) {
            if (this._all.length >= this.maxPackets) break;
            const ent = this._spawnPacket(linkId, srcId, tgtId, speed,
                                          i / count, color, baseScale);
            if (ent) packets.push(ent);
        }

        this._byLink.set(linkId, { srcId, tgtId, count: packets.length, packets });
    }

    /**
     * Create a single glowing pulse billboard whose position lerps from the
     * live source-node position to the live target-node position, cycling
     * forever, with scale + alpha throbbing over the travel phase.
     */
    _spawnPacket(linkId, srcId, tgtId, speed, phase0, color, baseScale) {
        const cm = this.cm;
        const scratch = this._scratch;

        // Per-packet mutable state captured by the closure.
        const pk = {
            lastPos: new Cesium.Cartesian3(),
            hasPos: false,
            col: new Cesium.Color(), // reused colour result (avoids GC churn)
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
            billboard: {
                image: this._glowCanvas(color),
                color: new Cesium.CallbackProperty(() => {
                    const now = (typeof performance !== 'undefined')
                        ? performance.now() : Date.now();
                    const ph = (now * speed + phase0) % 1;
                    // Throb the brightness over the travel phase.
                    const a = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(ph * Math.PI * 2));
                    // Fade near the endpoints so pulses emerge / dissolve.
                    const edge = Math.min(1, Math.min(ph, 1 - ph) * 6);
                    return Cesium.Color.clone(color, pk.col).withAlpha(a * edge);
                }, false),
                scale: new Cesium.CallbackProperty(() => {
                    const now = (typeof performance !== 'undefined')
                        ? performance.now() : Date.now();
                    const ph = (now * speed + phase0) % 1;
                    return baseScale * (0.8 + 0.45 * (0.5 + 0.5 * Math.sin(ph * Math.PI * 2)));
                }, false),
                width: 22,
                height: 22,
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

    // --- File pulse (Milestone A tie-in) ----------------------------------

    /** (Re)create the file pulse entity from the stored state. */
    _respawnFilePulse() {
        if (!this._filePulse) return;
        this._removeFilePulseEntity();
        const fp = this._filePulse;
        const cm = this.cm;
        const col = new Cesium.Color();

        fp.entity = cm.viewer.entities.add({
            position: new Cesium.CallbackProperty(() => {
                const nodes = fp.nodes;
                const n = nodes.length;
                if (n < 2) return undefined;
                const now = (typeof performance !== 'undefined')
                    ? performance.now() : Date.now();
                const u = (now * fp.speed) % 1;
                const f = u * (n - 1);
                let i = Math.floor(f);
                if (i > n - 2) i = n - 2;
                const a = cm._sampleNode(nodes[i]);
                const b = cm._sampleNode(nodes[i + 1]);
                if (!a || !b) return undefined;
                Cesium.Cartesian3.lerp(a, b, f - i, fp.scratch);
                return fp.scratch;
            }, false),
            billboard: {
                image: this._glowCanvas(fp.color),
                color: new Cesium.CallbackProperty(() => {
                    const now = (typeof performance !== 'undefined')
                        ? performance.now() : Date.now();
                    const ph = (now * fp.speed * 3) % 1;
                    const a = 0.7 + 0.3 * Math.sin(ph * Math.PI * 2);
                    return Cesium.Color.clone(fp.color, col).withAlpha(a);
                }, false),
                scale: new Cesium.CallbackProperty(() => {
                    const now = (typeof performance !== 'undefined')
                        ? performance.now() : Date.now();
                    const ph = (now * fp.speed * 3) % 1;
                    return 1.15 * (0.85 + 0.3 * (0.5 + 0.5 * Math.sin(ph * Math.PI * 2)));
                }, false),
                width: 30,
                height: 30,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            properties: { type: 'file_pulse' },
        });
    }

    /** Remove just the file pulse entity (keeps state for re-enable). */
    _removeFilePulseEntity() {
        if (this._filePulse && this._filePulse.entity) {
            this.cm.viewer.entities.remove(this._filePulse.entity);
            this._filePulse.entity = null;
        }
    }

    /** Remove the file pulse entity and forget its state. */
    _removeFilePulse() {
        this._removeFilePulseEntity();
        this._filePulse = null;
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

    /** Remove every packet entity (and the file pulse) and reset all state. */
    _removeAll() {
        for (const ent of this._all) {
            this.cm.viewer.entities.remove(ent);
        }
        this._all = [];
        this._byLink.clear();
        this._removeFilePulse();
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PacketFlowManager;
}
