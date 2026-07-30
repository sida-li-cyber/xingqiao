/**
 * Packet Flow Animation Module v5 — wave-antinode rendering
 *
 * Renders traffic as a travelling "antinode" (波腹) that rides ON each active
 * link: the dashed line locally thickens into a bright core with a tight glow
 * halo, and that bulge glides from the link's source toward its target. This
 * replaces the old free-floating glow billboards, which read as separate dots
 * and competed visually with the file-path moving-dash highlight.
 *
 * Design notes:
 *   - One overlay polyline per active link (coincident with the link itself),
 *     dressed in a custom `PolylinePulseWave` material. The shader uses the
 *     polyline texture coordinates (st.s = 0..1 along the line, st.t = 0..1
 *     across it) to place `pulseCount` bulges and advance them with an
 *     animated `phase` uniform — so the bulges stay glued to the geometry
 *     (they rotate with the globe) instead of being screen-anchored.
 *   - The overlay width (i.e. the bulge / glow size) shrinks as the camera
 *     zooms out, so the animation doesn't turn into dense noise at global
 *     scale. Width is recomputed once per frame from the camera height.
 *   - The base link polylines are NEVER touched (same as before): this stays
 *     a purely cosmetic layer over the metrics-driven link materials.
 *
 * Interaction with the file-path highlight (cesium-manager.highlightFilePath):
 *   - Direction 1: links that are part of the selected transfer's path already
 *     wear animated moving dashes, so sync() skips them — no pulse is layered
 *     on top of the highlight.
 *   - Direction 2: while a file path is selected, `dimAlpha` is lowered so the
 *     remaining background pulses recede and the magenta route stands out.
 *
 * Integration points (see cesium-manager.js):
 *   - created in CesiumManager.initialize()
 *   - reconciled at the end of CesiumManager.syncLinks()
 *   - re-synced when the file-path highlight is applied / cleared
 *   - cleared in CesiumManager.clearAll()
 *   - dropped per-link in CesiumManager.removeLink()
 *   - pick-guarded in CesiumManager.setupEventHandlers() (type === 'packet')
 */

// --- Wave-antinode material -------------------------------------------------
// The bulge envelope travels along st.s (0 at the link source, 1 at target);
// increasing `phase` advances it toward the target. Across the line, a bright
// core (the "thickened" line) falls off into a soft glow halo. `globalAlpha`
// is the master dim used while a file path is selected.
const PULSE_WAVE_SOURCE = `uniform vec4 color;
uniform float phase;
uniform float pulseCount;
uniform float bulgeLength;
uniform float glowRadius;
uniform float globalAlpha;

czm_material czm_getMaterial(czm_materialInput materialInput)
{
    czm_material material = czm_getDefaultMaterial(materialInput);
    vec2 st = materialInput.st;

    // Along the line: pulseCount bulges, each centred at u = 0.5 within its
    // period. fract() keeps the pattern seamless as phase advances.
    float u = fract(st.s * pulseCount - phase);
    float dAlong = abs(u - 0.5);
    float halfLen = max(bulgeLength * 0.5, 0.001);
    float env = 1.0 - smoothstep(halfLen * 0.3, halfLen, dAlong);

    // Across the line: bright core (thickened line) + soft glow halo.
    float across = abs(st.t - 0.5) * 2.0;
    float core = 1.0 - smoothstep(0.0, 0.45, across);
    float glow = 1.0 - smoothstep(0.3, glowRadius, across);
    float shape = max(core, glow * 0.4);

    float a = env * shape * globalAlpha;
    if (a < 0.005) {
        discard;
    }

    // White-hot centre so the bulge reads as energy on the line.
    vec3 col = mix(color.rgb, vec3(1.0), core * 0.35);
    vec4 fragColor = czm_gammaCorrect(vec4(col, color.a * a));
    material.emission = fragColor.rgb;
    material.alpha = fragColor.a;
    return material;
}
`;

// Overlay width (px) when fully zoomed in — the maximum bulge/glow diameter.
const BASE_PULSE_WIDTH = 8.0;
// Camera height (m) at which the pulse is at full size; higher (zoomed out)
// shrinks it proportionally so the animation stays sparse at global scale.
const PULSE_REF_HEIGHT = 2000000.0;
// Floor for the zoom-based scale factor (keeps a faint pulse when far out).
const PULSE_MIN_SCALE = 0.12;

let _pulseWaveRegistered = false;
function ensurePulseWaveMaterial() {
    if (_pulseWaveRegistered) return;
    Cesium.Material._materialCache.addMaterial('PolylinePulseWave', {
        fabric: {
            type: 'PolylinePulseWave',
            uniforms: {
                color: new Cesium.Color(1, 1, 1, 1),
                phase: 0.0,
                pulseCount: 1.0,
                bulgeLength: 0.45,
                glowRadius: 1.0,
                globalAlpha: 1.0,
            },
            source: PULSE_WAVE_SOURCE,
        },
        translucent: true,
    });
    _pulseWaveRegistered = true;
}

/**
 * A MaterialProperty for the PolylinePulseWave material. The `phase` and
 * `globalAlpha` uniforms are advanced every frame by the owning manager via
 * tick(), which mutates the material's live uniforms object (captured in
 * getValue). Mirrors the MovingDashMaterialProperty pattern in cesium-manager.
 */
class PulseWaveMaterialProperty {
    constructor(options = {}) {
        this._color = options.color || Cesium.Color.WHITE;
        this._pulseCount = options.pulseCount || 1;
        this._bulgeLength = options.bulgeLength || 0.45;
        this._glowRadius = options.glowRadius || 1.0;
        this._phaseRate = options.phaseRate || 1.0;  // phase cycles / second
        this._uniforms = null;                        // live uniforms (captured)
        this._definitionChanged = new Cesium.Event();
    }

    get isConstant() { return true; }
    get definitionChanged() { return this._definitionChanged; }

    getType() { return 'PolylinePulseWave'; }

    getValue(time, result) {
        if (!Cesium.defined(result)) result = {};
        result.color = this._color;
        result.phase = 0.0;
        result.pulseCount = this._pulseCount;
        result.bulgeLength = this._bulgeLength;
        result.glowRadius = this._glowRadius;
        result.globalAlpha = 1.0;
        this._uniforms = result;   // keep a live reference for per-frame updates
        return result;
    }

    /** Advance the bulge phase + apply the master dim. Called every frame. */
    tick(nowSeconds, globalAlpha) {
        if (this._uniforms) {
            // Wrap at 1.0 (the pattern's period in phase) to keep the float
            // small and precise even after long runtimes.
            this._uniforms.phase = (nowSeconds * this._phaseRate) % 1.0;
            this._uniforms.globalAlpha = globalAlpha;
        }
    }

    equals(other) { return this === other; }
}

class PacketFlowManager {
    /**
     * @param {CesiumManager} cm - The owning CesiumManager instance. Used to
     *   reach the viewer/scene, the entity stores, and the node-position
     *   sampler (_sampleNode).
     */
    constructor(cm) {
        this.cm = cm;

        /** Master on/off switch (UI toggle). */
        this.enabled = true;

        /**
         * Master alpha multiplier for all pulses (Direction 2). Lowered while
         * a file-transfer path is selected so the background recedes.
         */
        this.dimAlpha = 1.0;

        /** linkId -> { srcId, tgtId, count, entity, prop } */
        this._byLink = new Map();

        /** Overlay width for the current frame (zoom-linked, see _animate). */
        this._currentWidth = BASE_PULSE_WIDTH;

        ensurePulseWaveMaterial();
        this.cm.scene.preRender.addEventListener(() => this._animate());
    }

    // ======================================================================
    // Public API
    // ======================================================================

    /** Turn the animation on/off. Turning off removes all overlay entities. */
    setEnabled(on) {
        this.enabled = !!on;
        if (!this.enabled) {
            this._removeAll();
        } else {
            this.sync();
        }
    }

    /** Remove every overlay entity and forget all per-link state. */
    clear() {
        this._removeAll();
    }

    /** Drop the overlay belonging to a single link (called on link removal). */
    dropLink(linkId) {
        const rec = this._byLink.get(linkId);
        if (rec) {
            this._removeOverlay(rec);
            this._byLink.delete(linkId);
        }
    }

    /**
     * Reconcile overlay entities with the current set of active links.
     * Called at the end of CesiumManager.syncLinks() (≈5Hz) and whenever the
     * file-path highlight is applied / cleared.
     */
    sync() {
        if (!this.enabled) return;

        const links = this.cm.entities.links;
        const highlighted = this.cm._highlightedLinks;

        // --- Pass 1: compute desired overlays ------------------------------
        const desired = new Map(); // linkId -> {srcId, tgtId, n}
        for (const [linkId, link] of links.entries()) {
            const p = link.properties;
            if (!p) continue;

            // Direction 1: the selected file path already shows moving dashes
            // on its links — don't layer a traffic bulge on top of them.
            if (highlighted && highlighted.has(linkId)) continue;

            const isActive = this._gv(p, 'is_active', true) !== false;
            if (!isActive) continue;

            const srcId = this._gv(p, 'source', null);
            const tgtId = this._gv(p, 'target', null);
            if (!srcId || !tgtId) continue;

            const util = Math.max(0, Math.min(1, this._gv(p, 'bandwidth_utilization', 0)));
            // 1..3 antinodes: busier links carry more visible bulges.
            const n = 1 + Math.min(2, Math.floor(util * 3));

            desired.set(linkId, { srcId, tgtId, n });
        }

        // --- Pass 2: drop overlays that are gone / changed -----------------
        for (const [linkId, rec] of this._byLink.entries()) {
            const want = desired.get(linkId);
            const changed = !want ||
                want.srcId !== rec.srcId ||
                want.tgtId !== rec.tgtId ||
                want.n !== rec.count;
            if (changed) {
                this._removeOverlay(rec);
                this._byLink.delete(linkId);
            }
        }

        // --- Pass 3: spawn overlays that are missing -----------------------
        for (const [linkId, want] of desired.entries()) {
            if (!this._byLink.has(linkId)) {
                this._spawnOverlay(linkId, want.srcId, want.tgtId, want.n);
            }
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

    /**
     * Overlay width for the current camera zoom. Full size at PULSE_REF_HEIGHT
     * and below; shrinks inversely with camera height above that, clamped to
     * PULSE_MIN_SCALE, so the pulses stay small and sparse when zoomed out.
     */
    _pulseWidth() {
        let h = PULSE_REF_HEIGHT;
        try {
            const carto = this.cm.viewer.camera.positionCartographic;
            if (carto && isFinite(carto.height)) h = carto.height;
        } catch (e) { /* 2D / morphing — fall back to full size */ }
        const scale = Cesium.Math.clamp(
            PULSE_REF_HEIGHT / Math.max(h, 1.0), PULSE_MIN_SCALE, 1.0);
        return BASE_PULSE_WIDTH * scale;
    }

    /** Create the wave-antinode overlay polyline for a single link. */
    _spawnOverlay(linkId, srcId, tgtId, count) {
        const cm = this.cm;
        const link = cm.entities.links.get(linkId);
        const linkType = link && link.properties
            ? this._gv(link.properties, 'linkType', 'isl') : 'isl';
        const util = link && link.properties
            ? Math.max(0, Math.min(1, this._gv(link.properties, 'bandwidth_utilization', 0)))
            : 0;

        // Bulge colour follows the link type; busier links traverse faster.
        const color = cm.linkTypeColors[linkType] || Cesium.Color.WHITE;
        const traversalMs = 1200 - 700 * util;
        // phase cycles/second so each antinode crosses the line in traversalMs
        // (pulse speed = phaseRate / count = 1000 / traversalMs line/s).
        const phaseRate = (count * 1000) / traversalMs;

        const prop = new PulseWaveMaterialProperty({
            color: color,
            pulseCount: count,
            phaseRate: phaseRate,
        });

        const entity = cm.viewer.entities.add({
            polyline: {
                positions: new Cesium.CallbackProperty(() => {
                    const src = cm._sampleNode(srcId);
                    const tgt = cm._sampleNode(tgtId);
                    if (!src || !tgt) return [];
                    return [src, tgt];
                }, false),
                width: new Cesium.CallbackProperty(
                    () => this._currentWidth, false),
                material: prop,
                clampToGround: false,
            },
            properties: {
                type: 'packet',   // decorative — excluded from picking
                linkId: linkId,
            },
        });

        this._byLink.set(linkId, { srcId, tgtId, count, entity, prop });
    }

    /** Remove one link's overlay entity. */
    _removeOverlay(rec) {
        if (rec.entity) {
            this.cm.viewer.entities.remove(rec.entity);
            rec.entity = null;
        }
    }

    /** Remove every overlay entity and reset all state. */
    _removeAll() {
        for (const rec of this._byLink.values()) {
            this._removeOverlay(rec);
        }
        this._byLink.clear();
    }

    /** Per-frame update: zoom-linked width + advance every bulge's phase. */
    _animate() {
        this._currentWidth = this._pulseWidth();
        if (!this._byLink.size) return;
        const now = (typeof performance !== 'undefined'
            ? performance.now() : Date.now()) / 1000;
        for (const rec of this._byLink.values()) {
            rec.prop.tick(now, this.dimAlpha);
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PacketFlowManager;
}
