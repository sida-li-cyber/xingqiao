/**
 * UI Controller Module v3 — Minimal floating-panel UI
 * Full-screen globe + collapsible layers/stats panels + link/node detail
 * panel + bottom playback bar. v1 legacy controls (constellation/scenario/
 * per-node filter lists/offline CZML) removed.
 */

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

        // Display metadata for badges
        this.linkTypeMeta = {
            isl: { label: 'ISL 星间链路', color: '#4FC3F7' },
            gsl: { label: 'GSL 地面-卫星', color: '#FF8A65' },
            sul: { label: 'SUL 卫星-无人机', color: '#81C784' },
            ssl: { label: 'SSL 卫星-船舶', color: '#FFB74D' },
        };
        this.nodeTypeMeta = {
            satellite:      { label: '卫星',   color: '#1E90FF' },
            uav:            { label: '无人机', color: '#32CD32' },
            ship:           { label: '船舶',   color: '#FFA500' },
            ground_station: { label: '地面站', color: '#FF4500' },
        };
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

        // Link type visibility
        const linkToggles = {
            toggleISL: 'isl', toggleGSL: 'gsl', toggleSUL: 'sul', toggleSSL: 'ssl',
        };
        for (const [elId, linkType] of Object.entries(linkToggles)) {
            document.getElementById(elId).addEventListener('change', (e) => {
                this.cesium.setLinkTypeVisibility(linkType, e.target.checked);
            });
        }

        // Panel collapse / reopen
        this._bindCollapse('layersPanel', 'layersCollapse', 'layersReopen');
        this._bindCollapse('statsPanel', 'statsCollapse', 'statsReopen');

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

        console.log('[UIController] UI initialized (v3 minimal)');
    }

    _bindCollapse(panelId, btnId, reopenId) {
        const panel = document.getElementById(panelId);
        const reopen = document.getElementById(reopenId);
        document.getElementById(btnId).addEventListener('click', () => {
            panel.classList.add('collapsed');
            panel.style.display = 'none';
            reopen.style.display = 'block';
        });
        reopen.addEventListener('click', () => {
            panel.classList.remove('collapsed');
            panel.style.display = 'block';
            reopen.style.display = 'none';
        });
    }

    // ==================================================================
    // Playback
    // ==================================================================

    togglePlayPause() {
        const btn = document.getElementById('playPauseBtn');
        if (this.isPlaying) {
            this.ws.sendPauseCommand();
            btn.textContent = '▶';
            this.isPlaying = false;
        } else {
            this.ws.sendPlayCommand();
            btn.textContent = '⏸';
            this.isPlaying = true;
        }
    }

    stopPlayback() {
        this.ws.sendStopCommand();
        const btn = document.getElementById('playPauseBtn');
        btn.textContent = '▶';
        this.isPlaying = false;
    }

    setSpeed(speed) {
        this.ws.sendSpeedCommand(speed);
        this.cesium.setAnimationSpeed(speed);
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

    hideDetail() {
        this._detailKind = null;
        this._detailId = null;
        document.getElementById('detailPanel').style.display = 'none';
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

    updateConnectionStatus(isConnected) {
        const dot = document.getElementById('connectionIndicator');
        const text = document.getElementById('connectionStatus');
        dot.classList.toggle('on', isConnected);
        text.textContent = isConnected ? '已连接' : '未连接';
        text.style.color = isConnected ? 'var(--good)' : 'var(--bad)';
    }

    updateStatistics(stats) {
        document.getElementById('satCount').textContent = stats.satellites || 0;
        document.getElementById('uavCount').textContent = stats.uavs || 0;
        document.getElementById('shipCount').textContent = stats.ships || 0;
        document.getElementById('staCount').textContent = stats.ground_stations || 0;
        document.getElementById('linkCount').textContent = stats.links || 0;
        document.getElementById('fpsCounter').textContent = (stats.fps || 0) + ' FPS';
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
            el.innerHTML =
                `活跃链路 <b style="color:var(--text)">${summary.active_links}</b> · ` +
                `节点 <b style="color:var(--text)">${summary.total_nodes}</b><br>` +
                `平均利用率 <b style="color:var(--text)">${(summary.avg_utilization * 100).toFixed(1)}%</b> · ` +
                `最大时延 <b style="color:var(--text)">${summary.max_latency_ms}ms</b>`;
        }
    }

    // ==================================================================
    // Compat stubs (kept so app.js calls don't break)
    // ==================================================================

    /** v2 simulation_init nodes — filter lists removed in v3 UI, no-op. */
    populateNodeFilters(nodesByType) {
        // Intentionally empty: per-node filter lists were removed for a
        // cleaner UI. Type-level visibility is handled by the layers panel.
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
