"""
风控 Agent
监控操作频率、检测异常、自动暂停、模拟人类节奏
"""

from datetime import datetime, date
from loguru import logger

from src.agents.base import BaseAgent
from src.config import get_config
from src.models.database import AgentRole, RiskLevel
from src.models.repo import RiskRepo, StatsRepo
from src.tools.risk_control import RateLimiter, RiskMonitor


class RiskControlAgent(BaseAgent):
    """风控智能体"""

    role = AgentRole.RISK_CONTROL
    llm_config_name = "deepseek_r1"  # 风控需要严谨推理
    prompt_file = "risk_control.md"

    async def daily_health_check(self, account_name: str = "主号") -> dict:
        """每日健康检查（启动时和定时执行）"""
        logger.info(f"[风控] 执行账号健康检查: {account_name}")

        # 基础健康数据
        health = await RiskMonitor.check_account_health(account_name)

        # 担保交易资金检查
        from src.tools.risk_control import RiskMonitor as RM
        escrow = await RM.check_escrow_cashflow(account_name)

        # 统计今日操作
        stat = await StatsRepo.get_or_create_today(account_name)
        today_actions = stat.items_published + stat.items_polished + stat.messages_replied

        # 构造 LLM 分析输入
        context = f"""
账号: {account_name}
日期: {date.today().isoformat()}

今日操作统计:
- 发布商品: {stat.items_published}
- 擦亮商品: {stat.items_polished}
- 收到消息: {stat.messages_received}
- 回复消息: {stat.messages_replied}
- 创建订单: {stat.orders_created}
- 风控告警: {stat.risk_warnings}

账号状态:
- 是否暂停: {health['paused']}
- 24h内告警: {health['warnings_24h']}
- 严重告警: {health['critical_count']}

担保交易:
- 冻结订单: {escrow['frozen_orders']}笔 ¥{escrow['frozen_amount']:.2f}
- 超期未放款: {escrow['overdue_orders']}笔

请评估账号健康状况，返回 JSON:
{{
  "level": "info|warning|critical",
  "category": "风险类别",
  "message": "具体描述",
  "action": "continue|pause|stop",
  "recommendation": "建议措施"
}}
"""
        result = await self.think_json(context, action_name="daily_health_check")

        # 根据建议执行操作
        action = result.get("action", "continue")
        level_str = result.get("level", "info")

        level_map = {
            "info": RiskLevel.INFO,
            "warning": RiskLevel.WARNING,
            "critical": RiskLevel.CRITICAL,
        }
        level = level_map.get(level_str, RiskLevel.INFO)

        await RiskRepo.log(
            level=level,
            category=result.get("category", "health_check"),
            message=result.get("message", ""),
            account_name=account_name,
            action_taken=action,
            auto_paused=(action in ("pause", "stop")),
        )

        if action in ("pause", "stop"):
            RiskMonitor.pause(account_name, result.get("message", "风控建议暂停"))
            await StatsRepo.increment(account_name, auto_paused_count=1)

        await StatsRepo.increment(account_name, risk_warnings=1 if level != RiskLevel.INFO else 0)

        logger.info(f"[风控] 健康检查完成: level={level_str} action={action}")
        return result

    async def check_before_publish(self, account_name: str = "主号") -> bool:
        """发布前检查"""
        rate_limiter = RateLimiter(account_name)
        if not await rate_limiter.check_publish_limit():
            return False
        if RiskMonitor.is_paused(account_name):
            logger.warning("[风控] 账号已暂停，拒绝发布")
            return False
        return True

    async def check_before_polish(self, account_name: str = "主号") -> bool:
        """擦亮前检查"""
        rate_limiter = RateLimiter(account_name)
        if not await rate_limiter.check_polish_limit():
            return False
        if RiskMonitor.is_paused(account_name):
            return False
        return True

    async def detect_anomaly(self, metrics: dict) -> dict | None:
        """异常检测（流量骤降、操作失败率高等）"""
        context = f"""
运营指标:
{metrics}

请判断是否存在异常，返回 JSON（无异常则 action=continue）。
"""
        result = await self.think_json(context, action_name="detect_anomaly")
        if result.get("action") in ("pause", "stop"):
            logger.warning(f"[风控] 检测到异常: {result.get('message', '')}")
        return result if result.get("level") != "info" else None
