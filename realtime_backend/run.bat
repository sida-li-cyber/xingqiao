@echo off
REM 仿真实时后端启动脚本 (Windows)

setlocal enabledelayedexpansion

echo.
echo ==========================================
echo Realtime Simulation Backend Launcher
echo ==========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo Python version: %PYTHON_VERSION%
echo.

REM 获取脚本所在目录
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM 创建虚拟环境（如果不存在）
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment
        exit /b 1
    )
)

REM 激活虚拟环境
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error: Failed to activate virtual environment
    exit /b 1
)

REM 升级pip
echo Upgrading pip...
python -m pip install --quiet --upgrade pip

REM 安装依赖
echo Installing dependencies...
if exist "requirements.txt" (
    pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Error: Failed to install dependencies
        exit /b 1
    )
) else (
    echo Error: requirements.txt not found
    exit /b 1
)

echo Dependencies installed successfully
echo.

REM 获取配置参数
if not defined APP_HOST set APP_HOST=0.0.0.0
if not defined APP_PORT set APP_PORT=8000
if not defined APP_LOG_LEVEL set APP_LOG_LEVEL=info

echo ==========================================
echo Configuration:
echo   Host: %APP_HOST%
echo   Port: %APP_PORT%
echo   Log Level: %APP_LOG_LEVEL%
echo ==========================================
echo.

REM 启动服务器
echo Starting Realtime Simulation Backend...
echo WebSocket Client endpoint: ws://%APP_HOST%:%APP_PORT%/ws/client
echo WebSocket Core endpoint: ws://%APP_HOST%:%APP_PORT%/ws/core
echo API Documentation: http://%APP_HOST%:%APP_PORT%/docs
echo Alternative Docs: http://%APP_HOST%:%APP_PORT%/redoc
echo.
echo Press Ctrl+C to stop the server
echo.

python -m realtime_backend.run --host %APP_HOST% --port %APP_PORT% --log-level %APP_LOG_LEVEL%
