#!/usr/bin/env python3
"""
闲鱼一人公司 - 桌面版入口
启动 FastAPI 后台 + pywebview 窗口 + 系统托盘
"""

import os
import sys
import socket
import threading
import time
import json
import importlib.util
from pathlib import Path
from typing import Optional
from datetime import datetime

try:
    import webview
    _has_webview = True
except Exception:
    _has_webview = False
    webview = None

# ========== 打包环境检测 ==========

def is_frozen() -> bool:
    """检测是否运行在 PyInstaller 打包环境中"""
    return getattr(sys, 'frozen', False)

def get_resource_path(relative_path: str) -> Path:
    """获取资源文件路径（兼容开发和打包环境）"""
    if is_frozen():
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return base / relative_path

# ========== 用户数据目录 ==========

from platformdirs import user_data_dir, user_config_dir, user_log_dir

APP_NAME = "xianyu-company"
APP_AUTHOR = "xianyu"

DATA_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR))
CONFIG_DIR = Path(user_config_dir(APP_NAME, APP_AUTHOR))
LOG_DIR = Path(user_log_dir(APP_NAME, APP_AUTHOR))
EXTENSIONS_DIR = DATA_DIR / "extensions"
PLUGINS_DIR = DATA_DIR / "plugins"

# 确保目录存在
for d in [DATA_DIR, CONFIG_DIR, LOG_DIR, EXTENSIONS_DIR, PLUGINS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 设置环境变量，供其他模块使用
os.environ["XIANYU_DATA_DIR"] = str(DATA_DIR)
os.environ["XIANYU_CONFIG_DIR"] = str(CONFIG_DIR)
os.environ["XIANYU_EXTENSIONS_DIR"] = str(EXTENSIONS_DIR)

# ========== 日志配置 ==========

from loguru import logger

logger.remove()
logger.add(
    LOG_DIR / "app.log",
    rotation="10 MB",
    retention="7 days",
    level="INFO",
    encoding="utf-8",
)
logger.add(sys.stdout, level="INFO")

# ========== 端口管理 ==========

def find_free_port(start: int = 18765, max_tries: int = 100) -> int:
    """查找可用端口"""
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError("找不到可用端口")

_server_port: Optional[int] = None
_server_thread: Optional[threading.Thread] = None
_server_ready = threading.Event()

def start_backend() -> int:
    """启动 FastAPI 后台服务器，返回端口号"""
    global _server_port, _server_thread

    _server_port = find_free_port()

    def run_server():
        import uvicorn
        # 延迟导入，确保 sys.path 已设置
        from src.web.app import app
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=_server_port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        _server_ready.set()
        server.run()

    _server_thread = threading.Thread(target=run_server, daemon=True)
    _server_thread.start()

    # 等待服务器启动
    _server_ready.wait(timeout=10)
    time.sleep(1)  # 额外等待路由注册完成
    logger.info(f"后台服务已启动: http://127.0.0.1:{_server_port}")
    return _server_port

# ========== 扩展管理器 ==========

class ExtensionManager:
    """插件扩展管理器 - 动态加载用户扩展"""

    def __init__(self, extensions_dir: Path):
        self.extensions_dir = extensions_dir
        self.extensions: list[dict] = []
        self._loaded_modules: list = []

    def scan(self) -> list[dict]:
        """扫描扩展目录"""
        self.extensions = []
        if not self.extensions_dir.exists():
            return []

        for ext_file in sorted(self.extensions_dir.glob("*.py")):
            if ext_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    ext_file.stem, ext_file
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    info = {
                        "name": getattr(mod, "__name__", ext_file.stem),
                        "version": getattr(mod, "__version__", "0.0.1"),
                        "description": getattr(mod, "__doc__", ""),
                        "file": str(ext_file.name),
                        "loaded": False,
                    }
                    self.extensions.append(info)
            except Exception as e:
                logger.warning(f"扩展扫描失败 {ext_file.name}: {e}")

        return self.extensions

    def load(self, ext_name: str) -> bool:
        """加载指定扩展"""
        ext_file = self.extensions_dir / f"{ext_name}.py"
        if not ext_file.exists():
            logger.error(f"扩展文件不存在: {ext_file}")
            return False

        try:
            spec = importlib.util.spec_from_file_location(ext_name, ext_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._loaded_modules.append(mod)

                # 调用扩展初始化函数
                init_fn = getattr(mod, "init", None)
                if init_fn and callable(init_fn):
                    init_fn()

                # 更新状态
                for ext in self.extensions:
                    if ext["file"] == ext_file.name:
                        ext["loaded"] = True

                logger.info(f"扩展已加载: {ext_name}")
                return True
        except Exception as e:
            logger.error(f"扩展加载失败 {ext_name}: {e}")
        return False

    def load_all(self) -> int:
        """加载所有扩展，返回成功数量"""
        self.scan()
        count = 0
        for ext in self.extensions:
            if self.load(ext["file"].replace(".py", "")):
                count += 1
        return count

    def get_api_extensions(self) -> list[dict]:
        """获取所有扩展的 API 信息"""
        result = []
        for mod in self._loaded_modules:
            routes = getattr(mod, "routes", None)
            if routes:
                result.append({
                    "name": getattr(mod, "__name__", "unknown"),
                    "routes": routes,
                })
        return result

# ========== 系统托盘 ==========

try:
    import pystray
    from PIL import Image, ImageDraw
    _has_tray = True
except Exception:
    _has_tray = False
    pystray = None

def create_icon_image() -> "Image.Image":
    """生成托盘图标（蓝色方形）"""
    width = 64
    height = 64
    image = Image.new("RGB", (width, height), color="white")
    dc = ImageDraw.Draw(image)
    # 绘制蓝色方块
    dc.rectangle([8, 8, 56, 56], fill="#2563eb", outline="#1d4ed8", width=2)
    # 绘制 "闲" 字的简化表示（一个点）
    dc.ellipse([24, 24, 40, 40], fill="white")
    return image

def setup_tray(window, port: int):
    """设置系统托盘"""
    if not _has_tray:
        return None

    def on_open(icon, item):
        url = f"http://127.0.0.1:{port}"
        if window:
            try:
                window.show()
            except Exception:
                import webbrowser
                webbrowser.open(url)
        else:
            import webbrowser
            webbrowser.open(url)

    def on_exit(icon, item):
        icon.stop()
        if window:
            try:
                window.destroy()
            except Exception:
                pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", on_open),
        pystray.MenuItem("退出", on_exit),
    )

    icon = pystray.Icon(
        "xianyu-company",
        create_icon_image(),
        "闲鱼一人公司",
        menu,
    )
    return icon

# ========== 主入口 ==========

def main():
    """桌面应用主入口"""
    logger.info("=" * 40)
    logger.info("闲鱼一人公司 - 桌面版启动")
    logger.info(f"数据目录: {DATA_DIR}")
    logger.info(f"配置目录: {CONFIG_DIR}")
    logger.info(f"扩展目录: {EXTENSIONS_DIR}")
    logger.info("=" * 40)

    # 确保项目根目录在 sys.path（用于打包后导入 src 模块）
    project_root = get_resource_path("")
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # 预先检查后端是否能正常导入（Windows 无控制台模式下异常不可见）
    try:
        from src.web.app import app as _app_check  # noqa: F401
    except Exception as e:
        logger.error(f"后端应用导入失败: {e}")
        raise RuntimeError(f"无法加载后端应用，请检查安装包完整性: {e}") from e

    # 复制默认配置文件到用户目录（如果不存在）
    default_config = get_resource_path("config/settings.yaml")
    user_config = CONFIG_DIR / "settings.yaml"
    if default_config.exists() and not user_config.exists():
        import shutil
        shutil.copy(default_config, user_config)
        logger.info(f"已复制默认配置到: {user_config}")

    # 创建示例扩展文件
    sample_ext = EXTENSIONS_DIR / "sample_extension.py"
    if not sample_ext.exists():
        sample_ext.write_text('''"""示例扩展 - 展示扩展功能接口"""
__version__ = "1.0.0"

def init():
    """扩展初始化时调用"""
    print("[扩展] 示例扩展已加载")

# 扩展可以定义额外的路由
routes = [
    {"path": "/ext/sample", "method": "GET", "description": "示例扩展接口"}
]
''', encoding="utf-8")

    # 加载扩展
    ext_manager = ExtensionManager(EXTENSIONS_DIR)
    loaded_count = ext_manager.load_all()
    logger.info(f"已加载 {loaded_count} 个扩展")

    # 启动后台服务
    port = start_backend()
    url = f"http://127.0.0.1:{port}"

    # 创建系统托盘
    tray_icon = None
    if _has_tray:
        tray_icon = setup_tray(None, port)
        if tray_icon:
            tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
            tray_thread.start()
            logger.info("系统托盘已启动")

    # 启动桌面窗口
    logger.info(f"正在打开窗口: {url}")
    if _has_webview:
        try:
            window = webview.create_window(
                "闲鱼一人公司",
                url,
                width=1400,
                height=900,
                min_size=(800, 600),
                text_select=True,
            )

            # 窗口关闭时退出托盘
            def on_closed():
                logger.info("窗口已关闭")
                if tray_icon:
                    tray_icon.stop()

            window.events.closed += on_closed

            webview.start(
                debug=False,
                http_server=False,  # 我们用自带的 uvicorn
            )
            return
        except Exception as e:
            logger.error(f"窗口启动失败: {e}")

    # 回退模式：系统浏览器
    logger.info("回退到系统浏览器模式")
    import webbrowser
    webbrowser.open(url)
    if tray_icon:
        tray_icon.run()  # 阻塞直到托盘退出
    else:
        input("按回车键退出...")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Windows 无控制台模式 (console=False) 下，异常不可见
        # 写入应急崩溃日志到用户桌面或主目录
        try:
            import traceback
            crash_path = Path.home() / ".xianyu-company-crash.log"
            crash_path.write_text(
                f"[{datetime.now().isoformat()}] 启动崩溃\n"
                f"{traceback.format_exc()}\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        raise
