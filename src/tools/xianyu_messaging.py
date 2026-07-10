"""
闲鱼消息 WebSocket 监听器
实时接收买家消息，供运营 Agent 自动回复

参考开源项目 XianyuAutoAgent 的 WebSocket + Cookie 方案
"""

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Callable, Any

import websockets
from loguru import logger

from src.config import get_config
from src.models.database import XianyuMessage, MessageDirection, MessageIntent
from src.models.repo import MessageRepo, StatsRepo


class XianyuMessageListener:
    """闲鱼消息 WebSocket 监听器"""

    # 闲鱼 WebSocket 端点（可能随版本变化，需维护）
    WS_URL_TEMPLATE = "wss://wss-goofish.dingtalk.com/lapipa/connect?token={token}&userId={user_id}"

    def __init__(
        self,
        account_name: str,
        ws_token: str,
        user_id: str,
        reply_delay_min: int = 2,
        reply_delay_max: int = 8,
    ):
        self.account_name = account_name
        self.ws_token = ws_token
        self.user_id = user_id
        self.reply_delay_min = reply_delay_min
        self.reply_delay_max = reply_delay_max

        self._ws = None
        self._running = False
        self._reconnect_delay = 5  # 重连延迟（秒）
        self._heartbeat_interval = 30  # 心跳间隔（秒）

        # 消息回调：外部注册的处理函数
        self._message_handler: Callable[[XianyuMessage], Any] | None = None

    def set_message_handler(self, handler: Callable[[XianyuMessage], Any]):
        """注册消息处理回调（运营 Agent 用）"""
        self._message_handler = handler

    async def start(self):
        """启动监听"""
        self._running = True
        logger.info(f"[{self.account_name}] 启动消息监听...")
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"[{self.account_name}] WebSocket 异常: {e}")
                if self._running:
                    logger.info(f"[{self.account_name}] {self._reconnect_delay}秒后重连...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 60)  # 指数退避

    async def stop(self):
        """停止监听"""
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info(f"[{self.account_name}] 消息监听已停止")

    async def _connect_and_listen(self):
        """连接 WebSocket 并监听消息"""
        if not self.ws_token or not self.user_id:
            logger.warning(f"[{self.account_name}] 缺少 ws_token 或 user_id，无法监听消息")
            logger.info("请配置 .env 中的 XIANYU_WS_TOKEN 和 XIANYU_USER_ID")
            # 等待配置后重试
            await asyncio.sleep(30)
            return

        ws_url = self.WS_URL_TEMPLATE.format(
            token=self.ws_token,
            user_id=self.user_id,
        )

        async with websockets.connect(ws_url, ping_interval=self._heartbeat_interval) as ws:
            self._ws = ws
            self._reconnect_delay = 5  # 重置退避
            logger.info(f"[{self.account_name}] WebSocket 已连接")

            # 启动心跳
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

            try:
                async for raw_msg in ws:
                    await self._handle_raw_message(raw_msg)
            finally:
                heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws):
        """发送心跳保活"""
        while self._running:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                heartbeat = json.dumps({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                await ws.send(heartbeat)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"心跳发送失败: {e}")
                break

    async def _handle_raw_message(self, raw_msg: str):
        """解析并处理原始消息"""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.debug(f"非 JSON 消息: {raw_msg[:100]}")
            return

        msg_type = data.get("type", data.get("cmd", ""))

        # 只处理聊天消息
        if msg_type not in ("message", "chat", "msg", "100"):
            return

        # 解析消息内容（格式可能随闲鱼版本变化）
        content = data.get("content", data.get("msg", data.get("text", "")))
        buyer_id = data.get("fromUserId", data.get("senderId", data.get("userId", "")))
        buyer_nick = data.get("fromUserNick", data.get("senderNick", ""))
        item_id = data.get("itemId", data.get("productId", ""))
        item_title = data.get("itemTitle", data.get("productName", ""))
        msg_id = data.get("msgId", str(uuid.uuid4()))

        if not content or not buyer_id:
            return

        logger.info(f"[{self.account_name}] 收到消息 | 买家:{buyer_nick} | 商品:{item_title[:20]} | 内容:{content[:50]}")

        # 构建 Message 对象
        message = XianyuMessage(
            message_id=msg_id,
            account_name=self.account_name,
            buyer_id=buyer_id,
            buyer_nickname=buyer_nick,
            item_id=item_id,
            item_title=item_title,
            direction=MessageDirection.INCOMING,
            content=content,
            intent=MessageIntent.UNKNOWN,  # 由运营 Agent 分类
            raw_data=data,
        )

        # 保存到数据库
        await MessageRepo.save_message(message)
        await StatsRepo.increment(self.account_name, messages_received=1)

        # 调用外部处理回调
        if self._message_handler:
            # 模拟人类回复延时
            delay = __import__("random").uniform(self.reply_delay_min, self.reply_delay_max)
            logger.info(f"将在 {delay:.1f} 秒后处理回复...")
            await asyncio.sleep(delay)
            try:
                await self._message_handler(message)
            except Exception as e:
                logger.error(f"消息处理回调异常: {e}")

    async def send_reply(self, buyer_id: str, content: str, item_id: str = "") -> bool:
        """通过 WebSocket 发送回复消息"""
        if not self._ws:
            logger.error("WebSocket 未连接，无法发送回复")
            return False

        try:
            reply_msg = json.dumps({
                "type": "message",
                "toUserId": buyer_id,
                "itemId": item_id,
                "content": content,
                "timestamp": int(time.time() * 1000),
            })
            await self._ws.send(reply_msg)
            logger.info(f"[{self.account_name}] 已回复买家 {buyer_id}: {content[:50]}")

            # 记录发出的消息
            outgoing = XianyuMessage(
                message_id=str(uuid.uuid4()),
                account_name=self.account_name,
                buyer_id=buyer_id,
                direction=MessageDirection.OUTGOING,
                content=content,
            )
            await MessageRepo.save_message(outgoing)
            await StatsRepo.increment(self.account_name, messages_replied=1)
            return True
        except Exception as e:
            logger.error(f"发送回复失败: {e}")
            return False


# ========== 模拟消息源（开发测试用，无真实 Cookie 时可用） ==========

class MockMessageSource:
    """模拟买家消息源（开发/测试用，不连接真实闲鱼）"""

    SAMPLE_MESSAGES = [
        {"buyer_nick": "张三", "content": "这个还有吗？", "item_title": "厨房收纳盒 全新包邮"},
        {"buyer_nick": "李四", "content": "能便宜点吗？50行不行", "item_title": "不锈钢置物架"},
        {"buyer_nick": "王五", "content": "发什么快递？几天到？", "item_title": "门缝保护条"},
        {"buyer_nick": "赵六", "content": "支持验货吗", "item_title": "母婴收纳袋"},
        {"buyer_nick": "钱七", "content": "已拍，麻烦尽快发货", "item_title": "厨房置物架 可折叠"},
    ]

    def __init__(self, account_name: str, reply_delay_min: int = 2, reply_delay_max: int = 8):
        self.account_name = account_name
        self.reply_delay_min = reply_delay_min
        self.reply_delay_max = reply_delay_max
        self._running = False
        self._message_handler = None

    def set_message_handler(self, handler):
        self._message_handler = handler

    async def start(self):
        self._running = True
        logger.info(f"[{self.account_name}] 启动模拟消息源（测试模式）...")
        import random
        idx = 0
        while self._running:
            await asyncio.sleep(random.uniform(10, 20))  # 每10-20秒来一条消息
            if not self._running:
                break
            msg_data = self.SAMPLE_MESSAGES[idx % len(self.SAMPLE_MESSAGES)]
            idx += 1

            message = XianyuMessage(
                message_id=str(uuid.uuid4()),
                account_name=self.account_name,
                buyer_id=f"mock_buyer_{idx}",
                buyer_nickname=msg_data["buyer_nick"],
                item_id=f"mock_item_{idx}",
                item_title=msg_data["item_title"],
                direction=MessageDirection.INCOMING,
                content=msg_data["content"],
                raw_data=msg_data,
            )
            await MessageRepo.save_message(message)
            await StatsRepo.increment(self.account_name, messages_received=1)
            logger.info(f"[模拟] 收到消息: {msg_data['buyer_nick']} - {msg_data['content']}")

            if self._message_handler:
                delay = random.uniform(self.reply_delay_min, self.reply_delay_max)
                await asyncio.sleep(delay)
                try:
                    await self._message_handler(message)
                except Exception as e:
                    logger.error(f"消息处理异常: {e}")

    async def stop(self):
        self._running = False
        logger.info(f"[{self.account_name}] 模拟消息源已停止")

    async def send_reply(self, buyer_id: str, content: str, item_id: str = "") -> bool:
        """模拟发送回复"""
        logger.info(f"[模拟回复] → {buyer_id}: {content[:50]}")
        outgoing = XianyuMessage(
            message_id=str(uuid.uuid4()),
            account_name=self.account_name,
            buyer_id=buyer_id,
            direction=MessageDirection.OUTGOING,
            content=content,
        )
        await MessageRepo.save_message(outgoing)
        await StatsRepo.increment(self.account_name, messages_replied=1)
        return True
