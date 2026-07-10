"""
总裁 Agent
负责制定经营策略、审阅日报、关键决策拍板
"""

from datetime import date
from loguru import logger

from src.agents.base import BaseAgent
from src.models.database import AgentRole
from src.models.repo import StatsRepo, RiskRepo, TransactionRepo, OrderRepo


class CEOAgent(BaseAgent):
    """总裁智能体"""

    role = AgentRole.CEO
    llm_config_name = "deepseek_v3"
    prompt_file = "ceo.md"

    async def make_strategy(self, market_hint: str = "") -> dict:
        """制定经营策略（每日启动时调用）"""
        logger.info("[总裁] 制定今日经营策略...")

        # 收集昨日数据
        account = "主号"
        yesterday = date.today().isoformat()
        daily_report = await StatsRepo.get_daily_report(account, yesterday)
        risk_warnings = await RiskRepo.get_recent_warnings(hours=24)

        context = f"""
今日日期: {yesterday}
昨日运营数据: {daily_report}
近期风控告警数: {len(risk_warnings)}
市场提示: {market_hint or "无"}

请制定今日经营策略，包括选品方向和利润目标。
"""
        result = await self.think_json(context, action_name="make_strategy")
        logger.info(f"[总裁] 策略已制定: {result}")
        return result

    async def review_daily_report(self) -> str:
        """审阅日报并给出指示"""
        logger.info("[总裁] 审阅日报...")
        account = "主号"
        report = await StatsRepo.get_daily_report(account)
        tx_summary = await TransactionRepo.get_daily_summary(account)
        escrow = await TransactionRepo.get_escrow_pending(account)

        context = f"""
今日日报:
{report}

资金状态:
- 担保交易冻结订单: {escrow}

请审阅并给出指示。
"""
        result = await self.think(context, action_name="review_daily_report")
        logger.info(f"[总裁] 日报审阅完成")
        return result

    async def decide_on_risk(self, risk_info: dict) -> dict:
        """对风控告警做决策"""
        logger.info(f"[总裁] 处理风控告警: {risk_info.get('category', '')}")
        context = f"""
收到风控告警:
{risk_info}

请决定如何处理（继续运营/暂停/调整策略）。
"""
        result = await self.think_json(context, action_name="decide_on_risk")
        return result
