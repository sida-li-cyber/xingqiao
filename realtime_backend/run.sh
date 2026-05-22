#!/bin/bash
# 仿真实时后端启动脚本

set -e

echo "=========================================="
echo "Realtime Simulation Backend Launcher"
echo "=========================================="

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed"
    exit 1
fi

echo "✓ Python version: $(python3 --version)"

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "🚀 Activating virtual environment..."
source venv/bin/activate

# 升级pip
echo "📦 Upgrading pip..."
pip install --quiet --upgrade pip

# 安装依赖
echo "📦 Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip install --quiet -r requirements.txt
else
    echo "❌ Error: requirements.txt not found"
    exit 1
fi

echo "✓ Dependencies installed successfully"

# 获取配置参数
HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"
LOG_LEVEL="${APP_LOG_LEVEL:-info}"

echo ""
echo "=========================================="
echo "Configuration:"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Log Level: $LOG_LEVEL"
echo "=========================================="
echo ""

# 启动服务器
echo "🚀 Starting Realtime Simulation Backend..."
echo "📡 WebSocket Client endpoint: ws://$HOST:$PORT/ws/client"
echo "📡 WebSocket Core endpoint: ws://$HOST:$PORT/ws/core"
echo "🔍 API Documentation: http://$HOST:$PORT/docs"
echo "🔍 Alternative Docs: http://$HOST:$PORT/redoc"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python3 -m realtime_backend.run \
    --host "$HOST" \
    --port "$PORT" \
    --log-level "$LOG_LEVEL"
