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

        // Protocol 3.1 (Phase 7): compact state frames. Satellite positions
        // arrive as sat_pos arrays aligned to sat_order (altitude is constant
        // and comes from init); links arrive as short-key deltas merged into
        // linkCache; a one-shot static ISL mesh may be drawn at small scales.
        this.satOrder = [];
        this.satAltM = {};
        this.linkCache = {};
        this.queueCapacityPkts = 0;
        this.pendingISLMesh = null;

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

            // Initialize WebSocket Manager（地址来自 SBConfig，可用 ?ws=host:port 覆盖）
            this.ws = new WebSocketManager({
                host: window.SBConfig ? window.SBConfig.host : '127.0.0.1',
                port: window.SBConfig ? window.SBConfig.port : 8000,
                path: '/ws/client',
                onConnect: () => this.handleWSConnect(),
                onDisconnect: () => this.handleWSDisconnect(),
                onReconnectFailed: () => {
                    // 自动重连耗尽：遮罩切换为手动重连提示
                    if (this.ui) this.ui.updateConnectionStatus(false, 'failed');
                },
                onStateUpdate: (payload) => this.handleStateUpdate(payload),
                onSimulationInit: (payload) => this.handleSimulationInit(payload),
                onAck: (payload) => this.handleAck(payload),
                onError: (payload) => this.handleWSError(payload),
                onMessage: (message) => this.handleWSMessage(message),
                onExperimentUpdate: (payload) => this.handleExperimentUpdate(payload),
            });

            // 教学实验面板（改进 #2）：E1~E4 沙箱实验 + 报告导出
            this.experiments = new ExperimentLab(this.ws);
            this.experiments.init();      // 立即渲染入口按钮（目录到达后自动填充）

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

        // 防御：空 init（如误连同一后端的测试 mock 核心）不清空现有场景；
        // 真实核心的 init 必带 nodes（v3）或非空 satellites（v2）
        if (!payload.nodes &&
            !(Array.isArray(payload.satellites) && payload.satellites.length)) {
            console.warn('[App] Ignoring simulation_init without node definitions');
            return;
        }

        // Clear previous state
        this.cesium.clearAll();
        this.nodeTypeMap.clear();
        this.nodeMetadata = {};
        this.currentRoute = null;
        this.linkCache = {};
        this.nodeMetrics = {};
        this.satOrder = [];
        this.satAltM = {};
        this.pendingISLMesh = null;
        this.ui.hideDetail();
        this.ui.resetCharts();
        // 可选星座：把选择器同步为核心当前生效的星座
        this.ui.setConstellationEcho(payload.constellation);

        // Duration & timeline
        if (payload.duration) {
            this.simulationDuration = payload.duration;
            this.ui.setTimelineRange(0, payload.duration);
        }

        // Link type definitions (colors, labels)
        if (payload.link_types) {
            this.linkTypes = payload.link_types;
        }

        // 教学实验目录（E1~E4）
        if (payload.experiments) {
            this.experiments.setCatalog(payload.experiments);
        }

        // Protocol 3.1: compact state-frame support
        this.satOrder = payload.sat_order || [];
        if (payload.nodes) {
            for (const [nodeId, meta] of Object.entries(payload.nodes)) {
                if (meta.type === 'satellite' && meta.orbit) {
                    this.satAltM[nodeId] =
                        (meta.orbit.altitude_km || 550) * 1000;
                }
            }
        }
        if (payload.packet_model) {
            this.queueCapacityPkts =
                payload.packet_model.queue_capacity_pkts || 0;
        }
        // Static ISL mesh only for small constellations (cheap to draw all
        // 2N links); at thousand-sat scale ISLs render only when active.
        this.pendingISLMesh =
            (this.satOrder.length > 0 && this.satOrder.length <= 200)
                ? (payload.isl_topology || [])
                : null;

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
            // Milestone A (C期): fill the file-transfer src/dst selects
            this.ui.populateFileNodes();

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

            // Protocol 3.1: rebuild full positions from the compact sat_pos
            // array (satellites) plus the dynamic-node positions dict.
            this.updatePositions(this._rebuildPositions(payload));

            // Build the static ISL mesh once, after satellite entities exist.
            if (this.pendingISLMesh) {
                this.cesium.setStaticISLMesh(this.pendingISLMesh);
                this.pendingISLMesh = null;
            }

            // Protocol 3.1: merge short-key link deltas into the client-side
            // cache, then sync the full active set (idle links are omitted by
            // the core, so they simply never enter the cache).
            this._mergeLinks(payload);
            this.cesium.syncLinks(this.linkCache);

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

            // Protocol v3: per-node packet telemetry. Protocol 3.1 only
            // includes window-active nodes, so merge rather than replace to
            // keep the last known metrics for currently-quiet nodes.
            if (payload.node_metrics) {
                Object.assign(this.nodeMetrics, payload.node_metrics);
            }

            // Milestone A (C期): live file-transfer tracker. The core omits
            // the field when idle, so only refresh the UI when it is present.
            if (payload.file_transfers) {
                this.ui.updateFileTransfers(payload.file_transfers);
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

            // 多客户端播放状态同步：核心是唯一权威，播放按钮跟随
            // is_playing（旧版核心不带该字段时跳过，退化为本地状态）
            if (typeof payload.is_playing === 'boolean') {
                this.ui.syncPlayState(payload.is_playing);
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

    // ==================================================================
    // Protocol 3.1 helpers (compact state frames)
    // ==================================================================

    /**
     * Rebuild the full positions dict from a 3.1 frame: satellites travel as
     * a compact sat_pos array ([[lat, lon], ...] aligned to sat_order, with
     * constant altitude from init); UAVs / ships arrive in `positions`.
     * （P3：纯函数已提取至 protocol31.js，便于 node:test 单测）
     */
    _rebuildPositions(payload) {
        return Protocol31.rebuildPositions(payload, this.satOrder, this.satAltM);
    }

    /**
     * Merge a 3.1 link delta into the client-side active-link cache.
     * `links_full` marks a complete resync (discard everything first);
     * `links_removed` lists keys to prune between full frames.
     */
    _mergeLinks(payload) {
        this.linkCache = Protocol31.mergeLinks(
            this.linkCache, payload, this.linkTypes, this.queueCapacityPkts);
    }

    /**
     * Expand a short-key link record {t,u,l,d,tx,q,p} into the long-form
     * shape consumed by CesiumManager.syncLinks / UIController link detail.
     */
    _expandLink(key, v) {
        return Protocol31.expandLink(key, v, this.linkTypes, this.queueCapacityPkts);
    }

    /**
     * Update routing highlight path.
     */
    updateRouting(routing) {
        // Milestone A (C期): a selected file transfer owns the route
        // highlight — don't let the demo auto-cycle override it.
        if (this.ui.isFileHighlightActive()) return;

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

    handleExperimentUpdate(payload) {
        if (this.experiments) this.experiments.handleUpdate(payload);
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
