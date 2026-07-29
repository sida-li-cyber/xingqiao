/**
 * Main Application Module v2 — Multi-Domain
 * Integrates CesiumManager, WebSocketManager, UIController for
 * satellites / UAVs / ships / ground stations visualization.
 */

class SatelliteVisualizationApp {
    constructor() {
        this.cesium = null;
        this.ws = null;
        this.ui = null;

        // Simulation state
        this.simulationTime = 0;
        this.simulationDuration = 600;
        this.updateInterval = null;
        this.currentRoute = null;

        // v2 node registry: nodeId -> nodeType
        this.nodeTypeMap = new Map();
        // Node metadata from simulation_init
        this.nodeMetadata = {};
        // Link type definitions from init
        this.linkTypes = {};
        // Protocol v3: per-node packet telemetry (nodeId -> metrics)
        this.nodeMetrics = {};

        this._wsConnected = false;
    }

    // ==================================================================
    // Initialization
    // ==================================================================

    async initialize() {
        console.log('[App] Initializing Multi-Domain Visualization (v2)');

        try {
            // Initialize Cesium Manager
            const cesiumToken = localStorage.getItem('cesiumToken');
            this.cesium = new CesiumManager('cesiumContainer', { cesiumToken });
            this.cesium.initialize();

            // Initialize WebSocket Manager
            this.ws = new WebSocketManager({
                host: (!window.location.hostname || window.location.hostname === 'localhost')
                    ? '127.0.0.1'
                    : window.location.hostname,
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

            // Initialize UI Controller
            this.ui = new UIController(this.cesium, this.ws, this);
            this.ui.initializeUI();
            this.ui.loadCesiumToken();

            // Route 3D picks (link/node) to the detail panel
            this.cesium.onSelect = (info) => this.handleSelection(info);

            // Connect WebSocket after Cesium renders first frame
            this.cesium.scene.postRender.addEventListener(() => {
                if (!this._wsConnected) {
                    this._wsConnected = true;
                    this._wsConnectTime = Date.now();
                    console.log('[App] Cesium ready, opening WebSocket');
                    this.ws.connect();
                }
            });

            // Statistics loop
            this.startStatisticsLoop();

            console.log('[App] Application initialized successfully');

        } catch (error) {
            console.error('[App] Initialization failed:', error);
        }
    }

    // ==================================================================
    // simulation_init handler (v2)
    // ==================================================================

    handleSimulationInit(payload) {
        console.log('[App] simulation_init received, version:', payload.version);

        // Clear previous state
        this.cesium.clearAll();
        this.nodeTypeMap.clear();
        this.nodeMetadata = {};
        this.currentRoute = null;
        this.ui.hideDetail();
        this.ui.resetCharts();

        // Duration & timeline
        if (payload.duration) {
            this.simulationDuration = payload.duration;
            this.ui.setTimelineRange(0, payload.duration);
        }

        // Link type definitions (colors, labels)
        if (payload.link_types) {
            this.linkTypes = payload.link_types;
        }

        // Parse nodes
        if (payload.nodes) {
            const nodesByType = { satellite: [], uav: [], ship: [], ground_station: [] };

            for (const [nodeId, meta] of Object.entries(payload.nodes)) {
                this.nodeTypeMap.set(nodeId, meta.type);
                this.nodeMetadata[nodeId] = meta;

                if (nodesByType[meta.type]) {
                    nodesByType[meta.type].push(nodeId);
                }

                // Ground stations have static positions — create immediately
                if (meta.type === 'ground_station' && meta.lat != null) {
                    this.cesium.addOrUpdateNode(nodeId, 'ground_station', {
                        lat: meta.lat,
                        lon: meta.lon,
                        alt: 0,
                    }, { label: meta.label || nodeId });
                }
            }

            // Populate UI filters
            this.ui.populateNodeFilters(nodesByType);

            console.log(
                `[App] Nodes registered: ${nodesByType.satellite.length} sat, ` +
                `${nodesByType.uav.length} uav, ${nodesByType.ship.length} ship, ` +
                `${nodesByType.ground_station.length} gs`
            );
        }
    }

    // ==================================================================
    // state_update handler (v2)
    // ==================================================================

    handleStateUpdate(payload) {
        try {
            this.cesium.beginBatch();

            // Update all node positions
            if (payload.positions) {
                this.updatePositions(payload.positions);
            }

            // Sync links (add new, update existing, remove stale)
            if (payload.links) {
                this.cesium.syncLinks(payload.links);
            }

            this.cesium.endBatch();

            // Routing highlight
            if (payload.routing) {
                this.updateRouting(payload.routing);
            }

            // Metrics summary → UI panel (throttled in UI layer)
            if (payload.metrics_summary) {
                this.ui.updateMetricsSummary(payload.metrics_summary);
                this.ui.pushCharts(payload.timestamp, payload.metrics_summary);
            }

            // Protocol v3: per-node packet telemetry
            if (payload.node_metrics) {
                this.nodeMetrics = payload.node_metrics;
            }

            // Live-refresh the open detail panel (link or node)
            this.refreshDetailPanel();

            // Simulation time — only update display when user isn't dragging
            if (payload.timestamp != null) {
                this.simulationTime = payload.timestamp;
                if (!this.ui.isUserSeeking) {
                    this.ui.updateTimeDisplay(payload.timestamp);
                }
            }

        } catch (error) {
            this.cesium.endBatch();
            console.error('[App] Error processing state_update:', error);
        }
    }

    /**
     * Update positions for all node types using the unified positions dict.
     */
    updatePositions(positions) {
        for (const [nodeId, pos] of Object.entries(positions)) {
            const nodeType = this.nodeTypeMap.get(nodeId);
            if (!nodeType) continue; // unknown node, skip

            const meta = this.nodeMetadata[nodeId] || {};
            this.cesium.addOrUpdateNode(nodeId, nodeType, pos, {
                label: meta.label || nodeId,
                heading: pos.heading,
            });
        }
    }

    /**
     * Update routing highlight path.
     */
    updateRouting(routing) {
        if (routing.highlight_path && routing.highlight_path.length >= 2) {
            const routeKey = routing.highlight_path.join(',');
            if (this.currentRoute !== routeKey) {
                this.currentRoute = routeKey;
                this.cesium.highlightRoute(routing.highlight_path);
            }
        } else if (!routing.highlight_path && this.currentRoute) {
            this.currentRoute = null;
            this.cesium.clearRouteHighlights();
        }
    }

    // ==================================================================
    // Selection → detail panel
    // ==================================================================

    /**
     * Handle a 3D pick emitted by CesiumManager.onSelect.
     * info: {kind:'link',...} | {kind:'node',...} | null
     */
    handleSelection(info) {
        if (!info) {
            this.ui.hideDetail();
            return;
        }
        if (info.kind === 'link') {
            this.ui.showLinkDetail(info);
        } else if (info.kind === 'node') {
            this.ui.showNodeDetail(this._withNodeMetrics(info));
        }
    }

    /** Merge protocol-v3 per-node packet metrics into a node info object. */
    _withNodeMetrics(info) {
        const m = this.nodeMetrics[info.nodeId];
        return m ? Object.assign({}, info, m) : info;
    }

    /**
     * Refresh the currently open detail panel with the latest live data.
     * Called on every state_update so metrics stay current.
     */
    refreshDetailPanel() {
        const kind = this.ui.detailKind;
        const id = this.ui.detailId;
        if (!kind || !id) return;

        if (kind === 'link') {
            const data = this.cesium.getLinkData(id);
            if (data) {
                this.ui.updateLinkDetail(data);
            } else {
                // Link disappeared (e.g. dropped) — close the panel
                this.ui.hideDetail();
                this.cesium.clearSelection();
            }
        } else if (kind === 'node') {
            const data = this.cesium.getNodeData(id);
            if (data) {
                this.ui.updateNodeDetail(this._withNodeMetrics(data));
            }
        }
    }

    // ==================================================================
    // WebSocket event handlers
    // ==================================================================

    handleWSConnect() {
        console.log('[App] WebSocket connected');
        this.ui.updateConnectionStatus(true);
    }

    handleWSDisconnect() {
        console.log('[App] WebSocket disconnected');
        this.ui.updateConnectionStatus(false);
    }

    handleAck(payload) {
        console.log('[App] ACK:', payload.action, payload.status);
    }

    handleWSMessage(message) {
        // Hook for future message types
    }

    handleWSError(payload) {
        console.error('[App] Server error:', payload);
    }

    // ==================================================================
    // Statistics & lifecycle
    // ==================================================================

    startStatisticsLoop() {
        if (this.updateInterval) clearInterval(this.updateInterval);
        this.updateInterval = setInterval(() => {
            const stats = this.cesium.getStats();
            this.ui.updateStatistics(stats);
        }, 500);
    }

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

// ======================================================================
// Bootstrap
// ======================================================================

let app = null;

document.addEventListener('DOMContentLoaded', () => {
    app = new SatelliteVisualizationApp();
    app.initialize();
});

window.addEventListener('beforeunload', () => {
    if (app) app.stop();
});

if (typeof module !== 'undefined' && module.exports) {
    module.exports = SatelliteVisualizationApp;
}
