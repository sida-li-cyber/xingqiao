# Hypatia Dependency Audit Report

## Current workspace state
- Backup copy created at `backup_original/` containing the original project snapshot.
- Git metadata is missing from the repository root (`.git` not present), so creating a Git branch is not possible in this environment.
- Python virtual environment created at `hypatia_py314_venv/` using Python 3.14.5.
- A snapshot of installed Python packages was written to `requirements.txt`.

## Installed Python dependencies
The following packages are installed in `hypatia_py314_venv/`:

- astropy==7.2.0
- astropy-iers-data==0.2026.5.18.1.11.28
- bidict==0.23.1
- blinker==1.9.0
- click==8.4.0
- colorama==0.4.6
- contourpy==1.3.3
- cycler==0.12.1
- ephem==4.2.1
- Flask==3.1.3
- Flask-SocketIO==5.6.1
- fonttools==4.63.0
- geographiclib==2.1
- geopy==2.4.1
- h11==0.16.0
- itsdangerous==2.2.0
- Jinja2==3.1.6
- kiwisolver==1.5.0
- MarkupSafe==3.0.3
- matplotlib==3.10.9
- networkx==3.6.1
- numpy==2.4.5
- packaging==26.2
- pandas==3.0.3
- patsy==1.0.2
- pillow==12.2.0
- pyerfa==2.0.1.5
- pyparsing==3.3.2
- python-dateutil==2.9.0.post0
- python-engineio==4.13.1
- python-socketio==5.16.1
- PyYAML==6.0.3
- scipy==1.17.1
- setuptools==82.0.1
- sgp4==2.25
- simple-websocket==1.1.0
- six==1.17.0
- statsmodels==0.14.6
- tzdata==2026.2
- Werkzeug==3.1.8
- wheel==0.47.0
- wsproto==1.3.2

## Dependency audit findings

### High-risk items
1. **Git metadata missing**
   - The repository does not contain a `.git` folder. Git operations such as creating a branch cannot be performed.

2. **External source dependencies**
   - `git+https://github.com/snkas/exputilpy.git@v1.6`
   - `git+https://github.com/snkas/networkload.git@v1.3`
   - These are not captured by `pip freeze` unless installed, and they introduce dependency-on-host availability and remote Git access.

3. **OS-level build dependencies not installed on Windows**
   - `libproj-dev`, `proj-data`, `proj-bin`, `libgeos-dev` (required for `cartopy`)
   - `openmpi-bin`, `openmpi-common`, `libopenmpi-dev`, `lcov`, `gnuplot`
   - These are Ubuntu/Linux-specific packages referenced in `hypatia_install_dependencies.sh` and may prevent full installation of paper/ns3-sat-sim components in the current Windows environment.

4. **Outdated frontend library**
   - `satviz/static_html/top.html` uses CesiumJS `1.57` from CDN.
   - Cesium 1.57 is old and may be incompatible with modern browser/WebGL environments or current Cesium Ion behavior.

### Medium-risk items
1. **Async concurrency mode for realtime server**
   - `satviz/server.py` uses `SocketIO(..., async_mode='threading')`.
   - This works for low-frequency demo updates but is not scalable for heavy real-time loads. If you move to production or high-rate telemetry, consider `eventlet` or `gevent`.

2. **Flask 3 / extension compatibility**
   - `Flask==3.1.3` is installed; ensure all imported extensions and custom code are compatible with Flask 3 API changes.

3. **Potential API drift from Python 3.14**
   - The repository's install script says Python 3.7+, but current environment is Python 3.14.5.
   - Most packages installed successfully, but the code should still be tested for compatibility with newer language/runtime behavior.

### Low-risk items
1. **Pure Python libraries installed**
   - `numpy`, `networkx`, `scipy`, `pandas`, `matplotlib`, `statsmodels`, `geopy`, `sgp4`, `ephem`.
   - These packages are common and appear compatible with Python 3.14.

2. **`ephem` fallback path**
   - `satviz/scripts/util.py` contains a fallback generator that does not require full orbital propagation dependencies.

## Suggested immediate actions
1. Preserve the current backup and install state.
2. Add a versioned `requirements.txt` to the repo if you want repeatable environment setup.
3. If you need a real Git branch, clone the repository from a Git-enabled machine or initialize Git locally once `.git` metadata is available.
4. Update the Cesium frontend reference to a newer supported version when moving to real-time interactive visualization.
5. For paper/ns3-sat-sim work on Windows, use a Linux environment or WSL to satisfy native dependencies.

## Files created
- `requirements.txt` (Python package snapshot)
- `dependency_audit_report.md` (this report)

## Notes
- The working `satviz` real-time server shown in `hypatia-master/hypatia-master/satviz/server.py` is already compatible with `Flask` and `Flask-SocketIO`.
- The backup tree under `backup_original/` contains the original project content before changes.
