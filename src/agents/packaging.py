"""
包装上架 Agent（标题图片发布）
将通过复核的商品包装成爆款链接并发布到闲鱼
"""

from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.config import get_config
from src.models.database import AgentRole
from src.models.repo import AgentLogRepo
from src.tools.risk_control import RateLimiter, RiskMonitor
from src.tools.xianyu_web import XianyuWebClient


class PackagingAgent(BaseAgent):
    """包装上架智能体（标题图片发布）"""

    role = AgentRole.PACKAGING
    llm_config_name = "qwen_plus"
    prompt_file = "packaging.md"

    def __init__(self):
        super().__init__()
        self.rate_limiter = RateLimiter("主号")
        self._web_client: XianyuWebClient | None = None

    def set_web_client(self, client: XianyuWebClient | None):
        """注入闲鱼 Web 客户端（实际发布用，None 时为模拟模式）"""
        self._web_client = client

    async def package(self, item: dict[str, Any]) -> dict[str, Any]:
        """
        包装商品：生成爆款标题、图片策略、商品描述
        :param item: 复核通过的商品信息（含 title, suggested_price, source_price 等）
        :return: 包装素材 {title, description, suggested_price, image_strategy}
        """
        logger.info(f"[包装] 开始包装商品: {item.get('title','')[:20]}")

        context = f"""
复核通过的商品信息:
- 原标题: {item.get('title', '')}
- 货源平台: {item.get('platform', '')}
- 货源价: ¥{item.get('source_price', 0)}
- 建议售价: ¥{item.get('suggested_price', 0)}
- 闲鱼均价: ¥{item.get('xianyu_avg_price', 0)}
- 竞争度评分: {item.get('competition_score', 3)}/5
- 议价空间: {item.get('bargain_room', '')}
- 货源图片: {item.get('image_url', '')}
- 发货地: {item.get('location', '')}

请按包装规范生成：
1. 爆款标题（核心词前12字，≤30字，含场景标签）
2. 商品描述（按模板，含品名/规格/成色/发货/售后）
3. 图片拍摄策略（3-5张，含文字标签）
4. 沿用复核通过的建议售价

注意：标题不得含违规词（最/第一/正品/专柜/官方等），成色必须真实。
返回标准JSON格式。
"""
        try:
            result = await self.think_json(context, action_name="package")

            # 补全缺失字段
            title = result.get("title", "")
            description = result.get("description", "")
            suggested_price = result.get("suggested_price", item.get("suggested_price", 0))

            # 演示模式下 LLM 返回固定标题，用 _fallback_package 生成唯一标题
            fallback_title, fallback_desc = self._fallback_package(item)
            if not title or self.llm.is_demo_mode:
                title = fallback_title
            if not description or self.llm.is_demo_mode:
                description = fallback_desc

            packaged = {
                "title": title,
                "description": description,
                "suggested_price": suggested_price,
                "image_strategy": result.get("image_strategy", {
                    "main_image": "正面主图，干净背景，加'实拍''全新'文字标签",
                    "detail_images": ["细节特写", "侧面图", "尺寸对比图", "使用场景图"],
                    "text_tags": ["实拍", "全新", "包邮"],
                    "shot_count": 5,
                }),
                "source_product_id": item.get("product_id", ""),
                "source_platform": item.get("platform", ""),
                "source_price": item.get("source_price", 0),
            }

            logger.info(f"[包装] 包装完成: {packaged['title'][:30]}")

            await AgentLogRepo.log(
                role=self.role, action="package",
                input_summary=f"商品:{item.get('title','')[:30]}",
                output_summary=f"标题:{packaged['title'][:30]}",
                success=True,
            )

            return packaged

        except Exception as e:
            logger.error(f"[包装] 异常: {e}")
            title, description = self._fallback_package(item)
            packaged = {
                "title": title,
                "description": description,
                "suggested_price": item.get("suggested_price", 0),
                "image_strategy": {
                    "main_image": "正面主图",
                    "detail_images": ["细节图", "场景图"],
                    "text_tags": ["实拍", "全新"],
                    "shot_count": 3,
                },
                "source_product_id": item.get("product_id", ""),
                "source_platform": item.get("platform", ""),
                "source_price": item.get("source_price", 0),
            }
            await AgentLogRepo.log(
                role=self.role, action="package",
                input_summary=f"商品:{item.get('title','')[:30]}",
                output_summary="降级包装",
                success=False, error_message=str(e),
            )
            return packaged

    async def publish(self, packaged: dict[str, Any]) -> dict[str, Any]:
        """
        发布包装好的商品到闲鱼
        :param packaged: package() 方法的输出
        :return: {success, item_id, error}
        """
        # 风控检查
        if RiskMonitor.is_paused("主号"):
            logger.warning("[包装] 账号已暂停，跳过发布")
            return {"success": False, "error": "account_paused"}

        if not await self.rate_limiter.check_publish_limit():
            logger.warning("[包装] 今日发布已达上限")
            return {"success": False, "error": "publish_limit_reached"}

        if not await self.rate_limiter.acquire("publish_item"):
            return {"success": False, "error": "rate_limited"}

        title = packaged.get("title", "")
        description = packaged.get("description", "")
        price = packaged.get("suggested_price", 0)

        logger.info(f"[包装] 发布商品: {title[:30]} (¥{price})")

        # 如果有 Web 客户端，实际发布
        if self._web_client:
            try:
                result = await self._web_client.publish_item(
                    title=title,
                    description=description,
                    price=price,
                    images=None,  # 实际使用时传入图片路径
                )
                if result.get("success"):
                    await self.rate_limiter.record_publish()
                    logger.info(f"[包装] 发布成功: {result.get('item_id')}")

                    await AgentLogRepo.log(
                        role=self.role, action="publish",
                        input_summary=f"标题:{title[:30]}",
                        output_summary=f"item_id:{result.get('item_id')}",
                        success=True,
                    )
                    return result
                else:
                    logger.error(f"[包装] 发布失败: {result.get('error')}")
                    await AgentLogRepo.log(
                        role=self.role, action="publish",
                        input_summary=f"标题:{title[:30]}",
                        output_summary=f"失败:{result.get('error')}",
                        success=False, error_message=result.get("error", ""),
                    )
                    return result
            except Exception as e:
                logger.error(f"[包装] 发布异常: {e}")
                await AgentLogRepo.log(
                    role=self.role, action="publish",
                    input_summary=f"标题:{title[:30]}",
                    output_summary=f"异常:{str(e)[:50]}",
                    success=False, error_message=str(e),
                )
                return {"success": False, "error": str(e)}
        else:
            # 模拟模式：不实际发布
            logger.info(f"[包装][模拟] 商品已发布: {title[:30]} (¥{price})")
            await self.rate_limiter.record_publish()

            await AgentLogRepo.log(
                role=self.role, action="publish",
                input_summary=f"标题:{title[:30]}",
                output_summary="[模拟] 发布成功",
                success=True,
            )
            return {"success": True, "item_id": f"MOCK_{packaged.get('source_product_id','')}", "mock": True}

    async def package_and_publish(self, item: dict[str, Any]) -> dict[str, Any]:
        """
        一站式：包装 + 发布
        :param item: 复核通过的商品信息
        :return: {packaged, publish_result}
        """
        # 1. 包装
        packaged = await self.package(item)

        # 2. 发布
        publish_result = await self.publish(packaged)

        return {
            "packaged": packaged,
            "publish_result": publish_result,
        }

    @staticmethod
    def _fallback_package(item: dict[str, Any]) -> tuple[str, str]:
        """降级包装方案：简单标题 + 模板描述"""
        raw_title = item.get("title", "商品").replace("[DEMO]", "").strip()

        # 提取核心词（取前10个字符作为核心词）
        core = raw_title[:12] if len(raw_title) > 12 else raw_title

        # 生成标题：核心词 + 场景标签
        tags = []
        if item.get("suggested_price", 0) <= 20:
            tags.append("全新包邮")
        else:
            tags.append("全新")
        tags.append("搬家急转")

        title = f"{core}{''.join(tags)}"
        if len(title) > 30:
            title = title[:30]

        # 生成描述
        price = item.get("suggested_price", 0)
        location = item.get("location", "")
        description = (
            f"【品名】{core}\n"
            f"【规格】详见图片\n"
            f"【成色】全新未拆封\n"
            f"【发货说明】下单后24小时内发货，全国包邮（偏远地区补差价）\n"
            f"【售后说明】支持7天无理由，质量问题包退换，签收前请验货\n"
            f"【备注】{location}发货，货源充足"
        )

        return title, description
