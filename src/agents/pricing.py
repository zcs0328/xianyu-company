"""
比价 Agent（算利润）
对比货源价与闲鱼均价，计算利润并给出定价建议，过滤无利润商品
"""

from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.config import get_config
from src.models.database import AgentRole
from src.models.repo import AgentLogRepo
from src.tools.source_platforms import SourceManager


class PricingAgent(BaseAgent):
    """比价智能体（算利润）"""

    role = AgentRole.PRICING
    llm_config_name = "deepseek_v3"
    prompt_file = "pricing.md"

    def __init__(self):
        super().__init__()
        self.source_manager = SourceManager()

    async def calculate_pricing(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """
        核心方法：对采购候选清单逐个核算利润，输出可上架清单
        :param candidates: 采购 Agent 输出的候选清单
        :return: {
            listable: [...],   # 可上架清单（含建议售价、利润、竞争度评分）
            filtered: [...],   # 被过滤商品及原因
            summary: {...}     # 汇总统计
        }
        """
        if not candidates:
            logger.warning("[比价] 候选清单为空")
            return {"listable": [], "filtered": [], "summary": {"total_candidates": 0, "listable_count": 0, "filtered_count": 0, "avg_margin_percent": 0}}

        logger.info(f"[比价] 开始核算 {len(candidates)} 条候选商品利润")

        # 1. 对每个候选商品用 SourceManager.compare_price 计算利润空间
        priced_items = []
        for c in candidates:
            source_price = float(c.get("price", 0))
            xianyu_avg = float(c.get("xianyu_avg_price", source_price * 2))

            # 调用货源管理器的比价方法
            comparison = self.source_manager.compare_price(source_price, xianyu_avg)

            priced_items.append({
                **c,
                "source_price": source_price,
                "xianyu_avg_price": xianyu_avg,
                "estimated_cost": comparison["estimated_cost"],
                "profit_amount": comparison["profit_amount"],
                "profit_margin_percent": comparison["profit_margin"],
                "is_profitable": comparison["is_profitable"],
                "recommendation": comparison["recommendation"],
            })

        # 2. 构造 LLM 输入：让 LLM 生成建议售价、竞争度评分、过滤决策
        items_text = []
        for i, item in enumerate(priced_items):
            items_text.append(
                f"{i+1}. {item.get('title','')[:30]}\n"
                f"   货源价: ¥{item['source_price']:.2f} | 闲鱼均价: ¥{item['xianyu_avg_price']:.2f}\n"
                f"   预估成本: ¥{item['estimated_cost']:.2f} | 预估毛利: ¥{item['profit_amount']:.2f}\n"
                f"   毛利率: {item['profit_margin_percent']:.1f}% | 平台: {item.get('platform','')}\n"
                f"   月销: {item.get('monthly_sales',0)} | 店铺: {item.get('shop_name','')}"
            )

        context = f"""
采购候选清单（共{len(priced_items)}条，已计算基础利润）:
{chr(10).join(items_text)}

请按利润红线（单笔毛利≥3元，毛利率≥10%）过滤，并为可上架商品生成建议售价。
要求：
1. 过滤不达标商品，放入 filtered 列表并说明原因
2. 为可上架商品生成建议售价（取9.9/19.9/29.9等心理价位）
3. 评估竞争度评分（1-5，5为红海）
4. 标注定价阶段（新号冲量/稳定期）和议价空间
5. 返回标准JSON格式
"""

        try:
            result = await self.think_json(context, action_name="calculate_pricing")

            # 确保 result 有完整结构
            listable = result.get("listable", [])
            filtered = result.get("filtered", [])
            summary = result.get("summary", {})

            # 如果 LLM 没有输出 filtered，用本地利润计算补全
            if not filtered:
                local_filtered = [
                    {
                        "product_id": item.get("product_id", ""),
                        "title": item.get("title", ""),
                        "source_price": item["source_price"],
                        "xianyu_avg_price": item["xianyu_avg_price"],
                        "estimated_profit": item["profit_amount"],
                        "reason": item.get("recommendation", "利润不达标"),
                    }
                    for item in priced_items
                    if not item["is_profitable"] or item["profit_amount"] < 3 or item["profit_margin_percent"] < 10
                ]
                filtered = local_filtered

            # 如果 LLM 没有输出 listable，用本地计算补全
            if not listable:
                listable = [
                    {
                        "product_id": item.get("product_id", ""),
                        "title": item.get("title", ""),
                        "source_price": item["source_price"],
                        "xianyu_avg_price": item["xianyu_avg_price"],
                        "suggested_price": self._psychological_price(item["xianyu_avg_price"]),
                        "estimated_profit": item["profit_amount"],
                        "profit_margin_percent": item["profit_margin_percent"],
                        "freight": 3.0,
                        "platform_fee": round(item["xianyu_avg_price"] * 0.006, 2),
                        "competition_score": 3,
                        "pricing_stage": "新号冲量",
                        "bargain_room": "可小刀",
                        "note": item.get("recommendation", ""),
                    }
                    for item in priced_items
                    if item["is_profitable"] and item["profit_amount"] >= 3 and item["profit_margin_percent"] >= 10
                ]

            # 补全 summary
            if not summary:
                margins = [l.get("profit_margin_percent", 0) for l in listable]
                avg_margin = sum(margins) / len(margins) if margins else 0
                summary = {
                    "total_candidates": len(priced_items),
                    "listable_count": len(listable),
                    "filtered_count": len(filtered),
                    "avg_margin_percent": round(avg_margin, 1),
                }

            logger.info(
                f"[比价] 核算完成: 可上架 {len(listable)} 条，过滤 {len(filtered)} 条，"
                f"平均毛利率 {summary.get('avg_margin_percent', 0)}%"
            )

            await AgentLogRepo.log(
                role=self.role, action="calculate_pricing",
                input_summary=f"候选:{len(candidates)}条",
                output_summary=f"可上架:{len(listable)}, 过滤:{len(filtered)}",
                success=True,
            )

            return {"listable": listable, "filtered": filtered, "summary": summary}

        except Exception as e:
            logger.error(f"[比价] 核算异常: {e}")
            # 降级：用本地利润计算
            listable, filtered = self._fallback_pricing(priced_items)
            result = {
                "listable": listable,
                "filtered": filtered,
                "summary": {
                    "total_candidates": len(priced_items),
                    "listable_count": len(listable),
                    "filtered_count": len(filtered),
                    "avg_margin_percent": 0,
                },
            }
            await AgentLogRepo.log(
                role=self.role, action="calculate_pricing",
                input_summary=f"候选:{len(candidates)}条",
                output_summary=f"降级: 可上架:{len(listable)}",
                success=False, error_message=str(e),
            )
            return result

    @staticmethod
    def _psychological_price(base_price: float) -> float:
        """将价格转为心理价位（9.9/19.9/29.9...）"""
        if base_price <= 0:
            return 9.9
        # 取最接近的 X.9 价位
        tiers = [9.9, 14.9, 19.9, 24.9, 29.9, 39.9, 49.9, 59.9, 69.9, 99.9]
        closest = min(tiers, key=lambda t: abs(t - base_price))
        return closest

    def _fallback_pricing(self, priced_items: list[dict]) -> tuple[list[dict], list[dict]]:
        """降级方案：用本地利润计算直接过滤"""
        listable = []
        filtered = []
        for item in priced_items:
            entry = {
                "product_id": item.get("product_id", ""),
                "title": item.get("title", ""),
                "source_price": item["source_price"],
                "xianyu_avg_price": item["xianyu_avg_price"],
                "suggested_price": self._psychological_price(item["xianyu_avg_price"]),
                "estimated_profit": item["profit_amount"],
                "profit_margin_percent": item["profit_margin_percent"],
                "freight": 3.0,
                "platform_fee": round(item["xianyu_avg_price"] * 0.006, 2),
                "competition_score": 3,
                "pricing_stage": "新号冲量",
                "bargain_room": "可小刀",
                "note": "[降级] " + item.get("recommendation", ""),
            }
            if item["is_profitable"] and item["profit_amount"] >= 3 and item["profit_margin_percent"] >= 10:
                listable.append(entry)
            else:
                filtered.append({
                    "product_id": item.get("product_id", ""),
                    "title": item.get("title", ""),
                    "source_price": item["source_price"],
                    "xianyu_avg_price": item["xianyu_avg_price"],
                    "estimated_profit": item["profit_amount"],
                    "reason": item.get("recommendation", "利润不达标"),
                })
        return listable, filtered
