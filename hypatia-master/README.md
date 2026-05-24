# Hypatia

Hypatia is a low earth orbit (LEO) satellite network simulation framework. It pre-calculates network state over time, enables packet-level simulations using ns-3 and provides visualizations to aid understanding.

> **2024 Enhancement**: This fork adds **real-time interactive visualization** with WebSocket-based state streaming, playback control (play/pause/speed/timeline), and a modern vanilla JavaScript CesiumJS frontend. See [What's New](#whats-new) for details.

<a href="#"><img alt="Kuiper side-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Kuiper_side_view.png" width="20%" /></a>
<a href="#"><img alt="Telesat top-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Telesat_top_view.png" width="20%" /></a>
<a href="#"><img alt="starlink_paris_luanda_short" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/starlink_paris_luanda_short.png" width="10%" /></a>

It consists of five main components:

* `satgenpy` : Python framework to generate LEO satellite networks and generate 
  routing over time over a period of time. It additionally includes several 
  analysis tools to study individual cases. It makes use of several Python modules
  among which: numpy, astropy, ephem, networkx, sgp4, geopy, matplotlib, 
  statsmodels, cartopy (and its dependent (data) packages: libproj-dev, proj-data,
  proj-bin, libgeos-dev), and exputil.
  More information can be found in `satgenpy/README.md`.
  (license: MIT)

* `ns3-sat-sim` : ns-3 based framework which takes as input the state generated 
  by `satgenpy` to perform packet-level simulations over LEO satellite networks.
  It makes use of the [`satellite`](https://gitlab.inesctec.pt/pmms/ns3-satellite)
  ns-3 module by Pedro Silva to calculate satellite locations over time.
  It uses the [`basic-sim`](https://github.com/snkas/basic-sim/tree/3b32597c183e1039be7f0bede17d36d354696776) 
  ns-3 module to make e.g., running end-to-end TCP flows easier, which makes use of several Python
  modules (e.g., numpy, statsmodels, exputil) as well as several other packages (e.g., OpenMPI, lcov, gnuplot).
  More information can be found in `ns3-sat-sim/README.md`.
  (license: GNU GPL version 2)
  
* `satviz` : Cesium visualization pipeline to generate interactive satellite network
  visualizations. It makes use of the online Cesium API by generating CesiumJS code.
  The API calls require its user to obtain a Cesium access token (via [https://cesium.com/]()).
  **Enhanced with real-time interactive mode** featuring WebSocket connectivity,
  playback controls, and dynamic 3D scene updates. The frontend is a modular vanilla JS
  application (`static_html/js/`) with a detailed frontend integration guide in
  `satviz/FRONTEND_README.md`. Also includes `wstest.html` for WebSocket testing and
  `test_constellation.py` for constellation validation. More information can be found in `satviz/README.md`.
  (license: MIT)

* `realtime_backend` : **New** FastAPI + WebSocket relay server that bridges the simulation core
  and frontend visualization clients. Supports real-time state broadcasting, command forwarding,
  and multi-client connections. Located alongside `hypatia-master/` as a sibling directory.
  See `../realtime_backend/README.md` for details.
  (license: MIT)

* `paper` : Experimental and plotting code to reproduce the experiments and 
  figures which are presented in the paper.
  It makes use of several Python modules among which: satgenpy, numpy, networkload, and exputil.
  It uses the gnuplot package for most of its plotting.
  More information can be found in `paper/README.md`.
  (license: MIT)
  
(there is a sixth folder called `integration_tests` which is used for integration testing purposes)

## What's New

This fork adds **real-time interactive visualization** capabilities to the original Hypatia framework:

### New Features
- **Real-time State Streaming**: Satellite positions, link status, routing, and bandwidth utilization are streamed live via WebSocket at 10 Hz
- **Interactive Playback Control**: Play, pause, stop, reset, speed adjustment (0.1x–10x), and timeline scrubbing
- **Multi-client Support**: Multiple browser clients can simultaneously view the same simulation
- **Dynamic Visualization**: Link colors change in real-time based on utilization (green→yellow→red gradient)
- **Node Filtering**: Selectively show/hide specific satellites and ground stations
- **Metrics Switching**: Toggle between bandwidth, latency, loss rate, and link status displays

### Architecture Changes
| Component | Original | Enhanced |
|-----------|----------|----------|
| Visualization | Static CesiumJS HTML generation via Python scripts | Real-time CesiumJS with WebSocket client + static offline mode |
| Backend | Flask-SocketIO (satviz/server.py) | FastAPI + WebSocket relay (realtime_backend/) |
| Frontend | Generated static HTML with embedded CZML | Modular vanilla JS (app.js, cesium-manager.js, ui-controller.js, websocket.js) |
| Simulation Core | ns-3 only (batch mode) | ns-3 (batch) + demo_sim_core.py (real-time demo) |

### Data Flow
```
Simulation Core (demo_sim_core.py / ns-3)
    │  WebSocket /ws/core
    ▼
Realtime Backend (FastAPI + WebSocket Relay)
    │  WebSocket /ws/client
    ▼
Browser (CesiumJS 3D Globe)
    - Real-time satellite/link positions
    - Playback controls → commands → backend → core
```

### Compatibility
- All original Hypatia features are preserved and functional
- satgenpy: constellation generation, routing computation, post-analysis — unchanged
- ns3-sat-sim: packet-level simulation — unchanged (requires OpenMPI)
- satviz offline mode: static CZML visualization — unchanged
- The real-time features work independently with the demo_sim_core.py, no ns-3 build required

This is the code repository introduced and used in “Exploring the “Internet from space” with Hypatia” 
by Simon Kassing*, Debopam Bhattacherjee*, André Baptista Águas, Jens Eirik Saethre and Ankit Singla
(*equal contribution), which is published in the Internet Measurement Conference (IMC) 2020.

BibTeX citation:
```
@inproceedings {hypatia,
    author = {Kassing, Simon and Bhattacherjee, Debopam and Águas, André Baptista and Saethre, Jens Eirik and Singla, Ankit},
    title = {{Exploring the “Internet from space” with Hypatia}},
    booktitle = {{ACM IMC}},
    year = {2020}
}
```

## Getting started

### Original Hypatia (Offline Simulation)

1. System setup:
   - Python version 3.7+
   - Recent Linux operating system (e.g., Ubuntu 18+)

2. Install dependencies:
   ```
   bash hypatia_install_dependencies.sh
   ```
   
3. Build all four modules (as far as possible):
   ```
   bash hypatia_build.sh
   ```
   
4. Run tests:
   ```
   bash hypatia_run_tests.sh
   ```

5. The reproduction of the paper is essentially the tutorial for Hypatia.
   Please navigate to `paper/README.md`.

### Realtime Interactive Visualization (New)

1. Install Python dependencies (using conda recommended):
   ```bash
   # Create and activate conda environment
   conda create -n hypatia python=3.12
   conda activate hypatia
   
   # Install original Hypatia dependencies
   pip install -r requirements.txt
   
   # Install realtime backend dependencies
   cd ../realtime_backend
   pip install -r requirements.txt
   ```

2. Start the realtime backend server:
   ```bash
   cd /path/to/hypatia-master
   PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend \
     python -m realtime_backend.run --port 8000
   ```

3. Start the demo simulation core (in a new terminal):
   ```bash
   conda activate hypatia
   cd /path/to/hypatia-master/satviz
   python demo_sim_core.py
   ```

4. Open the frontend in a browser:
   ```bash
   cd /path/to/hypatia-master/satviz/static_html
   python -m http.server 8080
   # Open http://localhost:8080/index.html
   ```

5. Run integration tests:
   ```bash
   conda activate hypatia
   cd /path/to/hypatia-master
   PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend \
     python integration_tests/test_realtime_integration.py
   ```

### Quick Start Script

```bash
# Terminal 1: Backend
conda activate hypatia && \
  PYTHONPATH=$(pwd):$(pwd)/../realtime_backend \
  python -m realtime_backend.run --port 8000

# Terminal 2: Simulation Core
conda activate hypatia && cd satviz && python demo_sim_core.py

# Terminal 3: HTTP Server for Frontend
cd satviz/static_html && python -m http.server 8080

# Then open http://localhost:8080/index.html in your browser
```

### Visualizations
Most of the visualizations in the paper are available [here](https://leosatsim.github.io/).
All of the visualizations can be regenerated using scripts available in `satviz` as discussed above.

Below are some examples of visualizations:

- SpaceX Starlink 5-shell side-view (left) and top-view (right). To know the configuration of the shells, click [here](https://leosatsim.github.io/).

  <a href="#"><img alt="Starlink side-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Starlink_side_view.png" width="45%" /></a>
  <a href="#"><img alt="Starlink top-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Starlink_top_view.png" width="45%" /></a>

- Amazon Kuiper 3-shell side-view (left) and top-view (right). To know the configuration of the shells, click [here](https://leosatsim.github.io/kuiper.html).

  <a href="#"><img alt="Kuiper side-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Kuiper_side_view.png" width="45%" /></a>
  <a href="#"><img alt="Kuiper top-view" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/Kuiper_top_view.png" width="45%" /></a>

- RTT changes over time between Paris and Luanda over Starlink 1st shell. Left: 117 ms, Right: 85 ms. Click on the images for 3D interactive visualizations.

  <a href="https://leosatsim.github.io/starlink_550_path_Paris_1608_Luanda_1650_46800.html"><img alt="starlink_paris_luanda_long" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/starlink_paris_luanda_long.png" width="35%" /></a>
  <a href="https://leosatsim.github.io/starlink_550_path_Paris_1608_Luanda_1650_139900.html"><img alt="starlink_paris_luanda_short" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/starlink_paris_luanda_short.png" width="35%" /></a>

- Link utilizations change over time, even with the input traffic being static. For Kuiper 1st shell, path between Chicago and Zhengzhou at 10s (top) and 150s (bottom). Click on the images for 3D interactive visualizations.

  <a href="https://leosatsim.github.io/kuiper_630_path_wise_util_Chicago_1193_Zhengzhou_1243_10000.html"><img alt="kuiper_Chicago_Zhengzhou_10s" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/kuiper_Chicago_Zhengzhou_10s.png" width="90%" /></a>
  <a href="https://leosatsim.github.io/kuiper_630_path_wise_util_Chicago_1193_Zhengzhou_1243_150000.html"><img alt="kuiper_Chicago_Zhengzhou_150s" src="https://raw.githubusercontent.com/leosatsim/leosatsim.github.io/master/images/kuiper_Chicago_Zhengzhou_150s.png" width="90%" /></a>
