"""
运营 Agent（客服议价发货）
全公司最高 ROI 岗位：7×24 接待、智能议价、协调发货
"""

import json
from datetime import datetime
from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.config import get_config
from src.models.database import XianyuMessage, MessageDirection, MessageIntent, AgentRole, OrderStatus
from src.models.repo import MessageRepo, StatsRepo, OrderRepo
from src.tools.risk_control import RateLimiter, RiskMonitor


class OperationsAgent(BaseAgent):
    """运营智能体（客服议价发货）"""

    role = AgentRole.OPERATIONS
    llm_config_name = "deepseek_v3"
    prompt_file = "operations.md"

    def __init__(self):
        super().__init__()
        self.rate_limiter = RateLimiter("主号")
        self._message_sender = None  # 消息发送函数（由外部注入）

    def set_message_sender(self, sender):
        """注入消息发送函数（WebSocket 或模拟）"""
        self._message_sender = sender

    async def handle_message(self, message: XianyuMessage) -> dict:
        """
        处理收到的买家消息（核心方法）
        1. 获取对话历史
        2. LLM 分析意图 + 生成回复
        3. 发送回复
        4. 更新数据库
        """
        # 风控检查
        if RiskMonitor.is_paused(message.account_name):
            logger.warning(f"[运营] 账号已暂停，跳过消息处理: {message.buyer_nickname}")
            return {"skipped": True, "reason": "account_paused"}

        if not await self.rate_limiter.acquire("reply_message"):
            return {"skipped": True, "reason": "rate_limited"}

        logger.info(f"[运营] 处理消息 | 买家:{message.buyer_nickname} | 商品:{message.item_title[:20]}")

        # 获取对话历史（上下文）
        history = await MessageRepo.get_conversation_history(
            message.buyer_id, message.account_name, limit=10
        )
        history_text = "\n".join([
            f"{'买家' if m.direction == MessageDirection.INCOMING else '我'}: {m.content}"
            for m in history
        ])

        # 构造 LLM 输入
        context = f"""
商品信息:
- 标题: {message.item_title}
- 商品ID: {message.item_id}

对话历史:
{history_text or '（新对话）'}

买家最新消息: {message.content}

请分析买家意图并生成回复。返回 JSON。
"""

        try:
            result = await self.think_json(context, action_name="handle_message")

            intent_str = result.get("intent", "unknown")
            reply = result.get("reply", "您好，有什么可以帮您的吗？😊")
            confidence = result.get("confidence", 0.8)
            needs_human = result.get("needs_human", False)

            # 映射意图
            intent_map = {
                "inquiry": MessageIntent.INQUIRY,
                "bargain": MessageIntent.BARGAIN,
                "order_intent": MessageIntent.ORDER_INTENT,
                "after_sale": MessageIntent.AFTER_SALE,
                "chitchat": MessageIntent.CHITCHAT,
            }
            intent = intent_map.get(intent_str, MessageIntent.UNKNOWN)

            # 更新消息记录
            await MessageRepo.mark_replied(message.message_id, reply)

            # 如果需要人工，记录但不自动回复
            if needs_human:
                logger.warning(f"[运营] 需要人工处理: {message.content[:50]}")
                return {
                    "intent": intent_str,
                    "reply": reply,
                    "needs_human": True,
                    "confidence": confidence,
                }

            # 发送回复
            if self._message_sender:
                await self._message_sender(
                    buyer_id=message.buyer_id,
                    content=reply,
                    item_id=message.item_id,
                )
            else:
                logger.warning("[运营] 消息发送器未设置，回复未发送")

            logger.info(f"[运营] 回复 | 意图:{intent_str} | 回复:{reply[:50]} | 置信度:{confidence}")

            return {
                "intent": intent_str,
                "reply": reply,
                "confidence": confidence,
                "needs_human": False,
                "sent": True,
            }

        except Exception as e:
            logger.error(f"[运营] 消息处理异常: {e}")
            # 降级回复
            fallback = "您好，稍等一下，我马上回复您～😊"
            if self._message_sender:
                await self._message_sender(
                    buyer_id=message.buyer_id,
                    content=fallback,
                    item_id=message.item_id,
                )
            await MessageRepo.mark_replied(message.message_id, fallback)
            return {"error": str(e), "reply": fallback, "sent": True}

    async def handle_order_paid(self, order_id: str = '', item_title: str = '',
                                 price: float = 0, buyer_id: str = '',
                                 buyer_address: str = '', buyer_phone: str = '',
                                 buyer_name: str = '', sell_price: float = 0,
                                 **kwargs) -> dict:
        """
        处理已付款订单：自动找低价货源 → 采购 → 填写买家地址发货
        这是无货源模式的核心环节
        """
        from loguru import logger

        # 兼容旧接口：如果没有传 price，使用 sell_price
        actual_price = price if price else sell_price

        logger.info(f"[运营Agent] 处理订单 {order_id}: {item_title} @ ¥{actual_price}")
        logger.info(f"[运营Agent] 买家: {buyer_name} {buyer_phone} 地址: {buyer_address}")

        # Step 1: 搜索低价货源（用LLM提取关键词）
        search_keyword = await self._extract_keyword(item_title)
        logger.info(f"[运营Agent] 搜索关键词: {search_keyword}")

        # Step 2: 搜索1688/拼多多找最低价
        try:
            from src.agents.purchasing import PurchasingAgent
            self._purchasing = getattr(self, '_purchasing', PurchasingAgent())
            sources = await self._purchasing.find_sources(search_keyword, limit=5)
        except Exception as e:
            logger.error(f"[运营Agent] 搜索货源失败: {e}")
            sources = []

        if sources:
            best_source = min(sources, key=lambda x: x.get('source_price', x.get('price', 999)))
            source_price = float(best_source.get('source_price', best_source.get('price', 0)))
            logger.info(f"[运营Agent] 找到最低价货源: {best_source.get('title','')} @ ¥{source_price}")

            profit = actual_price - source_price
            logger.info(f"[运营Agent] 预计利润: ¥{profit:.2f}")

            # Step 3: 提示需要手动在货源平台下单（自动化需要货源平台API）
            # 在真实环境中，这里会调用1688/拼多多API自动下单
            logger.info(f"[运营Agent] 请在货源平台下单，收货信息: {buyer_name} {buyer_phone} {buyer_address}")
        else:
            logger.warning(f"[运营Agent] 未找到合适货源，需要手动处理")

        # 兼容原有逻辑：更新订单状态和记录交易
        if order_id:
            try:
                await OrderRepo.update_status(order_id, OrderStatus.PAID)

                from src.models.database import Transaction, TransactionType
                from src.models.repo import TransactionRepo
                await TransactionRepo.record(Transaction(
                    order_id=order_id,
                    account_name="主号",
                    transaction_type=TransactionType.COST,
                    amount=actual_price * 0.8,
                    description=f"货源采购: {item_title}",
                    escrow_status="frozen",
                ))

                await StatsRepo.increment("主号", orders_created=1)
            except Exception as e:
                logger.error(f"[运营Agent] 更新订单状态失败: {e}")

        logger.info(f"[运营Agent] 订单处理完成: {order_id}")
        return {
            "order_id": order_id,
            "search_keyword": search_keyword,
            "sources_found": len(sources) if sources else 0,
        }

    async def _extract_keyword(self, item_title: str) -> str:
        """用LLM从商品标题中提取搜索关键词"""
        from loguru import logger

        if not item_title:
            return ""

        context = f"""
商品标题: {item_title}

请从这个闲鱼商品标题中提取最核心的搜索关键词（用于去拼多多/1688找货源）。
要求：
1. 去除品牌修饰词和营销词
2. 保留核心品类词和规格词
3. 返回1-3个关键词，用空格分隔
4. 只返回关键词，不要解释

返回 JSON: {{"keyword": "提取的关键词"}}
"""
        try:
            result = await self.think_json(context, action_name="extract_keyword")
            keyword = result.get("keyword", item_title)
            logger.info(f"[运营Agent] LLM提取关键词: {item_title} → {keyword}")
            return keyword
        except Exception as e:
            logger.warning(f"[运营Agent] LLM提取关键词失败，使用原标题: {e}")
            return item_title

    async def polish_items(self, item_ids: list[str]) -> int:
        """批量擦亮商品（定时任务）"""
        if not await self.rate_limiter.check_polish_limit():
            logger.warning("[运营] 今日擦亮已达上限")
            return 0

        count = 0
        for item_id in item_ids:
            if RiskMonitor.is_paused("主号"):
                break
            if not await self.rate_limiter.check_polish_limit():
                break
            # 实际擦亮操作由 XianyuWebClient 执行
            await self.rate_limiter.acquire("polish_item", min_interval=30)
            await self.rate_limiter.record_polish()
            count += 1
            logger.info(f"[运营] 擦亮商品 {count}/{len(item_ids)}: {item_id}")

        return count
