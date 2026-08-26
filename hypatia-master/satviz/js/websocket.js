/**
 * WebSocket Communication Module
 * Handles real-time communication with the backend server
 */

class WebSocketManager {
    constructor(config = {}) {
        // 默认地址来自 SBConfig（config.js，支持 ?ws=host:port 覆盖）
        this.host = config.host || (window.SBConfig ? window.SBConfig.host : 'localhost');
        this.port = config.port || (window.SBConfig ? window.SBConfig.port : 8000);
        this.path = config.path || '/ws/client';
        this.ws = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.messageQueue = [];
        this.maxQueueSize = 50; // 断线期间指令队列 FIFO 上限，防止无限增长

        this.callbacks = {
            onConnect: config.onConnect || (() => {}),
            onDisconnect: config.onDisconnect || (() => {}),
            // 自动重连耗尽后触发，由 UI 层提示手动重连
            onReconnectFailed: config.onReconnectFailed || (() => {}),
            onStateUpdate: config.onStateUpdate || (() => {}),
            onSimulationInit: config.onSimulationInit || (() => {}),
            onAck: config.onAck || (() => {}),
            onError: config.onError || (() => {}),
            onMessage: config.onMessage || (() => {}),
            onExperimentUpdate: config.onExperimentUpdate || (() => {}),
        };

        this.lastMessageTime = 0;
    }

    /**
     * Connect to WebSocket server
     */
    connect() {
        try {
            // 协议优先取 SBConfig（与页面 http/https 一致），其次回退自动判断
            const protocol = (window.SBConfig && window.SBConfig.wsProtocol) ||
                (window.location.protocol === 'https:' ? 'wss' : 'ws');
            const url = `${protocol}://${this.host}:${this.port}${this.path}`;
            console.log(`[WebSocket] Connecting to ${url}`);

            this.ws = new WebSocket(url);

            this.ws.onopen = () => this.handleOpen();
            this.ws.onmessage = (event) => this.handleMessage(event);
            this.ws.onerror = (error) => this.handleError(error);
            this.ws.onclose = (event) => this.handleClose(event);

        } catch (error) {
            console.error('[WebSocket] Connection failed:', error);
            this.callbacks.onError(error);
            this.scheduleReconnect();
        }
    }

    /**
     * Handle connection opened
     */
    handleOpen() {
        this._openTime = Date.now();
        console.log('[WebSocket] Connected to server at', new Date().toISOString());
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.callbacks.onConnect();

        // Flush queued messages
        while (this.messageQueue.length > 0) {
            const message = this.messageQueue.shift();
            this.send(message);
        }
    }

    /**
     * Handle incoming messages
     */
    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);
            this.lastMessageTime = Date.now();

            const msgType = data.message_type;

            // Route to specific handlers
            switch (msgType) {
                case 'state_update':
                    this.callbacks.onStateUpdate(data.payload);
                    break;

                case 'simulation_init':
                    this.callbacks.onSimulationInit(data.payload);
                    break;

                case 'ack':
                    this.callbacks.onAck(data.payload);
                    break;

                case 'experiment_update':
                    this.callbacks.onExperimentUpdate(data.payload);
                    break;

                case 'error':
                    console.warn('[WebSocket] Server error:', data.payload);
                    this.callbacks.onError(data.payload);
                    break;

                default:
                    console.log('[WebSocket] Unknown message type:', msgType);
                    break;
            }

            this.callbacks.onMessage(data);

        } catch (error) {
            console.error('[WebSocket] Message parse error:', error);
        }
    }

    /**
     * Handle WebSocket errors
     */
    handleError(error) {
        console.error('[WebSocket] Error:', error);
    }

    /**
     * Handle connection closed
     */
    handleClose(event) {
        const aliveMs = this._openTime ? (Date.now() - this._openTime) : -1;
        const aliveSec = aliveMs >= 0 ? (aliveMs / 1000).toFixed(1) + 's' : 'unknown';
        console.log(`[WebSocket] Disconnected — code=${event.code} reason="${event.reason}" wasClean=${event.wasClean} alive=${aliveSec}`);
        if (event.code === 1006) {
            console.warn('[WebSocket] CODE 1006 = Abnormal closure (TCP-level disconnect). Alive: ' + aliveSec);
        }
        this.isConnected = false;
        this.callbacks.onDisconnect();
        this.scheduleReconnect();
    }

    /**
     * Schedule reconnection attempt with exponential backoff
     */
    scheduleReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = Math.min(
                this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1),
                30000
            );
            console.log(
                `[WebSocket] Reconnecting in ${delay}ms... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`
            );

            setTimeout(() => this.connect(), delay);
        } else {
            console.error('[WebSocket] Max reconnection attempts reached');
            this.callbacks.onReconnectFailed();
        }
    }

    /**
     * 手动重连：自动重连耗尽后由 UI 遮罩上的按钮触发
     */
    manualReconnect() {
        this.reconnectAttempts = 0;
        if (this.ws) {
            try { this.ws.close(); } catch (e) { /* 忽略关闭异常 */ }
        }
        this.connect();
    }

    /**
     * Send a message to server
     */
    send(message) {
        if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
            try {
                this.ws.send(JSON.stringify(message));
            } catch (error) {
                console.error('[WebSocket] Send error:', error);
            }
        } else {
            if (this.messageQueue.length >= this.maxQueueSize) {
                // 队列满时丢弃最旧的（过期）指令，保留最新操作意图
                this.messageQueue.shift();
                console.warn('[WebSocket] Message queue full, dropped oldest');
            }
            console.warn('[WebSocket] Not connected, queuing message');
            this.messageQueue.push(message);
        }
    }

    /**
     * Send a command to the backend
     */
    sendCommand(action, params = null) {
        const message = {
            message_type: 'command',
            payload: {
                action: action,
                params: params,
            },
        };
        this.send(message);
    }

    sendPlayCommand() {
        this.sendCommand('play');
    }

    sendPauseCommand() {
        this.sendCommand('pause');
    }

    sendStopCommand() {
        this.sendCommand('stop');
    }

    sendSpeedCommand(speed) {
        this.sendCommand('speed', { multiplier: speed });
    }

    sendTimelineCommand(timestamp) {
        this.sendCommand('timeline', { timestamp: timestamp });
    }

    /**
     * Disconnect WebSocket
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.isConnected = false;
        }
    }

    /**
     * Get connection status
     */
    getStatus() {
        return {
            isConnected: this.isConnected,
            readyState: this.ws ? this.ws.readyState : null,
            queueSize: this.messageQueue.length,
            reconnectAttempts: this.reconnectAttempts,
        };
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WebSocketManager;
}
