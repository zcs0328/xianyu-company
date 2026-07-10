"""
多账号矩阵管理器
管理多个闲鱼账号，轮换发布、独立风控、均衡流量分配
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

from loguru import logger

from src.config import get_config, XianyuAccountConfig
from src.models.repo import StatsRepo, RiskRepo
from src.models.database import RiskLevel
from src.tools.risk_control import RateLimiter, RiskMonitor


@dataclass
class AccountState:
    """单账号运行状态"""
    name: str
    config: XianyuAccountConfig
    rate_limiter: RateLimiter
    is_active: bool = True
    is_paused: bool = False
    today_publish_count: int = 0
    today_polish_count: int = 0
    total_published: int = 0
    last_publish_time: datetime | None = None
    health_score: float = 100.0  # 0-100，低于阈值自动降级

    @property
    def can_publish(self) -> bool:
        """是否可以继续发布"""
        if not self.is_active or self.is_paused:
            return False
        if self.today_publish_count >= self.config.max_daily_publish:
            return False
        if self.health_score < 30:
            return False
        return True

    @property
    def can_polish(self) -> bool:
        """是否可以继续擦亮"""
        if not self.is_active or self.is_paused:
            return False
        if self.today_polish_count >= self.config.max_daily_polish:
            return False
        return True


class AccountManager:
    """
    多账号矩阵管理器
    - 轮换发布：优先选择健康度最高、当日发布最少的账号
    - 独立风控：每个账号独立的风控状态和频率控制
    - 均衡分配：确保各账号发布量均衡，避免单号过载
    """

    def __init__(self):
        self._accounts: dict[str, AccountState] = {}
        self._init_accounts()

    def _init_accounts(self):
        """从配置初始化所有账号"""
        config = get_config()
        for acc_config in config.xianyu:
            state = AccountState(
                name=acc_config.name or f"账号{len(self._accounts)+1}",
                config=acc_config,
                rate_limiter=RateLimiter(acc_config.name or f"账号{len(self._accounts)+1}"),
            )
            self._accounts[state.name] = state
            logger.info(f"[账号管理] 注册账号: {state.name}")

        # 如果没有配置任何账号，创建一个默认主号
        if not self._accounts:
            default = XianyuAccountConfig(name="主号")
            state = AccountState(name="主号", config=default, rate_limiter=RateLimiter("主号"))
            self._accounts["主号"] = state
            logger.info("[账号管理] 无账号配置，使用默认主号")

    @property
    def accounts(self) -> list[AccountState]:
        return list(self._accounts.values())

    @property
    def active_accounts(self) -> list[AccountState]:
        return [a for a in self._accounts.values() if a.is_active and not a.is_paused]

    def get_account(self, name: str) -> AccountState | None:
        return self._accounts.get(name)

    def select_publish_account(self) -> AccountState | None:
        """
        选择最佳发布账号
        策略：健康度 > 60 且可发布的账号中，选当日发布最少的
        """
        candidates = [a for a in self.active_accounts if a.can_publish]
        if not candidates:
            logger.warning("[账号管理] 无可用发布账号")
            return None

        # 按当日发布数升序，健康度降序排列
        candidates.sort(key=lambda a: (a.today_publish_count, -a.health_score))
        selected = candidates[0]
        logger.info(
            f"[账号管理] 选择发布账号: {selected.name} "
            f"(今日已发{selected.today_publish_count}/{selected.config.max_daily_publish}, "
            f"健康度{selected.health_score:.0f})"
        )
        return selected

    def select_polish_account(self, item_account: str | None = None) -> AccountState | None:
        """
        选择擦亮账号
        :param item_account: 商品所属账号（优先在同一账号擦亮）
        """
        if item_account:
            acc = self._accounts.get(item_account)
            if acc and acc.can_polish:
                return acc

        candidates = [a for a in self.active_accounts if a.can_polish]
        if not candidates:
            return None
        candidates.sort(key=lambda a: a.today_polish_count)
        return candidates[0]

    async def record_publish(self, account_name: str):
        """记录发布操作"""
        acc = self._accounts.get(account_name)
        if acc:
            acc.today_publish_count += 1
            acc.total_published += 1
            acc.last_publish_time = datetime.now()
            await acc.rate_limiter.record_publish()
            await StatsRepo.increment(account_name, items_published=1)
            logger.debug(f"[账号管理] {account_name} 今日发布 {acc.today_publish_count}/{acc.config.max_daily_publish}")

    async def record_polish(self, account_name: str):
        """记录擦亮操作"""
        acc = self._accounts.get(account_name)
        if acc:
            acc.today_polish_count += 1
            await acc.rate_limiter.record_polish()
            await StatsRepo.increment(account_name, items_polished=1)

    def pause_account(self, name: str, reason: str):
        """暂停账号"""
        acc = self._accounts.get(name)
        if acc:
            acc.is_paused = True
            logger.warning(f"[账号管理] 账号 {name} 已暂停: {reason}")

    def resume_account(self, name: str):
        """恢复账号"""
        acc = self._accounts.get(name)
        if acc:
            acc.is_paused = False
            acc.health_score = min(acc.health_score + 20, 100)
            logger.info(f"[账号管理] 账号 {name} 已恢复，健康度重置为 {acc.health_score:.0f}")

    async def update_health_scores(self):
        """更新所有账号的健康分数"""
        for name, acc in self._accounts.items():
            if not acc.is_active:
                continue

            score = 100.0
            # 检查风控告警
            warnings = await RiskRepo.get_recent_warnings(hours=24)
            account_warnings = [w for w in warnings if w.account_name == name]
            critical_count = sum(1 for w in account_warnings if w.level == RiskLevel.CRITICAL)
            warning_count = sum(1 for w in account_warnings if w.level == RiskLevel.WARNING)

            score -= critical_count * 30
            score -= warning_count * 10

            # 检查是否被 RiskMonitor 暂停
            if RiskMonitor.is_paused(name):
                score -= 50
                acc.is_paused = True

            acc.health_score = max(0, min(100, score))

            # 健康度太低自动暂停
            if acc.health_score < 20 and not acc.is_paused:
                self.pause_account(name, f"健康度过低({acc.health_score:.0f})")

    async def daily_reset(self):
        """每日重置（凌晨调用）"""
        for acc in self._accounts.values():
            acc.today_publish_count = 0
            acc.today_polish_count = 0
            # 恢复被暂停的账号（除了严重违规的）
            if acc.is_paused and acc.health_score >= 30:
                acc.is_paused = False
                logger.info(f"[账号管理] {acc.name} 每日重置恢复运行")

    def get_matrix_status(self) -> dict[str, Any]:
        """获取矩阵状态概览"""
        return {
            "total_accounts": len(self._accounts),
            "active_accounts": len(self.active_accounts),
            "accounts": [
                {
                    "name": acc.name,
                    "active": acc.is_active,
                    "paused": acc.is_paused,
                    "health_score": round(acc.health_score, 1),
                    "today_publish": f"{acc.today_publish_count}/{acc.config.max_daily_publish}",
                    "today_polish": f"{acc.today_polish_count}/{acc.config.max_daily_polish}",
                    "total_published": acc.total_published,
                    "can_publish": acc.can_publish,
                    "can_polish": acc.can_polish,
                }
                for acc in self._accounts.values()
            ],
        }
