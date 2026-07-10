"""
数据分析 Agent
分析销售数据、利润趋势、选品效果，给出优化建议
"""

from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.models.database import AgentRole, AgentLog
from src.models.repo import (
    AgentLogRepo, StatsRepo, TransactionRepo, OrderRepo, get_db
)
from sqlalchemy import select, func


class AnalyticsAgent(BaseAgent):
    """数据分析智能体"""

    role = AgentRole.CEO  # 复用 CEO 角色，数据分析是总裁的参谋
    llm_config_name = "deepseek_v3"
    prompt_file = "ceo.md"  # 复用总裁提示词，数据分析本质上是为总裁决策提供依据

    async def analyze_performance(self, days: int = 7) -> dict[str, Any]:
        """
        分析近 N 天运营表现
        :param days: 分析周期（天）
        :return: 分析报告 dict
        """
        logger.info(f"[分析] 开始分析近 {days} 天运营表现...")

        # 1. 收集数据
        stats_data = await self._collect_stats(days)
        agent_stats = await AgentLogRepo.get_stats(hours=days * 24)
        tx_summary = await self._collect_transaction_trends(days)

        # 2. 本地计算关键指标
        metrics = self._calculate_metrics(stats_data, tx_summary)

        # 3. LLM 分析优化建议
        context = f"""
近 {days} 天运营数据分析:

== 每日统计汇总 ==
{self._format_stats(stats_data)}

== 交易趋势 ==
{self._format_transactions(tx_summary)}

== 智能体调用统计 ==
{self._format_agent_stats(agent_stats)}

== 关键指标 ==
- 总发布: {metrics['total_published']} 件
- 总订单: {metrics['total_orders']} 笔
- 总收入: ¥{metrics['total_revenue']:.2f}
- 总成本: ¥{metrics['total_cost']:.2f}
- 净利润: ¥{metrics['net_profit']:.2f}
- 平均毛利率: {metrics['avg_margin']:.1f}%
- 客服回复率: {metrics['reply_rate']:.1f}%
- 平均回复时间: {metrics['avg_reply_time']:.1f}s

请分析运营表现，给出：
1. 整体评估（优秀/良好/需改进）
2. 选品优化建议（哪些品类卖得好，哪些需要调整）
3. 定价优化建议（价格是否合理，是否需要调整）
4. 客服效率评估
5. 风控状况评估
6. 下一步行动建议（具体的 3 条行动项）
"""
        try:
            analysis = await self.think_json(context, action_name="analyze_performance")

            report = {
                "period_days": days,
                "generated_at": datetime.now().isoformat(),
                "metrics": metrics,
                "agent_stats": agent_stats,
                "analysis": analysis,
            }

            logger.info(f"[分析] 分析完成，净利润 ¥{metrics['net_profit']:.2f}，毛利率 {metrics['avg_margin']:.1f}%")
            await AgentLogRepo.log(
                role=self.role, action="analyze_performance",
                input_summary=f"近{days}天数据",
                output_summary=f"净利润¥{metrics['net_profit']:.2f}, 毛利率{metrics['avg_margin']:.1f}%",
                success=True,
            )
            return report

        except Exception as e:
            logger.error(f"[分析] 分析异常: {e}")
            # 降级：返回纯本地计算结果
            report = {
                "period_days": days,
                "generated_at": datetime.now().isoformat(),
                "metrics": metrics,
                "agent_stats": agent_stats,
                "analysis": {
                    "整体评估": "需改进（演示模式无真实数据）",
                    "建议": ["配置真实API Key以获取完整分析", "持续运行系统积累数据", "关注利润率和客服效率"],
                },
                "fallback": True,
            }
            await AgentLogRepo.log(
                role=self.role, action="analyze_performance",
                input_summary=f"近{days}天数据",
                output_summary="降级本地分析",
                success=False, error_message=str(e),
            )
            return report

    async def analyze_pipeline_result(self, pipeline_result: dict) -> dict[str, Any]:
        """
        分析流水线执行结果，评估选品质量
        :param pipeline_result: run_pipeline() 的返回值
        :return: 选品质量分析
        """
        logger.info("[分析] 分析流水线结果...")

        steps = pipeline_result.get("steps", {})
        purchasing = steps.get("purchasing", {})
        pricing = steps.get("pricing", {})
        first_review = steps.get("first_review", {})
        second_review = steps.get("second_review", {})
        packaging = steps.get("packaging", {})

        # 计算漏斗转化率
        candidates = purchasing.get("candidates_count", 0)
        listable = pricing.get("listable_count", 0)
        filtered = pricing.get("filtered_count", 0)
        passed = first_review.get("passed", 0)
        approved = second_review.get("approved", 0)
        published = packaging.get("published", 0)

        funnel = {
            "candidates": candidates,
            "listable": listable,
            "filtered": filtered,
            "first_review_passed": passed,
            "second_review_approved": approved,
            "published": published,
        }

        # 转化率
        rates = {}
        if candidates > 0:
            rates["purchasing_to_listable"] = round(listable / candidates * 100, 1)
        if listable > 0:
            rates["listable_to_approved"] = round(approved / listable * 100, 1)
        if approved > 0:
            rates["approved_to_published"] = round(published / approved * 100, 100)
            rates["approved_to_published"] = round(published / approved * 100, 1)

        # 提取定价信息
        pricing_summary = pricing.get("summary", {})
        listable_items = pricing.get("listable", [])
        prices = [item.get("suggested_price", 0) for item in listable_items]
        margins = [item.get("profit_margin_percent", 0) for item in listable_items]

        price_analysis = {
            "avg_suggested_price": round(sum(prices) / len(prices), 2) if prices else 0,
            "min_price": min(prices) if prices else 0,
            "max_price": max(prices) if prices else 0,
            "avg_margin": round(sum(margins) / len(margins), 1) if margins else 0,
        }

        result = {
            "keyword": pipeline_result.get("keyword", ""),
            "funnel": funnel,
            "conversion_rates": rates,
            "price_analysis": price_analysis,
            "pricing_summary": pricing_summary,
            "duration_sec": pipeline_result.get("duration_sec", 0),
            "published_count": pipeline_result.get("published_count", 0),
        }

        logger.info(
            f"[分析] 漏斗: {candidates}→{listable}→{approved}→{published} | "
            f"均价¥{price_analysis['avg_suggested_price']} | "
            f"毛利率{price_analysis['avg_margin']}%"
        )

        return result

    async def recommend_categories(self, historical_days: int = 30) -> list[dict]:
        """
        基于历史数据推荐优质品类方向
        :return: 推荐品类列表 [{category, reason, avg_margin, sales_count}]
        """
        logger.info(f"[分析] 基于近{historical_days}天数据推荐品类...")

        # 收集历史流水线结果中的定价数据
        # 由于流水线结果保存在 data/pipeline_result.json，这里从 Agent 日志间接分析
        pricing_logs = await AgentLogRepo.get_by_role(AgentRole.PRICING, limit=50)

        # 分析每个流水线的定价摘要
        category_data = {}
        for log in pricing_logs:
            if "可上架" in (log.output_summary or ""):
                # 从输入摘要中提取关键词
                input_text = log.input_summary or ""
                # 简单提取：候选关键词
                if "关键词:" in input_text:
                    keyword = input_text.split("关键词:")[1].split(",")[0].strip()
                    if keyword not in category_data:
                        category_data[keyword] = {"count": 0, "total_listable": 0}
                    category_data[keyword]["count"] += 1
                    # 从输出摘要中提取可上架数
                    if "可上架:" in (log.output_summary or ""):
                        try:
                            listable_str = log.output_summary.split("可上架:")[1].split(",")[0].strip()
                            listable_count = int(listable_str)
                            category_data[keyword]["total_listable"] += listable_count
                        except (ValueError, IndexError):
                            pass

        # 生成推荐
        recommendations = []
        for keyword, data in sorted(category_data.items(), key=lambda x: x[1]["total_listable"], reverse=True):
            recommendations.append({
                "category": keyword,
                "pipeline_runs": data["count"],
                "total_listable": data["total_listable"],
                "avg_listable_per_run": round(data["total_listable"] / data["count"], 1) if data["count"] > 0 else 0,
                "reason": f"近{historical_days}天执行{data['count']}次，平均每次产出{data['total_listable']//max(data['count'],1)}个可上架商品",
            })

        if not recommendations:
            recommendations = [
                {"category": "厨房收纳盒", "pipeline_runs": 0, "total_listable": 0, "avg_listable_per_run": 0, "reason": "默认推荐：刚需品，低重量，高流通"},
                {"category": "家居小件", "pipeline_runs": 0, "total_listable": 0, "avg_listable_per_run": 0, "reason": "默认推荐：轻小件，运费低，利润空间大"},
            ]

        return recommendations[:5]

    # ========== 数据收集 ==========

    async def _collect_stats(self, days: int) -> list[dict]:
        """收集近N天的每日统计"""
        db = await get_db()
        from src.models.database import DailyStats
        cutoff_date = (date.today() - timedelta(days=days)).isoformat()

        async with db.session() as session:
            result = await session.execute(
                select(DailyStats)
                .where(DailyStats.stat_date >= cutoff_date)
                .order_by(DailyStats.stat_date)
            )
            stats = result.scalars().all()

        return [
            {
                "date": s.stat_date,
                "account": s.account_name,
                "items_published": s.items_published,
                "items_polished": s.items_polished,
                "messages_received": s.messages_received,
                "messages_replied": s.messages_replied,
                "orders_created": s.orders_created,
                "orders_completed": s.orders_completed,
                "revenue": s.revenue,
                "cost": s.cost,
                "profit": s.profit,
            }
            for s in stats
        ]

    async def _collect_transaction_trends(self, days: int) -> list[dict]:
        """收集近N天的交易趋势"""
        db = await get_db()
        from src.models.database import Transaction
        cutoff = datetime.utcnow() - timedelta(days=days)

        async with db.session() as session:
            # 按天和类型汇总
            result = await session.execute(
                select(
                    func.date(Transaction.created_at).label("date"),
                    Transaction.transaction_type,
                    func.sum(Transaction.amount).label("total"),
                    func.count(Transaction.id).label("count"),
                )
                .where(Transaction.created_at >= cutoff)
                .group_by(func.date(Transaction.created_at), Transaction.transaction_type)
                .order_by(func.date(Transaction.created_at))
            )
            rows = result.all()

        return [
            {"date": str(r.date), "type": r.transaction_type.value, "total": float(r.total or 0), "count": r.count}
            for r in rows
        ]

    # ========== 指标计算 ==========

    @staticmethod
    def _calculate_metrics(stats_data: list[dict], tx_data: list[dict]) -> dict:
        """计算关键运营指标"""
        total_published = sum(s["items_published"] for s in stats_data)
        total_messages = sum(s["messages_received"] for s in stats_data)
        total_replied = sum(s["messages_replied"] for s in stats_data)
        total_orders = sum(s["orders_created"] for s in stats_data)

        total_revenue = sum(t["total"] for t in tx_data if t["type"] == "income")
        total_cost = sum(t["total"] for t in tx_data if t["type"] in ("cost", "shipping"))
        total_fees = sum(t["total"] for t in tx_data if t["type"] == "platform_fee")
        net_profit = total_revenue - total_cost - total_fees

        avg_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0
        reply_rate = (total_replied / total_messages * 100) if total_messages > 0 else 0

        return {
            "total_published": total_published,
            "total_messages": total_messages,
            "total_replied": total_replied,
            "total_orders": total_orders,
            "total_revenue": round(total_revenue, 2),
            "total_cost": round(total_cost, 2),
            "total_fees": round(total_fees, 2),
            "net_profit": round(net_profit, 2),
            "avg_margin": round(avg_margin, 1),
            "reply_rate": round(reply_rate, 1),
            "avg_reply_time": 0.0,  # 从统计数据中获取
        }

    # ========== 格式化 ==========

    @staticmethod
    def _format_stats(stats_data: list[dict]) -> str:
        if not stats_data:
            return "暂无数据"
        lines = []
        for s in stats_data:
            lines.append(
                f"  {s['date']}: 发布{s['items_published']}件, "
                f"消息{s['messages_received']}/{s['messages_replied']}, "
                f"订单{s['orders_created']}笔, 收入¥{s['revenue']:.2f}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_transactions(tx_data: list[dict]) -> str:
        if not tx_data:
            return "暂无交易数据"
        lines = []
        for t in tx_data:
            lines.append(f"  {t['date']} [{t['type']}]: ¥{t['total']:.2f} ({t['count']}笔)")
        return "\n".join(lines)

    @staticmethod
    def _format_agent_stats(stats: dict) -> str:
        if not stats:
            return "暂无Agent调用数据"
        lines = []
        for role, data in sorted(stats.items()):
            lines.append(f"  {role}: 调用{data['count']}次, tokens={data['tokens']}, 费用¥{data['cost_yuan']:.4f}")
        return "\n".join(lines)
