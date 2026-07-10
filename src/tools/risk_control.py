"""
风控工具：频率控制、操作节奏模拟、异常检测
"""

import asyncio
import time
from datetime import datetime, date
from typing import Any

from loguru import logger

from src.config import get_config
from src.models.database import RiskLevel
from src.models.repo import RiskRepo, StatsRepo, OrderRepo, TransactionRepo


class RateLimiter:
    """频率控制器：确保操作节奏模拟人类行为"""

    def __init__(self, account_name: str):
        self.account_name = account_name
        self._last_action_time: float = 0.0
        self._action_history: list[dict] = []  # 当日操作历史

    async def acquire(self, action_type: str, min_interval: float | None = None) -> bool:
        """
        获取操作许可
        返回 True 表示可以执行，False 表示被风控拒绝
        """
        config = get_config()
        if min_interval is None:
            min_interval = config.risk_control.global_action_interval

        now = time.time()
        elapsed = now - self._last_action_time
        if elapsed < min_interval:
            wait = min_interval - elapsed
            logger.debug(f"[风控] 等待 {wait:.1f}s 满足操作间隔")
            await asyncio.sleep(wait)

        # 检查当日操作总量
        today_actions = len([a for a in self._action_history
                           if a["time"] > now - 86400])
        if today_actions >= config.risk_control.daily_action_limit:
            await RiskRepo.log(
                level=RiskLevel.CRITICAL,
                category="daily_limit_exceeded",
                message=f"当日操作数 {today_actions} 超过上限 {config.risk_control.daily_action_limit}",
                account_name=self.account_name,
                action_taken="拒绝操作",
                auto_paused=True,
            )
            logger.warning(f"[风控] 当日操作超限，拒绝 {action_type}")
            return False

        self._last_action_time = time.time()
        self._action_history.append({"type": action_type, "time": self._last_action_time})
        # 清理超过24小时的历史
        self._action_history = [a for a in self._action_history if a["time"] > now - 86400]
        return True

    async def check_publish_limit(self) -> bool:
        """检查今日发布是否超限"""
        config = get_config()
        account = config.primary_account
        if not account:
            return True
        # 从统计数据获取今日发布数
        stat = await StatsRepo.get_or_create_today(self.account_name)
        if stat.items_published >= account.max_daily_publish:
            await RiskRepo.log(
                level=RiskLevel.WARNING,
                category="publish_limit",
                message=f"今日已发布 {stat.items_published} 件，超过上限 {account.max_daily_publish}",
                account_name=self.account_name,
                action_taken="阻止发布",
            )
            logger.warning(f"[风控] 今日发布已达上限 {account.max_daily_publish}")
            return False
        return True

    async def check_polish_limit(self) -> bool:
        """检查今日擦亮是否超限"""
        config = get_config()
        account = config.primary_account
        if not account:
            return True
        stat = await StatsRepo.get_or_create_today(self.account_name)
        if stat.items_polished >= account.max_daily_polish:
            await RiskRepo.log(
                level=RiskLevel.WARNING,
                category="polish_limit",
                message=f"今日已擦亮 {stat.items_polished} 次，超过上限 {account.max_daily_polish}",
                account_name=self.account_name,
            )
            return False
        return True

    async def record_publish(self):
        await StatsRepo.increment(self.account_name, items_published=1)

    async def record_polish(self):
        await StatsRepo.increment(self.account_name, items_polished=1)


class RiskMonitor:
    """风控监控器：异常检测、自动暂停"""

    _paused_accounts: set[str] = set()

    @classmethod
    def is_paused(cls, account_name: str) -> bool:
        return account_name in cls._paused_accounts

    @classmethod
    def pause(cls, account_name: str, reason: str):
        cls._paused_accounts.add(account_name)
        logger.warning(f"[风控] 账号 {account_name} 已暂停: {reason}")

    @classmethod
    def resume(cls, account_name: str):
        cls._paused_accounts.discard(account_name)
        logger.info(f"[风控] 账号 {account_name} 已恢复")

    @classmethod
    async def check_account_health(cls, account_name: str) -> dict:
        """检查账号健康状况"""
        warnings = await RiskRepo.get_recent_warnings(hours=24)
        today_warnings = await RiskRepo.count_today_actions(account_name)

        health = {
            "account": account_name,
            "paused": cls.is_paused(account_name),
            "warnings_24h": len(warnings),
            "warnings_today": today_warnings,
            "critical_count": sum(1 for w in warnings if w.level == RiskLevel.CRITICAL),
            "recommendation": "正常",
        }

        # 自动暂停逻辑
        config = get_config()
        if config.risk_control.auto_pause_on_warning:
            if health["critical_count"] > 0:
                cls.pause(account_name, "检测到严重风控告警")
                health["recommendation"] = "已自动暂停，需人工检查"
            elif today_warnings >= 5:
                cls.pause(account_name, f"今日风控告警 {today_warnings} 次")
                health["recommendation"] = "告警过多，已暂停"

        return health

    @classmethod
    async def check_escrow_cashflow(cls, account_name: str) -> dict:
        """监控担保交易资金周转（无货源垫资风险）"""
        pending = await TransactionRepo.get_escrow_pending(account_name)
        total_frozen = sum(o["sell_price"] for o in pending)
        overdue = [o for o in pending if o["days_since_ship"] > 10]

        result = {
            "frozen_orders": len(pending),
            "frozen_amount": total_frozen,
            "overdue_orders": len(overdue),
            "overdue_amount": sum(o["sell_price"] for o in overdue),
            "warning": False,
        }

        if overdue:
            result["warning"] = True
            await RiskRepo.log(
                level=RiskLevel.WARNING,
                category="escrow_overdue",
                message=f"{len(overdue)} 笔订单超过10天未放款，涉及金额 ¥{result['overdue_amount']:.2f}",
                account_name=account_name,
                action_taken="已记录，需人工跟进",
            )
            logger.warning(f"[风控] {account_name} 有 {len(overdue)} 笔订单超期未放款")

        return result
