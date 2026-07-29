/**
 * Cesium Manager Module v2 — Multi-Domain Rendering
 * Handles 3D scene management for satellites, UAVs, ships, ground stations,
 * and four link types (ISL / GSL / SUL / SSL).
 */

class CesiumManager {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.viewer = null;
        this.scene = null;

        // Unified entity stores
        this.entities = {
            nodes: new Map(),   // nodeId -> Cesium entity (all types)
            links: new Map(),   // linkId -> Cesium entity
            islMesh: [],        // Phase 7: static ISL mesh (decorative)
        };

        // Node type metadata
        this.nodeTypes = {
            satellite: {
                color: Cesium.Color.DODGERBLUE,
                pixelSize: 5,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1,
                labelShow: false,
                labelFont: '11px sans-serif',
            },
            uav: {
                color: Cesium.Color.LIMEGREEN,
                pixelSize: 7,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1,
                labelShow: false,
                labelFont: '11px sans-serif',
            },
            ship: {
                color: Cesium.Color.ORANGE,
                pixelSize: 6,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1,
                labelShow: false,
                labelFont: '11px sans-serif',
            },
            ground_station: {
                color: Cesium.Color.ORANGERED,
                pixelSize: 10,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 2,
                labelShow: true,
                labelFont: '12px sans-serif',
            },
        };

        // Link type base colors (from protocol v2)
        this.linkTypeColors = {
            isl: Cesium.Color.fromCssColorString('#4FC3F7'),
            gsl: Cesium.Color.fromCssColorString('#FF8A65'),
            sul: Cesium.Color.fromCssColorString('#81C784'),
            ssl: Cesium.Color.fromCssColorString('#FFB74D'),
        };

        this.cesiumToken = options.cesiumToken || '';
        this.selectedEntity = null;
        this.metricsMode = 'none';
        this.animationSpeed = 1.0;

        // Selection callback — app.js sets this to receive link/node picks.
        // Called with a plain data object, or null when selection is cleared.
        this.onSelect = null;

        // Visibility toggles per node type
        this.typeVisibility = {
            satellite: true,
            uav: true,
            ship: true,
            ground_station: true,
        };

        // Link type visibility
        this.linkTypeVisibility = {
            isl: true,
            gsl: true,
            sul: true,
            ssl: true,
        };

        this.stats = {
            satellites: 0,
            uavs: 0,
            ships: 0,
            ground_stations: 0,
            links: 0,
            fps: 0,
        };

        this.frameCount = 0;
        this.lastFpsTime = Date.now();

        // Highlighted route path
        this._highlightedLinks = new Set();

        // Performance: cache last color per link to avoid redundant material updates
        this._linkColorCache = new Map(); // linkId -> last color string
        this._batchDepth = 0;

        // Phase 4 render smoothing: wall-time interpolation between 5Hz position
        // samples so entities glide at the display refresh rate (≈60fps) instead
        // of snapping on every state_update. nodeId -> segment state
        // {prev, target, t0, dur, lastArrival, result}.
        this.nodeInterp = new Map();
        this.interpInterval = 200;   // expected backend push period (ms, 5Hz)
    }

    // ======================================================================
    // Initialization
    // ======================================================================

    initialize() {
        try {
            if (this.cesiumToken) {
                Cesium.Ion.defaultAccessToken = this.cesiumToken;
            }

            this.viewer = new Cesium.Viewer(this.containerId, {
                animation: false,
                baseLayerPicker: false,
                fullscreenButton: true,
                vrButton: false,
                geocoder: false,
                homeButton: true,
                infoBox: true,
                sceneModePicker: true,
                selectionIndicator: true,
                timeline: false,
                navigationHelpButton: false,
                navigationInstructionsInitiallyVisible: false,
                shouldAnimate: true,
            });

            this.scene = this.viewer.scene;
            this.scene.backgroundColor = Cesium.Color.BLACK;
            this.scene.highDynamicRange = false;

            // Dark-style base map
            this.viewer.imageryLayers.removeAll();
            this.viewer.imageryLayers.addImageryProvider(
                new Cesium.TileMapServiceImageryProvider({
                    url: Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
                })
            );

            // Initial camera: Asia-Pacific overview
            this.viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(110, 20, 25000000),
                duration: 2,
            });

            // Packet-flow animation layer (cosmetic, wall-time driven).
            if (typeof PacketFlowManager !== 'undefined') {
                this.packetFlow = new PacketFlowManager(this);
            }

            this.setupEventHandlers();
            this.startFpsCounter();

            console.log('[CesiumManager] Viewer initialized (v2 multi-domain)');
            return true;

        } catch (error) {
            console.error('[CesiumManager] Initialization failed:', error);
            return false;
        }
    }

    setupEventHandlers() {
        const handler = new Cesium.ScreenSpaceEventHandler(this.scene.canvas);

        handler.setInputAction((click) => {
            const picked = this.scene.pick(click.position);
            if (picked && picked.id) {
                // Packet entities are decorative — never select them. Treat a
                // packet hit as empty space so it doesn't block link/node picks.
                const ptype = picked.id.properties && picked.id.properties.type
                    ? picked.id.properties.type.getValue()
                    : null;
                if (ptype === 'packet' || ptype === 'isl_mesh') {
                    this.selectEntity(null);
                } else {
                    this.selectEntity(picked.id);
                }
            } else {
                this.selectEntity(null);
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        handler.setInputAction(() => {
            this.selectEntity(null);
        }, Cesium.ScreenSpaceEventType.RIGHT_CLICK);
    }

    startFpsCounter() {
        this.viewer.scene.postRender.addEventListener(() => {
            this.frameCount++;
        });
        setInterval(() => {
            const now = Date.now();
            const elapsed = (now - this.lastFpsTime) / 1000;
            if (elapsed > 0) {
                this.stats.fps = Math.round(this.frameCount / elapsed);
                this.frameCount = 0;
                this.lastFpsTime = now;
            }
        }, 1000);
    }

    // ======================================================================
    // Entity Selection
    // ======================================================================

    selectEntity(entity) {
        // Restore previous selection
        if (this.selectedEntity && this.selectedEntity.id) {
            this._restoreEntityStyle(this.selectedEntity);
        }

        this.selectedEntity = entity;

        if (!entity || !entity.properties) {
            this._emitSelect(null);
            return;
        }

        const type = entity.properties.type
            ? entity.properties.type.getValue()
            : null;

        if (type === 'link') {
            entity.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                glowPower: 0.25,
                color: Cesium.Color.CYAN,
            });
            entity.polyline.width = new Cesium.ConstantProperty(3);
            this._emitSelect(this._buildLinkInfo(entity));
        } else if (type && this.nodeTypes[type]) {
            entity.point.color = new Cesium.ConstantProperty(Cesium.Color.CYAN);
            entity.point.pixelSize = new Cesium.ConstantProperty(
                this.nodeTypes[type].pixelSize + 5
            );
            if (entity.label) {
                entity.label.show = new Cesium.ConstantProperty(true);
            }
            this._emitSelect(this._buildNodeInfo(entity, type));
        } else {
            this._emitSelect(null);
        }
    }

    /**
     * Programmatically clear the current selection (e.g. when the detail
     * panel is closed) and restore the entity's original style.
     */
    clearSelection() {
        if (this.selectedEntity && this.selectedEntity.id) {
            this._restoreEntityStyle(this.selectedEntity);
        }
        this.selectedEntity = null;
        this._emitSelect(null);
    }

    _emitSelect(info) {
        if (typeof this.onSelect === 'function') {
            this.onSelect(info);
        }
    }

    /** Build a plain data object describing a link entity. */
    _buildLinkInfo(entity) {
        const p = entity.properties;
        const get = (name) => (p[name] ? p[name].getValue() : undefined);
        return {
            kind: 'link',
            linkId: get('linkId'),
            linkType: get('linkType') || 'isl',
            source: get('source'),
            target: get('target'),
            bandwidth_utilization: get('bandwidth_utilization') || 0,
            latency_ms: get('latency_ms') || 0,
            loss_rate: get('loss_rate') || 0,
            is_active: get('is_active') !== false,
            // Protocol v3 packet-level telemetry
            tx_bps: get('tx_bps') || 0,
            capacity_bps: get('capacity_bps') || 0,
            queue_depth: get('queue_depth') || 0,
            queue_capacity: get('queue_capacity') || 0,
            propagation_ms: get('propagation_ms') || 0,
        };
    }

    /** Build a plain data object describing a node entity. */
    _buildNodeInfo(entity, nodeType) {
        const p = entity.properties;
        const get = (name) => (p[name] ? p[name].getValue() : undefined);

        // Current geographic position
        let lat = null, lon = null, alt = null;
        if (entity.position) {
            const carto = Cesium.Cartographic.fromCartesian(
                entity.position.getValue(this.viewer.clock.currentTime)
            );
            if (carto) {
                lon = Cesium.Math.toDegrees(carto.longitude);
                lat = Cesium.Math.toDegrees(carto.latitude);
                alt = carto.height / 1000.0; // m -> km
            }
        }

        return {
            kind: 'node',
            nodeId: get('nodeId'),
            nodeType: nodeType,
            label: get('label') || get('nodeId'),
            lat, lon, alt,
        };
    }

    /**
     * Read the latest metrics for a link by id (for live detail updates).
     * Returns null if the link no longer exists.
     */
    getLinkData(linkId) {
        const entity = this.entities.links.get(linkId);
        if (!entity) return null;
        return this._buildLinkInfo(entity);
    }

    /**
     * Read the latest position/metadata for a node by id.
     * Returns null if the node no longer exists.
     */
    getNodeData(nodeId) {
        const entity = this.entities.nodes.get(nodeId);
        if (!entity || !entity.properties) return null;
        const type = entity.properties.type
            ? entity.properties.type.getValue()
            : null;
        return this._buildNodeInfo(entity, type);
    }

    _restoreEntityStyle(entity) {
        if (!entity.properties) return;
        const type = entity.properties.type
            ? entity.properties.type.getValue()
            : null;

        if (type === 'link') {
            const linkType = entity.properties.linkType
                ? entity.properties.linkType.getValue()
                : 'isl';
            const util = entity.properties.bandwidth_utilization
                ? entity.properties.bandwidth_utilization.getValue()
                : 0;
            const gv = (k) => (entity.properties[k] ? entity.properties[k].getValue() : 0);
            const snap = {
                bandwidth_utilization: util,
                latency_ms: gv('latency_ms'),
                loss_rate: gv('loss_rate'),
                is_active: entity.properties.is_active
                    ? entity.properties.is_active.getValue() : true,
                queue_depth: gv('queue_depth'),
                queue_capacity: gv('queue_capacity'),
            };
            const color = this._getLinkDisplayColor(linkType, util, snap);
            entity.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: color,
            });
            entity.polyline.width = new Cesium.ConstantProperty(
                this._getLinkWidth(linkType)
            );
        } else if (type && this.nodeTypes[type]) {
            const style = this.nodeTypes[type];
            entity.point.color = new Cesium.ConstantProperty(style.color);
            entity.point.pixelSize = new Cesium.ConstantProperty(style.pixelSize);
            if (entity.label) {
                entity.label.show = new Cesium.ConstantProperty(style.labelShow);
            }
        }
    }

    // ======================================================================
    // Batch Update (performance)
    // ======================================================================

    beginBatch() {
        if (this._batchDepth === 0) {
            this.viewer.entities.suspendEvents();
        }
        this._batchDepth++;
    }

    endBatch() {
        this._batchDepth--;
        if (this._batchDepth <= 0) {
            this._batchDepth = 0;
            this.viewer.entities.resumeEvents();
        }
    }

    // ======================================================================
    // Node Management (unified)
    // ======================================================================

    /**
     * Add or update any node (satellite / uav / ship / ground_station).
     * @param {string} nodeId - Unique identifier
     * @param {string} nodeType - One of: satellite, uav, ship, ground_station
     * @param {{lat, lon, alt}} position - WGS84 position
     * @param {object} properties - Extra metadata (label, group, etc.)
     */
    addOrUpdateNode(nodeId, nodeType, position, properties = {}) {
        try {
            const alt = position.alt || 0;
            const cartesian = Cesium.Cartesian3.fromDegrees(
                position.lon, position.lat, alt
            );

            const style = this.nodeTypes[nodeType] || this.nodeTypes.satellite;
            const label = properties.label || nodeId;

            if (!this.entities.nodes.has(nodeId)) {
                // Seed interpolation state so the position callback has data
                // before the first render frame.
                const now = (typeof performance !== 'undefined')
                    ? performance.now() : Date.now();
                this.nodeInterp.set(nodeId, {
                    prev: cartesian.clone(),
                    target: cartesian.clone(),
                    t0: now,
                    dur: this.interpInterval,
                    lastArrival: now,
                    result: new Cesium.Cartesian3(),
                });

                // Create new entity
                const entity = this.viewer.entities.add({
                    id: `${nodeType}-${nodeId}`,
                    position: new Cesium.CallbackProperty(
                        () => this._sampleNode(nodeId), false),
                    point: {
                        pixelSize: style.pixelSize,
                        color: style.color,
                        outlineColor: style.outlineColor,
                        outlineWidth: style.outlineWidth,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    label: {
                        text: label,
                        font: style.labelFont,
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        pixelOffset: new Cesium.Cartesian2(0, -14),
                        show: style.labelShow,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    properties: {
                        type: nodeType,
                        nodeId: nodeId,
                        label: label,
                        ...properties,
                    },
                });

                // Apply visibility
                entity.show = this.typeVisibility[nodeType] !== false;

                this.entities.nodes.set(nodeId, entity);

                // Update stats
                const statKey = nodeType === 'ground_station'
                    ? 'ground_stations'
                    : nodeType + 's';
                if (this.stats[statKey] !== undefined) {
                    this.stats[statKey]++;
                }
            } else {
                // Update existing entity: advance the interpolation segment
                // (entity.position is a CallbackProperty bound to nodeInterp).
                const entity = this.entities.nodes.get(nodeId);
                const now = (typeof performance !== 'undefined')
                    ? performance.now() : Date.now();
                let st = this.nodeInterp.get(nodeId);
                if (!st) {
                    st = {
                        prev: cartesian.clone(),
                        target: cartesian.clone(),
                        t0: now,
                        dur: this.interpInterval,
                        lastArrival: now,
                        result: new Cesium.Cartesian3(),
                    };
                    this.nodeInterp.set(nodeId, st);
                } else {
                    // Start the new segment from wherever the node currently is
                    // so there is no visible jump, then glide to the new sample.
                    const cur = this._sampleNode(nodeId, now);
                    if (cur) Cesium.Cartesian3.clone(cur, st.prev);
                    Cesium.Cartesian3.clone(cartesian, st.target);
                    st.dur = Math.min(Math.max(now - st.lastArrival, 40), 1000);
                    st.lastArrival = now;
                    st.t0 = now;
                }

                // Update dynamic properties (heading, etc.)
                for (const [key, value] of Object.entries(properties)) {
                    if (entity.properties.hasProperty(key)) {
                        entity.properties[key] = value;
                    } else {
                        entity.properties.addProperty(key, value);
                    }
                }
            }

            return this.entities.nodes.get(nodeId);

        } catch (error) {
            console.error(`[CesiumManager] Error addOrUpdateNode(${nodeId}):`, error);
            return null;
        }
    }

    // Backward-compatible wrappers
    addOrUpdateSatellite(satId, position, properties = {}) {
        return this.addOrUpdateNode(satId, 'satellite', position, properties);
    }

    addOrUpdateStation(stationId, position, properties = {}) {
        return this.addOrUpdateNode(stationId, 'ground_station', position, properties);
    }

    addOrUpdateUAV(uavId, position, properties = {}) {
        return this.addOrUpdateNode(uavId, 'uav', position, properties);
    }

    addOrUpdateShip(shipId, position, properties = {}) {
        return this.addOrUpdateNode(shipId, 'ship', position, properties);
    }

    /**
     * Phase 4 render smoothing: sample a node's interpolated position.
     * Invoked every render frame by the entity's position CallbackProperty
     * (and by link endpoints). Linearly interpolates between the previous and
     * latest 5Hz samples over the measured arrival interval, clamping once the
     * next sample is overdue so nodes settle instead of extrapolating.
     * @param {string} nodeId
     * @param {number} [now] - wall-time ms (defaults to performance.now())
     * @returns {Cesium.Cartesian3|undefined}
     */
    _sampleNode(nodeId, now) {
        const st = this.nodeInterp.get(nodeId);
        if (!st) return undefined;
        if (now === undefined) {
            now = (typeof performance !== 'undefined')
                ? performance.now() : Date.now();
        }
        let a = (now - st.t0) / st.dur;
        if (a <= 0) return st.prev;
        if (a >= 1) return st.target;
        return Cesium.Cartesian3.lerp(st.prev, st.target, a, st.result);
    }

    // ======================================================================
    // Link Management
    // ======================================================================

    /**
     * Add or update a link between two nodes.
     * @param {string} linkId - Unique link identifier (e.g. "Sat-0-1--Sat-0-2")
     * @param {string} source - Source node ID
     * @param {string} target - Target node ID
     * @param {object} properties - {type, is_active, bandwidth_utilization, latency_ms, loss_rate}
     */
    addOrUpdateLink(linkId, source, target, properties = {}) {
        try {
            const sourceEntity = this.entities.nodes.get(source);
            const targetEntity = this.entities.nodes.get(target);

            if (!sourceEntity || !targetEntity) {
                return null;
            }

            const linkType = properties.type || 'isl';
            const util = properties.bandwidth_utilization || 0;

            if (!this.entities.links.has(linkId)) {
                const color = this._getLinkDisplayColor(linkType, util, properties);
                const width = this._getLinkWidth(linkType);

                const link = this.viewer.entities.add({
                    polyline: {
                        positions: new Cesium.CallbackProperty(() => {
                            const srcPos = sourceEntity.position
                                ? sourceEntity.position.getValue(
                                      this.viewer.clock.currentTime
                                  )
                                : null;
                            const tgtPos = targetEntity.position
                                ? targetEntity.position.getValue(
                                      this.viewer.clock.currentTime
                                  )
                                : null;
                            if (!srcPos || !tgtPos) return [];
                            return [srcPos, tgtPos];
                        }, false),
                        width: width,
                        material: new Cesium.PolylineDashMaterialProperty({
                            color: color,
                        }),
                        clampToGround: false,
                    },
                    properties: {
                        type: 'link',
                        linkType: linkType,
                        linkId: linkId,
                        source: source,
                        target: target,
                        bandwidth_utilization: util,
                        latency_ms: properties.latency_ms || 0,
                        loss_rate: properties.loss_rate || 0,
                        is_active: properties.is_active !== false,
                        // Protocol v3 packet-level telemetry
                        tx_bps: properties.tx_bps || 0,
                        capacity_bps: properties.capacity_bps || 0,
                        queue_depth: properties.queue_depth || 0,
                        queue_capacity: properties.queue_capacity || 0,
                        propagation_ms: properties.propagation_ms || 0,
                    },
                });

                // Apply link type visibility
                link.show = this.linkTypeVisibility[linkType] !== false;

                this.entities.links.set(linkId, link);
                this.stats.links++;
            } else {
                // Update existing link — only touch material if color changed
                const link = this.entities.links.get(linkId);

                if (!this._highlightedLinks.has(linkId)) {
                    const color = this._getLinkDisplayColor(linkType, util, properties);
                    const colorKey = color.toCssColorString();
                    const cached = this._linkColorCache.get(linkId);
                    if (cached !== colorKey) {
                        link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                            color: color,
                        });
                        this._linkColorCache.set(linkId, colorKey);
                    }
                }

                // Update properties (lightweight, no render trigger)
                link.properties.bandwidth_utilization = util;
                link.properties.latency_ms = properties.latency_ms || 0;
                link.properties.loss_rate = properties.loss_rate || 0;
                link.properties.is_active = properties.is_active !== false;
                // Protocol v3 packet-level telemetry
                link.properties.tx_bps = properties.tx_bps || 0;
                link.properties.capacity_bps = properties.capacity_bps || 0;
                link.properties.queue_depth = properties.queue_depth || 0;
                link.properties.queue_capacity = properties.queue_capacity || 0;
                link.properties.propagation_ms = properties.propagation_ms || 0;
            }

            return this.entities.links.get(linkId);

        } catch (error) {
            console.error(`[CesiumManager] Error addOrUpdateLink(${linkId}):`, error);
            return null;
        }
    }

    /**
     * Remove a link that is no longer active.
     */
    removeLink(linkId) {
        const link = this.entities.links.get(linkId);
        if (link) {
            this.viewer.entities.remove(link);
            this.entities.links.delete(linkId);
            this._highlightedLinks.delete(linkId);
            this._linkColorCache.delete(linkId);
            this.stats.links = Math.max(0, this.stats.links - 1);
        }
        if (this.packetFlow) {
            this.packetFlow.dropLink(linkId);
        }
    }

    /**
     * Synchronize links: add/update those in `linksData`, remove stale ones.
     * @param {object} linksData - {linkId: {type, source, target, ...}}
     */
    syncLinks(linksData) {
        const incomingIds = new Set(Object.keys(linksData));

        // Remove links no longer present
        for (const existingId of this.entities.links.keys()) {
            if (!incomingIds.has(existingId)) {
                this.removeLink(existingId);
            }
        }

        // Add or update incoming links
        for (const [linkId, data] of Object.entries(linksData)) {
            this.addOrUpdateLink(linkId, data.source, data.target, data);
        }

        // Reconcile the packet-flow animation layer with the new link set.
        if (this.packetFlow) {
            this.packetFlow.sync();
        }
    }

    /**
     * Phase 7 / protocol 3.1: draw the static ISL mesh announced once in
     * simulation_init (`isl_topology`). Decorative only — faint solid lines
     * that follow the live satellite entities; not pickable, no metrics.
     *
     * Used for small constellations (<= ~200 sats) where drawing all 2N
     * links is cheap. At thousand-satellite scale the caller passes null and
     * ISLs appear only while carrying traffic (via syncLinks), so idle links
     * cost nothing.
     *
     * Must be called AFTER the satellite node entities exist (i.e. after the
     * first state_update has positioned them).
     *
     * @param {Array<[string,string]>|null} pairs - ISL endpoint pairs.
     */
    setStaticISLMesh(pairs) {
        for (const ent of this.entities.islMesh) {
            this.viewer.entities.remove(ent);
        }
        this.entities.islMesh = [];
        if (!pairs || !pairs.length) return;

        const base = this.linkTypeColors.isl || Cesium.Color.CYAN;
        const color = base.withAlpha(0.16);
        const show = this.linkTypeVisibility.isl !== false;
        const clock = this.viewer.clock;

        for (const pair of pairs) {
            const a = this.entities.nodes.get(pair[0]);
            const b = this.entities.nodes.get(pair[1]);
            if (!a || !b) continue;
            const ent = this.viewer.entities.add({
                polyline: {
                    positions: new Cesium.CallbackProperty(() => {
                        const pa = a.position
                            ? a.position.getValue(clock.currentTime) : null;
                        const pb = b.position
                            ? b.position.getValue(clock.currentTime) : null;
                        if (!pa || !pb) return [];
                        return [pa, pb];
                    }, false),
                    width: 1,
                    material: color,
                    clampToGround: false,
                },
                properties: { type: 'isl_mesh' },
                show: show,
            });
            this.entities.islMesh.push(ent);
        }
    }

    // ======================================================================
    // Link Visual Helpers
    // ======================================================================

    /**
     * Get link color: in metrics mode use gradient, otherwise use type color
     * with alpha modulated by utilization.
     */
    _getLinkDisplayColor(linkType, utilization, props) {
        if (this.metricsMode !== 'none') {
            return this._getGradientColor(
                this._linkMetricValue(props || { bandwidth_utilization: utilization },
                                      this.metricsMode)
            );
        }

        // Default: type-based color, alpha scaled by utilization.
        // Quantize util to 0.1 steps so per-frame jitter doesn't force a
        // material rebuild every update (works with _linkColorCache).
        const base = this.linkTypeColors[linkType] || Cesium.Color.WHITE;
        const quantized = Math.round(utilization * 10) / 10;
        const alpha = 0.3 + 0.7 * Math.max(0, Math.min(1, quantized));
        return base.withAlpha(alpha);
    }

    /**
     * Normalize a link metric to 0..1 for the green→red gradient, based on
     * the active coloring mode. `p` is a plain link-properties object.
     */
    _linkMetricValue(p, mode) {
        let v;
        switch (mode) {
            case 'bandwidth':
                v = p.bandwidth_utilization || 0;
                break;
            case 'queue': {
                const cap = p.queue_capacity || 0;
                v = cap > 0 ? (p.queue_depth || 0) / cap : 0;
                break;
            }
            case 'latency':
                v = (p.latency_ms || 0) / 50;
                break;
            case 'loss_rate':
                v = (p.loss_rate || 0) * 100;
                break;
            case 'link_status':
                v = p.is_active ? 0 : 1;
                break;
            default:
                v = p.bandwidth_utilization || 0;
        }
        v = Math.max(0, Math.min(1, v));
        // Quantize so per-tick jitter doesn't rebuild materials every frame.
        return Math.round(v * 20) / 20;
    }

    _getLinkWidth(linkType) {
        // Cross-domain links slightly thicker for visibility
        switch (linkType) {
            case 'gsl': return 1.5;
            case 'sul': return 1.5;
            case 'ssl': return 1.5;
            default: return 1.0;
        }
    }

    /**
     * Green (0) -> Yellow (0.5) -> Red (1.0) gradient
     */
    _getGradientColor(value) {
        const u = Math.max(0, Math.min(1, value));
        let r, g, b;
        if (u < 0.5) {
            r = u * 2;
            g = 1;
            b = 0;
        } else {
            r = 1;
            g = 1 - (u - 0.5) * 2;
            b = 0;
        }
        return new Cesium.Color(r, g, b, 1.0);
    }

    // ======================================================================
    // Visibility & Filtering
    // ======================================================================

    /**
     * Toggle visibility of an entire node type.
     */
    setNodeTypeVisibility(nodeType, visible) {
        this.typeVisibility[nodeType] = visible;
        for (const [id, entity] of this.entities.nodes.entries()) {
            if (entity.properties.type &&
                entity.properties.type.getValue() === nodeType) {
                entity.show = visible;
            }
        }
    }

    /**
     * Toggle visibility of a link type.
     */
    setLinkTypeVisibility(linkType, visible) {
        this.linkTypeVisibility[linkType] = visible;
        for (const [id, link] of this.entities.links.entries()) {
            if (link.properties.linkType &&
                link.properties.linkType.getValue() === linkType) {
                link.show = visible;
            }
        }
        // The static ISL mesh belongs to the 'isl' layer too.
        if (linkType === 'isl') {
            for (const ent of this.entities.islMesh) {
                ent.show = visible;
            }
        }
    }

    /**
     * Filter specific node IDs visible (empty = show all of that type).
     */
    setNodeFilter(nodeType, visibleIds) {
        const idSet = new Set(visibleIds);
        for (const [id, entity] of this.entities.nodes.entries()) {
            if (entity.properties.type &&
                entity.properties.type.getValue() === nodeType) {
                entity.show = visibleIds.length === 0 || idSet.has(id);
            }
        }
    }

    // Backward-compatible
    setSatelliteFilter(visibleIds) {
        this.setNodeFilter('satellite', visibleIds);
    }

    setStationFilter(visibleIds) {
        this.setNodeFilter('ground_station', visibleIds);
    }

    // ======================================================================
    // Metrics Mode
    // ======================================================================

    setMetricsMode(mode) {
        this.metricsMode = mode;

        for (const [linkId, link] of this.entities.links.entries()) {
            if (this._highlightedLinks.has(linkId)) continue;

            const props = link.properties;
            const linkType = props.linkType ? props.linkType.getValue() : 'isl';
            const util = props.bandwidth_utilization || 0;

            // Plain snapshot of live per-link metrics for the color helper.
            const snap = {
                bandwidth_utilization: util,
                latency_ms: props.latency_ms || 0,
                loss_rate: props.loss_rate || 0,
                is_active: props.is_active !== false,
                queue_depth: props.queue_depth || 0,
                queue_capacity: props.queue_capacity || 0,
            };

            const color = this._getLinkDisplayColor(linkType, util, snap);
            link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: color,
            });
            this._linkColorCache.set(linkId, color.toCssColorString());
        }
    }

    /**
     * Toggle the packet-flow animation layer on/off (UI checkbox).
     * @param {boolean} on
     */
    setPacketFlow(on) {
        if (this.packetFlow) {
            this.packetFlow.setEnabled(on);
        }
    }

    // ======================================================================
    // Route Highlighting
    // ======================================================================

    highlightRoute(pathNodes) {
        this.clearRouteHighlights();

        if (!pathNodes || pathNodes.length < 2) return;

        for (let i = 0; i < pathNodes.length - 1; i++) {
            // Try both orderings of link ID
            const id1 = `${pathNodes[i]}--${pathNodes[i + 1]}`;
            const id2 = `${pathNodes[i + 1]}--${pathNodes[i]}`;
            const link = this.entities.links.get(id1) || this.entities.links.get(id2);
            if (link) {
                link.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.3,
                    color: Cesium.Color.CYAN,
                });
                link.polyline.width = new Cesium.ConstantProperty(3);
                this._highlightedLinks.add(link.properties.linkId
                    ? link.properties.linkId.getValue()
                    : id1);
            }
        }
    }

    clearRouteHighlights() {
        for (const linkId of this._highlightedLinks) {
            const link = this.entities.links.get(linkId);
            if (link && link !== this.selectedEntity) {
                const linkType = link.properties.linkType
                    ? link.properties.linkType.getValue()
                    : 'isl';
                const gv = (k) => (link.properties[k] ? link.properties[k].getValue() : 0);
                const util = gv('bandwidth_utilization');
                const snap = {
                    bandwidth_utilization: util,
                    latency_ms: gv('latency_ms'),
                    loss_rate: gv('loss_rate'),
                    is_active: link.properties.is_active
                        ? link.properties.is_active.getValue() : true,
                    queue_depth: gv('queue_depth'),
                    queue_capacity: gv('queue_capacity'),
                };
                const color = this._getLinkDisplayColor(linkType, util, snap);
                link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                    color: color,
                });
                link.polyline.width = new Cesium.ConstantProperty(
                    this._getLinkWidth(linkType)
                );
            }
        }
        this._highlightedLinks.clear();
    }

    // ======================================================================
    // Camera / View Presets
    // ======================================================================

    flyToNode(nodeId) {
        const entity = this.entities.nodes.get(nodeId);
        if (!entity) return;

        const pos = entity.position
            ? entity.position.getValue(this.viewer.clock.currentTime)
            : null;
        if (!pos) return;

        this.viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(
                ...this._cartesianToDegrees(pos),
            ),
            duration: 1.5,
        });
    }

    flyToPreset(preset) {
        const presets = {
            global: { lon: 110, lat: 20, alt: 25000000 },
            south_china_sea: { lon: 116, lat: 18, alt: 2000000 },
            asia_pacific: { lon: 120, lat: 25, alt: 12000000 },
            europe: { lon: 10, lat: 50, alt: 8000000 },
        };
        const p = presets[preset] || presets.global;
        this.viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.alt),
            duration: 2,
        });
    }

    _cartesianToDegrees(cartesian) {
        const carto = Cesium.Cartographic.fromCartesian(cartesian);
        return [
            Cesium.Math.toDegrees(carto.longitude),
            Cesium.Math.toDegrees(carto.latitude),
            carto.height + 500000, // offset above
        ];
    }

    // ======================================================================
    // Utilities
    // ======================================================================

    setAnimationSpeed(speed) {
        this.animationSpeed = speed;
        if (this.viewer) {
            this.viewer.clock.multiplier = speed;
        }
    }

    clearAll() {
        this.viewer.entities.removeAll();
        this.entities.nodes.clear();
        this.entities.links.clear();
        this.entities.islMesh = [];
        this.nodeInterp.clear();
        this._highlightedLinks.clear();
        this._linkColorCache.clear();
        if (this.packetFlow) {
            this.packetFlow.clear();
        }
        this.selectedEntity = null;
        this.stats = {
            satellites: 0,
            uavs: 0,
            ships: 0,
            ground_stations: 0,
            links: 0,
            fps: this.stats.fps,
        };
    }

    getStats() {
        return { ...this.stats };
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CesiumManager;
}
