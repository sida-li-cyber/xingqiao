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

        this.scenarioInfo = {
            ideal:       'Avg loss: 0.1% | Jitter: 0.05%–0.5%',
            commercial:  'Avg loss: 1.0% | Jitter: 0.5%–4.0%',
            weather:     'Avg loss: 2.0% | Jitter: 1.0%–6.0%',
            handover:    'Avg loss: 3.0% | Jitter: 0.5%–10.0%',
            extreme:     'Avg loss: 5.0% | Jitter: 1.0%–15.0%',
        };

        // Shell metadata for building dropdown options (full-scale params)
        this._shellMeta = {
            Starlink: [
                { label: 'Shell 0 - 550km / 53.0°',  orbits: 72, sats: 22, inc: 53.0,  alt: 550 },
                { label: 'Shell 1 - 1110km / 53.8°', orbits: 32, sats: 50, inc: 53.8,  alt: 1110 },
                { label: 'Shell 2 - 1130km / 74.0°', orbits: 8,  sats: 50, inc: 74.0,  alt: 1130 },
                { label: 'Shell 3 - 1275km / 81.0°', orbits: 5,  sats: 75, inc: 81.0,  alt: 1275 },
                { label: 'Shell 4 - 1325km / 70.0°', orbits: 6,  sats: 75, inc: 70.0,  alt: 1325 },
            ],
            Kuiper: [
                { label: 'Shell 0 - 630km / 51.9°',  orbits: 34, sats: 34, inc: 51.9,  alt: 630 },
                { label: 'Shell 1 - 610km / 42.0°',  orbits: 36, sats: 36, inc: 42.0,  alt: 610 },
                { label: 'Shell 2 - 590km / 33.0°',  orbits: 28, sats: 28, inc: 33.0,  alt: 590 },
            ],
            Telesat: [
                { label: 'Shell 0 - 1015km / 98.98°', orbits: 27, sats: 13, inc: 98.98, alt: 1015 },
                { label: 'Shell 1 - 1325km / 50.88°', orbits: 40, sats: 33, inc: 50.88, alt: 1325 },
            ],
        };
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

        // Scenario selection
        document.getElementById('scenarioSelect').addEventListener('change', (e) => this.selectScenario(e.target.value));

        // Constellation / shell selection
        document.getElementById('constellationSelect').addEventListener('change', (e) => this.onConstellationChange(e.target.value));
        document.getElementById('shellSelect').addEventListener('change', (e) => this.onShellChange(parseInt(e.target.value)));

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
        this.updateMetricsLegend(metricsType);
    }

    /**
     * Show/hide and update the color legend bar based on selected metric.
     */
    updateMetricsLegend(metricsType) {
        const legend = document.getElementById('metricsLegend');
        const title = document.getElementById('legendTitle');
        const ticks = document.getElementById('legendTicks');
        const bar = document.getElementById('legendBar');

        const legends = {
            bandwidth: {
                title: 'Bandwidth Utilization',
                gradient: 'linear-gradient(to right, #006400 0%, #00FF00 30%, #ADFF2F 50%, #FFFF00 70%, #FF8C00 85%, #8B0000 100%)',
                stops: [
                    { pos: '0%',  label: '0%' },
                    { pos: '30%', label: '30%' },
                    { pos: '50%', label: '50%' },
                    { pos: '70%', label: '70%' },
                    { pos: '85%', label: '85%' },
                    { pos: '100%',label: '100%' },
                ],
            },
            latency: {
                title: 'Latency (ms)',
                gradient: 'linear-gradient(to right, #87F5FF 0%, #36E8A8 20%, #F9F871 40%, #FFB347 70%, #FF6B35 100%)',
                stops: [
                    { pos: '0%',  label: '0' },
                    { pos: '20%', label: '20' },
                    { pos: '40%', label: '40' },
                    { pos: '70%', label: '70' },
                    { pos: '100%',label: '100+' },
                ],
            },
            loss_rate: {
                title: 'Packet Loss Rate',
                gradient: 'linear-gradient(to right, #20A4F3 0%, #5ED9FF 10%, #64DD78 30%, #FFC857 55%, #FF5722 80%, #9E0000 100%)',
                stops: [
                    { pos: '0%',  label: '0%' },
                    { pos: '10%', label: '0.5%' },
                    { pos: '30%', label: '2%' },
                    { pos: '55%', label: '6%' },
                    { pos: '80%', label: '15%' },
                    { pos: '100%',label: '>15%' },
                ],
            },
            link_status: {
                title: 'Link Status',
                gradient: 'linear-gradient(to right, #4caf50, #f44336)',
                stops: [
                    { pos: '0%',  label: 'Active' },
                    { pos: '100%',label: 'Inactive' },
                ],
            },
        };

        const cfg = legends[metricsType];
        if (cfg) {
            title.textContent = cfg.title;
            bar.style.background = cfg.gradient;

            ticks.innerHTML = '';
            cfg.stops.forEach(function(s) {
                var el = document.createElement('span');
                el.className = 'legend-tick';
                el.style.left = s.pos;
                el.textContent = s.label;
                ticks.appendChild(el);
            });

            legend.style.display = 'block';
        } else {
            legend.style.display = 'none';
        }
    }

    /**
     * Select simulation scenario
     */
    selectScenario(scenario) {
        this.ws.sendScenarioCommand(scenario);
        const info = this.scenarioInfo[scenario] || '';
        document.getElementById('scenarioInfo').textContent = info;
        console.log('[UIController] Scenario:', scenario);
    }

    /**
     * Constellation dropdown changed — update shell dropdown options.
     */
    onConstellationChange(constellationName) {
        this.updateShellDropdown(constellationName);
        // Auto-switch to shell 0 of the new constellation
        this.switchConstellation(constellationName, 0);
    }

    /**
     * Shell dropdown changed — trigger constellation switch.
     */
    onShellChange(shellIndex) {
        var name = document.getElementById('constellationSelect').value;
        this.switchConstellation(name, shellIndex);
    }

    /**
     * Update shell dropdown options for the given constellation.
     */
    updateShellDropdown(constellationName) {
        var shellSelect = document.getElementById('shellSelect');
        var shells = this._shellMeta[constellationName] || [];
        shellSelect.innerHTML = '';
        shells.forEach(function (s, i) {
            var opt = document.createElement('option');
            opt.value = i;
            opt.textContent = s.label;
            shellSelect.appendChild(opt);
        });
        shellSelect.value = '0';
    }

    /**
     * Switch to a different constellation shell.
     */
    switchConstellation(constellationName, shellIndex) {
        console.log('[UIController] Switching to', constellationName, 'shell', shellIndex);
        // Block state updates from the old constellation until the new
        // simulation_init arrives and triggers the real clear+rebuild.
        this.app._constellationSwitching = true;
        // Clear filter selections — new satellites will have different IDs
        this.clearFilterSelections();
        // Send command to backend
        this.ws.sendConstellationCommand(constellationName, shellIndex);
        // Safety timeout: reset the guard if the backend never responds,
        // otherwise stale state_updates would be blocked forever.
        clearTimeout(this._switchTimeout);
        var self = this;
        this._switchTimeout = setTimeout(function () {
            if (self.app._constellationSwitching) {
                console.warn('[UIController] Constellation switch timed out — resetting guard');
                self.app._constellationSwitching = false;
            }
        }, 5000);
    }

    /**
     * Update constellation info display (called from app on simulation_init).
     */
    updateConstellationInfo(name, currentShell, shellCount, totalSats, totalLinks) {
        var shells = this._shellMeta[name] || [];
        var shellLabel = shells[currentShell] ? shells[currentShell].label : ('Shell ' + currentShell);
        var el = document.getElementById('constellationInfo');
        if (el) {
            el.textContent = name + ' | ' + shellLabel + ' | ' + totalSats + ' sats, ' + totalLinks + ' links';
        }
    }

    /**
     * Clear all filter selections (used on constellation switch).
     */
    clearFilterSelections() {
        this.selectedSatellites.clear();
        this.selectedStations.clear();
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
