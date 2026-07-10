"""
异步数据库管理器
基于 SQLAlchemy 2.0 + aiosqlite
"""

import asyncio
from datetime import datetime, date
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_config, PROJECT_ROOT
from src.models.database import (
    Base, XianyuMessage, XianyuOrder, Transaction, RiskLog,
    DailyStats, AgentLog, MessageDirection, OrderStatus,
    TransactionType, RiskLevel, AgentRole,
)


class Database:
    """异步数据库管理器"""

    def __init__(self):
        self._engine = None
        self._session_factory = None

    async def init(self):
        """初始化数据库连接和表结构"""
        config = get_config()
        db_url = config.database_url
        # 确保 SQLite 目录存在
        if "sqlite" in db_url:
            db_path = db_url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._engine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )
        # 创建所有表
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self):
        if self._engine:
            await self._engine.dispose()

    @property
    def session(self):
        return self._session_factory


# 全局单例
_db: Database | None = None


async def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        await _db.init()
    return _db


# ========== 消息仓库 ==========

class MessageRepo:
    """闲鱼消息数据访问"""

    @staticmethod
    async def save_message(msg: XianyuMessage) -> XianyuMessage:
        db = await get_db()
        async with db.session() as session:
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            return msg

    @staticmethod
    async def get_unreplied(limit: int = 50) -> Sequence[XianyuMessage]:
        """获取未回复的入站消息"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuMessage)
                .where(
                    XianyuMessage.direction == MessageDirection.INCOMING,
                    XianyuMessage.replied_at.is_(None),
                )
                .order_by(XianyuMessage.created_at)
                .limit(limit)
            )
            return result.scalars().all()

    @staticmethod
    async def mark_replied(message_id: str, ai_reply: str):
        db = await get_db()
        async with db.session() as session:
            await session.execute(
                update(XianyuMessage)
                .where(XianyuMessage.message_id == message_id)
                .values(
                    ai_reply=ai_reply,
                    ai_processed=True,
                    replied_at=datetime.utcnow(),
                )
            )
            await session.commit()

    @staticmethod
    async def get_conversation_history(buyer_id: str, account_name: str, limit: int = 20) -> Sequence[XianyuMessage]:
        """获取与某买家的对话历史"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuMessage)
                .where(
                    XianyuMessage.buyer_id == buyer_id,
                    XianyuMessage.account_name == account_name,
                )
                .order_by(XianyuMessage.created_at.desc())
                .limit(limit)
            )
            return list(reversed(result.scalars().all()))


# ========== 订单仓库 ==========

class OrderRepo:
    """闲鱼订单数据访问"""

    @staticmethod
    async def create_order(order: XianyuOrder) -> XianyuOrder:
        db = await get_db()
        async with db.session() as session:
            session.add(order)
            await session.commit()
            await session.refresh(order)
            return order

    @staticmethod
    async def get_by_order_id(order_id: str) -> XianyuOrder | None:
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder).where(XianyuOrder.order_id == order_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def update_status(order_id: str, status: OrderStatus, **extra):
        db = await get_db()
        async with db.session() as session:
            values = {"status": status, "updated_at": datetime.utcnow()}
            values.update(extra)
            # 状态时间线
            now = datetime.utcnow()
            if status == OrderStatus.PAID:
                values["paid_at"] = now
            elif status == OrderStatus.SHIPPED:
                values["shipped_at"] = now
            elif status == OrderStatus.RECEIVED:
                values["received_at"] = now
            elif status == OrderStatus.COMPLETED:
                values["completed_at"] = now
                values["escrow_released"] = True
            await session.execute(
                update(XianyuOrder).where(XianyuOrder.order_id == order_id).values(**values)
            )
            await session.commit()

    @staticmethod
    async def get_pending_shipment() -> Sequence[XianyuOrder]:
        """获取待发货订单（已付款未发货）"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder)
                .where(XianyuOrder.status == OrderStatus.PAID)
                .order_by(XianyuOrder.paid_at)
            )
            return result.scalars().all()

    @staticmethod
    async def get_pending_payment_release() -> Sequence[XianyuOrder]:
        """获取已发货待放款的订单（追踪担保交易回款）"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder)
                .where(
                    XianyuOrder.status == OrderStatus.SHIPPED,
                    XianyuOrder.escrow_released == False,
                )
                .order_by(XianyuOrder.shipped_at)
            )
            return result.scalars().all()

    @staticmethod
    async def calculate_profit(order_id: str) -> float:
        """计算订单利润 = 售价 - 货源价 - 运费 - 平台费"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder).where(XianyuOrder.order_id == order_id)
            )
            order = result.scalar_one_or_none()
            if not order:
                return 0.0
            profit = order.sell_price - order.source_price - order.shipping_cost - order.platform_fee
            await session.execute(
                update(XianyuOrder)
                .where(XianyuOrder.order_id == order_id)
                .values(profit=profit)
            )
            await session.commit()
            return profit

    @staticmethod
    async def is_processed(order_id: str) -> bool:
        """检查订单是否已处理（已存在于数据库且非初始状态）"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder).where(XianyuOrder.order_id == order_id)
            )
            order = result.scalar_one_or_none()
            return order is not None and order.source_platform != ""

    @staticmethod
    async def mark_processed(order_id: str):
        """标记订单已处理（更新为已发货状态）"""
        db = await get_db()
        async with db.session() as session:
            await session.execute(
                update(XianyuOrder)
                .where(XianyuOrder.order_id == order_id)
                .values(
                    status=OrderStatus.SHIPPED,
                    source_platform="mock",
                    shipped_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()


# ========== 交易仓库 ==========

class TransactionRepo:
    """交易记录数据访问"""

    @staticmethod
    async def record(tx: Transaction) -> Transaction:
        db = await get_db()
        async with db.session() as session:
            session.add(tx)
            await session.commit()
            await session.refresh(tx)
            return tx

    @staticmethod
    async def get_daily_summary(account_name: str, stat_date: str | None = None) -> dict:
        """获取某账号某日收支汇总"""
        if stat_date is None:
            stat_date = date.today().isoformat()
        db = await get_db()
        async with db.session() as session:
            # 当日所有交易
            result = await session.execute(
                select(Transaction)
                .where(
                    Transaction.account_name == account_name,
                    func.date(Transaction.created_at) == stat_date,
                )
            )
            txs = result.scalars().all()

        revenue = sum(t.amount for t in txs if t.transaction_type == TransactionType.INCOME)
        cost = sum(t.amount for t in txs if t.transaction_type in (TransactionType.COST, TransactionType.SHIPPING))
        fees = sum(t.amount for t in txs if t.transaction_type == TransactionType.PLATFORM_FEE)
        refunds = sum(t.amount for t in txs if t.transaction_type == TransactionType.REFUND)

        return {
            "date": stat_date,
            "account": account_name,
            "revenue": revenue,
            "cost": cost,
            "platform_fees": fees,
            "refunds": refunds,
            "net_profit": revenue - cost - fees - refunds,
            "transaction_count": len(txs),
        }

    @staticmethod
    async def get_escrow_pending(account_name: str) -> list[dict]:
        """获取担保交易中待放款的订单（资金周转监控）"""
        orders = await OrderRepo.get_pending_payment_release()
        return [
            {
                "order_id": o.order_id,
                "item_title": o.item_title,
                "sell_price": o.sell_price,
                "shipped_at": o.shipped_at.isoformat() if o.shipped_at else "",
                "days_since_ship": (datetime.utcnow() - o.shipped_at).days if o.shipped_at else 0,
            }
            for o in orders
            if o.account_name == account_name
        ]


# ========== 风控仓库 ==========

class RiskRepo:
    """风控日志数据访问"""

    @staticmethod
    async def log(
        level: RiskLevel,
        category: str,
        message: str,
        account_name: str = "",
        action_taken: str = "",
        auto_paused: bool = False,
    ) -> RiskLog:
        db = await get_db()
        async with db.session() as session:
            entry = RiskLog(
                account_name=account_name,
                level=level,
                category=category,
                message=message,
                action_taken=action_taken,
                auto_paused=auto_paused,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    @staticmethod
    async def get_recent_warnings(hours: int = 24) -> Sequence[RiskLog]:
        db = await get_db()
        async with db.session() as session:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            result = await session.execute(
                select(RiskLog)
                .where(RiskLog.level.in_([RiskLevel.WARNING, RiskLevel.CRITICAL]))
                .where(RiskLog.created_at >= cutoff)
                .order_by(RiskLog.created_at.desc())
            )
            return result.scalars().all()

    @staticmethod
    async def count_today_actions(account_name: str) -> int:
        """统计今日风控告警次数"""
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(func.count(RiskLog.id))
                .where(
                    RiskLog.account_name == account_name,
                    func.date(RiskLog.created_at) == date.today().isoformat(),
                )
            )
            return result.scalar() or 0


# ========== 统计仓库 ==========

class StatsRepo:
    """每日统计数据访问"""

    @staticmethod
    async def get_or_create_today(account_name: str) -> DailyStats:
        db = await get_db()
        today = date.today().isoformat()
        async with db.session() as session:
            result = await session.execute(
                select(DailyStats).where(
                    DailyStats.account_name == account_name,
                    DailyStats.stat_date == today,
                )
            )
            stat = result.scalar_one_or_none()
            if stat is None:
                stat = DailyStats(account_name=account_name, stat_date=today)
                session.add(stat)
                await session.commit()
                await session.refresh(stat)
            return stat

    @staticmethod
    async def increment(account_name: str, **fields):
        """递增统计字段"""
        db = await get_db()
        today = date.today().isoformat()
        async with db.session() as session:
            # 确保记录存在
            result = await session.execute(
                select(DailyStats).where(
                    DailyStats.account_name == account_name,
                    DailyStats.stat_date == today,
                )
            )
            stat = result.scalar_one_or_none()
            if stat is None:
                stat = DailyStats(account_name=account_name, stat_date=today)
                session.add(stat)
                await session.flush()

            for field, value in fields.items():
                current = getattr(stat, field, 0) or 0
                setattr(stat, field, current + value)

            await session.commit()

    @staticmethod
    async def get_daily_report(account_name: str, stat_date: str | None = None) -> dict:
        """生成日报"""
        if stat_date is None:
            stat_date = date.today().isoformat()
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(DailyStats).where(
                    DailyStats.account_name == account_name,
                    DailyStats.stat_date == stat_date,
                )
            )
            stat = result.scalar_one_or_none()

        if stat is None:
            return {"date": stat_date, "account": account_name, "message": "当日无数据"}

        tx_summary = await TransactionRepo.get_daily_summary(account_name, stat_date)
        return {
            "date": stat_date,
            "account": account_name,
            "publishing": {
                "items_published": stat.items_published,
                "items_polished": stat.items_polished,
            },
            "customer_service": {
                "messages_received": stat.messages_received,
                "messages_replied": stat.messages_replied,
                "avg_reply_time_sec": round(stat.avg_reply_time_sec, 1),
            },
            "orders": {
                "created": stat.orders_created,
                "completed": stat.orders_completed,
            },
            "finance": tx_summary,
            "risk": {
                "warnings": stat.risk_warnings,
                "auto_paused": stat.auto_paused_count,
            },
        }


# ========== Agent 日志仓库 ==========

class AgentLogRepo:
    """智能体操作日志"""

    @staticmethod
    async def log(
        role: AgentRole,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        llm_model: str = "",
        tokens_used: int = 0,
        cost_yuan: float = 0.0,
        duration_sec: float = 0.0,
        success: bool = True,
        error_message: str = "",
    ) -> AgentLog:
        db = await get_db()
        async with db.session() as session:
            entry = AgentLog(
                role=role, action=action,
                input_summary=input_summary, output_summary=output_summary,
                llm_model=llm_model, tokens_used=tokens_used,
                cost_yuan=cost_yuan, duration_sec=duration_sec,
                success=success, error_message=error_message,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    @staticmethod
    async def get_recent(limit: int = 50, role: AgentRole | None = None) -> Sequence[AgentLog]:
        """获取最近的 Agent 日志，可按角色过滤"""
        db = await get_db()
        async with db.session() as session:
            stmt = select(AgentLog).order_by(AgentLog.id.desc()).limit(limit)
            if role is not None:
                stmt = stmt.where(AgentLog.role == role)
            result = await session.execute(stmt)
            return result.scalars().all()

    @staticmethod
    async def get_by_role(role: AgentRole, limit: int = 50) -> Sequence[AgentLog]:
        """获取指定角色的日志"""
        return await AgentLogRepo.get_recent(limit=limit, role=role)

    @staticmethod
    async def get_stats(hours: int = 24) -> dict:
        """获取 Agent 操作统计（近 N 小时）"""
        db = await get_db()
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        async with db.session() as session:
            # 总调用数
            result = await session.execute(
                select(
                    AgentLog.role,
                    func.count(AgentLog.id).label("count"),
                    func.sum(AgentLog.tokens_used).label("tokens"),
                    func.sum(AgentLog.cost_yuan).label("cost"),
                    func.avg(AgentLog.duration_sec).label("avg_duration"),
                )
                .where(AgentLog.created_at >= cutoff)
                .group_by(AgentLog.role)
            )
            rows = result.all()

        stats = {}
        for row in rows:
            stats[row.role.value] = {
                "count": row.count,
                "tokens": row.tokens or 0,
                "cost_yuan": round(row.cost or 0, 4),
                "avg_duration_sec": round(row.avg_duration or 0, 2),
            }
        return stats
