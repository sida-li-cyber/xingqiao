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
                this.selectEntity(picked.id);
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

        if (!entity || !entity.properties) return;

        const type = entity.properties.type
            ? entity.properties.type.getValue()
            : null;

        if (type === 'link') {
            entity.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                glowPower: 0.25,
                color: Cesium.Color.CYAN,
            });
            entity.polyline.width = new Cesium.ConstantProperty(3);
        } else if (type && this.nodeTypes[type]) {
            entity.point.color = new Cesium.ConstantProperty(Cesium.Color.CYAN);
            entity.point.pixelSize = new Cesium.ConstantProperty(
                this.nodeTypes[type].pixelSize + 5
            );
            if (entity.label) {
                entity.label.show = new Cesium.ConstantProperty(true);
            }
        }
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
            const color = this._getLinkDisplayColor(linkType, util);
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
                // Create new entity
                const entity = this.viewer.entities.add({
                    id: `${nodeType}-${nodeId}`,
                    position: cartesian,
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
                // Update existing entity position
                const entity = this.entities.nodes.get(nodeId);
                entity.position = cartesian;

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
                const color = this._getLinkDisplayColor(linkType, util);
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
                    },
                });

                // Apply link type visibility
                link.show = this.linkTypeVisibility[linkType] !== false;

                this.entities.links.set(linkId, link);
                this.stats.links++;
            } else {
                // Update existing link
                const link = this.entities.links.get(linkId);
                const color = this._getLinkDisplayColor(linkType, util);

                if (!this._highlightedLinks.has(linkId)) {
                    link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                        color: color,
                    });
                }

                // Update properties
                link.properties.bandwidth_utilization = util;
                link.properties.latency_ms = properties.latency_ms || 0;
                link.properties.loss_rate = properties.loss_rate || 0;
                link.properties.is_active = properties.is_active !== false;
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
            this.stats.links = Math.max(0, this.stats.links - 1);
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
    }

    // ======================================================================
    // Link Visual Helpers
    // ======================================================================

    /**
     * Get link color: in metrics mode use gradient, otherwise use type color
     * with alpha modulated by utilization.
     */
    _getLinkDisplayColor(linkType, utilization) {
        if (this.metricsMode !== 'none') {
            let metricValue = 0;
            switch (this.metricsMode) {
                case 'bandwidth':
                    metricValue = utilization;
                    break;
                case 'latency':
                    metricValue = utilization; // caller normalizes
                    break;
                case 'loss_rate':
                    metricValue = utilization;
                    break;
                default:
                    metricValue = utilization;
            }
            return this._getGradientColor(metricValue);
        }

        // Default: type-based color, alpha scaled by utilization
        const base = this.linkTypeColors[linkType] || Cesium.Color.WHITE;
        const alpha = 0.3 + 0.7 * Math.max(0, Math.min(1, utilization));
        return base.withAlpha(alpha);
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
            let metricValue = 0;

            switch (mode) {
                case 'bandwidth':
                    metricValue = props.bandwidth_utilization || 0;
                    break;
                case 'latency':
                    metricValue = Math.min(1, (props.latency_ms || 0) / 50);
                    break;
                case 'loss_rate':
                    metricValue = Math.min(1, (props.loss_rate || 0) * 100);
                    break;
                case 'link_status':
                    metricValue = (props.is_active) ? 0 : 1;
                    break;
                default:
                    metricValue = props.bandwidth_utilization || 0;
                    break;
            }

            const color = mode === 'none'
                ? this._getLinkDisplayColor(linkType, props.bandwidth_utilization || 0)
                : this._getGradientColor(metricValue);

            link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: color,
            });
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
                const util = link.properties.bandwidth_utilization || 0;
                const color = this._getLinkDisplayColor(linkType, util);
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
        this._highlightedLinks.clear();
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
