"""
采购 Agent（找货源）
在拼多多/1688 搜索低价货源，筛选候选商品交比价 Agent 核算利润
"""

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.models.database import AgentRole
from src.models.repo import AgentLogRepo
from src.tools.source_platforms import SourceManager, SourceProduct


class PurchasingAgent(BaseAgent):
    """采购智能体（找货源）"""

    role = AgentRole.PURCHASING
    llm_config_name = "qwen_turbo"
    prompt_file = "purchasing.md"

    def __init__(self):
        super().__init__()
        self.source_manager = SourceManager()

    async def find_sources(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        核心方法：按关键词搜索货源，LLM 筛选优质候选
        :param keyword: 搜索关键词（通常由总裁策略下发）
        :param limit: 每个平台返回上限
        :return: 采购候选清单（dict 列表，格式见 purchasing.md 输出规范）
        """
        logger.info(f"[采购] 开始搜索货源: {keyword}")

        # 1. 调用货源管理器并发搜索拼多多+1688
        products = await self.source_manager.search_all(keyword, limit=limit)
        if not products:
            logger.warning(f"[采购] 未找到任何货源: {keyword}")
            return []

        # 2. 构造 LLM 输入：将搜索结果摘要交给 LLM 筛选
        product_summaries = []
        for i, p in enumerate(products[:30]):  # 最多给 LLM 看30条
            product_summaries.append(
                f"{i+1}. [{p.platform}] ¥{p.price:.2f} | {p.title} | "
                f"店铺:{p.shop_name} | 销量:{p.sales} | 发货地:{p.location} | "
                f"图片:{len(p.image_urls)}张 | 链接:{p.source_url}"
            )

        # 估算闲鱼均价（货源价 × 2 作为粗估，后续比价 Agent 会精确查闲鱼）
        for p in products[:30]:
            p._est_xianyu_price = round(p.price * 2.0, 2)

        context = f"""
总裁下发的选品方向: {keyword}

货源搜索结果（共{len(product_summaries)}条，已按价格升序排列）:
{chr(10).join(product_summaries)}

预估闲鱼同款均价（粗估=货源价×2，仅供初筛参考）:
{chr(10).join(f"{i+1}. ¥{p._est_xianyu_price}" for i, p in enumerate(products[:30]))}

请按选品原则筛选优质货源，输出 JSON 数组。
注意：
- 优先选低重量、高流通的刚需品
- 货源价应比闲鱼均价低10%-20%
- 优先月销>100、好评率>95%的货源
- 避开大件、易碎、易过期商品
- 最多输出10条最佳候选
"""

        try:
            result = await self.think_json(context, action_name="find_sources")

            # LLM 返回可能是数组或 {candidates: [...]}
            if isinstance(result, list):
                candidates = result
            elif isinstance(result, dict) and "candidates" in result:
                candidates = result["candidates"]
            elif isinstance(result, dict) and "results" in result:
                candidates = result["results"]
            else:
                # 降级：直接取前N条搜索结果
                logger.warning("[采购] LLM 输出格式异常，降级为直接取低价前10条")
                candidates = self._fallback_candidates(products[:10])

            # 确保每条都有必要字段
            enriched = []
            for c in candidates:
                # 尝试从原始搜索结果补全缺失字段
                matched = self._match_product(c, products)
                if matched:
                    c = self._enrich_candidate(c, matched)
                enriched.append(c)

            logger.info(f"[采购] 筛选完成，输出 {len(enriched)} 条候选")
            await AgentLogRepo.log(
                role=self.role, action="find_sources",
                input_summary=f"关键词:{keyword}, 搜索结果:{len(products)}条",
                output_summary=f"候选:{len(enriched)}条",
                success=True,
            )
            return enriched

        except Exception as e:
            logger.error(f"[采购] 筛选异常: {e}")
            # 降级：返回前10条搜索结果
            fallback = self._fallback_candidates(products[:10])
            await AgentLogRepo.log(
                role=self.role, action="find_sources",
                input_summary=f"关键词:{keyword}",
                output_summary=f"降级返回:{len(fallback)}条",
                success=False, error_message=str(e),
            )
            return fallback

    def _match_product(self, candidate: dict, products: list[SourceProduct]) -> SourceProduct | None:
        """根据 product_id 或标题匹配原始搜索结果"""
        pid = candidate.get("product_id", "")
        for p in products:
            if p.product_id == pid:
                return p
        title = candidate.get("title", "")
        for p in products:
            if title and title in p.title:
                return p
        return None

    def _enrich_candidate(self, candidate: dict, product: SourceProduct) -> dict:
        """用原始搜索结果补全候选商品缺失的字段"""
        defaults = {
            "product_id": product.product_id,
            "title": product.title,
            "price": product.price,
            "platform": product.platform,
            "image_url": product.image_urls[0] if product.image_urls else "",
            "source_url": product.source_url,
            "shop_name": product.shop_name,
            "monthly_sales": product.sales,
            "location": product.location,
            "xianyu_avg_price": round(product.price * 2.0, 2),
            "reason": f"低价刚需品，货源¥{product.price}，预估闲鱼均价¥{product.price*2:.2f}",
        }
        for k, v in defaults.items():
            if k not in candidate or not candidate[k]:
                candidate[k] = v
        return candidate

    @staticmethod
    def _fallback_candidates(products: list[SourceProduct]) -> list[dict]:
        """降级方案：直接将搜索结果转为候选清单"""
        return [
            {
                "product_id": p.product_id,
                "title": p.title,
                "price": p.price,
                "platform": p.platform,
                "image_url": p.image_urls[0] if p.image_urls else "",
                "source_url": p.source_url,
                "shop_name": p.shop_name,
                "xianyu_avg_price": round(p.price * 2.0, 2),
                "price_gap_percent": -50.0,
                "monthly_sales": p.sales,
                "location": p.location,
                "reason": f"[降级] 低价货源¥{p.price}，预估闲鱼均价¥{p.price*2:.2f}",
            }
            for p in products
        ]
