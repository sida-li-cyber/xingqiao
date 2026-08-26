@echo off
REM ============================================================
REM  StarBridge one-click launcher (Windows)
REM  Starts 3 processes, then opens the browser:
REM    1. Realtime backend      : http://127.0.0.1:8000
REM    2. Simulation core       : connects to backend automatically
REM    3. Frontend HTTP server  : http://127.0.0.1:8080/index.html
REM  Double-click to run. Stop everything with stop_starbridge.bat
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo   StarBridge  -  One-click Launcher
echo ==========================================
echo.

REM ---------- 1. Python check ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo         Please install Python 3.10+ from https://www.python.org/downloads/
    echo         and check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo [OK] %PYTHON_VERSION%

REM ---------- 2. Python interpreter (venv preferred, system fallback) ----------
set PY=
if exist ".venv\Scripts\python.exe" (
    echo [1/5] Virtual environment ".venv" found.
    for %%I in ("%~dp0.venv\Scripts\python.exe") do set PY=%%~sI
    goto :py_ready
)
echo [1/5] Creating virtual environment ".venv" ...
python -m venv .venv >nul 2>&1
if exist ".venv\Scripts\python.exe" (
    for %%I in ("%~dp0.venv\Scripts\python.exe") do set PY=%%~sI
    goto :py_ready
)
REM venv failed on this "python" (stripped/minimal build) - try the
REM py launcher which points to the machine's full Python install.
py -3 -m venv .venv >nul 2>&1
if exist ".venv\Scripts\python.exe" (
    for %%I in ("%~dp0.venv\Scripts\python.exe") do set PY=%%~sI
    goto :py_ready
)
echo [WARN] No Python with "venv" module found.
echo        Falling back to system Python with --user packages.
set PY=python

:py_ready
echo [OK] Interpreter: %PY%

REM ---------- 3. Dependencies ----------
echo [2/5] Checking dependencies ^(first run may take 1-2 minutes^) ...
if "%PY%"=="python" (
    python -m pip install --quiet --disable-pip-version-check --user -r requirements-runtime.txt
) else (
    "%PY%" -m pip install --quiet --disable-pip-version-check -r requirements-runtime.txt
)
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. Check network / proxy settings.
    pause
    exit /b 1
)
echo [OK] Dependencies ready.

REM ---------- 4. Port check ----------
echo [3/5] Checking ports 8000 / 8080 ...
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8000 is already in use - StarBridge may already be running.
    echo        Run stop_starbridge.bat first, or close the occupying program.
    pause
    exit /b 1
)
netstat -ano | findstr /r /c:":8080 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8080 is already in use - close the occupying program or
    echo        edit the frontend port in this script.
    pause
    exit /b 1
)
echo [OK] Ports are free.

REM ---------- 5. Start processes ----------
echo [4/5] Starting backend / simulation core / frontend ...
start "StarBridge-Backend" /D "%~dp0" cmd /k %PY% -m realtime_backend.run --port 8000
ping -n 4 127.0.0.1 >nul

REM Real ships (AIS): auto-load the converted tracks JSON when present,
REM so the frontend's "真实船舶(AIS)" layer lights up without extra flags.
set AIS_ARGS=
if exist "realtime_backend\data\ais\ships_marine_cadastre.json" (
    set "AIS_ARGS=--ais-file "%~dp0realtime_backend\data\ais\ships_marine_cadastre.json""
    echo [AIS] Real ship tracks found - AIS replay layer enabled.
)

start "StarBridge-Core" /D "%~dp0hypatia-master\satviz" cmd /k %PY% demo_sim_core.py --port 8000 %AIS_ARGS%
ping -n 3 127.0.0.1 >nul

REM Serve the satviz folder as root: index.html references its scripts as
REM "../js/...", so the URL must be /static_html/index.html (serving
REM static_html directly breaks ../ on Python 3.13+ http.server).
start "StarBridge-Frontend" /D "%~dp0" cmd /k %PY% -m http.server 8080 --directory "%~dp0hypatia-master\satviz"

REM ---------- 6. Open browser ----------
echo [5/5] Opening browser ...
ping -n 4 127.0.0.1 >nul
start "" "http://127.0.0.1:8080/static_html/index.html"

echo.
echo ==========================================
echo   StarBridge is UP.
echo.
echo   Frontend : http://127.0.0.1:8080/static_html/index.html
echo   Backend  : http://127.0.0.1:8000/health
echo.
echo   3 console windows opened:
echo     StarBridge-Backend / StarBridge-Core / StarBridge-Frontend
echo   Keep them running. Stop everything: stop_starbridge.bat
echo.
echo   Tip: no Cesium token is needed - the app uses a free
echo        offline basemap automatically.
echo ==========================================
echo.
echo This window can be closed safely.
pause
