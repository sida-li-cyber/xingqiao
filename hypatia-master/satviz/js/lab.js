/**
 * 星桥实验台（教学闭环版：评分 / 预习测验 / 步骤日志 / 服务端存档）
 *
 * 数据流：simulation_init.experiments（含 inputs/guide/topology/quiz）→
 * 自动生成左侧列表 / 原理 tabs / 参数表单 / SVG 拓扑 / 预习测验；
 * 参数滑杆 → 前端理论公式实时联动预览；
 * experiment_run(exp_id, params) → experiment_update 帧（带 run_id）→
 * 进度条 / 拓扑包动画 / E3 切换特效 → 对账表 + 评分卡 + 报告；
 * 评分 100 分制 = 对账判定 70 + 参数探索 10 + 预习测验 10 + 思考题 10；
 * 步骤日志字段对齐 ilab-x 2022 版接口（seq/startTime/timeUsed/maxScore/
 * score/scoringModel），随记录存档，为后续国家平台回传铺路；
 * 结果双存档：localStorage（离线可用）+ 服务端 /api/edu（登录后可跨机恢复、
 * 提交报告供教师批改）。
 */

/* eslint-disable no-console */
'use strict';

const LS_KEY = 'starbridge_lab_v2';
const SS_KEY = 'starbridge_edu_v1';
const EDU_HOST = (!window.location.hostname ||
    window.location.hostname === 'localhost')
    ? '127.0.0.1' : window.location.hostname;
const EDU_API = `http://${EDU_HOST}:8000/api/edu`;

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
    E5: (p) => {
        const congested = p.src_pps > p.a_cap_pps;
        const loss = congested ? 1 - p.a_cap_pps / p.src_pps : 0;
        return { cards: [
            { label: '捷径容量', val: p.a_cap_pps, digits: 0, unit: 'pps' },
            { label: '源速率 λ', val: p.src_pps, digits: 0, unit: 'pps',
              state: congested ? 'cong' : 'free',
              stateText: congested ? 'λ > C · 最短时延将死守瓶颈' : 'λ ≤ C · 捷径畅通' },
            { label: '最短时延丢包率', val: loss * 100, digits: 1, unit: '%' },
        ] };
    },
    E6: (p) => {
        const hops = (p.planes - 1) + Math.floor(p.sats_per_plane / 2);
        return { cards: [
            { label: '星座规模', val: p.planes * p.sats_per_plane, digits: 0, unit: '星' },
            { label: '最短跳数', val: hops, digits: 0, unit: '跳' },
            { label: '理论端到端时延', val: 11 + hops * 8, digits: 0, unit: 'ms' },
        ] };
    },
    E7: (p) => {
        const capKpps = 1e9 / 12000 / (10 ** (p.rain_db / 10)) / 1000;
        const congested = p.src_pps / 1000 > capKpps;
        return { cards: [
            { label: '雨衰后有效容量', val: capKpps, digits: 1, unit: 'kpps',
              state: congested ? 'cong' : 'free',
              stateText: congested ? 'λ > C_eff · 雨衰中断' : 'λ ≤ C_eff · 链路可用' },
            { label: '业务速率', val: p.src_pps / 1000, digits: 1, unit: 'kpps' },
            { label: '理论丢包率', val: congested
                ? (1 - capKpps * 1000 / p.src_pps) * 100 : 0, digits: 1, unit: '%' },
        ] };
    },
    /* E9 逆向设计：跳数-时延换算 + 目标约束达标预判（达标线对齐后端 E9_TARGETS） */
    E9: (p) => {
        const hops = (p.planes - 1) + Math.floor(p.sats_per_plane / 2);
        const e2e = 11 + hops * 8;
        return { cards: [
            { label: '星座规模', val: p.planes * p.sats_per_plane, digits: 0, unit: '星' },
            { label: '最短跳数（目标 ≤ 4）', val: hops, digits: 0, unit: '跳',
              state: hops <= 4 ? 'free' : 'cong',
              stateText: hops <= 4 ? '跳数达标' : '跳数超标' },
            { label: '理论端到端时延（目标 ≤ 40）', val: e2e, digits: 0, unit: 'ms',
              state: e2e <= 40 ? 'free' : 'cong',
              stateText: e2e <= 40 ? 'e2e 达标' : 'e2e 超标' },
        ] };
    },
};

const STAGE_ZH = {
    warmup: '预热', simulating: '仿真推进', measuring: '数据测量',
    queueing: '队列堆积', restoring: '恢复期',
    run_delay: '最短时延路由', run_load_aware: '负载感知路由',
    queued: '排队等待',
};

/* 参数扫描键（与 experiments.py 各实验 sweep_key 对齐；E8 诊断型 /
   E9 设计型无扫描键，历史表中回退展示其参数摘要） */
const SWEEP_KEYS = {
    E1: 'pps', E2: 'rho', E3: 'src_pps', E4: 'low_pps',
    E5: 'src_pps', E6: 'sats_per_plane', E7: 'rain_db',
};

const ATTEMPTS_MAX = 20;    // attempts 历史上限（每实验）

const NODE_STYLE = {
    uav: { color: '#34d399', icon: 'UAV' },
    sat: { color: '#38bdf8', icon: 'SAT' },
    gs: { color: '#fbbf24', icon: 'GS' },
    router: { color: '#818cf8', icon: 'R' },
    src: { color: '#34d399', icon: 'SRC' },
};

/* 步骤元数据：字段与分值对齐 ilab-x 步骤赋分模型（总满分 100）。
   seq 4「结果确认」只计时不计分（maxScore=0）。 */
const STEPS_META = {
    1: { title: '预习测验', max: 10, model: '考察点：核心概念与理论公式' },
    2: { title: '参数设置', max: 10, model: '考察点：参数影响理解（非默认参数得探索分）' },
    3: { title: '仿真运行', max: 70, model: '考察点：理论-实测对账（按误差分档）' },
    4: { title: '结果确认', max: 0, model: '考察点：阅读对账表与结论' },
    5: { title: '思考题', max: 10, model: '考察点：按作答完整度给分' },
};

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const fmtTime = (ms) => {
    const d = new Date(ms);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

class LabApp {
    constructor() {
        this.catalog = [];
        this.current = null;      // 当前实验 spec
        this.params = {};         // 当前实验参数值（key -> value）
        this.results = {};        // exp_id -> result（内存 + 存档）
        this.quizGrades = {};     // exp_id -> quiz grade（最近一次判分）
        this.qAnswers = {};       // exp_id -> {"0": text, ...} 思考题作答
        this.attempts = {};       // exp_id -> [attempt]（参数-结果历史，上限 20）
        this.analysis = {};       // exp_id -> 分析结论文本（报告必填）
        this.running = false;
        this.topoState = 'initial';   // E3: initial | switched
        this._pktAnims = [];
        this.exams = [];              // 进行中的考核（登录后拉取）
        this.exam = null;             // 当前参加的考核（考核模式）
        this._examTick = null;        // 倒计时 interval
        this._examDeadline = 0;
        this._activeRun = null;   // 当前运行的 run_id（核心下发）
        this._steps = {};         // seq -> step 累计记录（本实验会话）
        this._selTs = 0;          // 选中实验时刻（总时长统计）
        this.user = null;         // 登录用户 {student_id, name, role}
        this.token = '';
        this._loadArchive();
        this._loadSession();
    }

    /* ---------------- 存档（本地 + 会话） ---------------- */
    _loadArchive() {
        try {
            const raw = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
            this.results = raw.results || {};
            this.qAnswers = raw.qAnswers || {};
            this.quizGrades = raw.quizGrades || {};
            this.attempts = raw.attempts || {};
            this.analysis = raw.analysis || {};
            this._lastCurrent = raw.current || null;
        } catch (e) {
            this.results = {}; this.qAnswers = {}; this.quizGrades = {};
            this.attempts = {}; this.analysis = {};
        }
    }

    _saveArchive() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                results: this.results, qAnswers: this.qAnswers,
                quizGrades: this.quizGrades,
                attempts: this.attempts, analysis: this.analysis,
                current: this.current && this.current.exp_id,
            }));
        } catch (e) { /* 存储满等场景忽略 */ }
    }

    _loadSession() {
        try {
            const raw = JSON.parse(sessionStorage.getItem(SS_KEY) || 'null');
            if (raw && raw.token) { this.user = raw.user; this.token = raw.token; }
        } catch (e) { /* ignore */ }
    }

    _saveSession() {
        try {
            sessionStorage.setItem(SS_KEY, JSON.stringify(
                this.user ? { user: this.user, token: this.token } : null));
        } catch (e) { /* ignore */ }
    }

    /* ---------------- 教学服务端（/api/edu） ---------------- */
    async _eduFetch(path, opts = {}) {
        if (!this.token) return null;
        opts.headers = Object.assign(
            { 'X-Edu-Token': this.token, 'Content-Type': 'application/json' },
            opts.headers || {});
        try {
            const r = await fetch(EDU_API + path, opts);
            if (r.status === 401) { this._logoutLocal(); return null; }
            if (!r.ok) return null;
            return await r.json();
        } catch (e) { return null; }   // 后端未启动等场景静默降级
    }

    async login() {
        const sid = document.getElementById('loginId').value.trim();
        const name = document.getElementById('loginName').value.trim();
        if (!sid || !name) { this._toast('请填写学号与姓名'); return; }
        try {
            const r = await fetch(`${EDU_API}/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ student_id: sid, name, role: 'student' }),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            this.user = data.user; this.token = data.token;
            this._saveSession(); this._renderLogin();
            this._toast(`欢迎，${name}（已登录，实验记录将同步到服务端）`);
            this._pullRecords();
            this._pullExams();
        } catch (e) {
            this._toast('登录失败：后端未启动或网络错误');
        }
    }

    logout() {
        this._logoutLocal();
        this._toast('已退出登录（本地存档仍保留）');
    }

    _logoutLocal() {
        this.user = null; this.token = '';
        this._saveSession(); this._renderLogin();
    }

    _renderLogin() {
        const form = document.getElementById('loginForm');
        const info = document.getElementById('loginInfo');
        if (this.user) {
            form.style.display = 'none';
            info.style.display = 'flex';
            document.getElementById('loginUserTxt').textContent =
                `${this.user.name}（${this.user.student_id}）`;
        } else {
            form.style.display = 'flex';
            info.style.display = 'none';
        }
        /* 已登录且有结果时，显示提交按钮 */
        this._refreshOutActions();
        this._renderExamEntry();
    }

    /* 登录后从服务端拉取历史（对齐 2020 规范「下次登录可恢复」） */
    async _pullRecords() {
        const data = await this._eduFetch('/records');
        if (!data) return;
        let n = 0;
        for (const rec of data.records) {
            const cur = this.results[rec.exp_id];
            const curTs = cur ? (cur._ts || 0) : 0;
            if (rec.ts * 1000 > curTs) {
                this.results[rec.exp_id] = {
                    _ts: rec.ts * 1000, _rec_id: rec.id,
                    _submitted: rec.status === 'submitted',
                    exp_id: rec.exp_id, name: rec.exp_name,
                    verdict: rec.verdict, conclusion: rec.conclusion,
                    all_pass: rec.all_pass, params_used: rec.params_used,
                    score: rec.score, score_detail: rec.score_detail,
                    score_total: rec.score, steps: rec.steps,
                    summary: '', theory_note: '', _restored: true,
                };
                n++;
            }
        }
        if (n) {
            this._saveArchive();
            this._renderList();
            if (this.current) this._renderResult(this.results[this.current.exp_id] || null);
            this._toast(`已从服务端恢复 ${n} 条实验记录`);
        }
    }

    /* 结果产生后推送到服务端存档 */
    async _pushRecord(result) {
        if (!this.token) return;
        const payload = {
            exp_id: result.exp_id, exp_name: result.name,
            score: result.score_total, all_pass: result.all_pass,
            score_detail: result.score_detail_full,
            verdict: result.verdict, conclusion: result.conclusion,
            params_used: result.params_used, steps: result.steps,
            quiz: this.quizGrades[result.exp_id] || null,
            questions: this.qAnswers[result.exp_id] || {},
            duration_s: Math.round((Date.now() - this._selTs) / 1000),
            exam_id: result._examId || '',
        };
        const data = await this._eduFetch('/records', {
            method: 'POST', body: JSON.stringify(payload) });
        if (data && data.record) {
            this.results[result.exp_id]._rec_id = data.record.id;
            this.results[result.exp_id]._submitted = false;
            this._saveArchive();
            this._refreshOutActions();
        }
    }

    async submitReport() {
        const r = this.results[this.current.exp_id];
        if (!r || !r._rec_id) { this._toast('请先登录并运行实验'); return; }
        const data = await this._eduFetch(`/records/${r._rec_id}/submit`,
            { method: 'POST', body: '{}' });
        if (data) {
            r._submitted = true;
            this._saveArchive(); this._refreshOutActions();
            this._toast('✔ 报告已提交，等待教师批改');
        } else {
            this._toast('提交失败：请确认已登录且后端在线');
        }
    }

    /* ---------------- 考核模式（I4） ---------------- */
    async _pullExams() {
        const data = await this._eduFetch('/exams');
        this.exams = (data && data.exams) || [];
        this._renderExamEntry();
    }

    _renderExamEntry() {
        const entry = document.getElementById('examEntry');
        if (!entry) return;
        const active = this.exams.filter((e) => e.active);
        entry.style.display = (this.user && !this.exam && active.length)
            ? 'flex' : 'none';
        const btn = document.getElementById('examEnterBtn');
        if (btn && active.length) {
            btn.textContent = `⚠ 进入考核：${active[0].name}`;
        }
    }

    async startExam(exam) {
        this.exam = exam;
        this._examDeadline = Date.now() + exam.duration_s * 1000;
        document.getElementById('examBanner').style.display = 'flex';
        document.getElementById('examName').textContent =
            `${exam.name}（${exam.exp_ids.join(' / ')}）`;
        this._renderExamEntry();
        this._tickExam();
        this._examTick = setInterval(() => this._tickExam(), 1000);
        this._renderList();
        const first = this.catalog.find((e) => e.exp_id === exam.exp_ids[0]);
        if (first) this.select(first.exp_id);
        this._toast(`已进入考核「${exam.name}」，参数已锁定，计时开始`);
    }

    _tickExam() {
        const left = Math.max(0, this._examDeadline - Date.now());
        const m = Math.floor(left / 60000);
        const s = Math.floor((left % 60000) / 1000);
        const el = document.getElementById('examTimer');
        el.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        el.classList.toggle('urgent', left < 300000);   // 最后 5 分钟告警
        if (left <= 0) this.endExam(true);
    }

    endExam(auto) {
        const name = this.exam ? this.exam.name : '';
        clearInterval(this._examTick);
        this._examTick = null;
        this.exam = null;
        document.getElementById('examBanner').style.display = 'none';
        this._renderExamEntry();
        this._renderList();
        this._toast(auto
            ? `⏰ 考核「${name}」时间到，已自动收卷`
            : '已退出考核模式');
        /* 重新选中当前实验以解除参数锁定 */
        if (this.current) this.select(this.current.exp_id);
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
        /* 考核模式下只显示考试规定的实验 */
        const list = this.exam
            ? this.catalog.filter((e) => this.exam.exp_ids.includes(e.exp_id))
            : this.catalog;
        for (const spec of list) {
            const r = this.results[spec.exp_id];
            const item = document.createElement('div');
            item.className = 'exp-item' +
                (this.current && spec.exp_id === this.current.exp_id ? ' active' : '');
            item.innerHTML =
                `<span class="code">${esc(spec.exp_id)}</span>` +
                `<span class="nm">${esc(spec.name)}` +
                (r && r.score_total != null
                    ? `<small class="sc">${r.score_total}分</small>` : '') +
                `</span>` +
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

    /* ---------------- 步骤日志（ilab-x 步骤字段结构） ---------------- */
    _stepReset() { this._steps = {}; }

    _stepStart(seq) {
        const st = this._steps[seq] || { openMs: 0,
            repeatCount: (this._steps[seq] || {}).repeatCount || 0 };
        st.openMs = Date.now();
        st.repeatCount += 1;
        this._steps[seq] = st;
    }

    _stepPause(seq) {
        const st = this._steps[seq];
        if (st && st.openMs) {
            st.ms = (st.ms || 0) + (Date.now() - st.openMs);
            st.openMs = 0;
        }
    }

    _stepFinalize(seq, score, evaluation) {
        this._stepPause(seq);
        const st = this._steps[seq] || {};
        const meta = STEPS_META[seq];
        return {
            seq, title: meta.title,
            startTime: st.startTs || this._selTs,
            endTime: Date.now(),
            timeUsed: Math.max(1, Math.round((st.ms || 0) / 1000)),
            maxScore: meta.max, score: score || 0,
            repeatCount: st.repeatCount || 1,
            scoringModel: meta.model, evaluation: evaluation || '',
        };
    }

    _stepsPayload(verdictScore, exploreScore, quizScore, qScore) {
        const out = [];
        out.push(this._stepFinalize(1, quizScore,
            quizScore > 0 ? `测验得分 ${quizScore}/10` : '未作答'));
        out.push(this._stepFinalize(2, exploreScore,
            exploreScore > 0 ? '使用非默认参数运行' : '使用默认参数'));
        out.push(this._stepFinalize(3, verdictScore,
            this.results[this.current.exp_id]
                ? (this.results[this.current.exp_id].all_pass ? '对账全部通过' : '部分判据未通过')
                : ''));
        out.push(this._stepFinalize(4, 0, '查看对账表与结论'));
        out.push(this._stepFinalize(5, qScore, '思考题作答'));
        return out;
    }

    /* ---------------- 选中实验 ---------------- */
    select(expId) {
        this.current = this.catalog.find((e) => e.exp_id === expId) || this.catalog[0];
        this.params = {};
        for (const f of this.current.inputs) this.params[f.key] = f.default;
        /* 考核模式：教师冻结的参数集覆盖默认值 */
        if (this.exam && this.exam.params) {
            for (const [k, v] of Object.entries(this.exam.params)) {
                if (k in this.params) this.params[k] = v;
            }
        }
        this.topoState = 'initial';
        this._stepReset();
        this._selTs = Date.now();
        this._steps[1] = { startTs: Date.now() };   // 会话起点
        this._stepStart(2);                          // 参数设置计时开始
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
        /* 切走时暂停思考题计时 */
        this._stepPause(5);
        if (tab === 'principle') {
            body.innerHTML =
                `<div class="obj"><b style="color:var(--brand)">实验目的</b>　${esc(g.objective)}</div>` +
                `<pre>${esc(g.principle)}</pre>`;
        } else if (tab === 'steps') {
            body.innerHTML = '<ol>' + g.steps.map((s) =>
                `<li>${esc(s)}</li>`).join('') + '</ol>';
        } else if (tab === 'quiz') {
            this._stepStart(1);
            this._renderQuiz(body);
        } else {                       // qa 思考题（可作答）
            this._stepStart(5);
            this._renderQA(body);
        }
        document.querySelectorAll('#tabs button').forEach((b) =>
            b.classList.toggle('on', b.dataset.tab === tab));
    }

    /* ---------------- 预习测验 ---------------- */
    _renderQuiz(body) {
        const quiz = this.current.quiz || [];
        const grade = this.quizGrades[this.current.exp_id];
        if (!quiz.length) {
            body.innerHTML = '<div class="qa">本实验暂无预习测验。</div>';
            return;
        }
        let html = '<div class="quiz-tip">作答后由平台判分（计入总分 10 分）。' +
            (grade ? `最近得分：<b>${grade.score}/${grade.max}</b>` : '') + '</div>';
        quiz.forEach((q, i) => {
            html += `<div class="qq"><b>Q${i + 1}</b>　${esc(q.q)}<div class="qopts">`;
            q.options.forEach((op, j) => {
                const picked = grade && grade.detail[i].picked === j;
                const right = grade && grade.detail[i].answer === j;
                const cls = grade ? (right ? 'right' : (picked ? 'wrong' : '')) : '';
                html += `<label class="qopt ${cls}">` +
                    `<input type="radio" name="qz${i}" value="${j}"` +
                    (grade ? ' disabled' : '') + `>${esc(op)}</label>`;
            });
            html += '</div>';
            if (grade) {
                const d = grade.detail[i];
                html += `<div class="qexpl ${d.correct ? 'ok' : 'bad'}">` +
                    `${d.correct ? '✔ 正确' : '✘ 正确答案见高亮'}　${esc(d.explain)}</div>`;
            }
            html += '</div>';
        });
        html += grade
            ? `<button class="btn btn-ghost quiz-retry" id="quizRetry">重新作答</button>`
            : `<button class="btn btn-primary quiz-submit" id="quizSubmit">提交判分</button>`;
        body.innerHTML = html;
        const sub = document.getElementById('quizSubmit');
        if (sub) sub.addEventListener('click', () => this._submitQuiz());
        const retry = document.getElementById('quizRetry');
        if (retry) retry.addEventListener('click', () => {
            delete this.quizGrades[this.current.exp_id];
            this._saveArchive();
            this._renderTab('quiz');
        });
    }

    _submitQuiz() {
        const quiz = this.current.quiz || [];
        const answers = {};
        for (let i = 0; i < quiz.length; i++) {
            const sel = document.querySelector(`input[name="qz${i}"]:checked`);
            if (!sel) { this._toast('请完成所有题目再提交'); return; }
            answers[String(i)] = parseInt(sel.value, 10);
        }
        this._quizPending = answers;
        this.ws.sendCommand('experiment_quiz',
            { exp_id: this.current.exp_id, answers });
    }

    _onQuizGraded(quiz) {
        this._stepPause(1);
        this.quizGrades[this.current.exp_id] = quiz;
        this._saveArchive();
        this._renderTab('quiz');
        this._toast(`预习测验：${quiz.n_correct}/${quiz.n_total} 正确，` +
            `得 ${quiz.score} 分`);
        /* 已有结果时刷新总分 */
        const r = this.results[this.current.exp_id];
        if (r && r.verdict) this._rescore();
    }

    /* ---------------- 思考题（在线作答） ---------------- */
    _renderQA(body) {
        const g = this.current.guide;
        const ans = this.qAnswers[this.current.exp_id] || {};
        body.innerHTML = g.questions.map((q, i) =>
            `<div class="qa"><b>Q${i + 1}</b>　${esc(q)}` +
            `<textarea rows="2" placeholder="在此作答（计入总分 10 分，随报告存档）"` +
            ` data-qi="${i}">${esc(ans[String(i)] || '')}</textarea></div>`).join('') +
            `<div class="qa-note">作答自动保存，无需手动提交。</div>`;
        body.querySelectorAll('textarea').forEach((t) => {
            t.addEventListener('input', () => {
                const expId = this.current.exp_id;
                this.qAnswers[expId] = this.qAnswers[expId] || {};
                this.qAnswers[expId][t.dataset.qi] = t.value;
                this._saveArchive();
            });
        });
    }

    _gradeQuestions() {
        const n = this.current.guide.questions.length;
        const ans = this.qAnswers[this.current.exp_id] || {};
        const answered = this.current.guide.questions.filter(
            (_, i) => String(ans[String(i)] || '').trim()).length;
        const max = (this.current.score_max && this.current.score_max.questions) || 10;
        return { score: n ? Math.round(max * answered / n * 10) / 10 : 0,
                 max, answered, n_total: n };
    }

    /* ---------------- 总分合成 ---------------- */
    _rescore() {
        const r = this.results[this.current.exp_id];
        if (!r || !r.score_detail) return;
        const quiz = this.quizGrades[this.current.exp_id];
        const qg = this._gradeQuestions();
        const quizScore = quiz ? quiz.score : 0;
        const detail = r.score_detail.map((d) => ({ ...d }));
        detail.push({ item: '预习测验', max: 10, score: quizScore });
        detail.push({ item: '思考题作答', max: qg.max, score: qg.score });
        r.score_detail_full = detail;
        r.score_total = Math.round(Math.min(100,
            detail.reduce((a, d) => a + d.score, 0)) * 10) / 10;
        r.steps = this._stepsPayload(
            (r.score_detail[0] || {}).score || 0,
            (r.score_detail[1] || {}).score || 0,
            quizScore, qg.score);
        this._saveArchive();
        this._renderScorecard(r);
    }

    _renderScorecard(r) {
        const box = document.getElementById('scoreCard');
        if (!r || !r.score_detail_full) { box.innerHTML = ''; return; }
        const rows = r.score_detail_full.map((d) =>
            `<tr><td>${esc(d.item)}</td><td>${d.max}</td>` +
            `<td class="${d.score >= d.max ? 'ok' : (d.score > 0 ? '' : 'bad-t')}">` +
            `${d.score}</td></tr>`).join('');
        box.innerHTML =
            `<div class="sc-total"><span>综合得分</span>` +
            `<b>${r.score_total}</b><small>/ 100</small></div>` +
            `<table class="vt"><thead><tr><th>评分项</th><th>满分</th>` +
            `<th>得分</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    /* ---------------- 参数表单 ---------------- */
    _renderParams() {
        const box = document.getElementById('paramList');
        box.innerHTML = '';
        /* 考核模式：参数被教师冻结（exam.params 或实验默认值），滑杆禁用 */
        const locked = !!this.exam;
        /* E8 诊断型：两阶段流程说明（观测 → 提交） */
        if (this.current.exp_id === 'E8') {
            const guide = document.createElement('div');
            guide.className = 'p-guide';
            guide.innerHTML =
                '两阶段流程：① <b>观测</b>——只填「探测节点」运行，' +
                '从探测观测表找干净/劣化分界；② <b>提交</b>——填「根因链路」' +
                '与「证据链」再次运行完成诊断。';
            box.appendChild(guide);
        }
        for (const f of this.current.inputs) {
            const div = document.createElement('div');
            div.className = 'param' + (locked ? ' locked' : '');
            const val = this.params[f.key];
            if (f.type === 'str' || f.type === 'text') {
                /* 文本型输入（E8）：str 单行 / text 多行 */
                div.innerHTML =
                    `<div class="p-head"><span class="p-label">${esc(f.label)}</span></div>` +
                    (f.type === 'text'
                        ? `<textarea class="p-area" rows="3" maxlength="500"${locked ? ' disabled' : ''}>${esc(val)}</textarea>`
                        : `<input type="text" class="p-text" maxlength="64" value="${esc(val)}"${locked ? ' disabled' : ''}>`) +
                    (locked
                        ? '<div class="p-tip">🔒 考核模式：参数已锁定</div>'
                        : (f.tip ? `<div class="p-tip">${esc(f.tip)}</div>` : ''));
                const input = div.querySelector('input, textarea');
                input.addEventListener('input', () => {
                    this.params[f.key] = input.value;
                });
                box.appendChild(div);
                continue;
            }
            div.innerHTML =
                `<div class="p-head"><span class="p-label">${esc(f.label)}</span>` +
                `<span class="p-val">${val}</span>` +
                (f.unit ? `<span class="p-unit">${esc(f.unit)}</span>` : '') +
                `</div>` +
                `<input type="range" min="${f.min}" max="${f.max}" step="${f.step}" value="${val}"
                       ${locked ? 'disabled' : ''}>` +
                (locked
                    ? '<div class="p-tip">🔒 考核模式：参数已锁定</div>'
                    : (f.tip ? `<div class="p-tip">${esc(f.tip)}</div>` : ''));
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
        this._activeRun = null;
        this._stepPause(2);                       // 参数设置计时结束
        const btn = document.getElementById('runBtn');
        btn.textContent = '✕ 取消实验';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-danger');
        document.querySelectorAll('#paramList input, #paramList textarea')
            .forEach((i) => i.disabled = true);
        document.getElementById('outActions').style.display = 'none';
        this._renderProgress({ stage: 'warmup', progress: 0, note: '命令已下发' });
        /* 考核模式：注入教师指定的种子（核心侧保留键 _seed），记录绑定考试 */
        const runParams = Object.assign({}, this.params);
        this._runExamId = this.exam ? this.exam.id : '';
        if (this.exam) runParams._seed = this.exam.seed;
        /* P1 参数扫描：携带本实验 attempts 历史（发送副本，防核心侧变异） */
        const attempts = (this.attempts[this.current.exp_id] || []).map(
            (a) => Object.assign({}, a,
                { params: Object.assign({}, a.params) }));
        this.ws.sendCommand('experiment_run',
            { exp_id: this.current.exp_id, params: runParams, attempts });
    }

    cancel() {
        this.ws.sendCommand('experiment_cancel',
            this._activeRun ? { run_id: this._activeRun } : {});
    }

    _idle() {
        this.running = false;
        const btn = document.getElementById('runBtn');
        btn.textContent = '▶ 运行实验';
        btn.classList.add('btn-primary');
        btn.classList.remove('btn-danger');
        btn.disabled = !this.ws.isConnected;
        document.querySelectorAll('#paramList input, #paramList textarea')
            .forEach((i) => i.disabled = false);
    }

    /* ---------------- 实验更新帧 ---------------- */
    handleUpdate(p) {
        if (!this.current || p.exp_id !== this.current.exp_id) return;
        if (p.status === 'quiz') {               // 预习测验判分（与运行无关）
            this._onQuizGraded(p.quiz);
            return;
        }
        /* 并发保护：只处理本客户端发起的 run */
        if (p.run_id) {
            if (this._activeRun && p.run_id !== this._activeRun) return;
            this._activeRun = p.run_id;
        }
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
        } else if (p.status === 'queued') {
            // S5：并发满 → 排队中，显示排队位置（实验仍处于运行态）。
            this._renderProgress({
                stage: 'queued', progress: 0,
                note: `第 ${p.queue_pos || 1} 位，等待前序实验结束`,
            });
            this._toast(`实验室已满，已排队（第 ${p.queue_pos || 1} 位）`);
        } else if (p.status === 'done') {
            const r = Object.assign({ _ts: Date.now(),
                _examId: this._runExamId || '' }, p.result);
            this.results[p.exp_id] = r;
            /* P1 attempts 存档：本次运行摘要追加本地历史（上限 20 条） */
            if (r.attempt) {
                const list = this.attempts[p.exp_id] =
                    this.attempts[p.exp_id] || [];
                list.push(Object.assign({}, r.attempt,
                    { params: Object.assign({}, r.attempt.params) }));
                if (list.length > ATTEMPTS_MAX) {
                    this.attempts[p.exp_id] = list.slice(-ATTEMPTS_MAX);
                }
            }
            this._saveArchive();
            this._rescore();                     // 合成总分 + 步骤日志
            this._renderProgress(null);
            this._renderResult(r);
            this._idle();
            this._renderList();
            this._refreshOutActions();
            this._pushRecord(r);                 // 服务端存档（登录时）
            this._toast(r.all_pass
                ? `✔ ${p.exp_id} 对账通过 · 综合 ${r.score_total} 分`
                : `✘ ${p.exp_id} 部分判据未通过 · 综合 ${r.score_total} 分`);
        } else if (p.status === 'cancelled') {
            this._renderProgress(null);
            this._idle();
            this._stepStart(2);                  // 回到参数设置阶段
            this._toast('实验已取消');
        } else if (p.status === 'error') {
            this._renderProgress(null);
            this._idle();
            this._stepStart(2);
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
            document.getElementById('scoreCard').innerHTML = '';
            document.getElementById('histWrap').innerHTML = '';
            document.getElementById('analysisBox').style.display = 'none';
            return;
        }
        const rows = result.verdict.map((r) =>
            `<tr class="${r.pass ? 'pass-row' : 'fail-row'}">` +
            `<td>${esc(r.label)}</td>` +
            `<td>${esc(r.theory)}</td>` +
            `<td>${esc(r.measured)}${r.unit ? ' ' + esc(r.unit) : ''}</td>` +
            `<td class="${r.pass ? 'ok' : 'bad-t'}">${r.pass ? '✔ 通过' : '✘ 未通过'}</td></tr>`)
            .join('');
        /* E9 设计型：目标约束徽标条（判据表上方，达标线 vs 本次实测，
           达标绿 / 未达标红；targets/measured 由后端随结果下发） */
        let tgtHtml = '';
        if (result.targets) {
            const t = result.targets;
            const mv = result.measured || {};
            const badges = [
                { name: '端到端时延', limit: `≤ ${t.e2e_max_ms} ms`,
                  val: mv.e2e_ms != null ? mv.e2e_ms.toFixed(1) + ' ms' : '—',
                  ok: mv.e2e_ms != null && mv.e2e_ms <= t.e2e_max_ms },
                { name: '丢包率', limit: `≤ ${(t.loss_max * 100).toFixed(0)}%`,
                  val: mv.loss != null ? (mv.loss * 100).toFixed(2) + '%' : '—',
                  ok: mv.loss != null && mv.loss <= t.loss_max },
                { name: 'ISL 跳数', limit: `≤ ${t.hops_max}`,
                  val: mv.hops != null ? String(mv.hops) : '—',
                  ok: mv.hops != null && mv.hops <= t.hops_max },
            ];
            tgtHtml = '<div class="tgt-bar">' + badges.map((b) =>
                `<div class="tgt-badge ${b.ok ? 'ok' : 'bad'}">` +
                `<span class="tgt-name">${esc(b.name)}</span>` +
                `<b class="tgt-val">${esc(b.val)}</b>` +
                `<span class="tgt-limit">目标 ${esc(b.limit)}</span>` +
                `<span class="tgt-mark">${b.ok ? '✔ 达标' : '✘ 未达标'}</span>` +
                '</div>').join('') + '</div>';
        }
        /* E8 探测观测表：丢包率 ≥ 20% 判定劣化（红），否则正常（绿） */
        let obsHtml = '';
        if (Array.isArray(result.observations) && result.observations.length) {
            const obsRows = result.observations.map((o) => {
                const bad = o.loss_pct >= 20;
                return `<tr class="${bad ? 'fail-row' : 'pass-row'}">` +
                    `<td>${esc(o.node)}</td>` +
                    `<td class="${bad ? 'bad-t' : 'ok'}">${o.loss_pct}%</td>` +
                    `<td>${o.e2e_ms} ms</td>` +
                    `<td>${o.pkts_sent}</td>` +
                    `<td>${o.pkts_dropped}</td>` +
                    `<td><span class="obs-tag ${bad ? 'bad' : 'ok'}">` +
                    `${bad ? '劣化' : '正常'}</span></td></tr>`;
            }).join('');
            obsHtml =
                '<div class="obs-title">探测观测表' +
                '<small>（丢包率 ≥ 20% 判定劣化，分界即根因所在段）</small></div>' +
                '<table class="vt"><thead><tr><th>探测节点</th><th>丢包率</th>' +
                '<th>e2e 时延</th><th>发送</th><th>丢弃</th><th>状态</th>' +
                `</thead><tbody>${obsRows}</tbody></table>`;
        }
        /* E9 设计型：方案对比表（history ≥2 条时显示；末行为本次方案，
           本次行高亮，缺 e2e/跳数指标的历史行显示 —；本次得分取
           result.score——runner 组装 history 时本次尚未判分） */
        let cmpHtml = '';
        if (Array.isArray(result.history) && result.history.length >= 2) {
            const hist = result.history;
            const cmpRows = hist.map((h, i) => {
                const cur = i === hist.length - 1;
                const score = h.score != null ? h.score
                    : (cur ? result.score : null);
                const cell = (v, digits) => v != null
                    ? esc(String(digits != null
                        ? Number(v).toFixed(digits) : v)) : '—';
                return `<tr class="${cur ? 'cur-row' : ''}">` +
                    `<td>${i + 1}${cur ? '<b class="cur-mark"> · 本次</b>' : ''}</td>` +
                    `<td>${cell(h.planes)}×${cell(h.sats_per_plane)}</td>` +
                    `<td>${cell(h.src_pps)}</td>` +
                    `<td>${cell(h.e2e_ms, 1)}</td>` +
                    `<td>${cell(h.hops)}</td>` +
                    `<td>${score != null ? esc(String(score)) : '—'}</td></tr>`;
            }).join('');
            cmpHtml =
                '<div class="obs-title">方案对比' +
                '<small>（多组设计迭代对比，末行为本次方案）</small></div>' +
                '<table class="vt"><thead><tr><th>#</th><th>P×M</th>' +
                '<th>源速率 (pps)</th><th>e2e (ms)</th><th>跳数</th><th>得分</th>' +
                `</tr></thead><tbody>${cmpRows}</tbody></table>`;
        }
        wrap.innerHTML =
            tgtHtml +
            `<table class="vt"><thead><tr><th>判据</th><th>理论值</th>` +
            `<th>实测值</th><th>判定</th></tr></thead><tbody>${rows}</tbody></table>` +
            obsHtml + cmpHtml;
        concl.innerHTML =
            `<div class="concl ${result.all_pass ? '' : 'fail'}">` +
            `<b class="${result.all_pass ? 'ok' : 'bad-t'}" style="color:inherit">` +
            `${result.all_pass ? '✔ 对账通过' : '✘ 对账未通过'}</b>　` +
            `${esc(result.conclusion)}</div>`;
        this._renderScorecard(result);
        this._renderHistory(result);             // 参数-结果历史（≥2 条显示）
        this._renderAnalysis();                  // 分析结论（报告必填）
        actions.style.display = 'flex';
        this._refreshOutActions();
    }

    _refreshOutActions() {
        const r = this.current && this.results[this.current.exp_id];
        const sub = document.getElementById('submitBtn');
        if (!sub) return;
        if (!r || !r.verdict) { sub.style.display = 'none'; return; }
        sub.style.display = '';
        if (!this.user) {
            sub.disabled = true; sub.textContent = '提交报告（需登录）';
        } else if (r._submitted) {
            sub.disabled = true; sub.textContent = '✔ 已提交';
        } else if (!r._rec_id) {
            sub.disabled = true; sub.textContent = '存档中…';
        } else {
            sub.disabled = false; sub.textContent = '⇧ 提交报告';
        }
    }

    /* ---------------- 参数-结果历史 & 分析结论（P1/P3） ---------------- */
    /* 合并本地 attempts 历史与当前 result.attempt（按 ts 去重），时间升序 */
    _attemptList(result) {
        const list = (this.attempts[this.current.exp_id] || []).slice();
        const att = result && result.attempt;
        if (att && !list.some((a) => a.ts === att.ts)) {
            list.push(Object.assign({}, att));
        }
        list.sort((a, b) => String(a.ts || '').localeCompare(String(b.ts || '')));
        return list;
    }

    /* 历史行「参数」格：优先 sweep_key 取值；无扫描键（E8）回退文本参数摘要 */
    _attParamCell(att) {
        const inputs = this.current.inputs || [];
        const key = SWEEP_KEYS[this.current.exp_id];
        if (key) {
            const f = inputs.find((i) => i.key === key);
            const v = (att.params || {})[key];
            if (f && v != null) {
                return `${esc(f.label)} = ${esc(String(v))}` +
                    (f.unit ? ` ${esc(f.unit)}` : '');
            }
        }
        const parts = [];
        for (const [k, v] of Object.entries(att.params || {})) {
            const s = String(v == null ? '' : v);
            if (s.length > 40) continue;          // 证据链等长文本不入表
            const f = inputs.find((i) => i.key === k);
            parts.push(`${f ? esc(f.label) : esc(k)}=${esc(s || '（未填）')}`);
        }
        return parts.join('，') || '—';
    }

    /* 历史行「关键指标」格：attempt.metrics 前 3 个数值判据 */
    _attMetricsCell(att) {
        return Object.entries(att.metrics || {}).map(
            ([k, v]) => `${esc(k)} ${esc(String(v))}`).join('；') || '—';
    }

    _renderHistory(result) {
        const box = document.getElementById('histWrap');
        if (!box) return;
        const list = this._attemptList(result);
        if (list.length < 2) { box.innerHTML = ''; return; }
        const rows = list.map((a, i) =>
            `<tr class="${a.all_pass ? 'pass-row' : 'fail-row'}">` +
            `<td>${i + 1}</td>` +
            `<td>${esc(String(a.ts || '').replace('T', ' ').slice(5, 19))}</td>` +
            `<td>${this._attParamCell(a)}</td>` +
            `<td>${this._attMetricsCell(a)}</td>` +
            `<td>${a.score != null ? a.score : '—'}</td>` +
            `<td class="${a.all_pass ? 'ok' : 'bad-t'}">` +
            `${a.all_pass ? '✔ 通过' : '✘ 未通过'}</td></tr>`).join('');
        box.innerHTML =
            `<div class="hist-title">参数-结果历史` +
            `<small>（共 ${list.length} 次尝试 · 参数扫描满分需 ≥4 组取值）` +
            `</small></div>` +
            '<table class="vt"><thead><tr><th>#</th><th>时间</th>' +
            '<th>参数</th><th>关键指标</th><th>得分</th><th>判定</th></tr>' +
            `</thead><tbody>${rows}</tbody></table>`;
    }

    _renderAnalysis() {
        const box = document.getElementById('analysisBox');
        const ta = document.getElementById('anaText');
        if (!box || !ta) return;
        const r = this.current && this.results[this.current.exp_id];
        if (!r || !r.verdict) { box.style.display = 'none'; return; }
        box.style.display = '';
        ta.value = this.analysis[this.current.exp_id] || '';
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
        /* P3 分析结论必填：空则阻止下载 */
        const analysis = String(this.analysis[this.current.exp_id] || '').trim();
        if (!analysis) {
            this._toast('请先在「分析结论」中填写结论，再下载报告');
            return;
        }
        this._stepPause(4);
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
        /* P1/P3 报告增强：参数-结果对比（本实验 attempts 全历史） */
        const attList = this._attemptList(r);
        const attRows = attList.map((a, i) =>
            `<tr class="${a.all_pass ? '' : 'bad'}">` +
            `<td>${i + 1}</td>` +
            `<td>${esc(String(a.ts || '').replace('T', ' '))}</td>` +
            `<td>${this._attParamCell(a)}</td>` +
            `<td>${this._attMetricsCell(a)}</td>` +
            `<td>${a.score != null ? a.score : '—'}</td>` +
            `<td class="${a.all_pass ? 'ok' : 'bad'}">` +
            `${a.all_pass ? '通过' : '未通过'}</td></tr>`)
            .join('');
        /* 评分明细 */
        const scRows = (r.score_detail_full || r.score_detail || []).map((d) =>
            `<tr><td>${esc(d.item)}</td><td>${d.max}</td><td>${d.score}</td></tr>`)
            .join('');
        /* 步骤日志（ilab-x 字段） */
        const stepRows = (r.steps || []).map((s) =>
            `<tr><td>${s.seq}</td><td>${esc(s.title)}</td>` +
            `<td>${fmtTime(s.startTime)}</td><td>${fmtTime(s.endTime)}</td>` +
            `<td>${s.timeUsed} s</td><td>${s.maxScore}</td><td>${s.score}</td>` +
            `<td>${esc(s.scoringModel)}</td></tr>`).join('');
        /* 思考题作答 */
        const ans = this.qAnswers[this.current.exp_id] || {};
        const qaRows = this.current.guide.questions.map((q, i) =>
            `<tr><td>${esc(q)}</td><td>${esc(String(ans[String(i)] || '（未作答）'))}</td></tr>`)
            .join('');
        const quiz = this.quizGrades[this.current.exp_id];
        const student = this.user
            ? `${esc(this.user.name)} / ${esc(this.user.student_id)}` : '';

        const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>星桥实验报告 ${esc(r.exp_id)} ${esc(r.name)}</title><style>
body{font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#1a2230;
max-width:860px;margin:32px auto;padding:0 20px;line-height:1.65}
h1{font-size:21px;border-bottom:3px solid #0e7490;padding-bottom:8px}
.meta{color:#5a6a7e;font-size:13px;margin-bottom:18px}
.stamp{display:inline-block;padding:4px 18px;border-radius:999px;font-weight:700;margin-left:10px}
.stamp.ok{background:#d9f3e6;color:#0a7a4a}.stamp.bad{background:#fde2e2;color:#b42318}
.score-total{font-size:15px;font-weight:700;color:#0e7490;margin:8px 0}
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
<div class="meta">实验编号 ${esc(r.exp_id)} · ${esc(r.name)} · 生成时间 ${ts}
${student ? ` · 学生 ${student}` : ''}</div>
${r.summary ? `<p>${esc(r.summary)}</p>` : ''}
${r.theory_note ? `<div class="theory"><b>理论判据：</b>${esc(r.theory_note)}</div>` : ''}
${r.score_total != null ? `<div class="score-total">综合得分：${r.score_total} / 100</div>` : ''}
${scRows ? `<h3>评分明细</h3><table><thead><tr><th>评分项</th><th>满分</th><th>得分</th></tr></thead><tbody>${scRows}</tbody></table>` : ''}
<h3>本组实验参数</h3><table><tbody>${paramRows}</tbody></table>
${theoryRows ? `<h3>理论预览（本组参数）</h3><table><tbody>${theoryRows}</tbody></table>` : ''}
<h3>理论-实测对账</h3>
<table><thead><tr><th>判据</th><th>理论值</th><th>实测值</th><th>判定</th></tr></thead>
<tbody>${vRows}</tbody></table>
<div class="concl"><b>结论：</b>${esc(r.conclusion)}</div>
${attRows ? `<h3>参数-结果对比（本实验全部 ${attList.length} 次尝试）</h3>
<table><thead><tr><th>#</th><th>时间</th><th>参数</th><th>关键指标</th><th>得分</th><th>判定</th></tr></thead>
<tbody>${attRows}</tbody></table>` : ''}
<h3>学生分析结论</h3>
<div class="concl">${esc(analysis).replace(/\n/g, '<br>')}</div>
${quiz ? `<h3>预习测验（${quiz.n_correct}/${quiz.n_total} 正确，${quiz.score} 分）</h3>` : ''}
${stepRows ? `<h3>操作步骤日志</h3><table><thead><tr><th>步骤</th><th>名称</th><th>开始</th><th>结束</th><th>用时</th><th>满分</th><th>得分</th><th>考察点</th></tr></thead><tbody>${stepRows}</tbody></table>` : ''}
${qaRows ? `<h3>思考题作答</h3><table><thead><tr><th>题目</th><th>作答</th></tr></thead><tbody>${qaRows}</tbody></table>` : ''}
<div class="student">姓名 <span>${this.user ? esc(this.user.name) : ''}</span>
学号 <span>${this.user ? esc(this.user.student_id) : ''}</span>
班级 <span></span> 日期 <span>${ts.slice(0, 10)}</span></div>
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
    lab._renderLogin();
    if (lab.user) { lab._pullRecords(); lab._pullExams(); }

    document.querySelectorAll('#tabs button').forEach((b) =>
        b.addEventListener('click', () => lab._renderTab(b.dataset.tab)));
    document.getElementById('runBtn').addEventListener('click', () =>
        lab.running ? lab.cancel() : lab.run());
    document.getElementById('resetBtn').addEventListener('click', () => {
        if (lab.running || lab.exam) return;
        lab.select(lab.current.exp_id);
        lab._toast('参数已重置为默认值');
    });
    document.getElementById('reportBtn').addEventListener('click', () =>
        lab.downloadReport());
    /* 分析结论自动保存（按实验，报告下载必填） */
    document.getElementById('anaText').addEventListener('input', () => {
        if (!lab.current) return;
        lab.analysis[lab.current.exp_id] =
            document.getElementById('anaText').value;
        lab._saveArchive();
    });
    document.getElementById('submitBtn').addEventListener('click', () =>
        lab.submitReport());
    document.getElementById('rerunBtn').addEventListener('click', () => {
        if (!lab.running) lab.run();
    });
    document.getElementById('loginBtn').addEventListener('click', () =>
        lab.login());
    document.getElementById('logoutBtn').addEventListener('click', () =>
        lab.logout());
    document.getElementById('examEnterBtn').addEventListener('click', () => {
        const active = lab.exams.filter((e) => e.active);
        if (active.length) lab.startExam(active[0]);
    });
    document.getElementById('examExitBtn').addEventListener('click', () =>
        lab.endExam(false));
})();
