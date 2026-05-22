/**
 * Cesium Manager Module
 * Handles 3D scene management and visualization
 */

class CesiumManager {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.viewer = null;
        this.scene = null;
        this.entities = {
            satellites: new Map(),
            stations: new Map(),
            links: new Map(),
        };
        this.cesiumToken = options.cesiumToken || '';
        this.visibleSatellites = new Set();
        this.visibleStations = new Set();
        this.selectedEntity = null;
        this.metricsMode = 'none';
        this.animationSpeed = 1.0;

        this.stats = {
            satellites: 0,
            stations: 0,
            links: 0,
            fps: 0,
        };

        this.frameCount = 0;
        this.lastFpsTime = Date.now();
    }

    /**
     * Initialize Cesium viewer
     */
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

            // Configure scene
            this.scene.backgroundColor = Cesium.Color.BLACK;
            this.scene.highDynamicRange = false;

            // Remove default imagery and add a dark-style base map
            this.viewer.imageryLayers.removeAll();
            this.viewer.imageryLayers.addImageryProvider(
                new Cesium.TileMapServiceImageryProvider({
                    url: Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
                })
            );

            // Configure camera
            this.viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(0, 20, 25000000),
                duration: 2,
            });

            // Set up event handlers
            this.setupEventHandlers();

            // Start FPS counter
            this.startFpsCounter();

            console.log('[CesiumManager] Viewer initialized successfully');
            return true;

        } catch (error) {
            console.error('[CesiumManager] Initialization failed:', error);
            return false;
        }
    }

    /**
     * Set up event handlers for picking and selection
     */
    setupEventHandlers() {
        const handler = new Cesium.ScreenSpaceEventHandler(this.scene.canvas);

        // Left click - select entity
        handler.setInputAction((click) => {
            const pickedObject = this.scene.pick(click.position);
            if (pickedObject && pickedObject.id) {
                this.selectEntity(pickedObject.id);
            } else {
                this.selectEntity(null);
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        // Right click - deselect
        handler.setInputAction(() => {
            this.selectEntity(null);
        }, Cesium.ScreenSpaceEventType.RIGHT_CLICK);
    }

    /**
     * Start FPS counter
     */
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

    /**
     * Select an entity and highlight it
     */
    selectEntity(entity) {
        // Deselect previous
        if (this.selectedEntity && this.selectedEntity.id) {
            const prevProps = this.selectedEntity.properties;
            if (prevProps) {
                if (prevProps.type.getValue() === 'link') {
                    const origColor = prevProps.color
                        ? prevProps.color.getValue()
                        : Cesium.Color.WHITE;
                    this.selectedEntity.polyline.material = new Cesium.PolylineDashMaterialProperty({
                        color: origColor,
                    });
                    this.selectedEntity.polyline.width = new Cesium.ConstantProperty(1);
                } else if (prevProps.type.getValue() === 'satellite') {
                    this.selectedEntity.point.color = new Cesium.ConstantProperty(Cesium.Color.DODGERBLUE);
                    this.selectedEntity.point.pixelSize = new Cesium.ConstantProperty(5);
                } else if (prevProps.type.getValue() === 'station') {
                    this.selectedEntity.point.color = new Cesium.ConstantProperty(Cesium.Color.ORANGERED);
                    this.selectedEntity.point.pixelSize = new Cesium.ConstantProperty(8);
                }
            }
        }

        // Select new
        this.selectedEntity = entity;
        if (entity && entity.properties) {
            const type = entity.properties.type.getValue();
            if (type === 'link') {
                entity.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.25,
                    color: Cesium.Color.CYAN,
                });
                entity.polyline.width = new Cesium.ConstantProperty(3);
            } else if (type === 'satellite') {
                entity.point.color = new Cesium.ConstantProperty(Cesium.Color.CYAN);
                entity.point.pixelSize = new Cesium.ConstantProperty(10);
            } else if (type === 'station') {
                entity.point.color = new Cesium.ConstantProperty(Cesium.Color.YELLOW);
                entity.point.pixelSize = new Cesium.ConstantProperty(14);
            }
        }
    }

    /**
     * Add or update satellite
     */
    addOrUpdateSatellite(satelliteId, position, properties = {}) {
        try {
            const cartesian = Cesium.Cartesian3.fromDegrees(
                position.lon, position.lat, position.alt
            );

            if (!this.entities.satellites.has(satelliteId)) {
                const satellite = this.viewer.entities.add({
                    id: `sat-${satelliteId}`,
                    position: cartesian,
                    point: {
                        pixelSize: 5,
                        color: Cesium.Color.DODGERBLUE,
                        outlineColor: Cesium.Color.WHITE,
                        outlineWidth: 1,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    label: {
                        text: properties.name || satelliteId,
                        font: '11px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        pixelOffset: new Cesium.Cartesian2(0, -12),
                        show: false,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    properties: {
                        type: 'satellite',
                        satelliteId: satelliteId,
                        ...properties,
                    },
                });

                this.entities.satellites.set(satelliteId, satellite);
                this.stats.satellites++;
            } else {
                const satellite = this.entities.satellites.get(satelliteId);
                satellite.position = cartesian;

                for (const [key, value] of Object.entries(properties)) {
                    if (satellite.properties.hasProperty(key)) {
                        satellite.properties[key] = value;
                    } else {
                        satellite.properties.addProperty(key, value);
                    }
                }
            }

            return this.entities.satellites.get(satelliteId);

        } catch (error) {
            console.error('[CesiumManager] Error adding satellite:', satelliteId, error);
            return null;
        }
    }

    /**
     * Add or update ground station
     */
    addOrUpdateStation(stationId, position, properties = {}) {
        try {
            const cartesian = Cesium.Cartesian3.fromDegrees(
                position.lon, position.lat, 0
            );

            if (!this.entities.stations.has(stationId)) {
                const station = this.viewer.entities.add({
                    id: `sta-${stationId}`,
                    position: cartesian,
                    point: {
                        pixelSize: 8,
                        color: Cesium.Color.ORANGERED,
                        outlineColor: Cesium.Color.WHITE,
                        outlineWidth: 2,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    label: {
                        text: properties.name || stationId,
                        font: '12px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        pixelOffset: new Cesium.Cartesian2(0, -18),
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    },
                    properties: {
                        type: 'station',
                        stationId: stationId,
                        ...properties,
                    },
                });

                this.entities.stations.set(stationId, station);
                this.stats.stations++;
            } else {
                const station = this.entities.stations.get(stationId);
                station.position = cartesian;

                for (const [key, value] of Object.entries(properties)) {
                    if (station.properties.hasProperty(key)) {
                        station.properties[key] = value;
                    } else {
                        station.properties.addProperty(key, value);
                    }
                }
            }

            return this.entities.stations.get(stationId);

        } catch (error) {
            console.error('[CesiumManager] Error adding station:', stationId, error);
            return null;
        }
    }

    /**
     * Add or update link between two entities
     */
    addOrUpdateLink(linkId, source, target, properties = {}) {
        try {
            const sourceEntity =
                this.entities.satellites.get(source) || this.entities.stations.get(source);
            const targetEntity =
                this.entities.satellites.get(target) || this.entities.stations.get(target);

            if (!sourceEntity || !targetEntity) {
                return null;
            }

            if (!this.entities.links.has(linkId)) {
                const linkColor = this.getLinkColor(properties.bandwidth_utilization || 0);

                const link = this.viewer.entities.add({
                    polyline: {
                        positions: new Cesium.CallbackProperty(() => {
                            const srcPos = sourceEntity.position
                                ? sourceEntity.position.getValue(this.viewer.clock.currentTime)
                                : null;
                            const tgtPos = targetEntity.position
                                ? targetEntity.position.getValue(this.viewer.clock.currentTime)
                                : null;
                            if (!srcPos || !tgtPos) return [];
                            return [srcPos, tgtPos];
                        }, false),
                        width: 1,
                        material: new Cesium.PolylineDashMaterialProperty({
                            color: linkColor,
                        }),
                        clampToGround: false,
                    },
                    properties: {
                        type: 'link',
                        linkId: linkId,
                        source: source,
                        target: target,
                        bandwidth_utilization: properties.bandwidth_utilization || 0,
                        latency: properties.latency || 0,
                        loss_rate: properties.loss_rate || 0,
                        is_active: properties.is_active !== false,
                        color: linkColor,
                    },
                });

                this.entities.links.set(linkId, link);
                this.stats.links++;
            } else {
                const link = this.entities.links.get(linkId);
                const newColor = this.getLinkColor(properties.bandwidth_utilization || 0);

                link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                    color: newColor,
                });

                link.properties.bandwidth_utilization = properties.bandwidth_utilization || 0;
                link.properties.latency = properties.latency || 0;
                link.properties.loss_rate = properties.loss_rate || 0;
                link.properties.is_active = properties.is_active !== false;
                link.properties.color = newColor;
            }

            return this.entities.links.get(linkId);

        } catch (error) {
            console.error('[CesiumManager] Error adding link:', linkId, error);
            return null;
        }
    }

    /**
     * Determine link color based on bandwidth utilization
     * Smooth gradient: green (0) -> yellow (0.5) -> red (1.0)
     */
    getLinkColor(utilization) {
        const u = Math.max(0, Math.min(1, utilization));
        let r, g, b;
        if (u < 0.5) {
            // Green to Yellow
            r = u * 2 * 255;
            g = 255;
            b = 0;
        } else {
            // Yellow to Red
            r = 255;
            g = (1 - (u - 0.5) * 2) * 255;
            b = 0;
        }
        return new Cesium.Color(r / 255, g / 255, b / 255, 1.0);
    }

    /**
     * Set satellite visibility filter
     */
    setSatelliteFilter(visibleIds) {
        this.visibleSatellites = new Set(visibleIds);

        for (const [id, satellite] of this.entities.satellites.entries()) {
            satellite.show = visibleIds.length === 0 || this.visibleSatellites.has(id);
        }
    }

    /**
     * Set station visibility filter
     */
    setStationFilter(visibleIds) {
        this.visibleStations = new Set(visibleIds);

        for (const [id, station] of this.entities.stations.entries()) {
            station.show = visibleIds.length === 0 || this.visibleStations.has(id);
        }
    }

    /**
     * Clear all entities
     */
    clearAll() {
        this.viewer.entities.removeAll();
        this.entities.satellites.clear();
        this.entities.stations.clear();
        this.entities.links.clear();
        this.stats.satellites = 0;
        this.stats.stations = 0;
        this.stats.links = 0;
    }

    /**
     * Set metrics display mode
     */
    setMetricsMode(mode) {
        this.metricsMode = mode;

        for (const [linkId, link] of this.entities.links.entries()) {
            const props = link.properties;
            let utilization = 0;

            switch (mode) {
                case 'bandwidth':
                    utilization = props.bandwidth_utilization || 0;
                    break;
                case 'latency':
                    utilization = Math.min(1, (props.latency || 0) / 100);
                    break;
                case 'loss_rate':
                    utilization = Math.min(1, (props.loss_rate || 0) * 10);
                    break;
                case 'link_status':
                    utilization = props.is_active ? 0 : 1;
                    break;
                default:
                    utilization = 0;
                    break;
            }

            const newColor = this.getLinkColor(utilization);
            link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: newColor,
            });
        }
    }

    /**
     * Set animation speed
     */
    setAnimationSpeed(speed) {
        this.animationSpeed = speed;
        if (this.viewer) {
            this.viewer.clock.multiplier = speed;
        }
    }

    /**
     * Get statistics
     */
    getStats() {
        return {
            satellites: this.stats.satellites,
            stations: this.stats.stations,
            links: this.stats.links,
            fps: this.stats.fps,
        };
    }

    /**
     * Highlight a specific route path
     */
    highlightRoute(pathNodes) {
        if (!pathNodes || pathNodes.length < 2) return;

        for (let i = 0; i < pathNodes.length - 1; i++) {
            const linkId = `${pathNodes[i]}-${pathNodes[i + 1]}`;
            const link = this.entities.links.get(linkId);
            if (link) {
                link.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.3,
                    color: Cesium.Color.CYAN,
                });
                link.polyline.width = new Cesium.ConstantProperty(3);
            }
        }
    }

    /**
     * Reset all route highlights to utilization-based colors
     */
    clearRouteHighlights() {
        for (const [linkId, link] of this.entities.links.entries()) {
            if (link !== this.selectedEntity) {
                const utilization = link.properties.bandwidth_utilization || 0;
                const color = this.getLinkColor(utilization);
                link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                    color: color,
                });
                link.polyline.width = new Cesium.ConstantProperty(1);
            }
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CesiumManager;
}
