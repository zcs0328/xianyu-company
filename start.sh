#!/bin/bash
# 闲鱼一人公司 - 一键启动脚本
# 用法: bash start.sh [web|mock|pipeline]

set -e

echo "============================================"
echo "  闲鱼一人公司多智能体系统"
echo "============================================"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

echo "[1/4] 检查 Python 环境..."
python3 --version

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "[2/4] 创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "[3/4] 安装依赖..."
pip install -r requirements.txt -q
playwright install chromium 2>/dev/null || echo "  (Playwright浏览器安装跳过)"

# 检查 .env
if [ ! -f ".env" ]; then
    echo "[提示] 未找到 .env 文件，将以演示模式运行"
    echo "  配置API Key请参考 .env.example"
fi

# 启动
echo "[4/4] 启动系统..."
MODE=${1:-web}

case $MODE in
    web)
        echo "  启动Web管理界面: http://localhost:8000"
        python main.py --web
        ;;
    mock)
        echo "  启动模拟模式（全自动运行）"
        python main.py --mock
        ;;
    pipeline)
        echo "  执行选品上架流水线"
        python main.py --pipeline "厨房收纳盒"
        ;;
    *)
        echo "  用法: bash start.sh [web|mock|pipeline]"
        echo "    web      - 启动Web管理界面（推荐）"
        echo "    mock     - 模拟模式全自动运行"
        echo "    pipeline - 执行一次选品流水线"
        ;;
esac
