/**
 * TimeSeriesChart — lightweight dependency-free canvas line chart.
 * Used by the v3 "时序指标" panel to plot throughput / latency / loss
 * against simulation time. Auto-scaling Y axis, rolling point buffer.
 */

class TimeSeriesChart {
    /**
     * @param {string} canvasId
     * @param {object} opts - {color, maxPoints, unit, label, decimals}
     */
    constructor(canvasId, opts = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
        this.color = opts.color || '#38bdf8';
        this.maxPoints = opts.maxPoints || 180;
        this.unit = opts.unit || '';
        this.decimals = opts.decimals != null ? opts.decimals : 1;
        this.data = []; // {t, v}
        this._lastDraw = 0;
    }

    push(t, v) {
        const last = this.data[this.data.length - 1];
        // Detect simulation restart / seek-back → reset the buffer.
        if (last && t < last.t - 1) this.data.length = 0;
        this.data.push({ t, v });
        if (this.data.length > this.maxPoints) this.data.shift();
    }

    clear() {
        this.data.length = 0;
        this.draw();
    }

    _niceMax(m) {
        if (m <= 0) return 1;
        const pow = Math.pow(10, Math.floor(Math.log10(m)));
        const n = m / pow;
        let step;
        if (n <= 1) step = 1;
        else if (n <= 2) step = 2;
        else if (n <= 5) step = 5;
        else step = 10;
        return step * pow;
    }

    _fmt(v) {
        if (v >= 1000) return (v / 1000).toFixed(1) + 'k';
        return v.toFixed(this.decimals);
    }

    /** Draw at most ~10fps to keep the render cost negligible. */
    draw(force) {
        if (!this.ctx) return;
        const now = performance.now();
        if (!force && now - this._lastDraw < 100) return;
        this._lastDraw = now;

        const c = this.canvas;
        const dpr = window.devicePixelRatio || 1;
        const w = c.clientWidth, h = c.clientHeight;
        if (w === 0 || h === 0) return;
        if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
            c.width = Math.round(w * dpr);
            c.height = Math.round(h * dpr);
        }
        const ctx = this.ctx;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);

        const padL = 4, padR = 4, padT = 6, padB = 6;
        const iw = w - padL - padR, ih = h - padT - padB;

        // Baseline grid line
        ctx.strokeStyle = 'rgba(255,255,255,0.10)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, padT + ih);
        ctx.lineTo(padL + iw, padT + ih);
        ctx.stroke();

        if (this.data.length < 2) {
            ctx.fillStyle = 'rgba(255,255,255,0.3)';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('等待数据…', w / 2, h / 2 + 3);
            return;
        }

        const t0 = this.data[0].t;
        const t1 = this.data[this.data.length - 1].t;
        const span = Math.max(1, t1 - t0);
        let maxV = 0;
        for (const p of this.data) if (p.v > maxV) maxV = p.v;
        const yMax = this._niceMax(maxV * 1.15);

        // Max-value label (top-right)
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(this._fmt(yMax) + (this.unit ? ' ' + this.unit : ''), w - padR, padT + 8);

        const X = (t) => padL + ((t - t0) / span) * iw;
        const Y = (v) => padT + ih - (v / yMax) * ih;

        // Area fill
        ctx.beginPath();
        ctx.moveTo(X(this.data[0].t), Y(this.data[0].v));
        for (let i = 1; i < this.data.length; i++) {
            ctx.lineTo(X(this.data[i].t), Y(this.data[i].v));
        }
        ctx.strokeStyle = this.color;
        ctx.lineWidth = 1.5;
        ctx.lineJoin = 'round';
        ctx.stroke();

        ctx.lineTo(X(t1), padT + ih);
        ctx.lineTo(X(t0), padT + ih);
        ctx.closePath();
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = this.color;
        ctx.fill();
        ctx.globalAlpha = 1;

        // Current-value dot
        const lp = this.data[this.data.length - 1];
        ctx.beginPath();
        ctx.arc(X(lp.t), Y(lp.v), 2.2, 0, Math.PI * 2);
        ctx.fillStyle = this.color;
        ctx.fill();
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = TimeSeriesChart;
}
