/**
 * Example Backend Data Adapter for Hypatia Integration
 * This file demonstrates how to collect simulation data from Hypatia
 * and push it to the frontend via WebSocket
 * 
 * 注意: 这是一个示例，需要根据实际的hypatia数据源进行调整
 */

class HypatiaDataAdapter {
    /**
     * Initialize the adapter with hypatia data sources
     */
    constructor(simulationData) {
        this.simulationData = simulationData;
        this.currentTimestamp = 0;
        this.satellites = new Map();
        this.groundStations = new Map();
        this.links = new Map();
    }

    /**
     * Extract satellite positions at given timestamp
     */
    getSatellitePositions(timestamp) {
        const positions = {};
        
        // Example: assuming simulationData contains position tracking
        // Replace with actual hypatia data access pattern
        if (this.simulationData.satellite_positions) {
            for (const [satId, trajectories] of Object.entries(this.simulationData.satellite_positions)) {
                const position = this.interpolatePosition(trajectories, timestamp);
                if (position) {
                    positions[satId] = {
                        lat: position.latitude,
                        lon: position.longitude,
                        alt: position.altitude,
                    };
                }
            }
        }
        
        return positions;
    }

    /**
     * Extract link status information
     */
    getLinkStatus(timestamp) {
        const linkStatus = {};
        
        // Example: extract ISL (inter-satellite link) and GSL (ground-satellite link) status
        if (this.simulationData.links) {
            for (const [linkId, linkData] of Object.entries(this.simulationData.links)) {
                const status = this.extractLinkStatus(linkData, timestamp);
                if (status) {
                    linkStatus[linkId] = {
                        is_active: status.is_active,
                        bandwidth_utilization: status.bandwidth_utilization || 0,
                        latency: status.latency || 0,
                        loss_rate: status.loss_rate || 0,
                    };
                }
            }
        }
        
        return linkStatus;
    }

    /**
     * Extract routing path information
     */
    getRouting(timestamp) {
        // Example: extract active routes at given timestamp
        const routing = {};
        
        if (this.simulationData.routing) {
            routing.path = this.simulationData.routing[timestamp] || [];
        }
        
        return routing;
    }

    /**
     * Extract bandwidth utilization metrics
     */
    getBandwidthMetrics(timestamp) {
        const metrics = {};
        
        // Example: extract per-link bandwidth utilization
        if (this.simulationData.bandwidth_utilization) {
            for (const [linkId, values] of Object.entries(this.simulationData.bandwidth_utilization)) {
                metrics[linkId] = values[timestamp] || 0;
            }
        }
        
        return metrics;
    }

    /**
     * Get complete state update at given timestamp
     */
    getStateUpdate(timestamp) {
        return {
            satellite_positions: this.getSatellitePositions(timestamp),
            link_status: this.getLinkStatus(timestamp),
            routing: this.getRouting(timestamp),
            bandwidth_utilization: this.getBandwidthMetrics(timestamp),
            timestamp: new Date(timestamp * 1000).toISOString(),
        };
    }

    /**
     * Helper: Interpolate position between two time points
     */
    interpolatePosition(trajectories, timestamp) {
        // Find the two nearest time points
        const times = Object.keys(trajectories).map(Number).sort((a, b) => a - b);
        
        let idx = times.findIndex(t => t >= timestamp);
        if (idx === -1) idx = times.length - 1;
        if (idx === 0) idx = 1;
        
        const t1 = times[idx - 1];
        const t2 = times[idx];
        const pos1 = trajectories[t1];
        const pos2 = trajectories[t2];
        
        if (!pos1 || !pos2) return null;
        
        // Linear interpolation
        const ratio = (timestamp - t1) / (t2 - t1);
        return {
            latitude: pos1.latitude + (pos2.latitude - pos1.latitude) * ratio,
            longitude: pos1.longitude + (pos2.longitude - pos1.longitude) * ratio,
            altitude: pos1.altitude + (pos2.altitude - pos1.altitude) * ratio,
        };
    }

    /**
     * Helper: Extract link status at given timestamp
     */
    extractLinkStatus(linkData, timestamp) {
        // Check if link is active at this timestamp
        const isActive = linkData.active_windows
            ? linkData.active_windows.some(window => 
                timestamp >= window.start && timestamp <= window.end
              )
            : false;
        
        // Get metrics at this timestamp
        const metrics = linkData.metrics || {};
        
        return {
            is_active: isActive,
            bandwidth_utilization: metrics.bandwidth_utilization || 0,
            latency: metrics.latency || 0,
            loss_rate: metrics.loss_rate || 0,
        };
    }
}

/**
 * Example WebSocket server integration code (Python/FastAPI)
 * 
 * from realtime_backend.core import manager
 * from realtime_backend.schemas import StateUpdate, StatePayload
 * import asyncio
 * 
 * class HypatiaSimulator:
 *     def __init__(self):
 *         # Load hypatia simulation data
 *         self.adapter = HypatiaDataAdapter(simulation_data)
 *         self.current_time = 0
 *         self.is_playing = False
 *         self.speed = 1.0
 *         self.duration = 1000  # simulation duration in seconds
 * 
 *     async def broadcast_state(self):
 *         while True:
 *             if self.is_playing:
 *                 # Get current state from hypatia data
 *                 state_payload = self.adapter.getStateUpdate(self.current_time)
 *                 
 *                 # Create StateUpdate message
 *                 message = StateUpdate(
 *                     message_type="state_update",
 *                     payload=StatePayload(**state_payload)
 *                 )
 *                 
 *                 # Broadcast to all clients
 *                 await manager.broadcast_state(message.dict())
 *                 
 *                 # Advance time
 *                 self.current_time += 0.1 * self.speed
 *                 
 *                 # Check if simulation ended
 *                 if self.current_time >= self.duration:
 *                     self.is_playing = False
 *             
 *             # Sleep to control update frequency (e.g., 10 updates per second)
 *             await asyncio.sleep(0.1)
 * 
 *     async def handle_client_command(self, command_payload):
 *         action = command_payload.action
 *         params = command_payload.params or {}
 * 
 *         if action == "play":
 *             self.is_playing = True
 *         elif action == "pause":
 *             self.is_playing = False
 *         elif action == "stop":
 *             self.is_playing = False
 *             self.current_time = 0
 *         elif action == "reset":
 *             self.current_time = 0
 *             self.is_playing = False
 *         elif action == "speed":
 *             self.speed = params.get("multiplier", 1.0)
 *         elif action == "timeline":
 *             self.current_time = params.get("timestamp", 0)
 *         elif action == "metrics":
 *             # Handle metrics change
 *             metrics_type = params.get("type", "none")
 *             # Update visualization mode
 *         elif action == "filter":
 *             # Handle filtering
 *             satellites = params.get("satellites", [])
 *             stations = params.get("stations", [])
 *             # Update visibility filters
 */

// Example data structure expected from Hypatia
const HYPATIA_DATA_EXAMPLE = {
    // Satellite position trajectories by timestamp
    satellite_positions: {
        'Sat-1': {
            0: { latitude: 45.0, longitude: 120.0, altitude: 400000 },
            10: { latitude: 45.5, longitude: 121.0, altitude: 400000 },
            20: { latitude: 46.0, longitude: 122.0, altitude: 400000 },
        },
        'Sat-2': {
            0: { latitude: 46.0, longitude: 121.0, altitude: 400000 },
            10: { latitude: 46.5, longitude: 122.0, altitude: 400000 },
            20: { latitude: 47.0, longitude: 123.0, altitude: 400000 },
        },
    },
    
    // Ground station positions (static)
    ground_stations: {
        'Ground-1': { latitude: 37.7749, longitude: -122.4194, altitude: 0 },
        'Ground-2': { latitude: 51.5074, longitude: -0.1278, altitude: 0 },
    },
    
    // Link status information
    links: {
        'Sat-1-Sat-2': {
            active_windows: [
                { start: 0, end: 50 },
                { start: 100, end: 150 },
            ],
            metrics: {
                bandwidth_utilization: 0.65,
                latency: 15.2,
                loss_rate: 0.001,
            },
        },
        'Sat-1-Ground-1': {
            active_windows: [
                { start: 5, end: 35 },
                { start: 55, end: 85 },
            ],
            metrics: {
                bandwidth_utilization: 0.45,
                latency: 125.5,
                loss_rate: 0.002,
            },
        },
    },
    
    // Bandwidth utilization over time
    bandwidth_utilization: {
        'Sat-1-Sat-2': {
            0: 0.65, 5: 0.68, 10: 0.70, 15: 0.72, 20: 0.75,
        },
        'Sat-1-Ground-1': {
            0: 0.45, 5: 0.48, 10: 0.50, 15: 0.52, 20: 0.55,
        },
    },
    
    // Routing information
    routing: {
        0: ['Sat-1', 'Sat-2', 'Ground-1'],
        10: ['Sat-1', 'Sat-2', 'Sat-3', 'Ground-1'],
        20: ['Sat-1', 'Sat-3', 'Ground-1'],
    },
};

// Export adapter for server-side use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { HypatiaDataAdapter, HYPATIA_DATA_EXAMPLE };
}
