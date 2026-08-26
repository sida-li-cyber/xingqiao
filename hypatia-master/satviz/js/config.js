/**
 * 全局运行配置：WebSocket / HTTP API 地址的统一来源。
 *
 * 支持 URL 参数覆盖，便于部署到非本机环境或跨端口调试：
 *   ?ws=host:port   —— 同时指定 WebSocket 与 API 的主机和端口
 *   ?ws=host        —— 只指定主机，端口沿用默认值
 *
 * 必须在 websocket.js 之前加载。
 */
(function () {
    'use strict';

    const DEFAULT_PORT = 8000;

    function resolve() {
        const params = new URLSearchParams(window.location.search);
        const override = (params.get('ws') || '').trim();

        let host = '';
        let port = DEFAULT_PORT;

        if (override) {
            const idx = override.lastIndexOf(':');
            if (idx > 0 && !Number.isNaN(Number(override.slice(idx + 1)))) {
                host = override.slice(0, idx);
                port = Number(override.slice(idx + 1));
            } else {
                host = override;
            }
        } else {
            // file:// 协议或 localhost 访问时回退到 127.0.0.1
            host = (!window.location.hostname || window.location.hostname === 'localhost')
                ? '127.0.0.1' : window.location.hostname;
        }

        const secure = window.location.protocol === 'https:';
        return {
            host,
            port,
            wsProtocol: secure ? 'wss' : 'ws',
            apiBase: `${secure ? 'https' : 'http'}://${host}:${port}`,
            wsUrl(path) {
                return `${this.wsProtocol}://${this.host}:${this.port}${path}`;
            },
        };
    }

    // 挂载到 window：本项目 JS 模块以普通 <script> 顺序加载，无打包器
    window.SBConfig = resolve();
})();
