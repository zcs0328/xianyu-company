"""
全局配置加载器
从 config/settings.yaml 读取配置，支持 .env 环境变量覆盖
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 桌面版用户数据目录（由 desktop.py 设置）
_USER_DATA_DIR = os.getenv("XIANYU_DATA_DIR")
USER_DATA_DIR = Path(_USER_DATA_DIR) if _USER_DATA_DIR else PROJECT_ROOT / "data"
USER_CONFIG_DIR = Path(os.getenv("XIANYU_CONFIG_DIR", str(PROJECT_ROOT / "config")))

# 确保数据目录存在
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# 加载 .env（优先用户配置目录，其次项目目录）
_env_user = USER_CONFIG_DIR / ".env"
_env_proj = PROJECT_ROOT / ".env"
load_dotenv(_env_user if _env_user.exists() else _env_proj)


class LLMConfig(BaseModel):
    """单个大模型配置"""
    api_type: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096


class XianyuAccountConfig(BaseModel):
    """闲鱼账号配置"""
    name: str = ""
    cookie_file: str = ""
    ws_token: str = ""
    user_id: str = ""
    max_daily_publish: int = 5
    max_daily_polish: int = 20
    reply_delay_min: int = 2
    reply_delay_max: int = 8
    publish_interval_min: int = 300


class RiskControlConfig(BaseModel):
    """风控配置"""
    global_action_interval: int = 3
    daily_action_limit: int = 100
    auto_pause_on_warning: bool = True


class AppConfig(BaseModel):
    """应用全局配置"""
    # 用 dict 存储灵活的 LLM 配置
    llm: dict[str, LLMConfig] = Field(default_factory=dict)
    xianyu: list[XianyuAccountConfig] = Field(default_factory=list)
    database_url: str = f"sqlite+aiosqlite:///{USER_DATA_DIR}/company.db"
    risk_control: RiskControlConfig = Field(default_factory=RiskControlConfig)
    logging_level: str = "INFO"

    def get_llm(self, name: str) -> LLMConfig:
        """按名称获取模型配置，不存在则返回默认 DeepSeek V3"""
        if name in self.llm:
            cfg = self.llm[name]
            # 从环境变量覆盖 API Key
            if "DEEPSEEK" in name and os.getenv("DEEPSEEK_API_KEY"):
                cfg.api_key = os.getenv("DEEPSEEK_API_KEY")
            if "QWEN" in name and os.getenv("QWEN_API_KEY"):
                cfg.api_key = os.getenv("QWEN_API_KEY")
            if "OLLAMA" in name and os.getenv("OLLAMA_BASE_URL"):
                cfg.base_url = os.getenv("OLLAMA_BASE_URL")
            return cfg
        # 默认返回 DeepSeek V3
        return LLMConfig(
            base_url="https://api.deepseek.com/v1",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model="deepseek-chat",
        )

    @property
    def primary_account(self) -> XianyuAccountConfig | None:
        """获取主账号配置"""
        return self.xianyu[0] if self.xianyu else None


# 全局单例
_config: AppConfig | None = None


def load_config(config_path: str | None = None) -> AppConfig:
    """加载配置文件，优先使用用户配置目录，其次项目目录"""
    global _config

    if config_path is None:
        # 优先级: 用户本地配置 > 用户默认配置 > 项目本地配置 > 项目默认配置
        paths = [
            USER_CONFIG_DIR / "settings.local.yaml",
            USER_CONFIG_DIR / "settings.yaml",
            PROJECT_ROOT / "config" / "settings.local.yaml",
            PROJECT_ROOT / "config" / "settings.yaml",
        ]
        config_path = None
        for p in paths:
            if p.exists():
                config_path = str(p)
                break
        if config_path is None:
            config_path = str(PROJECT_ROOT / "config" / "settings.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # 解析 LLM 配置
    llm_dict: dict[str, LLMConfig] = {}
    for name, cfg in (raw.get("llm") or {}).items():
        llm_dict[name] = LLMConfig(**cfg)

    # 解析闲鱼账号
    xianyu_list = [XianyuAccountConfig(**acc) for acc in (raw.get("xianyu") or [])]

    # 从环境变量补充闲鱼配置
    if xianyu_list:
        acc = xianyu_list[0]
        if not acc.user_id and os.getenv("XIANYU_USER_ID"):
            acc.user_id = os.getenv("XIANYU_USER_ID")
        if not acc.ws_token and os.getenv("XIANYU_WS_TOKEN"):
            acc.ws_token = os.getenv("XIANYU_WS_TOKEN")

    _config = AppConfig(
        llm=llm_dict,
        xianyu=xianyu_list,
        database_url=(raw.get("database") or {}).get("url", "sqlite+aiosqlite:///data/company.db"),
        risk_control=RiskControlConfig(**(raw.get("risk_control") or {})),
        logging_level=(raw.get("logging") or {}).get("level", "INFO"),
    )
    return _config


def get_config() -> AppConfig:
    """获取全局配置（首次调用自动加载）"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
