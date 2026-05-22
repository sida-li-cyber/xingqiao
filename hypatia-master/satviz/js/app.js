/**
 * Main Application Module
 * Integrates all components and manages application state
 */

class SatelliteVisualizationApp {
    constructor() {
        this.cesium = null;
        this.ws = null;
        this.ui = null;
        this.simulationTime = 0;
        this.simulationDuration = 100;
        this.updateInterval = null;
        this.satelliteList = [];
        this.stationList = [];
        this.currentRoute = null;
    }

    /**
     * Initialize the application
     */
    async initialize() {
        console.log('[App] Initializing Satellite Visualization Application');

        try {
            // Initialize Cesium Manager
            const cesiumToken = localStorage.getItem('cesiumToken');
            this.cesium = new CesiumManager('cesiumContainer', { cesiumToken });
            this.cesium.initialize();

            // Initialize WebSocket Manager
            this.ws = new WebSocketManager({
                host: window.location.hostname || 'localhost',
                port: 8000,
                path: '/ws/client',
                onConnect: () => this.handleWSConnect(),
                onDisconnect: () => this.handleWSDisconnect(),
                onStateUpdate: (payload) => this.handleStateUpdate(payload),
                onSimulationInit: (payload) => this.handleSimulationInit(payload),
                onAck: (payload) => this.handleAck(payload),
                onError: (payload) => this.handleWSError(payload),
                onMessage: (message) => this.handleWSMessage(message),
            });

            // Initialize UI Controller (after cesium and ws exist)
            this.ui = new UIController(this.cesium, this.ws, this);
            this.ui.initializeUI();

            // Load saved Cesium token
            this.ui.loadCesiumToken();

            // Connect to WebSocket
            this.ws.connect();

            // Start statistics update loop
            this.startStatisticsLoop();

            console.log('[App] Application initialized successfully');

        } catch (error) {
            console.error('[App] Initialization failed:', error);
        }
    }

    /**
     * Handle simulation_init message from backend
     * Provides initial constellation info, ground stations, and duration
     */
    handleSimulationInit(payload) {
        console.log('[App] Simulation initialized:', payload);

        if (payload.duration) {
            this.simulationDuration = payload.duration;
            this.ui.setTimelineRange(0, payload.duration);
        }

        if (payload.satellites) {
            this.satelliteList = payload.satellites;
            this.ui.populateSatelliteFilter(payload.satellites);
        }

        if (payload.ground_stations) {
            this.stationList = [];
            for (const [id, pos] of Object.entries(payload.ground_stations)) {
                this.stationList.push(id);
                this.cesium.addOrUpdateStation(id, pos, { name: pos.name || id });
            }
            this.ui.populateStationFilter(this.stationList);
        }
    }

    /**
     * Handle WebSocket connection
     */
    handleWSConnect() {
        console.log('[App] WebSocket connected');
        this.ui.updateConnectionStatus(true);
    }

    /**
     * Handle WebSocket disconnection
     */
    handleWSDisconnect() {
        console.log('[App] WebSocket disconnected');
        this.ui.updateConnectionStatus(false);
    }

    /**
     * Handle incoming state updates from backend
     */
    handleStateUpdate(payload) {
        try {
            // Update satellite positions
            if (payload.satellite_positions) {
                this.updateSatellitePositions(payload.satellite_positions);
            }

            // Update ground station positions (if dynamic)
            if (payload.ground_stations) {
                this.updateGroundStations(payload.ground_stations);
            }

            // Update link statuses
            if (payload.link_status) {
                this.updateLinkStatus(payload.link_status);
            }

            // Update routing information
            if (payload.routing) {
                this.updateRouting(payload.routing);
            }

            // Update bandwidth utilization metrics
            if (payload.bandwidth_utilization) {
                this.updateBandwidthMetrics(payload.bandwidth_utilization);
            }

            // Update simulation time
            if (payload.timestamp) {
                const ts = typeof payload.timestamp === 'number'
                    ? payload.timestamp
                    : new Date(payload.timestamp).getTime() / 1000;
                this.simulationTime = ts;
                this.ui.updateTimeDisplay(ts);
            }

        } catch (error) {
            console.error('[App] Error processing state update:', error);
        }
    }

    /**
     * Handle acknowledgment messages
     */
    handleAck(payload) {
        console.log('[App] ACK:', payload.action, payload.status);
    }

    /**
     * Handle generic WebSocket messages
     */
    handleWSMessage(message) {
        if (message.message_type && !['state_update', 'simulation_init', 'ack', 'error'].includes(message.message_type)) {
            console.log('[App] Received message:', message.message_type);
        }
    }

    /**
     * Handle WebSocket errors
     */
    handleWSError(payload) {
        console.error('[App] Server error:', payload);
        if (payload && payload.detail) {
            console.error('[App] Error detail:', payload.detail);
        }
    }

    /**
     * Update satellite positions in the 3D scene
     */
    updateSatellitePositions(positions) {
        const newSatIds = Object.keys(positions);

        // Add new satellites to filter list if needed
        const knownIds = new Set(this.satelliteList);
        const newIds = newSatIds.filter((id) => !knownIds.has(id));
        if (newIds.length > 0) {
            this.satelliteList = [...this.satelliteList, ...newIds];
            this.ui.populateSatelliteFilter(this.satelliteList);
        }

        for (const [satId, position] of Object.entries(positions)) {
            this.cesium.addOrUpdateSatellite(satId, position, {
                name: position.name || satId,
            });
        }
    }

    /**
     * Update ground station positions
     */
    updateGroundStations(stations) {
        const newStaIds = Object.keys(stations);

        const knownIds = new Set(this.stationList);
        const newIds = newStaIds.filter((id) => !knownIds.has(id));
        if (newIds.length > 0) {
            this.stationList = [...this.stationList, ...newIds];
            this.ui.populateStationFilter(this.stationList);
        }

        for (const [staId, position] of Object.entries(stations)) {
            this.cesium.addOrUpdateStation(staId, position, {
                name: position.name || staId,
            });
        }
    }

    /**
     * Update link status and visualization
     */
    updateLinkStatus(linkStatus) {
        for (const [linkId, status] of Object.entries(linkStatus)) {
            const parts = linkId.split('-');
            if (parts.length === 2) {
                const [source, target] = parts;

                this.cesium.addOrUpdateLink(linkId, source, target, {
                    bandwidth_utilization: status.bandwidth_utilization || 0,
                    latency: status.latency || 0,
                    loss_rate: status.loss_rate || 0,
                    is_active: status.is_active !== false,
                });
            }
        }
    }

    /**
     * Update routing information
     */
    updateRouting(routing) {
        if (routing.highlight_path && routing.highlight_path.length >= 2) {
            // Only re-highlight if route changed
            const routeKey = routing.highlight_path.join(',');
            if (this.currentRoute !== routeKey) {
                this.currentRoute = routeKey;
                this.cesium.clearRouteHighlights();
                this.cesium.highlightRoute(routing.highlight_path);
            }
        } else if (!routing.highlight_path) {
            if (this.currentRoute) {
                this.currentRoute = null;
                this.cesium.clearRouteHighlights();
            }
        }
    }

    /**
     * Update bandwidth metrics
     */
    updateBandwidthMetrics(metrics) {
        for (const [linkId, utilization] of Object.entries(metrics)) {
            const link = this.cesium.entities.links.get(linkId);
            if (link) {
                const newColor = this.cesium.getLinkColor(utilization);
                link.polyline.material = new Cesium.PolylineDashMaterialProperty({
                    color: newColor,
                });
                link.properties.bandwidth_utilization = utilization;
                link.properties.color = newColor;
            }
        }
    }

    /**
     * Start statistics update loop
     */
    startStatisticsLoop() {
        if (this.updateInterval) clearInterval(this.updateInterval);
        this.updateInterval = setInterval(() => {
            const stats = this.cesium.getStats();
            this.ui.updateStatistics(stats);
        }, 500);
    }

    /**
     * Stop the application
     */
    stop() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
        if (this.ws) {
            this.ws.disconnect();
        }
    }
}

/**
 * Global application instance
 */
let app = null;

/**
 * Initialize application when DOM is ready
 */
document.addEventListener('DOMContentLoaded', () => {
    app = new SatelliteVisualizationApp();
    app.initialize();
});

/**
 * Cleanup when leaving the page
 */
window.addEventListener('beforeunload', () => {
    if (app) {
        app.stop();
    }
});

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SatelliteVisualizationApp;
}
