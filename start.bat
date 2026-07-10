@echo off
REM 闲鱼一人公司 - Windows 一键启动脚本
REM 用法: start.bat [web^|mock^|pipeline]

echo ============================================
echo   闲鱼一人公司多智能体系统
echo ============================================

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo [1/4] 检查 Python 环境...
python --version

REM 创建虚拟环境
if not exist "venv" (
    echo [2/4] 创建虚拟环境...
    python -m venv venv
)

REM 激活虚拟环境
call venv\Scripts\activate

REM 安装依赖
echo [3/4] 安装依赖...
pip install -r requirements.txt -q
playwright install chromium 2>nul

REM 检查 .env
if not exist ".env" (
    echo [提示] 未找到 .env 文件，将以演示模式运行
    echo   配置API Key请参考 .env.example
)

REM 启动
echo [4/4] 启动系统...
set MODE=%1
if "%MODE%"=="" set MODE=web

if "%MODE%"=="web" (
    echo   启动Web管理界面: http://localhost:8000
    python main.py --web
) else if "%MODE%"=="mock" (
    echo   启动模拟模式
    python main.py --mock
) else if "%MODE%"=="pipeline" (
    echo   执行选品上架流水线
    python main.py --pipeline "厨房收纳盒"
) else (
    echo   用法: start.bat [web^|mock^|pipeline]
)

pause
