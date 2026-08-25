/**
 * 教学实验台入口（P0 起统一为独立页面 lab.html）。
 *
 * 演示模式（index.html）顶栏的「教学实验台」按钮跳转到实验台页面；
 * 实验台本身（参数表单 / 理论预览 / 对账 / 拓扑 / 报告）见 js/lab.js。
 * 旧版弹窗已移除，本模块仅保留入口与兼容桩。
 */

class ExperimentLab {
    constructor(wsManager) {
        this.ws = wsManager;
        this.catalog = [];
    }

    /** 在顶栏渲染入口按钮（app 初始化时调用一次）。 */
    init() {
        if (document.getElementById('expBtn')) return;
        const btn = document.createElement('button');
        btn.id = 'expBtn';
        btn.className = 'glass';
        btn.title = '进入教学实验台（E1–E4 参数化实验与报告）';
        btn.textContent = '🎓 教学实验台';
        btn.addEventListener('click', () => {
            window.location.href = 'lab.html';
        });
        const anchor = document.getElementById('connChip');
        if (anchor) anchor.before(btn);
        else document.querySelector('header')?.appendChild(btn);
    }

    /* 兼容桩：目录与更新帧由 lab.html 处理，演示页仅保留接口形状 */
    setCatalog(list) { this.catalog = Array.isArray(list) ? list : []; }
    handleUpdate() {}
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = ExperimentLab;
}
