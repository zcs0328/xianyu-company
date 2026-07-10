"""
智能体基类
提供统一的 LLM 调用、提示词加载、日志记录能力
"""

import time
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import PROJECT_ROOT
from src.models.database import AgentRole
from src.tools.llm_client import get_llm


class BaseAgent:
    """所有智能体的基类"""

    role: AgentRole = AgentRole.OPERATIONS
    llm_config_name: str = "deepseek_v3"
    prompt_file: str = ""

    def __init__(self):
        self.llm = get_llm()
        self._prompt: str | None = None

    @property
    def prompt(self) -> str:
        """加载提示词模板（懒加载）"""
        if self._prompt is None:
            path = PROJECT_ROOT / "config" / "prompts" / self.prompt_file
            if path.exists():
                self._prompt = path.read_text(encoding="utf-8")
            else:
                logger.warning(f"提示词文件不存在: {path}")
                self._prompt = f"你是{self.role.value}智能体。"
        return self._prompt

    async def think(self, user_content: str, action_name: str = "") -> str:
        """调用 LLM 进行思考"""
        return await self.llm.chat_with_system(
            system_prompt=self.prompt,
            user_content=user_content,
            config_name=self.llm_config_name,
            role=self.role,
            action_name=action_name,
        )

    async def think_json(self, user_content: str, action_name: str = "") -> dict:
        """调用 LLM 并解析 JSON 响应"""
        raw = await self.think(user_content, action_name)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 LLM 输出中提取 JSON"""
        import json
        import re

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 代码块
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试从任意位置提取JSON对象（找最后一个完整的JSON对象）
        matches = list(re.finditer(r"(\{(?:[^{}]|(?:\{[^{}]*\}))*\})", text))
        for match in reversed(matches):
            try:
                return json.loads(match.group(0).strip())
            except json.JSONDecodeError:
                pass

        # 尝试找数组
        matches = list(re.finditer(r"(\[(?:[^\[\]]|(?:\[[^\[\]]*\]))*\])", text))
        for match in reversed(matches):
            try:
                arr = json.loads(match.group(0).strip())
                # 如果成功解析为数组，包装成 {result: arr} 格式
                if isinstance(arr, list):
                    return {"result": arr, "list": arr}
            except json.JSONDecodeError:
                pass

        logger.warning(f"JSON 解析失败，返回原始文本: {text[:200]}")
        return {"raw": text, "error": "json_parse_failed"}
