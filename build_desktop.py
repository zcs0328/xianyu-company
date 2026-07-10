#!/usr/bin/env python3
"""
桌面版打包脚本
使用 PyInstaller 将项目打包成独立可执行文件

用法:
    python build_desktop.py

输出:
    dist/闲鱼一人公司.exe  (Windows)
    dist/闲鱼一人公司      (Linux/macOS)
"""

import os
import sys
import io
import shutil
import subprocess
from pathlib import Path

# Windows PowerShell 默认编码可能不是 UTF-8，强制设置为 UTF-8 避免中文输出崩溃
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPECFILE = PROJECT_ROOT / "desktop.spec"

APP_NAME = "闲鱼一人公司"
ENTRY_SCRIPT = PROJECT_ROOT / "desktop.py"


def clean():
    """清理之前的构建文件"""
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"已清理: {d}")


def check_pyinstaller():
    """检查 PyInstaller 是否已安装"""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    """安装 PyInstaller"""
    print("正在安装 PyInstaller...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller", "--break-system-packages"],
        check=True,
    )
    print("PyInstaller 安装完成")


def collect_data_files() -> list:
    """收集需要打包的数据文件"""
    datas = []

    # 配置文件
    config_dir = PROJECT_ROOT / "config"
    if config_dir.exists():
        datas.append((str(config_dir), "config"))

    # 前端静态文件
    static_dir = PROJECT_ROOT / "src" / "web" / "static"
    if static_dir.exists():
        datas.append((str(static_dir), "src/web/static"))

    # src 模块（Python 文件由 PyInstaller 自动收集，但可能需要显式包含某些子目录）
    # actions, agents, models, tools 都是 Python 包，会被自动分析

    return datas


def generate_spec():
    """生成 PyInstaller .spec 文件"""
    datas = collect_data_files()
    datas_str = ",\n        ".join([f"('{src}', '{dst}')" for src, dst in datas])

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

import sys
sys.setrecursionlimit(5000)

block_cipher = None

a = Analysis(
    ['{ENTRY_SCRIPT.as_posix()}'],
    pathex=['{PROJECT_ROOT.as_posix()}'],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        'src',
        'src.web.app',
        'src.config',
        'src.company',
        'src.models.database',
        'src.models.repo',
        'src.agents.base',
        'src.agents.ceo',
        'src.agents.purchasing',
        'src.agents.pricing',
        'src.agents.packaging',
        'src.agents.operations',
        'src.agents.review',
        'src.agents.accounting',
        'src.agents.analytics',
        'src.agents.risk_control',
        'src.tools.llm_client',
        'src.tools.dashboard',
        'src.tools.account_manager',
        'src.tools.risk_control',
        'src.tools.xianyu_messaging',
        'src.tools.xianyu_web',
        'src.tools.source_platforms',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'websockets',
        'websockets.legacy',
        'pydantic',
        'pydantic.deprecated',
        'sqlalchemy',
        'sqlalchemy.ext.asyncio',
        'aiosqlite',
        'yaml',
        'openai',
        'httpx',
        'apscheduler',
        'apscheduler.triggers',
        'apscheduler.triggers.cron',
        'apscheduler.triggers.interval',
        'apscheduler.executors',
        'apscheduler.executors.asyncio',
        'apscheduler.jobstores',
        'apscheduler.jobstores.memory',
        'loguru',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'platformdirs',
        'dotenv',
        'webview',
        'webview.platforms.gtk',
        'webview.platforms.qt',
        'webview.platforms.cocoa',
        'webview.platforms.winforms',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 关闭 UPX 减少杀毒软件误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
'''
    SPECFILE.write_text(spec_content, encoding="utf-8")
    print(f"已生成 spec 文件: {SPECFILE}")


def build():
    """执行打包"""
    print("=" * 50)
    print("开始打包桌面应用...")
    print("=" * 50)

    if not check_pyinstaller():
        install_pyinstaller()

    clean()
    generate_spec()

    # 执行 PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPECFILE),
        "--clean",
        "--noconfirm",
    ]
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)

    if result.returncode == 0:
        print("\n" + "=" * 50)
        print("打包成功!")
        print(f"输出目录: {DIST_DIR}")
        # 查找生成的可执行文件
        exe_files = list(DIST_DIR.glob("闲鱼一人公司*"))
        if exe_files:
            exe_path = exe_files[0]
            print(f"可执行文件: {exe_path}")
            print(f"文件大小: {exe_path.stat().st_size / 1024 / 1024:.1f} MB")
        print("=" * 50)
    else:
        print("\n打包失败!")
        sys.exit(1)


if __name__ == "__main__":
    build()
