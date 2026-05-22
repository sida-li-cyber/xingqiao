/**
 * WebSocket Communication Module
 * Handles real-time communication with the backend server
 */

class WebSocketManager {
    constructor(config = {}) {
        this.host = config.host || 'localhost';
        this.port = config.port || 8000;
        this.path = config.path || '/ws/client';
        this.ws = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.messageQueue = [];

        this.callbacks = {
            onConnect: config.onConnect || (() => {}),
            onDisconnect: config.onDisconnect || (() => {}),
            onStateUpdate: config.onStateUpdate || (() => {}),
            onSimulationInit: config.onSimulationInit || (() => {}),
            onAck: config.onAck || (() => {}),
            onError: config.onError || (() => {}),
            onMessage: config.onMessage || (() => {}),
        };

        this.lastMessageTime = 0;
    }

    /**
     * Connect to WebSocket server
     */
    connect() {
        try {
            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            const url = `${protocol}://${this.host}:${this.port}${this.path}`;
            console.log(`[WebSocket] Connecting to ${url}`);

            this.ws = new WebSocket(url);

            this.ws.onopen = () => this.handleOpen();
            this.ws.onmessage = (event) => this.handleMessage(event);
            this.ws.onerror = (error) => this.handleError(error);
            this.ws.onclose = () => this.handleClose();

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
        console.log('[WebSocket] Connected to server');
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
    handleClose() {
        console.log('[WebSocket] Disconnected from server');
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
        }
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

    sendResetCommand() {
        this.sendCommand('reset');
    }

    sendSpeedCommand(speed) {
        this.sendCommand('speed', { multiplier: speed });
    }

    sendMetricsCommand(metricsType) {
        this.sendCommand('metrics', { type: metricsType });
    }

    sendFilterCommand(satellites, stations) {
        this.sendCommand('filter', {
            satellites: satellites,
            stations: stations,
        });
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
