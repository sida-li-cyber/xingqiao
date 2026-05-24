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
            this.scene.backgroundColor = Cesium.Color.fromCssColorString('#0a1628');
            this.scene.highDynamicRange = false;

            // Disable day/night lighting so the entire globe surface is uniformly
            // visible — satellites and links stay visible against the Earth at all
            // longitudes, not just the sunlit hemisphere.
            this.scene.globe.enableLighting = false;
            // Light base color as fallback before imagery tiles load.
            this.scene.globe.baseColor = Cesium.Color.fromCssColorString('#1a3a5c');

            // Replace default (Bing) imagery with NaturalEarthII tiles loaded
            // via UrlTemplateImageryProvider.  Uses <img> tags (no CORS) so
            // the tilemapresource.xml XHR step is skipped entirely.
            // Use the full CDN URL directly instead of buildModuleUrl to
            // guarantee the {z}/{x}/{reverseY} template variables stay
            // literal and aren't percent-encoded.
            this.viewer.imageryLayers.removeAll();
            this.viewer.imageryLayers.addImageryProvider(
                new Cesium.UrlTemplateImageryProvider({
                    url: 'https://cesium.com/downloads/cesiumjs/releases/1.141/Build/Cesium/Assets/Textures/NaturalEarthII/{z}/{x}/{reverseY}.jpg',
                    tilingScheme: new Cesium.GeographicTilingScheme(),
                    maximumLevel: 2,
                })
            );

            // Configure camera
            this.viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(0, 20, 25000000),
                duration: 2,
            });

            // Move InfoBox to top-left so it doesn't overlap the control panel.
            // Cesium 1.141 defaults: position:absolute; top:50px; right:0;
            // border-right:none.
            const ib = this.viewer.container.querySelector('.cesium-infoBox');
            if (ib) {
                ib.style.setProperty('left', '10px', 'important');
                ib.style.setProperty('right', 'auto', 'important');
                ib.style.setProperty('top', '50px', 'important');
                // Flip border: was right-anchored (border-right:none),
                // now left-anchored → border-left:none, radii on right side.
                ib.style.setProperty('border-left', 'none', 'important');
                ib.style.setProperty('border-right', '1px solid #444', 'important');
                ib.style.setProperty('border-top-left-radius', '0', 'important');
                ib.style.setProperty('border-bottom-left-radius', '0', 'important');
                ib.style.setProperty('border-top-right-radius', '7px', 'important');
                ib.style.setProperty('border-bottom-right-radius', '7px', 'important');
            }

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
                var cw = this._colorForMode(this.metricsMode, properties);
                var linkColor = cw.color;
                var linkWidth = this._widthForNorm(cw.norm);

                const link = this.viewer.entities.add({
                    id: `link-${linkId}`,
                    name: `Link ${source} ↔ ${target}`,
                    description: this._buildLinkDescription(properties, this.metricsMode),
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
                        width: linkWidth,
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
                        loss_base: properties.loss_base != null ? properties.loss_base : 0,
                        loss_jitter: properties.loss_jitter != null ? properties.loss_jitter : 0,
                        loss_rate: properties.loss_rate || 0,
                        is_active: properties.is_active !== false,
                        color: linkColor,
                    },
                });

                this.entities.links.set(linkId, link);
                this.stats.links++;
            } else {
                const link = this.entities.links.get(linkId);

                // Store new property values first
                link.properties.bandwidth_utilization = properties.bandwidth_utilization || 0;
                link.properties.latency = properties.latency || 0;
                link.properties.loss_base = properties.loss_base != null ? properties.loss_base : 0;
                link.properties.loss_jitter = properties.loss_jitter != null ? properties.loss_jitter : 0;
                link.properties.loss_rate = properties.loss_rate || 0;
                link.properties.is_active = properties.is_active !== false;

                // Color + width by current metric mode
                var cw = this._colorForMode(this.metricsMode, link.properties);
                link.properties.color = cw.color;
                link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                    color: cw.color,
                });
                link.polyline.width = this._widthForNorm(cw.norm);

                // Keep the InfoBox description up-to-date
                link.description = this._buildLinkDescription(properties, this.metricsMode);
            }

            return this.entities.links.get(linkId);

        } catch (error) {
            console.error('[CesiumManager] Error adding link:', linkId, error);
            return null;
        }
    }

    /**
     * Build HTML description for the Cesium InfoBox when a link is clicked.
     */
    _buildLinkDescription(props, mode) {
        var modeKey = mode || this.metricsMode || 'none';
        var bw = ((props.bandwidth_utilization || 0) * 100).toFixed(0);
        var lat = (props.latency || 0).toFixed(1);
        var base = ((props.loss_base != null ? props.loss_base : 0) * 100).toFixed(2);
        var jitter = ((props.loss_jitter != null ? props.loss_jitter : 0) * 100).toFixed(2);
        var total = ((props.loss_rate || 0) * 100).toFixed(2);
        var active = props.is_active !== false;

        var td = 'style="padding:3px 12px 3px 0;color:#aaa;"';
        var tv = 'style="padding:3px 0;font-weight:600;"';

        var html = '<table style="font-size:13px;border-collapse:collapse;">';

        switch (modeKey) {
            case 'bandwidth':
                html += '<tr><td ' + td + '>Bandwidth</td><td ' + tv + '>' + bw + '%</td></tr>';
                break;
            case 'latency':
                html += '<tr><td ' + td + '>Latency</td><td ' + tv + '>' + lat + ' ms</td></tr>';
                break;
            case 'loss_rate':
                html += '<tr><td ' + td + '>Packet Loss Rate</td><td ' + tv + '>' + total + '%</td></tr>';
                html += '<tr><td ' + td + '>&nbsp;&nbsp;Scenario Base</td><td style="padding:3px 0;">' + base + '%</td></tr>';
                html += '<tr><td ' + td + '>&nbsp;&nbsp;Jitter</td><td style="padding:3px 0;">' + jitter + '%</td></tr>';
                break;
            case 'link_status':
                html += '<tr><td ' + td + '>Status</td><td ' + tv + ' style="color:' + (active ? '#4caf50' : '#f44336') + '">' + (active ? 'Active' : 'Inactive') + '</td></tr>';
                break;
            default:
                html += '<tr><td ' + td + '>Bandwidth</td><td ' + tv + '>' + bw + '%</td></tr>';
                html += '<tr><td ' + td + '>Latency</td><td ' + tv + '>' + lat + ' ms</td></tr>';
                html += '<tr><td ' + td + '>Loss Rate</td><td style="padding:3px 0;">' + total + '%</td></tr>';
                break;
        }

        html += '</table>';
        return html;
    }

    // ---- Color-stop tables (normalized 0-1 → hex values) ----

    /** Generic piecewise-linear color interpolator over stops [{t, r, g, b}] */
    _interpolateColor(u, stops) {
        var v = Math.max(0, Math.min(1, u));
        var lo = stops[0], hi = stops[stops.length - 1];
        for (var i = 0; i < stops.length - 1; i++) {
            if (v >= stops[i].t && v <= stops[i + 1].t) { lo = stops[i]; hi = stops[i + 1]; break; }
        }
        var seg = hi.t - lo.t;
        var f = seg > 0 ? (v - lo.t) / seg : 0;
        var r = Math.round(lo.r + (hi.r - lo.r) * f);
        var g = Math.round(lo.g + (hi.g - lo.g) * f);
        var b = Math.round(lo.b + (hi.b - lo.b) * f);
        return new Cesium.Color(r / 255, g / 255, b / 255, 1.0);
    }

    /** Bandwidth utilization → green-yellow-orange-red (5-segment) */
    getLinkColor(utilization) {
        return this._interpolateColor(utilization, [
            { t: 0.0,  r: 0x00, g: 0x64, b: 0x00 },
            { t: 0.3,  r: 0x00, g: 0xFF, b: 0x00 },
            { t: 0.5,  r: 0xAD, g: 0xFF, b: 0x2F },
            { t: 0.7,  r: 0xFF, g: 0xFF, b: 0x00 },
            { t: 0.85, r: 0xFF, g: 0x8C, b: 0x00 },
            { t: 1.0,  r: 0x8B, g: 0x00, b: 0x00 },
        ]);
    }

    /** End-to-end latency (ms) → cyan-green-yellow-orange-red */
    getLatencyColor(ms) {
        return this._interpolateColor(Math.min(1, ms / 100), [
            { t: 0.0, r: 0x87, g: 0xF5, b: 0xFF },
            { t: 0.2, r: 0x36, g: 0xE8, b: 0xA8 },
            { t: 0.4, r: 0xF9, g: 0xF8, b: 0x71 },
            { t: 0.7, r: 0xFF, g: 0xB3, b: 0x47 },
            { t: 1.0, r: 0xFF, g: 0x6B, b: 0x35 },
        ]);
    }

    /** Packet loss rate (0-1) → blue-cyan-green-orange-red */
    getLossRateColor(rate) {
        return this._interpolateColor(Math.min(1, rate * 5), [  // 0.2 (20%) → 1.0
            { t: 0.0,  r: 0x20, g: 0xA4, b: 0xF3 },
            { t: 0.05, r: 0x5E, g: 0xD9, b: 0xFF },
            { t: 0.15, r: 0x64, g: 0xDD, b: 0x78 },
            { t: 0.35, r: 0xFF, g: 0xC8, b: 0x57 },
            { t: 0.7,  r: 0xFF, g: 0x57, b: 0x22 },
            { t: 1.0,  r: 0x9E, g: 0x00, b: 0x00 },
        ]);
    }

    /**
     * Map the current metric to a color + normalized value for width.
     * Returns {color, norm} where norm is 0–1 for line width scaling.
     */
    _colorForMode(mode, props) {
        var bw = props.bandwidth_utilization || 0;
        var lat = props.latency || 0;
        var loss = props.loss_rate || 0;
        switch (mode) {
            case 'bandwidth':   return { color: this.getLinkColor(bw),       norm: bw };
            case 'latency':     return { color: this.getLatencyColor(lat),   norm: Math.min(1, lat / 100) };
            case 'loss_rate':   return { color: this.getLossRateColor(loss), norm: Math.min(1, loss * 5) };
            case 'link_status': return { color: this.getLinkColor(props.is_active !== false ? 0 : 1), norm: 0 };
            default:            return { color: this.getLinkColor(bw),       norm: bw };
        }
    }

    /** Line width from normalized value: 1.2px (min) → 5px (max) */
    _widthForNorm(norm) {
        return 1.2 + Math.max(0, Math.min(1, norm)) * 3.8;
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
        this.selectedEntity = null;
        this.viewer.entities.removeAll();
        this.entities.satellites.clear();
        this.entities.stations.clear();
        this.entities.links.clear();
        this.stats.satellites = 0;
        this.stats.stations = 0;
        this.stats.links = 0;
    }

    /**
     * Clear all entities for constellation switch (alias for clearAll).
     */
    clearAllEntities() {
        this.clearAll();
    }

    /**
     * Set metrics display mode
     */
    setMetricsMode(mode) {
        this.metricsMode = mode;

        for (const [linkId, link] of this.entities.links.entries()) {
            const props = link.properties;
            var cw = this._colorForMode(mode, props);
            link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: cw.color,
            });
            link.polyline.width = this._widthForNorm(cw.norm);
            link.description = this._buildLinkDescription(props, mode);
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
