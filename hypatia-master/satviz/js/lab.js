/**
 * 星桥实验台（P0 教学实验台）
 *
 * 数据流：simulation_init.experiments（含 inputs/guide/topology）→
 * 自动生成左侧列表 / 原理 tabs / 参数表单 / SVG 拓扑；
 * 参数滑杆 → 前端理论公式实时联动预览；
 * experiment_run(exp_id, params) → experiment_update 帧 →
 * 进度条 / 拓扑包动画 / E3 切换特效 → 对账表 + 结论 + 报告下载；
 * 结果 localStorage 存档，刷新不丢（完成度环由此驱动）。
 */

/* eslint-disable no-console */
'use strict';

const LS_KEY = 'starbridge_lab_v1';

/* 理论公式（与 satviz/experiments.py 保持一致） */
const THEORY = {
    E1: (p) => {
        const ser = p.pkt_bytes * 8 * (1 / 5e8 + 1 / 1e10 + 1 / 1e9) * 1000;
        return { cards: [
            { label: '发送时延（随包长）', val: ser, digits: 3, unit: 'ms' },
            { label: '理论端到端时延', val: 21 + ser, digits: 3, unit: 'ms' },
        ] };
    },
    E2: (p) => {
        const wq = p.rho * 6 / (2 * (1 - p.rho));
        return { cards: [
            { label: '理论排队时延 Wq', val: wq, digits: 1, unit: 'ms' },
            { label: '理论端到端时延', val: 12.0012 + wq, digits: 1, unit: 'ms' },
        ] };
    },
    E3: (p) => ({ cards: [
        { label: '理论切换尖峰 = Q + 1', val: p.queue_pkts + 1, digits: 0, unit: '包' },
        { label: '队列堆积时长', val: 10, digits: 0, unit: 's' },
    ] }),
    E4: (p) => {
        const cap = p.bottleneck_mbps * 1e6 / 12000;
        const load = p.high_pps + p.low_pps;
        return { cards: [
            { label: '瓶颈容量', val: cap, digits: 0, unit: 'pps' },
            { label: '总负载', val: load, digits: 0, unit: 'pps',
              state: load > cap ? 'cong' : 'free',
              stateText: load > cap ? '负载 > 容量 · 拥塞' : '负载 ≤ 容量 · 畅通' },
        ] };
    },
};

const STAGE_ZH = {
    warmup: '预热', simulating: '仿真推进', measuring: '数据测量',
    queueing: '队列堆积', restoring: '恢复期',
};

const NODE_STYLE = {
    uav: { color: '#34d399', icon: 'UAV' },
    sat: { color: '#38bdf8', icon: 'SAT' },
    gs: { color: '#fbbf24', icon: 'GS' },
    router: { color: '#818cf8', icon: 'R' },
    src: { color: '#34d399', icon: 'SRC' },
};

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

class LabApp {
    constructor() {
        this.catalog = [];
        this.current = null;      // 当前实验 spec
        this.params = {};         // 当前实验参数值（key -> value）
        this.results = {};        // exp_id -> result（内存 + 存档）
        this.running = false;
        this.topoState = 'initial';   // E3: initial | switched
        this._pktAnims = [];
        this._loadArchive();
    }

    /* ---------------- 存档 ---------------- */
    _loadArchive() {
        try {
            const raw = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
            this.results = raw.results || {};
            this._lastCurrent = raw.current || null;
        } catch (e) { this.results = {}; }
    }

    _saveArchive() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                results: this.results, current: this.current && this.current.exp_id,
            }));
        } catch (e) { /* 存储满等场景忽略 */ }
    }

    /* ---------------- 连接 ---------------- */
    connect() {
        this.ws = new WebSocketManager({
            // 地址来自 SBConfig（config.js，支持 ?ws=host:port 覆盖）
            host: window.SBConfig ? window.SBConfig.host : '127.0.0.1',
            port: window.SBConfig ? window.SBConfig.port : 8000,
            path: '/ws/client',
            onConnect: () => this._setConn(true),
            onDisconnect: () => this._setConn(false),
            onSimulationInit: (payload) => this._init(payload),
            onExperimentUpdate: (payload) => this.handleUpdate(payload),
            onError: (payload) => {
                if (payload && payload.error === 'experiment_busy') return;
            },
        });
        this.ws.connect();
    }

    _setConn(on) {
        const el = document.getElementById('conn');
        el.classList.toggle('on', on);
        document.getElementById('connTxt').textContent =
            on ? '核心已连接' : '连接中断，自动重连中…';
        document.getElementById('runBtn').disabled = !on || this.running;
    }

    _init(payload) {
        this.catalog = payload.experiments || [];
        if (!this.catalog.length) return;
        document.getElementById('booting').style.display = 'none';
        this._renderList();
        const first = this._lastCurrent &&
            this.catalog.find((e) => e.exp_id === this._lastCurrent)
            ? this._lastCurrent : this.catalog[0].exp_id;
        this.select(first);
    }

    /* ---------------- 列表与完成度 ---------------- */
    _renderList() {
        const box = document.getElementById('expList');
        box.innerHTML = '';
        for (const spec of this.catalog) {
            const r = this.results[spec.exp_id];
            const item = document.createElement('div');
            item.className = 'exp-item' +
                (this.current && spec.exp_id === this.current.exp_id ? ' active' : '');
            item.innerHTML =
                `<span class="code">${esc(spec.exp_id)}</span>` +
                `<span class="nm">${esc(spec.name)}</span>` +
                `<span class="st ${r ? (r.all_pass ? 'pass' : 'fail') : ''}"
                      title="${r ? (r.all_pass ? '对账通过' : '对账未通过') : '未完成'}"></span>`;
            item.addEventListener('click', () => this.select(spec.exp_id));
            box.appendChild(item);
        }
        this._renderRing();
    }

    _renderRing() {
        const total = this.catalog.length || 4;
        const passed = this.catalog.filter(
            (e) => this.results[e.exp_id] && this.results[e.exp_id].all_pass).length;
        const pct = Math.round(passed / total * 100);
        document.getElementById('ringFg').style.strokeDashoffset =
            (163.4 * (1 - passed / total)).toFixed(1);
        document.getElementById('ringPct').textContent = pct + '%';
        document.getElementById('ringSub').textContent =
            `${passed} / ${total} 实验`;
    }

    /* ---------------- 选中实验 ---------------- */
    select(expId) {
        this.current = this.catalog.find((e) => e.exp_id === expId) || this.catalog[0];
        this.params = {};
        for (const f of this.current.inputs) this.params[f.key] = f.default;
        this.topoState = 'initial';
        this._saveArchive();

        /* hero */
        document.getElementById('hCode').textContent = this.current.exp_id;
        document.getElementById('hName').textContent = this.current.name;
        document.getElementById('hDiff').textContent =
            '难度 · ' + this.current.difficulty;
        document.getElementById('hMin').textContent =
            '建议 ' + this.current.minutes + ' 分钟';
        document.getElementById('hSummary').textContent = this.current.summary;
        document.getElementById('hNote').textContent = this.current.theory_note;

        /* tabs */
        this._renderTab('principle');

        /* 参数 + 理论 + 拓扑 */
        this._renderParams();
        this._renderTheory();
        this._renderTopo();

        /* 结果区：显示历史结果或占位 */
        this._renderProgress(null);
        this._renderResult(this.results[expId] || null);

        this._renderList();
        document.getElementById('expMain')
            .style.animation = 'none';
        requestAnimationFrame(() => {
            document.getElementById('expMain').style.animation = '';
        });
    }

    _renderTab(tab) {
        const g = this.current.guide;
        const body = document.getElementById('tabBody');
        if (tab === 'principle') {
            body.innerHTML =
                `<div class="obj"><b style="color:var(--brand)">实验目的</b>　${esc(g.objective)}</div>` +
                `<pre>${esc(g.principle)}</pre>`;
        } else if (tab === 'steps') {
            body.innerHTML = '<ol>' + g.steps.map((s) =>
                `<li>${esc(s)}</li>`).join('') + '</ol>';
        } else {
            body.innerHTML = g.questions.map((q, i) =>
                `<div class="qa"><b>Q${i + 1}</b>　${esc(q)}</div>`).join('');
        }
        document.querySelectorAll('#tabs button').forEach((b) =>
            b.classList.toggle('on', b.dataset.tab === tab));
    }

    /* ---------------- 参数表单 ---------------- */
    _renderParams() {
        const box = document.getElementById('paramList');
        box.innerHTML = '';
        for (const f of this.current.inputs) {
            const div = document.createElement('div');
            div.className = 'param';
            const val = this.params[f.key];
            div.innerHTML =
                `<div class="p-head"><span class="p-label">${esc(f.label)}</span>` +
                `<span class="p-val">${val}</span>` +
                (f.unit ? `<span class="p-unit">${esc(f.unit)}</span>` : '') +
                `</div>` +
                `<input type="range" min="${f.min}" max="${f.max}" step="${f.step}" value="${val}">` +
                (f.tip ? `<div class="p-tip">${esc(f.tip)}</div>` : '');
            const input = div.querySelector('input');
            const chip = div.querySelector('.p-val');
            const sync = (flash) => {
                const v = parseFloat(input.value);
                this.params[f.key] = f.type === 'int' ? Math.round(v) : v;
                chip.textContent = this.params[f.key];
                input.style.setProperty('--fill',
                    ((v - f.min) / (f.max - f.min) * 100) + '%');
                if (flash) {
                    chip.classList.add('flash');
                    setTimeout(() => chip.classList.remove('flash'), 400);
                }
            };
            input.addEventListener('input', () => { sync(true); this._renderTheory(); });
            sync(false);
            box.appendChild(div);
        }
    }

    _renderTheory() {
        const t = THEORY[this.current.exp_id];
        const grid = document.getElementById('theoryGrid');
        if (!t) { grid.innerHTML = ''; return; }
        const { cards } = t(this.params);
        grid.innerHTML = cards.map((c) =>
            `<div class="tcard">` +
            `<div class="t-label">${esc(c.label)}</div>` +
            `<div class="t-val">${c.val.toFixed(c.digits != null ? c.digits : 2)}` +
            `<small>${esc(c.unit || '')}</small></div>` +
            (c.state ? `<span class="t-state ${c.state}">${esc(c.stateText)}</span>` : '') +
            `</div>`).join('');
        /* flash 一次提示联动 */
        grid.querySelectorAll('.tcard').forEach((el) => {
            el.classList.add('flash');
            setTimeout(() => el.classList.remove('flash'), 600);
        });
    }

    /* ---------------- 运行控制 ---------------- */
    run() {
        if (!this.current || this.running) return;
        this.running = true;
        this.topoState = 'initial';
        const btn = document.getElementById('runBtn');
        btn.textContent = '✕ 取消实验';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-danger');
        document.querySelectorAll('#paramList input').forEach((i) => i.disabled = true);
        document.getElementById('outActions').style.display = 'none';
        this._renderProgress({ stage: 'warmup', progress: 0, note: '命令已下发' });
        this.ws.sendCommand('experiment_run',
            { exp_id: this.current.exp_id, params: this.params });
    }

    cancel() {
        this.ws.sendCommand('experiment_cancel', {});
    }

    _idle() {
        this.running = false;
        const btn = document.getElementById('runBtn');
        btn.textContent = '▶ 运行实验';
        btn.classList.add('btn-primary');
        btn.classList.remove('btn-danger');
        btn.disabled = !this.ws.isConnected;
        document.querySelectorAll('#paramList input').forEach((i) => i.disabled = false);
    }

    /* ---------------- 实验更新帧 ---------------- */
    handleUpdate(p) {
        if (!this.current || p.exp_id !== this.current.exp_id) return;
        if (p.status === 'running') {
            this._renderProgress(p);
            this._pktAnimation(true);
            if (this.current.topology.switch_label &&
                p.stage && p.stage !== 'queueing' && this.topoState === 'initial') {
                this.topoState = 'switched';
                this._renderTopo();
                const note = document.getElementById('topoSwitchNote');
                note.textContent = '⟳ ' + this.current.topology.switch_label;
                note.classList.add('on');
            }
        } else if (p.status === 'done') {
            this.results[p.exp_id] = Object.assign(
                { _ts: Date.now() }, p.result);
            this._saveArchive();
            this._renderProgress(null);
            this._renderResult(p.result);
            this._idle();
            this._renderList();
            this._toast(p.result.all_pass
                ? `✔ ${p.exp_id} 对账全部通过` : `✘ ${p.exp_id} 部分判据未通过`);
        } else if (p.status === 'cancelled') {
            this._renderProgress(null);
            this._idle();
            this._toast('实验已取消');
        } else if (p.status === 'error') {
            this._renderProgress(null);
            this._idle();
            this._toast('运行失败：' + (p.error || '未知错误'));
        }
    }

    _renderProgress(p) {
        const wrap = document.getElementById('progWrap');
        if (!p) { wrap.classList.remove('on'); this._pktAnimation(false); return; }
        wrap.classList.add('on');
        const pct = Math.round((p.progress || 0) * 100);
        const stage = STAGE_ZH[p.stage] || p.stage || '';
        document.getElementById('progStage').textContent =
            stage + (p.note ? ' · ' + p.note : '');
        document.getElementById('progPct').textContent = pct + '%';
        document.getElementById('progFill').style.width = pct + '%';
    }

    _renderResult(result) {
        const wrap = document.getElementById('verdictWrap');
        const concl = document.getElementById('conclBox');
        const actions = document.getElementById('outActions');
        if (!result || !result.verdict) {
            wrap.innerHTML = '<div class="verdict-hint">运行实验后，这里将显示' +
                '「理论值 vs 实测值」逐行对账表</div>';
            concl.innerHTML = '';
            actions.style.display = 'none';
            return;
        }
        const rows = result.verdict.map((r) =>
            `<tr class="${r.pass ? 'pass-row' : 'fail-row'}">` +
            `<td>${esc(r.label)}</td>` +
            `<td>${esc(r.theory)}</td>` +
            `<td>${esc(r.measured)}${r.unit ? ' ' + esc(r.unit) : ''}</td>` +
            `<td class="${r.pass ? 'ok' : 'bad-t'}">${r.pass ? '✔ 通过' : '✘ 未通过'}</td></tr>`)
            .join('');
        wrap.innerHTML =
            `<table class="vt"><thead><tr><th>判据</th><th>理论值</th>` +
            `<th>实测值</th><th>判定</th></tr></thead><tbody>${rows}</tbody></table>`;
        concl.innerHTML =
            `<div class="concl ${result.all_pass ? '' : 'fail'}">` +
            `<b class="${result.all_pass ? 'ok' : 'bad-t'}" style="color:inherit">` +
            `${result.all_pass ? '✔ 对账通过' : '✘ 对账未通过'}</b>　` +
            `${esc(result.conclusion)}</div>`;
        actions.style.display = 'flex';
    }

    /* ---------------- SVG 拓扑 ---------------- */
    _renderTopo() {
        const svg = document.getElementById('topoSvg');
        const topo = this.current.topology;
        if (!topo) { svg.innerHTML = ''; return; }
        const W = 820, H = 210;
        const nodes = topo.nodes, edges = topo.edges || [];

        /* 分层布局：源（未作为边终点）深度 0 */
        const depth = {};
        nodes.forEach((n) => depth[n.id] = 0);
        for (let i = 0; i < nodes.length; i++) {
            for (const e of edges) {
                depth[e.b] = Math.max(depth[e.b], depth[e.a] + 1);
            }
        }
        const layers = {};
        nodes.forEach((n) =>
            (layers[depth[n.id]] = layers[depth[n.id]] || []).push(n));
        const maxD = Math.max(...Object.keys(layers).map(Number));
        const pos = {};
        Object.entries(layers).forEach(([d, ns]) => {
            const x = maxD === 0 ? W / 2 : 80 + d * ((W - 160) / maxD);
            ns.forEach((n, i) => {
                const y = ns.length === 1 ? H / 2 - 6
                    : (H - 60) * ((i + 1) / (ns.length + 1)) + 22;
                pos[n.id] = { x, y };
            });
        });

        const switched = this.topoState === 'switched';
        let out = '';
        /* 边 */
        for (const e of edges) {
            const a = pos[e.a], b = pos[e.b];
            if (!a || !b) continue;
            const isAlt = !!e.alt;
            let style = 'stroke-width:2;stroke-linecap:round;';
            if (isAlt && !switched) {
                style += 'stroke:#38bdf8;stroke-dasharray:6 5;opacity:0.35;';
            } else if (isAlt && switched) {          // 切换后 alt 变主路
                style += 'stroke:#34d399;opacity:0.95;';
            } else if (!isAlt && switched) {          // 原主路退役
                style += 'stroke:#f87171;stroke-dasharray:6 5;opacity:0.3;';
            } else {
                style += 'stroke:#38bdf8;opacity:0.85;';
            }
            out += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"
                    style="${style}"/>`;
            const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - 8;
            out += `<text x="${mx}" y="${my}" text-anchor="middle"
                    font-size="10" fill="#8fa3bd" opacity="0.9">${esc(e.label || '')}</text>`;
        }
        /* 节点 */
        for (const n of nodes) {
            const p = pos[n.id];
            const s = NODE_STYLE[n.type] || NODE_STYLE.router;
            out += `<circle cx="${p.x}" cy="${p.y}" r="17" fill="${s.color}22"
                    stroke="${s.color}" stroke-width="1.5"/>` +
                `<text x="${p.x}" y="${p.y + 3.5}" text-anchor="middle"
                    font-size="9" font-weight="700" fill="${s.color}">${esc(s.icon)}</text>` +
                `<text x="${p.x}" y="${p.y + 33}" text-anchor="middle"
                    font-size="11" fill="#c7d5e8">${esc(n.id)}</text>`;
        }
        /* 主路径端点标记 */
        svg.innerHTML = out;
        this._pos = pos;
        this._edges = edges;
    }

    /* 包流动画：主路径每条边 2 个光点 */
    _pktAnimation(on) {
        this._pktAnims.forEach((el) => el.remove());
        this._pktAnims = [];
        if (!on || !this._pos || !this._edges) return;
        const svg = document.getElementById('topoSvg');
        const switched = this.topoState === 'switched';
        const main = this._edges.filter((e) => !!e.alt === switched);
        for (const e of main) {
            const a = this._pos[e.a], b = this._pos[e.b];
            if (!a || !b) continue;
            for (let k = 0; k < 2; k++) {
                const c = document.createElementNS(
                    'http://www.w3.org/2000/svg', 'circle');
                c.setAttribute('r', '3.5');
                c.setAttribute('fill', switched ? '#34d399' : '#67d3fb');
                const anim = document.createElementNS(
                    'http://www.w3.org/2000/svg', 'animateMotion');
                anim.setAttribute('dur', '2.2s');
                anim.setAttribute('begin', (k * 1.1) + 's');
                anim.setAttribute('repeatCount', 'indefinite');
                anim.setAttribute('path', `M ${a.x} ${a.y} L ${b.x} ${b.y}`);
                c.appendChild(anim);
                svg.appendChild(c);
                this._pktAnims.push(c);
            }
        }
    }

    /* ---------------- 报告 ---------------- */
    downloadReport() {
        const r = this.results[this.current.exp_id];
        if (!r) return;
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
                   `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
        const paramRows = Object.entries(r.params_used || {}).map(([k, v]) =>
            `<tr><th>${esc(k)}</th><td>${esc(String(v))}</td></tr>`).join('');
        const theoryRows = (THEORY[this.current.exp_id]
            ? THEORY[this.current.exp_id](r.params_used).cards : []).map((c) =>
            `<tr><th>${esc(c.label)}</th><td>${c.val.toFixed(c.digits != null ? c.digits : 2)} ${esc(c.unit || '')}</td></tr>`)
            .join('');
        const vRows = r.verdict.map((row) =>
            `<tr class="${row.pass ? '' : 'bad'}">` +
            `<td>${esc(row.label)}</td><td>${esc(String(row.theory))}</td>` +
            `<td>${esc(String(row.measured))}${row.unit ? ' ' + esc(row.unit) : ''}</td>` +
            `<td class="${row.pass ? 'ok' : 'bad'}">${row.pass ? '通过' : '未通过'}</td></tr>`)
            .join('');

        const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>星桥实验报告 ${esc(r.exp_id)} ${esc(r.name)}</title><style>
body{font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#1a2230;
max-width:820px;margin:32px auto;padding:0 20px;line-height:1.65}
h1{font-size:21px;border-bottom:3px solid #0e7490;padding-bottom:8px}
.meta{color:#5a6a7e;font-size:13px;margin-bottom:18px}
.stamp{display:inline-block;padding:4px 18px;border-radius:999px;font-weight:700;margin-left:10px}
.stamp.ok{background:#d9f3e6;color:#0a7a4a}.stamp.bad{background:#fde2e2;color:#b42318}
table{border-collapse:collapse;width:100%;margin:10px 0 20px;font-size:13.5px}
th,td{border:1px solid #c8d2de;padding:7px 10px;text-align:left}
thead th{background:#eef4f8}
tr.bad td{background:#fdf1f1}td.ok{color:#0a7a4a;font-weight:600}td.bad{color:#b42318;font-weight:600}
.theory{background:#f2f7fb;border-left:4px solid #0e7490;padding:10px 14px;margin:14px 0}
.concl{background:#f6f8fa;border-left:4px solid #64748b;padding:10px 14px;margin:14px 0}
.student{margin-top:28px;font-size:13.5px}
.student span{display:inline-block;border-bottom:1px solid #94a3b8;min-width:160px;margin-right:28px}
@media print{body{margin:0}}
</style></head><body>
<h1>星桥 · 卫星网络虚拟仿真实验报告
<span class="stamp ${r.all_pass ? 'ok' : 'bad'}">${r.all_pass ? '对账通过' : '对账未通过'}</span></h1>
<div class="meta">实验编号 ${esc(r.exp_id)} · ${esc(r.name)} · 生成时间 ${ts}</div>
<p>${esc(r.summary)}</p>
<div class="theory"><b>理论判据：</b>${esc(r.theory_note)}</div>
<h3>本组实验参数</h3><table><tbody>${paramRows}</tbody></table>
${theoryRows ? `<h3>理论预览（本组参数）</h3><table><tbody>${theoryRows}</tbody></table>` : ''}
<h3>理论-实测对账</h3>
<table><thead><tr><th>判据</th><th>理论值</th><th>实测值</th><th>判定</th></tr></thead>
<tbody>${vRows}</tbody></table>
<div class="concl"><b>结论：</b>${esc(r.conclusion)}</div>
<div class="student">姓名 <span></span> 学号 <span></span> 班级 <span></span> 日期 <span></span></div>
</body></html>`;

        const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `StarBridge_${r.exp_id}_report_${ts.slice(0, 10).replace(/-/g, '')}.html`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 3000);
    }

    _toast(msg) {
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('on');
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
    }
}

/* ---------------- 启动 ---------------- */
(function boot() {
    /* 背景星点 */
    const stars = document.getElementById('stars');
    for (let i = 0; i < 90; i++) {
        const s = document.createElement('i');
        s.style.left = Math.random() * 100 + '%';
        s.style.top = Math.random() * 100 + '%';
        s.style.animationDelay = (Math.random() * 4) + 's';
        s.style.transform = `scale(${0.5 + Math.random()})`;
        stars.appendChild(s);
    }

    window.lab = new LabApp();
    lab.connect();

    document.querySelectorAll('#tabs button').forEach((b) =>
        b.addEventListener('click', () => lab._renderTab(b.dataset.tab)));
    document.getElementById('runBtn').addEventListener('click', () =>
        lab.running ? lab.cancel() : lab.run());
    document.getElementById('resetBtn').addEventListener('click', () => {
        if (lab.running) return;
        lab.select(lab.current.exp_id);
        lab._toast('参数已重置为默认值');
    });
    document.getElementById('reportBtn').addEventListener('click', () =>
        lab.downloadReport());
    document.getElementById('rerunBtn').addEventListener('click', () => {
        if (!lab.running) lab.run();
    });
})();
