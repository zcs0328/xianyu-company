"""
会计 Agent
自动记账、担保交易追踪、资金周转监控、日报生成
"""

from datetime import date, datetime, timedelta
from loguru import logger

from src.agents.base import BaseAgent
from src.models.database import (
    AgentRole, OrderStatus, Transaction, TransactionType, XianyuOrder
)
from src.models.repo import (
    OrderRepo, TransactionRepo, StatsRepo, RiskRepo
)


class AccountingAgent(BaseAgent):
    """会计智能体"""

    role = AgentRole.ACCOUNTING
    llm_config_name = "deepseek_r1"  # 会计需要精确计算
    prompt_file = "accounting.md"

    async def record_income(self, order_id: str, amount: float, account_name: str = "主号"):
        """记录收入（担保资金放款到账时）"""
        await TransactionRepo.record(Transaction(
            order_id=order_id,
            account_name=account_name,
            transaction_type=TransactionType.INCOME,
            amount=amount,
            description=f"闲鱼担保交易放款: 订单{order_id}",
            escrow_status="released",
        ))
        logger.info(f"[会计] 记录收入: 订单{order_id} ¥{amount}")

    async def record_cost(self, order_id: str, amount: float, description: str = "", account_name: str = "主号"):
        """记录成本（货源采购）"""
        await TransactionRepo.record(Transaction(
            order_id=order_id,
            account_name=account_name,
            transaction_type=TransactionType.COST,
            amount=amount,
            description=description or f"货源采购: 订单{order_id}",
            escrow_status="frozen",
        ))
        logger.info(f"[会计] 记录成本: 订单{order_id} ¥{amount}")

    async def record_shipping(self, order_id: str, amount: float, account_name: str = "主号"):
        """记录运费"""
        await TransactionRepo.record(Transaction(
            order_id=order_id,
            account_name=account_name,
            transaction_type=TransactionType.SHIPPING,
            amount=amount,
            description=f"运费: 订单{order_id}",
        ))

    async def record_platform_fee(self, order_id: str, sell_price: float, account_name: str = "主号"):
        """记录平台手续费"""
        # 普通卖家 0.6%，鱼小铺 1.6%
        fee_rate = 0.006
        fee = round(sell_price * fee_rate, 2)
        await TransactionRepo.record(Transaction(
            order_id=order_id,
            account_name=account_name,
            transaction_type=TransactionType.PLATFORM_FEE,
            amount=fee,
            description=f"闲鱼手续费({fee_rate*100}%): 订单{order_id}",
        ))
        return fee

    async def check_escrow_status(self, account_name: str = "主号") -> dict:
        """
        检查担保交易回款状态
        追踪所有"已发货未放款"的订单
        """
        pending = await OrderRepo.get_pending_payment_release()
        now = datetime.utcnow()

        results = {
            "total_frozen": 0,
            "total_amount": 0.0,
            "overdue": [],
            "normal": [],
        }

        for order in pending:
            if order.account_name != account_name:
                continue
            days = (now - order.shipped_at).days if order.shipped_at else 0
            info = {
                "order_id": order.order_id,
                "item_title": order.item_title,
                "sell_price": order.sell_price,
                "shipped_at": order.shipped_at.isoformat() if order.shipped_at else "",
                "days_since_ship": days,
            }
            results["total_frozen"] += 1
            results["total_amount"] += order.sell_price

            if days > 10:
                results["overdue"].append(info)
                # 记录风控告警
                await RiskRepo.log(
                    level="warning",
                    category="escrow_overdue",
                    message=f"订单{order.order_id}发货{days}天未放款，金额¥{order.sell_price}",
                    account_name=account_name,
                )
            else:
                results["normal"].append(info)

        logger.info(
            f"[会计] 担保交易检查: 冻结{results['total_frozen']}笔 ¥{results['total_amount']:.2f} "
            f"| 超期{len(results['overdue'])}笔"
        )
        return results

    async def generate_daily_report(self, account_name: str = "主号") -> str:
        """生成日报（供总裁审阅）"""
        logger.info("[会计] 生成日报...")
        today = date.today().isoformat()

        # 收集数据
        stats = await StatsRepo.get_daily_report(account_name, today)
        tx_summary = await TransactionRepo.get_daily_summary(account_name, today)
        escrow = await self.check_escrow_status(account_name)

        # 构造 LLM 输入
        context = f"""
今日日期: {today}
账号: {account_name}

== 运营数据 ==
发布商品: {stats.get('publishing', {})}
客服: {stats.get('customer_service', {})}
订单: {stats.get('orders', {})}

== 财务汇总 ==
{tx_summary}

== 担保交易状态 ==
冻结订单: {escrow['total_frozen']}笔, 金额¥{escrow['total_amount']:.2f}
超期未放款: {len(escrow['overdue'])}笔

请生成简洁的日报，包括：今日概况、财务状况、异常提醒、经营建议。
"""
        report = await self.think(context, action_name="generate_daily_report")
        logger.info("[会计] 日报生成完成")
        return report

    async def check_profitability(self, order_id: str) -> dict:
        """检查订单利润率，负利润则预警"""
        order = await OrderRepo.get_by_order_id(order_id)
        if not order:
            return {"error": "order_not_found"}

        profit = await OrderRepo.calculate_profit(order_id)
        margin = (profit / order.sell_price * 100) if order.sell_price > 0 else 0

        result = {
            "order_id": order_id,
            "sell_price": order.sell_price,
            "source_price": order.source_price,
            "profit": profit,
            "margin_pct": round(margin, 1),
            "warning": profit < 0,
        }

        if profit < 0:
            await RiskRepo.log(
                level="warning",
                category="negative_profit",
                message=f"订单{order_id}利润为负: ¥{profit:.2f}",
                account_name=order.account_name,
            )
            logger.warning(f"[会计] 负利润订单: {order_id} 利润¥{profit:.2f}")

        return result
