/**
 * UI Controller Module v3 — Minimal floating-panel UI
 * Full-screen globe + collapsible layers/stats panels + link/node detail
 * panel + bottom playback bar. v1 legacy controls (constellation/scenario/
 * per-node filter lists/offline CZML) removed.
 */

/** 转义不可信字符串（如用户上传的文件名）后再拼入 HTML，防止 XSS 注入 */
function _escHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

/** 类型元数据统一取自 constants.js（SBConstants），未加载时使用内置回退 */
function _sharedTypeMeta(key, fallback) {
    return (window.SBConstants && window.SBConstants[key]) || fallback;
}

/** 可选星座预设的展示名（与核心 CONSTELLATION_PRESETS 对齐） */
const CONSTELLATION_LABELS = {
    demo72: '演示 72 星',
    demo440: '演示 440 星',
    starlink: 'Starlink 1584 星',
    kuiper: 'Kuiper 1156 星',
    telesat: 'Telesat 351 星',
};

class UIController {
    constructor(cesiumManager, websocketManager, app) {
        this.cesium = cesiumManager;
        this.ws = websocketManager;
        this.app = app;
        this.isPlaying = false;

        // Timeline: guard against state_update overwriting a user drag
        this.isUserSeeking = false;
        this._lastTimeInt = -1;          // only touch DOM when whole second changes
        this._lastMetricsUpdate = 0;     // throttle metrics summary repaints

        // Detail panel state
        this._detailKind = null;         // 'link' | 'node' | null
        this._detailId = null;           // linkId or nodeId currently shown

        // File transfer (Milestone A, C期)
        this._pendingFile = null;        // File object chosen for upload
        this._selectedFileId = null;     // transfer whose path is highlighted
        this._fileTransfers = {};        // latest file_transfers snapshot
        this._filePathCache = null;      // last highlighted path signature
        // HTTP API 基址取自 SBConfig（与 WebSocket 同源，支持 ?ws=host:port 覆盖）
        this.apiBase = (window.SBConfig && window.SBConfig.apiBase) ||
            `http://${(this.ws && this.ws.host) || '127.0.0.1'}:8000`;

        // Display metadata for badges（P3：收敛自 constants.js）
        this.linkTypeMeta = _sharedTypeMeta('LINK_TYPES', {
            isl: { label: 'ISL 星间链路', color: '#4FC3F7' },
            gsl: { label: 'GSL 地面-卫星', color: '#FF8A65' },
            sul: { label: 'SUL 卫星-无人机', color: '#81C784' },
            ssl: { label: 'SSL 卫星-船舶', color: '#FFB74D' },
        });
        this.nodeTypeMeta = _sharedTypeMeta('NODE_TYPES', {
            satellite:      { label: '卫星',   color: '#1E90FF' },
            uav:            { label: '无人机', color: '#32CD32' },
            ship:           { label: '船舶',   color: '#FFA500' },
            real_ship:      { label: '真实船舶(AIS)', color: '#26A69A' },
            ground_station: { label: '地面站', color: '#FF4500' },
        });
    }

    // ==================================================================
    // Initialization
    // ==================================================================

    initializeUI() {
        // Playback
        document.getElementById('playPauseBtn').addEventListener('click', () => this.togglePlayPause());
        document.getElementById('stopBtn').addEventListener('click', () => this.stopPlayback());

        // Speed
        document.getElementById('speedSelect').addEventListener('change', (e) => {
            this.setSpeed(parseFloat(e.target.value));
        });

        // 可选星座：预设切换 + 自定义参数面板
        const constellationSelect = document.getElementById('constellationSelect');
        if (constellationSelect) {
            constellationSelect.addEventListener('change', (e) => {
                this.onConstellationSelect(e.target.value);
            });
            document.getElementById('cpApply').addEventListener('click', () => {
                this.applyCustomConstellation();
            });
            document.getElementById('cpCancel').addEventListener('click', () => {
                this.closeConstellationPanel();
            });
        }

        // Timeline
        const timelineSlider = document.getElementById('timelineSlider');
        timelineSlider.addEventListener('input', (e) => this.onTimelineSeek(parseFloat(e.target.value)));
        timelineSlider.addEventListener('pointerdown', () => { this.isUserSeeking = true; });
        window.addEventListener('pointerup', () => {
            if (this.isUserSeeking) {
                this.isUserSeeking = false;
                this.jumpToTime(parseFloat(timelineSlider.value));
            }
        });

        // Node type visibility
        const nodeToggles = {
            toggleSatellite: 'satellite',
            toggleUav: 'uav',
            toggleShip: 'ship',
            toggleGroundStation: 'ground_station',
        };
        for (const [elId, nodeType] of Object.entries(nodeToggles)) {
            document.getElementById(elId).addEventListener('change', (e) => {
                this.cesium.setNodeTypeVisibility(nodeType, e.target.checked);
            });
        }

        // 真实船舶（AIS）图层：除本地显隐外还要通知仿真核心，
        // 使 SSL 链路与 DES 流量同步启用/剥离；关闭时清理残留实体。
        const realShipToggle = document.getElementById('toggleRealShip');
        if (realShipToggle) {
            realShipToggle.addEventListener('change', (e) => {
                const on = e.target.checked;
                this.cesium.setNodeTypeVisibility('real_ship', on);
                if (!on) this.cesium.removeNodesByType('real_ship');
                this.ws.sendCommand('set_ais_layer', { enabled: on });
            });
        }

        // Link type visibility
        const linkToggles = {
            toggleISL: 'isl', toggleGSL: 'gsl', toggleSUL: 'sul', toggleSSL: 'ssl',
        };
        for (const [elId, linkType] of Object.entries(linkToggles)) {
            document.getElementById(elId).addEventListener('change', (e) => {
                this.cesium.setLinkTypeVisibility(linkType, e.target.checked);
            });
        }

        // Link coloring mode (metrics-driven gradient)
        const modeSelect = document.getElementById('metricsModeSelect');
        if (modeSelect) {
            modeSelect.addEventListener('change', (e) => {
                this.cesium.setMetricsMode(e.target.value);
            });
        }

        // Packet-flow animation toggle
        const packetFlowToggle = document.getElementById('packetFlowToggle');
        if (packetFlowToggle) {
            packetFlowToggle.addEventListener('change', (e) => {
                this.cesium.setPacketFlow(e.target.checked);
            });
        }

        // Time-series charts (throughput / latency / loss vs sim time)
        if (typeof TimeSeriesChart !== 'undefined') {
            this.charts = {
                throughput: new TimeSeriesChart('chartThroughput',
                    { color: '#38bdf8', unit: 'Mbps', decimals: 1 }),
                latency: new TimeSeriesChart('chartLatency',
                    { color: '#fbbf24', unit: 'ms', decimals: 0 }),
                loss: new TimeSeriesChart('chartLoss',
                    { color: '#f87171', unit: 'pkt/s', decimals: 0 }),
            };
        } else {
            this.charts = null;
        }
        this._lastChartT = null;
        this._lastChartDrops = null;
        this._lastChartPush = 0;

        // Panel collapse / reopen（注册到 _panels，供详情面板打开时程序化收起）
        this._panels = {};
        this._statsCollapsedByDetail = false;
        this._bindCollapse('layersPanel', 'layersCollapse', 'layersReopen');
        this._bindCollapse('statsPanel', 'statsCollapse', 'statsReopen');
        this._bindCollapse('chartPanel', 'chartCollapse', 'chartReopen');
        this._bindCollapse('filePanel', 'fileCollapse', 'fileReopen');

        // File transfer panel (Milestone A, C期)
        this.initFilePanel();

        // Detail panel close
        document.getElementById('detailClose').addEventListener('click', () => {
            this.hideDetail();
            this.cesium.clearSelection();
        });

        // Dismiss hint toast after a while
        setTimeout(() => {
            const toast = document.getElementById('hintToast');
            if (toast) toast.classList.add('hide');
        }, 8000);

        // 键盘快捷键：空格 = 播放/暂停，S = 停止，Esc = 取消选择并关闭详情
        const reconnectBtn = document.getElementById('reconnectBtn');
        if (reconnectBtn) {
            reconnectBtn.addEventListener('click', () => this.ws.manualReconnect());
        }

        document.addEventListener('keydown', (e) => {
            const tag = (e.target && e.target.tagName) || '';
            if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
            if (e.code === 'Space') {
                e.preventDefault();
                this.togglePlayPause();
            } else if (e.key === 's' || e.key === 'S') {
                this.stopPlayback();
            } else if (e.key === 'Escape') {
                this.hideDetail();
                this.cesium.clearSelection();
            }
        });

        console.log('[UIController] UI initialized (v3 minimal)');
    }

    _bindCollapse(panelId, btnId, reopenId) {
        const panel = document.getElementById(panelId);
        const reopen = document.getElementById(reopenId);
        // 收起 / 重开动作封装为函数，注册到 _panels 供程序化调用
        const collapse = () => {
            panel.classList.add('collapsed');
            panel.style.display = 'none';
            reopen.style.display = 'block';
        };
        const reopenFn = () => {
            panel.classList.remove('collapsed');
            panel.style.display = 'block';
            reopen.style.display = 'none';
        };
        document.getElementById(btnId).addEventListener('click', collapse);
        reopen.addEventListener('click', reopenFn);
        this._panels[panelId] = {
            collapse,
            reopen: reopenFn,
            isOpen: () => panel.style.display !== 'none',
        };
    }

    // ==================================================================
    // Playback
    // ==================================================================

    togglePlayPause() {
        // 只发送指令，不在本地翻转状态：按钮图标由 syncPlayState() 依据
        // 核心在 state_update 中下发的权威 is_playing 驱动，保证多客户端一致
        if (this.isPlaying) {
            this.ws.sendPauseCommand();
        } else {
            this.ws.sendPlayCommand();
        }
    }

    stopPlayback() {
        this.ws.sendStopCommand();
    }

    /**
     * 由核心下发的 is_playing 驱动播放按钮状态（多客户端一致性的唯一来源）。
     * 旧版核心不带该字段时 app 层不会调用本方法，退化为本地状态。
     */
    syncPlayState(isPlaying) {
        if (typeof isPlaying !== 'boolean' || isPlaying === this.isPlaying) return;
        this.isPlaying = isPlaying;
        document.getElementById('playPauseBtn').textContent =
            isPlaying ? '⏸' : '▶';
    }

    setSpeed(speed) {
        this.ws.sendSpeedCommand(speed);
        this.cesium.setAnimationSpeed(speed);
    }

    // ==================================================================
    // 可选星座（预设切换 + 自定义参数）
    // ==================================================================

    onConstellationSelect(value) {
        if (value === 'custom') {
            // 打开自定义参数面板，点“应用”才真正下发
            document.getElementById('constellationPanel').style.display = 'flex';
            return;
        }
        this.ws.sendSetConstellation(value);
        this.showToast(`正在切换星座：${CONSTELLATION_LABELS[value] || value}`, 'info');
    }

    applyCustomConstellation() {
        const spec = {
            planes: parseInt(document.getElementById('cpPlanes').value, 10),
            sats_per_plane: parseInt(document.getElementById('cpSatsPerPlane').value, 10),
            altitude_km: parseFloat(document.getElementById('cpAltitude').value),
            inclination_deg: parseFloat(document.getElementById('cpInclination').value),
        };
        for (const v of Object.values(spec)) {
            if (!Number.isFinite(v)) {
                this.showToast('自定义星座参数不完整', 'bad');
                return;
            }
        }
        if (spec.planes * spec.sats_per_plane > 1600) {
            this.showToast('总星数超过上限 1600，请减小轨道面数或每面星数', 'bad');
            return;
        }
        this.ws.sendSetConstellationCustom(spec);
        this.closeConstellationPanel(true);
        this.showToast(
            `正在切换星座：自定义 ${spec.planes}×${spec.sats_per_plane} ` +
            `(${spec.planes * spec.sats_per_plane} 星)`, 'info');
    }

    /** keepActive: 提交后保留 custom 选中态（等待 init 回显） */
    closeConstellationPanel(keepActive = false) {
        document.getElementById('constellationPanel').style.display = 'none';
        const sel = document.getElementById('constellationSelect');
        if (!keepActive && sel && sel.value === 'custom') {
            // 取消时回到当前生效星座，避免选择器停留在“自定义…”
            sel.value = this._activeConstellation || 'demo72';
        }
    }

    /**
     * simulation_init 回显：把选择器同步为核心当前生效的星座，
     * 并把壳层参数回填自定义表单；非首次 init 时提示切换完成。
     */
    setConstellationEcho(constellation) {
        if (!constellation) return;
        const sel = document.getElementById('constellationSelect');
        if (!sel) return;
        const name = constellation.name || 'custom';
        if (name !== 'custom' && name !== 'tle') {
            this._activeConstellation = name;
        }
        const hasOption = Array.from(sel.options).some((o) => o.value === name);
        sel.value = hasOption ? name : 'custom';
        // 回填壳层参数，方便在现有星座基础上微调
        const shell = Array.isArray(constellation.shells) &&
            constellation.shells[0];
        if (shell) {
            const fill = (id, v) => {
                const el = document.getElementById(id);
                if (el && v != null) el.value = v;
            };
            fill('cpPlanes', shell.planes);
            fill('cpSatsPerPlane', shell.sats_per_plane);
            fill('cpAltitude', shell.altitude_km);
            fill('cpInclination', shell.inclination_deg);
        }
        if (this._constellationAnnounced) {
            this.showToast(
                `星座已就绪：${constellation.label || name} ` +
                `(${constellation.sat_count || 0} 星)`, 'ok');
        }
        this._constellationAnnounced = true;
    }

    setTimelineRange(min, max) {
        const slider = document.getElementById('timelineSlider');
        slider.min = min;
        slider.max = max;
        slider.value = min;
    }

    /**
     * Update time display. Only touches DOM when whole second changes.
     */
    updateTimeDisplay(timestamp) {
        const t = Math.floor(timestamp);
        if (t === this._lastTimeInt) return;
        this._lastTimeInt = t;
        document.getElementById('simTime').textContent = t + 's';
        if (!this.isUserSeeking) {
            document.getElementById('timelineSlider').value = t;
        }
    }

    onTimelineSeek(value) {
        document.getElementById('simTime').textContent = Math.floor(value) + 's';
    }

    jumpToTime(timestamp) {
        if (isNaN(timestamp)) return;
        this.ws.sendTimelineCommand(timestamp);
        document.getElementById('timelineSlider').value = timestamp;
        document.getElementById('simTime').textContent = Math.floor(timestamp) + 's';
    }

    // ==================================================================
    // Detail panel (link / node)
    // ==================================================================

    /** Format a bits-per-second value as a readable string (Gbps/Mbps/Kbps). */
    fmtBps(bps) {
        const v = Number(bps) || 0;
        if (v >= 1e9) return (v / 1e9).toFixed(2) + ' Gbps';
        if (v >= 1e6) return (v / 1e6).toFixed(1) + ' Mbps';
        if (v >= 1e3) return (v / 1e3).toFixed(1) + ' Kbps';
        return v.toFixed(0) + ' bps';
    }

    /**
     * Show link detail panel.
     * data: {linkId, linkType, source, target, bandwidth_utilization,
     *        latency_ms, loss_rate, is_active}
     */
    showLinkDetail(data) {
        this._detailKind = 'link';
        this._detailId = data.linkId;

        const meta = this.linkTypeMeta[data.linkType] || { label: data.linkType, color: '#888' };
        document.getElementById('detailTitle').textContent = '链路详情';

        const body = document.getElementById('detailBody');
        body.innerHTML =
            `<span class="detail-type-badge" style="background:${meta.color}22;color:${meta.color};border:1px solid ${meta.color}55">${meta.label}</span>` +
            `<div class="detail-endpoints">` +
                `<span id="dSource">${data.source}</span>` +
                `<span class="arrow">⟷</span>` +
                `<span id="dTarget">${data.target}</span>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>带宽利用率</span><span class="mv" id="dUtil">--</span></div>` +
                `<div class="progress-track"><div class="progress-fill" id="dUtilBar"></div></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>时延</span><span class="mv" id="dLatency">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>丢包率</span><span class="mv" id="dLoss">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>吞吐 / 容量</span><span class="mv" id="dTx">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>队列深度</span><span class="mv" id="dQueue">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>传播时延</span><span class="mv" id="dProp">--</span></div>` +
            `</div>` +
            `<div class="detail-status"><span class="dot" id="dStatusDot"></span><span id="dStatus">--</span></div>`;

        document.getElementById('detailPanel').style.display = 'block';
        this._autoCollapseStats();
        this.updateLinkDetail(data);
    }

    /**
     * Live-update the open link detail panel with fresh metrics.
     */
    updateLinkDetail(data) {
        if (this._detailKind !== 'link' || this._detailId !== data.linkId) return;

        const util = Number(data.bandwidth_utilization) || 0;
        const utilPct = Math.round(util * 100);
        const utilEl = document.getElementById('dUtil');
        if (utilEl) utilEl.textContent = utilPct + '%';
        const bar = document.getElementById('dUtilBar');
        if (bar) bar.style.width = utilPct + '%';

        const lat = document.getElementById('dLatency');
        if (lat) lat.textContent = (Number(data.latency_ms) || 0).toFixed(1) + ' ms';

        const loss = document.getElementById('dLoss');
        if (loss) loss.textContent = ((Number(data.loss_rate) || 0) * 100).toFixed(3) + ' %';

        // Protocol v3 packet-level telemetry
        const tx = document.getElementById('dTx');
        if (tx) tx.textContent = this.fmtBps(data.tx_bps) + ' / ' + this.fmtBps(data.capacity_bps);

        const q = document.getElementById('dQueue');
        if (q) q.textContent = (Number(data.queue_depth) || 0) + ' / ' +
                               (Number(data.queue_capacity) || 0) + ' 包';

        const prop = document.getElementById('dProp');
        if (prop) prop.textContent = (Number(data.propagation_ms) || 0).toFixed(2) + ' ms';

        const active = data.is_active !== false;
        const dot = document.getElementById('dStatusDot');
        const st = document.getElementById('dStatus');
        if (dot) dot.classList.toggle('on', active);
        if (st) {
            st.textContent = active ? '链路活跃' : '链路中断';
            st.style.color = active ? 'var(--good)' : 'var(--bad)';
        }
    }

    /**
     * Show node detail panel.
     * data: {nodeId, nodeType, label, lat, lon, alt, ...}
     */
    showNodeDetail(data) {
        this._detailKind = 'node';
        this._detailId = data.nodeId;

        const meta = this.nodeTypeMeta[data.nodeType] || { label: data.nodeType, color: '#888' };
        document.getElementById('detailTitle').textContent = '节点详情';

        const body = document.getElementById('detailBody');
        body.innerHTML =
            `<span class="detail-type-badge" style="background:${meta.color}22;color:${meta.color};border:1px solid ${meta.color}55">${meta.label}</span>` +
            `<div class="detail-endpoints"><span>${data.label || data.nodeId}</span></div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>经度</span><span class="mv" id="dLon">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>纬度</span><span class="mv" id="dLat">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>高度</span><span class="mv" id="dAlt">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>发送 / 接收包数</span><span class="mv" id="dPktsSr">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>转发包数</span><span class="mv" id="dPktsFwd">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>丢包数</span><span class="mv" id="dPktsDrop">--</span></div>` +
            `</div>` +
            `<div class="metric-block">` +
                `<div class="metric-label"><span>端到端时延 / 抖动</span><span class="mv" id="dE2e">--</span></div>` +
            `</div>`;

        document.getElementById('detailPanel').style.display = 'block';
        this._autoCollapseStats();
        this.updateNodeDetail(data);
    }

    updateNodeDetail(data) {
        if (this._detailKind !== 'node' || this._detailId !== data.nodeId) return;
        const lon = document.getElementById('dLon');
        const lat = document.getElementById('dLat');
        const alt = document.getElementById('dAlt');
        if (lon && data.lon != null) lon.textContent = Number(data.lon).toFixed(2) + '°';
        if (lat && data.lat != null) lat.textContent = Number(data.lat).toFixed(2) + '°';
        if (alt && data.alt != null) alt.textContent = Math.round(Number(data.alt)).toLocaleString() + ' km';

        // Protocol v3 per-node packet telemetry (— when absent, e.g. v2 core)
        const sr = document.getElementById('dPktsSr');
        if (sr) sr.textContent = (data.pkts_sent != null && data.pkts_recv != null)
            ? Number(data.pkts_sent).toLocaleString() + ' / ' + Number(data.pkts_recv).toLocaleString()
            : '—';

        const fwd = document.getElementById('dPktsFwd');
        if (fwd) fwd.textContent = data.pkts_fwd != null
            ? Number(data.pkts_fwd).toLocaleString() : '—';

        const drop = document.getElementById('dPktsDrop');
        if (drop) drop.textContent = data.pkts_dropped != null
            ? Number(data.pkts_dropped).toLocaleString() : '—';

        const e2e = document.getElementById('dE2e');
        if (e2e) e2e.textContent = (data.e2e_latency_ms != null && data.e2e_latency_ms > 0)
            ? Number(data.e2e_latency_ms).toFixed(1) + ' / ' + Number(data.jitter_ms || 0).toFixed(2) + ' ms'
            : '—';
    }

    /** detailPanel 与 statsPanel 共用屏幕右上角位置：打开详情时自动
     *  收起统计面板，关闭详情时恢复（用户期间手动重开过则不再干预）。 */
    _autoCollapseStats() {
        const p = this._panels['statsPanel'];
        if (p && p.isOpen() && !this._statsCollapsedByDetail) {
            p.collapse();
            this._statsCollapsedByDetail = true;
        }
    }

    hideDetail() {
        this._detailKind = null;
        this._detailId = null;
        document.getElementById('detailPanel').style.display = 'none';
        if (this._statsCollapsedByDetail) {
            this._statsCollapsedByDetail = false;
            const p = this._panels['statsPanel'];
            if (p && !p.isOpen()) p.reopen();
        }
    }

    /** Is the detail panel currently showing this link? */
    isLinkDetailOpen(linkId) {
        return this._detailKind === 'link' && this._detailId === linkId;
    }

    isNodeDetailOpen(nodeId) {
        return this._detailKind === 'node' && this._detailId === nodeId;
    }

    get detailKind() { return this._detailKind; }
    get detailId() { return this._detailId; }

    // ==================================================================
    // Status / statistics
    // ==================================================================

    updateConnectionStatus(isConnected, reason) {
        const dot = document.getElementById('connectionIndicator');
        const text = document.getElementById('connectionStatus');
        dot.classList.toggle('on', isConnected);
        text.textContent = isConnected ? '已连接' : '未连接';
        text.style.color = isConnected ? 'var(--good)' : 'var(--bad)';
        // 断线全局遮罩：默认显示自动重连进度；自动重连耗尽
        // （reason === 'failed'）后切换为手动重连提示
        if (isConnected) {
            this._hideConnOverlay();
        } else {
            this._showConnOverlay(reason === 'failed');
        }
    }

    _showConnOverlay(reconnectExhausted) {
        const overlay = document.getElementById('connOverlay');
        if (!overlay) return;
        const msg = document.getElementById('connOverlayMsg');
        if (msg) {
            msg.textContent = reconnectExhausted
                ? '自动重连已达上限，请点击按钮手动重连'
                : '正在自动重连…';
        }
        overlay.style.display = 'flex';
    }

    _hideConnOverlay() {
        const overlay = document.getElementById('connOverlay');
        if (overlay) overlay.style.display = 'none';
    }

    /**
     * 轻量 toast 提示（替代 alert，不阻塞交互）。
     * type: 'info' | 'ok' | 'bad'
     */
    showToast(msg, type = 'info', duration = 3000) {
        const toast = document.getElementById('appToast');
        if (!toast) return;
        toast.textContent = msg;
        toast.className = `show ${type}`;
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => { toast.className = ''; }, duration);
    }

    updateStatistics(stats) {
        document.getElementById('satCount').textContent = stats.satellites || 0;
        document.getElementById('uavCount').textContent = stats.uavs || 0;
        document.getElementById('shipCount').textContent = stats.ships || 0;
        const realShipEl = document.getElementById('realShipCount');
        if (realShipEl) realShipEl.textContent = stats.real_ships || 0;
        document.getElementById('staCount').textContent = stats.ground_stations || 0;
        document.getElementById('linkCount').textContent = stats.links || 0;
        document.getElementById('fpsCounter').textContent = (stats.fps || 0) + ' FPS';
        // P2 渲染诊断：实体总数 · 插值段数 · 包流动 overlay 数
        const diag = document.getElementById('renderDiag');
        if (diag) {
            diag.textContent = `${stats.entities || 0} 实体 · `
                + `${stats.interpSegments || 0} 插值 · `
                + `${stats.flowOverlays || 0} 流动`;
        }
    }

    /**
     * Update metrics summary (throttled to ~2Hz).
     */
    updateMetricsSummary(summary) {
        const now = Date.now();
        if (now - this._lastMetricsUpdate < 500) return;
        this._lastMetricsUpdate = now;

        const el = document.getElementById('metricsSummary');
        if (el) {
            const delivered = Number(summary.pkts_delivered || 0).toLocaleString();
            const dropped = Number(summary.pkts_dropped || 0).toLocaleString();
            const inFlight = Number(summary.pkts_in_flight || 0).toLocaleString();
            const handover = Number(summary.pkts_handover_dropped || 0).toLocaleString();
            const e2e = Number(summary.avg_e2e_latency_ms || 0).toFixed(1);
            const thr = this.fmtBps(summary.aggregate_throughput_bps || 0);
            const qos = summary.qos || {};
            const qHigh = qos['0'] || {};
            const qLow = qos['1'] || {};
            const lossPct = (q) => q.generated
                ? (100 * (q.dropped || 0) / q.generated).toFixed(2) : '0.00';
            el.innerHTML =
                `活跃链路 <b style="color:var(--text)">${summary.active_links}</b> · ` +
                `节点 <b style="color:var(--text)">${summary.total_nodes}</b><br>` +
                `平均利用率 <b style="color:var(--text)">${(summary.avg_utilization * 100).toFixed(1)}%</b> · ` +
                `最大时延 <b style="color:var(--text)">${summary.max_latency_ms}ms</b><br>` +
                `送达 <b style="color:var(--text)">${delivered}</b> · ` +
                `丢包 <b style="color:var(--text)">${dropped}</b> · ` +
                `在途 <b style="color:var(--text)">${inFlight}</b><br>` +
                `端到端 <b style="color:var(--text)">${e2e}ms</b> · ` +
                `吞吐 <b style="color:var(--text)">${thr}</b><br>` +
                `切换丢包 <b style="color:var(--text)">${handover}</b> · ` +
                `QoS丢包 高优先<b style="color:var(--text)">${lossPct(qHigh)}%</b>` +
                `/尽力<b style="color:var(--text)">${lossPct(qLow)}%</b>`;
        }
    }

    /**
     * Feed the time-series charts (throttled to ~2Hz). Loss is plotted as an
     * instantaneous rate (pkt/s) derived from the cumulative drop counter.
     */
    pushCharts(timestamp, summary) {
        if (!this.charts || timestamp == null) return;
        const now = Date.now();
        if (now - this._lastChartPush < 500) return;
        this._lastChartPush = now;

        const thrMbps = (Number(summary.aggregate_throughput_bps) || 0) / 1e6;
        const lat = Number(summary.avg_e2e_latency_ms) || 0;

        let lossRate = 0;
        const drops = Number(summary.pkts_dropped) || 0;
        if (this._lastChartT != null && timestamp > this._lastChartT) {
            const dt = timestamp - this._lastChartT;
            const dd = drops - (this._lastChartDrops || 0);
            if (dd >= 0) lossRate = dd / dt;
        }
        this._lastChartT = timestamp;
        this._lastChartDrops = drops;

        this.charts.throughput.push(timestamp, thrMbps);
        this.charts.latency.push(timestamp, lat);
        this.charts.loss.push(timestamp, lossRate);
        this.charts.throughput.draw();
        this.charts.latency.draw();
        this.charts.loss.draw();
    }

    /** Clear chart buffers (called on simulation_init / restart). */
    resetCharts() {
        this._lastChartT = null;
        this._lastChartDrops = null;
        this._lastChartPush = 0;
        if (this.charts) {
            this.charts.throughput.clear();
            this.charts.latency.clear();
            this.charts.loss.clear();
        }
    }

    // ==================================================================
    // File transfer (Milestone A, C期)
    // ==================================================================

    initFilePanel() {
        const input = document.getElementById('fileInput');
        if (input) {
            input.addEventListener('change', () => this.onFilePicked(input.files[0]));
        }
        const send = document.getElementById('fileSendBtn');
        if (send) send.addEventListener('click', () => this.onFileSend());

        // Milestone C: satellite locate (pick a satellite by number quickly).
        const loc = document.getElementById('satLocate');
        if (loc) {
            loc.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); this.locateSatellite('fileSrc'); }
            });
        }
        const toSrc = document.getElementById('satToSrc');
        if (toSrc) toSrc.addEventListener('click', () => this.locateSatellite('fileSrc'));
        const toDst = document.getElementById('satToDst');
        if (toDst) toDst.addEventListener('click', () => this.locateSatellite('fileDst'));

        // Event delegation for per-card buttons (cancel / download) and select.
        const list = document.getElementById('fileList');
        if (list) {
            list.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                const card = e.target.closest('.file-card');
                if (!card) return;
                const fid = card.dataset.fid;
                if (btn && btn.classList.contains('cancel')) {
                    e.stopPropagation();
                    this.cancelFile(fid);
                    return;
                }
                if (btn && btn.classList.contains('dl')) {
                    e.stopPropagation();
                    this.downloadFile(fid);
                    return;
                }
                if (!btn) this.selectFile(fid);
            });
        }
    }

    /** Fill the source / destination selects from the simulation node set.
     *  Milestone C: every terminal type (satellite / uav / ship / ground_station)
     *  may act as both source and destination. Nodes are grouped into <optgroup>
     *  blocks by type; satellites are sorted by (shell, plane, index) and given a
     *  stable global running number (shown in the label) that the 卫星定位 box can
     *  use to jump straight to a satellite. */
    populateFileNodes() {
        const meta = (this.app && this.app.nodeMetadata) || {};
        const groups = { satellite: [], uav: [], ship: [], real_ship: [], ground_station: [] };
        for (const [id, m] of Object.entries(meta)) {
            if (groups[m.type]) groups[m.type].push([id, m]);
        }
        // Sort satellites so the global numbering is stable and follows the
        // orbital layout (shell -> plane -> index).
        groups.satellite.sort((a, b) => {
            const oa = a[1].orbit || {}, ob = b[1].orbit || {};
            return ((oa.shell || 0) - (ob.shell || 0)) ||
                   ((oa.plane || 0) - (ob.plane || 0)) ||
                   ((oa.index || 0) - (ob.index || 0));
        });
        // Ordered satellite ids for the numeric locate feature.
        this._satList = groups.satellite.map(([id]) => id);

        const typeLabels = { satellite: '卫星', uav: '无人机', ship: '船舶', real_ship: '真实船舶(AIS)', ground_station: '地面站' };
        const order = ['satellite', 'uav', 'ship', 'real_ship', 'ground_station'];
        const build = (sel) => {
            if (!sel) return;
            sel.innerHTML = '';
            let satNum = 0;
            for (const t of order) {
                const og = document.createElement('optgroup');
                og.label = typeLabels[t];
                for (const [id, m] of groups[t]) {
                    const o = document.createElement('option');
                    o.value = id;
                    if (t === 'satellite') {
                        satNum += 1;
                        o.textContent = '#' + String(satNum).padStart(4, '0') + ' ' + id;
                    } else {
                        o.textContent = m.label || id;
                    }
                    og.appendChild(o);
                }
                sel.appendChild(og);
            }
        };
        build(document.getElementById('fileSrc'));
        build(document.getElementById('fileDst'));
        // Sensible defaults
        const src = document.getElementById('fileSrc');
        if (src && src.querySelector('option[value="UAV-01"]')) src.value = 'UAV-01';
        const dst = document.getElementById('fileDst');
        if (dst && dst.querySelector('option[value="Beijing"]')) dst.value = 'Beijing';
    }

    /** Resolve a satellite id from a numeric query. Accepts a global running
     *  number ("123" -> the 123rd satellite in sorted order) or a plane/index
     *  pair ("5-3" -> Sat-5-3), optionally prefixed with a shell for multi-shell
     *  constellations ("0-5-3"). Returns the node id, or null if not found. */
    findSatelliteByNumber(query) {
        const meta = (this.app && this.app.nodeMetadata) || {};
        const list = this._satList || [];
        const q = String(query == null ? '' : query).trim()
            .toLowerCase().replace(/^sat[-_\s]?/, '');
        if (!q) return null;
        const parts = q.split(/[-_\s,]+/).filter(Boolean).map(Number);
        if (parts.length >= 2 && parts.every(Number.isInteger)) {
            for (const id of list) {
                const o = (meta[id] && meta[id].orbit) || {};
                if (parts.length >= 3) {
                    if (o.shell === parts[0] && o.plane === parts[1] && o.index === parts[2]) return id;
                } else if (o.plane === parts[0] && o.index === parts[1]) {
                    return id;
                }
            }
            return null;
        }
        if (/^\d+$/.test(q)) {
            const n = parseInt(q, 10);
            if (n >= 1 && n <= list.length) return list[n - 1];
        }
        return null;
    }

    /** Read the 卫星定位 input and assign the matched satellite to the given
     *  select ('fileSrc' or 'fileDst'). Flashes the input green on a match and
     *  red when nothing is found. */
    locateSatellite(targetSelId) {
        const input = document.getElementById('satLocate');
        const sel = document.getElementById(targetSelId);
        if (!input || !sel) return;
        const id = this.findSatelliteByNumber(input.value);
        input.classList.remove('loc-ok', 'loc-bad');
        if (id && sel.querySelector('option[value="' + id + '"]')) {
            sel.value = id;
            input.classList.add('loc-ok');
        } else {
            input.classList.add('loc-bad');
        }
        clearTimeout(this._locTimer);
        this._locTimer = setTimeout(() => input.classList.remove('loc-ok', 'loc-bad'), 1200);
    }

    _updateSendBtn() {
        const btn = document.getElementById('fileSendBtn');
        if (btn) btn.disabled = !this._pendingFile;
    }

    onFilePicked(file) {
        this._pendingFile = file || null;
        const nameEl = document.getElementById('fileName');
        const sizeEl = document.getElementById('fileSize');
        const pick = document.getElementById('filePick');
        if (file) {
            nameEl.textContent = file.name;
            sizeEl.textContent = this.fmtBytes(file.size);
            pick.classList.add('has-file');
        } else {
            nameEl.textContent = '选择要传输的文件…';
            sizeEl.textContent = '';
            pick.classList.remove('has-file');
        }
        this._updateSendBtn();
    }

    async onFileSend() {
        const file = this._pendingFile;
        if (!file) return;
        const btn = document.getElementById('fileSendBtn');
        const src = document.getElementById('fileSrc').value;
        const dst = document.getElementById('fileDst').value;
        const prio = parseInt(document.getElementById('filePrio').value, 10);
        const rateMbps = parseFloat(document.getElementById('fileRate').value) || 5;
        const rateBps = rateMbps * 1e6;

        btn.disabled = true;
        const oldLabel = btn.textContent;
        btn.textContent = '上传中…';
        try {
            const fd = new FormData();
            fd.append('file', file, file.name);
            const resp = await fetch(`${this.apiBase}/api/files/upload`,
                { method: 'POST', body: fd });
            if (!resp.ok) throw new Error('upload HTTP ' + resp.status);
            const rec = await resp.json();

            this.ws.sendCommand('file_send', {
                file_id: rec.file_id, src, dst, prio, rate_bps: rateBps,
            });

            // Seed an immediate card so the user sees feedback before the
            // first state_update arrives.
            this._fileTransfers[rec.file_id] = {
                name: file.name, src, dst, state: 'TRANSFERRING',
                progress: 0, delivered_bytes: 0, total_bytes: rec.total_bytes,
                eta_s: 0, throughput_bps: 0, path: [], in_flight: 0, retx: 0,
            };
            this.renderFileList();
            this.selectFile(rec.file_id);

            // Reset the picker for the next transfer.
            document.getElementById('fileInput').value = '';
            this.onFilePicked(null);
        } catch (err) {
            console.error('[UI] file upload/send failed:', err);
            this.showToast('上传失败：' + err.message + '（确认后端 realtime_backend 已启动）', 'bad', 5000);
            btn.disabled = false;
        } finally {
            btn.textContent = oldLabel;
        }
    }

    /** Called by app.js on every state_update carrying file_transfers. */
    updateFileTransfers(fileTransfers) {
        this._fileTransfers = fileTransfers || {};
        this.renderFileList();
        this._applyFileHighlight();
    }

    renderFileList() {
        const list = document.getElementById('fileList');
        if (!list) return;
        const entries = Object.entries(this._fileTransfers);
        if (!entries.length) {
            list.innerHTML = '<div class="file-empty">暂无传输任务</div>';
            return;
        }
        const stateLabel = {
            TRANSFERRING: '传输中', COMPLETE: '已完成',
            CANCELLED: '已取消', STORED: '待发送',
        };
        list.innerHTML = entries.map(([fid, t]) => {
            const pct = Math.round((t.progress || 0) * 100);
            const sel = fid === this._selectedFileId ? ' selected' : '';
            const path = (t.path && t.path.length) ? t.path.join(' → ') : '—';
            let actions = '';
            if (t.state === 'TRANSFERRING') {
                actions = `<div class="fc-actions"><button class="cancel">取消</button></div>`;
            } else if (t.state === 'COMPLETE') {
                actions = `<div class="fc-actions"><button class="dl">下载</button></div>`;
            }
            const thr = this.fmtThroughput(t.throughput_bps);
            const eta = t.state === 'TRANSFERRING' ? this.fmtEta(t.eta_s) : '—';
            // 文件名 / 端点名来自用户上传与网络下发，必须先转义再拼 HTML
            const name = _escHtml(t.name || fid);
            return `<div class="file-card${sel}" data-fid="${_escHtml(fid)}">` +
                `<div class="fc-top"><span class="fc-name" title="${name}">${name}</span>` +
                `<span class="fc-badge ${t.state}">${stateLabel[t.state] || t.state}</span></div>` +
                `<div class="fc-bar"><div style="width:${pct}%"></div></div>` +
                `<div class="fc-meta">` +
                `<b>${pct}%</b> · ${this.fmtBytes(t.delivered_bytes)}/${this.fmtBytes(t.total_bytes)}<br>` +
                `${thr} · ETA ${eta} · 重传 ${t.retx || 0}` +
                `</div>` +
                `<div class="fc-path">${_escHtml(t.src)} → ${_escHtml(t.dst)} · ${_escHtml(path)}</div>` +
                actions +
                `</div>`;
        }).join('');
    }

    selectFile(fid) {
        this._selectedFileId = (this._selectedFileId === fid) ? null : fid;
        this._filePathCache = null;
        this.renderFileList();
        this._applyFileHighlight();
    }

    /** Highlight the selected transfer's path; suppress the auto route cycle. */
    _applyFileHighlight() {
        if (!this._selectedFileId) {
            if (this._filePathCache !== null) {
                this._filePathCache = null;
                this.cesium.clearRouteHighlights();
            }
            return;
        }
        const t = this._fileTransfers[this._selectedFileId];
        const path = (t && t.path) ? t.path : [];
        const sig = path.join(',');
        if (sig && sig !== this._filePathCache && path.length >= 2) {
            this._filePathCache = sig;
            this.cesium.highlightFilePath(path);
        }
    }

    /** True when a transfer is selected (app.js skips the auto route cycle). */
    isFileHighlightActive() {
        return !!this._selectedFileId;
    }

    cancelFile(fid) {
        this.ws.sendCommand('file_cancel', { file_id: fid });
    }

    downloadFile(fid) {
        const t = this._fileTransfers[fid] || {};
        const a = document.createElement('a');
        a.href = `${this.apiBase}/api/files/${fid}/download`;
        a.download = t.name || fid;
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    fmtBytes(n) {
        n = Number(n) || 0;
        if (n >= 1e9) return (n / 1e9).toFixed(2) + ' GB';
        if (n >= 1e6) return (n / 1e6).toFixed(2) + ' MB';
        if (n >= 1e3) return (n / 1e3).toFixed(1) + ' KB';
        return n + ' B';
    }

    fmtThroughput(bps) {
        const v = Number(bps) || 0;
        if (v >= 1e6) return (v / 1e6).toFixed(2) + ' Mbps';
        if (v >= 1e3) return (v / 1e3).toFixed(1) + ' Kbps';
        return v.toFixed(0) + ' bps';
    }

    fmtEta(s) {
        s = Number(s) || 0;
        if (s <= 0) return '—';
        if (s < 60) return s.toFixed(0) + 's';
        return Math.floor(s / 60) + 'm' + Math.round(s % 60) + 's';
    }

    // ==================================================================
    // Compat stubs (kept so app.js calls don't break)
    // ==================================================================

    /** v2 simulation_init nodes — filter lists removed in v3 UI, no-op. */
    populateNodeFilters(nodesByType) {
        // Intentionally empty: per-node filter lists were removed for a
        // cleaner UI. Type-level visibility is handled by the layers panel.
    }

    /**
     * 真实船舶（AIS）图层元数据：仿真核心在 simulation_init 中携带
     * ais_layer 字段时回显开关状态；未加载轨迹时禁用复选框。
     */
    applyAisLayerMeta(layer) {
        const el = document.getElementById('toggleRealShip');
        if (!el) return;
        if (!layer || !layer.ship_count) {
            el.checked = false;
            el.disabled = true;
            el.title = '仿真核心未加载 AIS 轨迹（启动时加 --ais-file）';
            return;
        }
        el.disabled = false;
        el.checked = !!layer.enabled;
        el.title = `AIS 数据源: ${layer.source || 'ais'}` +
            (layer.date ? ` (${layer.date})` : '') +
            ` — ${layer.ship_count} 艘真实船舶`;
    }

    /** Cesium token is loaded silently from localStorage (no UI in v3). */
    loadCesiumToken() {
        const token = localStorage.getItem('cesiumToken');
        if (token && typeof Cesium !== 'undefined') {
            Cesium.Ion.defaultAccessToken = token;
        }
        return token;
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UIController;
}
