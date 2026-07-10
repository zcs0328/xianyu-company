@echo off
chcp 65001 >nul
echo ==========================================
echo  闲鱼一人公司 - Windows 打包脚本
echo ==========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 安装依赖...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller pywebview pystray platformdirs Pillow
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [2/4] 清理旧文件...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [3/4] 开始打包...
python build_desktop.py
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo [4/4] 打包完成!
echo.
echo 可执行文件位置:
dir /b dist\*.exe 2>nul
if errorlevel 1 (
    echo 未找到 exe 文件，请检查 dist 目录
) else (
    echo.
    echo 可以直接运行: dist\闲鱼一人公司.exe
)
echo.
pause
