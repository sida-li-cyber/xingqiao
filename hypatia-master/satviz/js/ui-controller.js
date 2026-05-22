/**
 * UI Controller Module
 * Handles user interface interactions
 */

class UIController {
    constructor(cesiumManager, websocketManager, app) {
        this.cesium = cesiumManager;
        this.ws = websocketManager;
        this.app = app;
        this.isPlaying = false;
        this.currentMode = 'realtime';
        this.selectedMetrics = 'none';
        this.selectedSatellites = new Set();
        this.selectedStations = new Set();
    }

    /**
     * Initialize UI event listeners
     */
    initializeUI() {
        // Mode buttons
        document.getElementById('modeRealtime').addEventListener('click', () => this.switchMode('realtime'));
        document.getElementById('modeOffline').addEventListener('click', () => this.switchMode('offline'));

        // Playback controls
        document.getElementById('playPauseBtn').addEventListener('click', () => this.togglePlayPause());
        document.getElementById('stopBtn').addEventListener('click', () => this.stopPlayback());
        document.getElementById('resetBtn').addEventListener('click', () => this.resetSimulation());

        // Speed control
        const speedSlider = document.getElementById('speedSlider');
        speedSlider.addEventListener('input', (e) => this.setSpeed(parseFloat(e.target.value)));

        // Timeline control
        const timelineSlider = document.getElementById('timelineSlider');
        timelineSlider.addEventListener('input', (e) => this.onTimelineSeek(parseFloat(e.target.value)));
        document.getElementById('timelineInput').addEventListener('change', (e) => this.jumpToTime(parseFloat(e.target.value)));

        // Metrics selection
        document.getElementById('metricsSelect').addEventListener('change', (e) => this.selectMetrics(e.target.value));

        // Satellite filter
        document.getElementById('satelliteSearch').addEventListener('input', (e) => this.searchSatellites(e.target.value));
        document.getElementById('selectAllSatellites').addEventListener('click', () => this.selectAllSatellites());
        document.getElementById('deselectAllSatellites').addEventListener('click', () => this.deselectAllSatellites());

        // Station filter
        document.getElementById('stationSearch').addEventListener('input', (e) => this.searchStations(e.target.value));
        document.getElementById('selectAllStations').addEventListener('click', () => this.selectAllStations());
        document.getElementById('deselectAllStations').addEventListener('click', () => this.deselectAllStations());

        // Offline mode controls
        document.getElementById('saveCesiumToken').addEventListener('click', () => this.saveCesiumToken());
        document.getElementById('loadOfflineData').addEventListener('click', () => this.loadOfflineData());
        document.getElementById('loadCzmlFromLocal').addEventListener('click', () => this.loadCzmlFromLocal());

        console.log('[UIController] UI initialized');
    }

    /**
     * Switch between realtime and offline mode
     */
    switchMode(mode) {
        this.currentMode = mode;
        const realtimeBtn = document.getElementById('modeRealtime');
        const offlineBtn = document.getElementById('modeOffline');
        const realtimeControls = document.getElementById('realtimeControls');
        const offlineControls = document.getElementById('offlineControls');

        if (mode === 'realtime') {
            realtimeBtn.classList.add('active');
            offlineBtn.classList.remove('active');
            realtimeControls.style.display = 'block';
            offlineControls.style.display = 'none';
            if (!this.ws.isConnected) {
                this.ws.connect();
            }
        } else {
            realtimeBtn.classList.remove('active');
            offlineBtn.classList.add('active');
            realtimeControls.style.display = 'none';
            offlineControls.style.display = 'block';
        }

        console.log(`[UIController] Switched to ${mode} mode`);
    }

    /**
     * Toggle play/pause
     */
    togglePlayPause() {
        const btn = document.getElementById('playPauseBtn');
        if (this.isPlaying) {
            this.ws.sendPauseCommand();
            btn.textContent = '▶ Play';
            btn.style.background = '#2196F3';
            this.isPlaying = false;
        } else {
            this.ws.sendPlayCommand();
            btn.textContent = '⏸ Pause';
            btn.style.background = '#ff9800';
            this.isPlaying = true;
        }
    }

    /**
     * Stop playback
     */
    stopPlayback() {
        this.ws.sendStopCommand();
        const btn = document.getElementById('playPauseBtn');
        btn.textContent = '▶ Play';
        btn.style.background = '#2196F3';
        this.isPlaying = false;
    }

    /**
     * Reset simulation
     */
    resetSimulation() {
        this.ws.sendResetCommand();
        document.getElementById('timelineSlider').value = '0';
        document.getElementById('timelineInput').value = '0';
        document.getElementById('simTime').textContent = '0s';
        this.isPlaying = false;
        const btn = document.getElementById('playPauseBtn');
        btn.textContent = '▶ Play';
        btn.style.background = '#2196F3';
    }

    /**
     * Set animation speed
     */
    setSpeed(speed) {
        document.getElementById('speedDisplay').textContent = speed.toFixed(1) + 'x';
        this.ws.sendSpeedCommand(speed);
        this.cesium.setAnimationSpeed(speed);
    }

    /**
     * Set timeline range (called when simulation_init is received)
     */
    setTimelineRange(min, max) {
        const slider = document.getElementById('timelineSlider');
        slider.min = min;
        slider.max = max;
        slider.value = min;
        document.getElementById('timelineInput').min = min;
        document.getElementById('timelineInput').max = max;
        document.getElementById('timelineInput').value = min;
    }

    /**
     * Update time display (called on each state update)
     */
    updateTimeDisplay(timestamp) {
        document.getElementById('simTime').textContent = Math.floor(timestamp) + 's';
        document.getElementById('timelineSlider').value = timestamp;
        document.getElementById('timelineInput').value = Math.floor(timestamp);
    }

    /**
     * Handle timeline slider drag
     */
    onTimelineSeek(value) {
        document.getElementById('timelineInput').value = value;
        document.getElementById('simTime').textContent = Math.floor(value) + 's';
    }

    /**
     * Jump to specific time
     */
    jumpToTime(timestamp) {
        if (isNaN(timestamp)) return;
        this.ws.sendTimelineCommand(timestamp);
        document.getElementById('timelineSlider').value = timestamp;
        document.getElementById('simTime').textContent = Math.floor(timestamp) + 's';
    }

    /**
     * Select metrics to display
     */
    selectMetrics(metricsType) {
        this.selectedMetrics = metricsType;
        this.ws.sendMetricsCommand(metricsType);
        this.cesium.setMetricsMode(metricsType);
    }

    /**
     * Search and filter satellite list
     */
    searchSatellites(query) {
        const filterList = document.getElementById('satelliteFilterList');
        const items = filterList.querySelectorAll('.filter-item');

        items.forEach((item) => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(query.toLowerCase()) ? 'flex' : 'none';
        });
    }

    /**
     * Search and filter station list
     */
    searchStations(query) {
        const filterList = document.getElementById('stationFilterList');
        const items = filterList.querySelectorAll('.filter-item');

        items.forEach((item) => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(query.toLowerCase()) ? 'flex' : 'none';
        });
    }

    /**
     * Select all satellites
     */
    selectAllSatellites() {
        const filterList = document.getElementById('satelliteFilterList');
        const checkboxes = filterList.querySelectorAll('input[type="checkbox"]');

        checkboxes.forEach((checkbox) => {
            if (checkbox.parentElement.style.display !== 'none') {
                checkbox.checked = true;
                this.selectedSatellites.add(checkbox.value);
            }
        });

        this.applyFilters();
    }

    /**
     * Deselect all satellites
     */
    deselectAllSatellites() {
        const filterList = document.getElementById('satelliteFilterList');
        const checkboxes = filterList.querySelectorAll('input[type="checkbox"]');

        checkboxes.forEach((checkbox) => {
            checkbox.checked = false;
            this.selectedSatellites.delete(checkbox.value);
        });

        this.applyFilters();
    }

    /**
     * Select all stations
     */
    selectAllStations() {
        const filterList = document.getElementById('stationFilterList');
        const checkboxes = filterList.querySelectorAll('input[type="checkbox"]');

        checkboxes.forEach((checkbox) => {
            if (checkbox.parentElement.style.display !== 'none') {
                checkbox.checked = true;
                this.selectedStations.add(checkbox.value);
            }
        });

        this.applyFilters();
    }

    /**
     * Deselect all stations
     */
    deselectAllStations() {
        const filterList = document.getElementById('stationFilterList');
        const checkboxes = filterList.querySelectorAll('input[type="checkbox"]');

        checkboxes.forEach((checkbox) => {
            checkbox.checked = false;
            this.selectedStations.delete(checkbox.value);
        });

        this.applyFilters();
    }

    /**
     * Apply selected filters
     */
    applyFilters() {
        this.cesium.setSatelliteFilter(Array.from(this.selectedSatellites));
        this.cesium.setStationFilter(Array.from(this.selectedStations));

        this.ws.sendFilterCommand(
            Array.from(this.selectedSatellites),
            Array.from(this.selectedStations)
        );
    }

    /**
     * Populate satellite filter list
     */
    populateSatelliteFilter(satellites) {
        const filterList = document.getElementById('satelliteFilterList');

        // Preserve existing selections
        const currentSelection = new Set(this.selectedSatellites);

        filterList.innerHTML = '';

        satellites.forEach((satId) => {
            const checked = currentSelection.size === 0 ? true : currentSelection.has(satId);
            const item = document.createElement('div');
            item.className = 'filter-item';
            item.innerHTML =
                '<input type="checkbox" value="' +
                satId +
                '" id="sat-' +
                satId +
                '"' +
                (checked ? ' checked' : '') +
                '>' +
                '<label for="sat-' +
                satId +
                '">' +
                satId +
                '</label>';

            const checkbox = item.querySelector('input');
            checkbox.addEventListener('change', (e) => {
                if (e.target.checked) {
                    this.selectedSatellites.add(satId);
                } else {
                    this.selectedSatellites.delete(satId);
                }
                this.applyFilters();
            });

            if (checked) {
                this.selectedSatellites.add(satId);
            }

            filterList.appendChild(item);
        });
    }

    /**
     * Populate station filter list
     */
    populateStationFilter(stations) {
        const filterList = document.getElementById('stationFilterList');

        const currentSelection = new Set(this.selectedStations);

        filterList.innerHTML = '';

        stations.forEach((stationId) => {
            const checked = currentSelection.size === 0 ? true : currentSelection.has(stationId);
            const item = document.createElement('div');
            item.className = 'filter-item';
            item.innerHTML =
                '<input type="checkbox" value="' +
                stationId +
                '" id="sta-' +
                stationId +
                '"' +
                (checked ? ' checked' : '') +
                '>' +
                '<label for="sta-' +
                stationId +
                '">' +
                stationId +
                '</label>';

            const checkbox = item.querySelector('input');
            checkbox.addEventListener('change', (e) => {
                if (e.target.checked) {
                    this.selectedStations.add(stationId);
                } else {
                    this.selectedStations.delete(stationId);
                }
                this.applyFilters();
            });

            if (checked) {
                this.selectedStations.add(stationId);
            }

            filterList.appendChild(item);
        });
    }

    /**
     * Update connection status indicator
     */
    updateConnectionStatus(isConnected) {
        const indicator = document.getElementById('connectionIndicator');
        const statusText = document.getElementById('connectionStatus');

        if (isConnected) {
            indicator.className = 'status-indicator status-connected';
            statusText.textContent = 'Connected';
            statusText.style.color = '#4caf50';
        } else {
            indicator.className = 'status-indicator status-disconnected';
            statusText.textContent = 'Disconnected';
            statusText.style.color = '#f44336';
        }
    }

    /**
     * Update statistics display
     */
    updateStatistics(stats) {
        document.getElementById('satCount').textContent = stats.satellites || 0;
        document.getElementById('staCount').textContent = stats.stations || 0;
        document.getElementById('linkCount').textContent = stats.links || 0;
        document.getElementById('fpsCounter').textContent = (stats.fps || 0) + ' FPS';
    }

    /**
     * Save Cesium token to localStorage
     */
    saveCesiumToken() {
        const token = document.getElementById('cesiumToken').value.trim();
        if (token) {
            localStorage.setItem('cesiumToken', token);
            Cesium.Ion.defaultAccessToken = token;
            document.getElementById('tokenStatus').textContent = 'Token saved';
            document.getElementById('tokenStatus').style.color = '#4caf50';
        } else {
            document.getElementById('tokenStatus').textContent = 'Please enter a token';
            document.getElementById('tokenStatus').style.color = '#f44336';
        }
    }

    /**
     * Load saved Cesium token
     */
    loadCesiumToken() {
        const token = localStorage.getItem('cesiumToken');
        if (token) {
            document.getElementById('cesiumToken').value = token;
            Cesium.Ion.defaultAccessToken = token;
            document.getElementById('tokenStatus').textContent = 'Loaded';
            document.getElementById('tokenStatus').style.color = '#4caf50';
            return token;
        }
        return null;
    }

    /**
     * Load offline data from URL (CZML file)
     */
    async loadOfflineData() {
        const filePath = document.getElementById('czmlFilePath').value.trim();
        const statusEl = document.getElementById('offlineStatus');

        if (!filePath) {
            statusEl.textContent = 'Error: No file path specified';
            return;
        }

        try {
            statusEl.textContent = 'Loading...';

            const dataSource = await Cesium.CzmlDataSource.load(filePath);
            this.cesium.viewer.dataSources.add(dataSource);
            this.cesium.viewer.flyTo(dataSource);

            statusEl.textContent = 'CZML loaded successfully';
            statusEl.style.color = '#4caf50';

        } catch (error) {
            console.error('[UIController] Error loading offline data:', error);
            statusEl.textContent = 'Error: ' + error.message;
            statusEl.style.color = '#f44336';
        }
    }

    /**
     * Load CZML from local file picker
     */
    loadCzmlFromLocal() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.czml,.json';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const statusEl = document.getElementById('offlineStatus');
            statusEl.textContent = 'Loading...';

            try {
                const text = await file.text();
                const czmlData = JSON.parse(text);

                const dataSource = await Cesium.CzmlDataSource.load(czmlData);
                this.cesium.viewer.dataSources.add(dataSource);
                this.cesium.viewer.flyTo(dataSource);

                statusEl.textContent = 'Loaded: ' + file.name;
                statusEl.style.color = '#4caf50';

            } catch (error) {
                console.error('[UIController] Error loading local file:', error);
                statusEl.textContent = 'Error: ' + error.message;
                statusEl.style.color = '#f44336';
            }
        };
        input.click();
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UIController;
}
