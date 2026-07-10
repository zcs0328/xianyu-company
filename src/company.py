"""
一人公司编排器
9 个智能体协同：总裁 → 采购 → 比价 → 审核(一审) → 审核(复核) → 包装上架
+ 运营(客服) / 会计 / 风控 全天候保障
"""

import asyncio
import signal
import sys
from datetime import datetime
from typing import Any

from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import get_config, PROJECT_ROOT
from src.models.repo import get_db, MessageRepo, StatsRepo, OrderRepo
from src.models.database import XianyuMessage, MessageDirection
from src.agents.ceo import CEOAgent
from src.agents.purchasing import PurchasingAgent
from src.agents.pricing import PricingAgent
from src.agents.review import ReviewAgent
from src.agents.packaging import PackagingAgent
from src.agents.operations import OperationsAgent
from src.agents.accounting import AccountingAgent
from src.agents.risk_control import RiskControlAgent
from src.agents.analytics import AnalyticsAgent
from src.tools.xianyu_messaging import XianyuMessageListener, MockMessageSource
from src.tools.risk_control import RiskMonitor
from src.tools.account_manager import AccountManager


class OnePersonCompany:
    """一人公司编排器（9 智能体协同）"""

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = get_config()
        self.account_name = "主号"

        # ===== 9 个智能体 =====
        self.ceo = CEOAgent()                        # 总裁
        self.purchasing = PurchasingAgent()           # 采购（找货源）
        self.pricing = PricingAgent()                 # 比价（算利润）
        self.review = ReviewAgent()                   # 审核（一审+复核）
        self.packaging = PackagingAgent()             # 包装上架
        self.operations = OperationsAgent()           # 运营（客服议价发货）
        self.accounting = AccountingAgent()           # 会计
        self.risk_control = RiskControlAgent()        # 风控
        self.analytics = AnalyticsAgent()             # 数据分析（阶段三）

        # 多账号矩阵管理器（阶段三）
        self.account_manager = AccountManager()

        # 消息监听器
        self._listener: XianyuMessageListener | MockMessageSource | None = None

        # 定时任务调度器
        self._scheduler = AsyncIOScheduler()

        # 运行状态
        self._running = False

        # 自动发货计数
        self._auto_fulfill_count: int = 0
        self._last_order_check: datetime | None = None

        # 订单仓库
        self._order_repo = OrderRepo()

        # 最近一次选品策略关键词
        self._last_strategy_keyword: str = "厨房收纳盒"

    async def start(self):
        """启动一人公司"""
        logger.info("=" * 60)
        logger.info("  闲鱼一人公司多智能体系统 启动中...")
        logger.info(f"  模式: {'模拟（测试）' if self.mock_mode else '生产'}")
        logger.info(f"  账号: {self.account_name}")
        logger.info("  智能体: 总裁/采购/比价/审核/包装/运营/会计/风控")
        logger.info("=" * 60)

        # 1. 初始化数据库
        logger.info("[系统] 初始化数据库...")
        await get_db()

        # 2. 初始化消息监听
        await self._init_message_listener()

        # 3. 设置定时任务
        self._setup_scheduler()

        # 4. 启动风控检查
        logger.info("[系统] 执行启动风控检查...")
        health = await self.risk_control.daily_health_check(self.account_name)
        logger.info(f"[系统] 风控状态: {health.get('level', 'unknown')}")

        # 5. 总裁制定今日策略
        logger.info("[系统] 总裁制定今日策略...")
        strategy = await self.ceo.make_strategy()
        # 从策略中提取选品方向关键词
        if isinstance(strategy, dict):
            keyword = strategy.get("选品方向", "")
            if keyword:
                # 取第一个品类作为搜索关键词
                self._last_strategy_keyword = keyword.split("、")[0].split(",")[0].strip()
        logger.info(f"[系统] 策略选品方向: {self._last_strategy_keyword}")

        # 6. 启动消息监听
        if self._listener:
            listener_task = asyncio.create_task(self._listener.start())
            logger.info("[系统] 消息监听已启动")

        # 7. 启动定时任务
        self._scheduler.start()
        logger.info("[系统] 定时任务已启动")

        self._running = True
        logger.info("=" * 60)
        logger.info("  系统已就绪，开始运营！")
        logger.info("  - 消息监听: 运行中")
        logger.info("  - 定时任务: 运行中")
        logger.info("  - 选品上架流水线: 每4小时执行")
        logger.info("  按 Ctrl+C 停止")
        logger.info("=" * 60)

        # 保持运行
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """停止系统"""
        logger.info("[系统] 正在停止...")
        self._running = False

        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

        if self._listener:
            await self._listener.stop()

        from src.models.repo import _db
        if _db:
            await _db.close()

        logger.info("[系统] 已停止")

    async def _init_message_listener(self):
        """初始化消息监听器"""
        account = self.config.primary_account
        if not account:
            logger.warning("[系统] 无闲鱼账号配置，使用模拟模式")
            self.mock_mode = True

        if self.mock_mode or (not account.ws_token):
            logger.info("[系统] 使用模拟消息源（测试模式）")
            self._listener = MockMessageSource(
                account_name=self.account_name,
                reply_delay_min=account.reply_delay_min if account else 2,
                reply_delay_max=account.reply_delay_max if account else 8,
            )
        else:
            logger.info(f"[系统] 使用 WebSocket 消息监听: {account.name}")
            self._listener = XianyuMessageListener(
                account_name=self.account_name,
                ws_token=account.ws_token,
                user_id=account.user_id,
                reply_delay_min=account.reply_delay_min,
                reply_delay_max=account.reply_delay_max,
            )

        # 注册消息处理回调
        self._listener.set_message_handler(self._on_message_received)

        # 注入消息发送器给运营 Agent
        self.operations.set_message_sender(self._listener.send_reply)

    async def _on_message_received(self, message: XianyuMessage):
        """消息处理回调（由监听器调用）"""
        # 风控检查
        if RiskMonitor.is_paused(message.account_name):
            logger.warning(f"[系统] 账号暂停中，消息暂不处理: {message.buyer_nickname}")
            return

        # 运营 Agent 处理
        await self.operations.handle_message(message)

    def _setup_scheduler(self):
        """设置定时任务"""
        # 1. 每5分钟检查担保交易回款状态
        self._scheduler.add_job(
            self._scheduled_escrow_check,
            "interval",
            minutes=5,
            id="escrow_check",
            name="担保交易回款检查",
        )

        # 2. 每小时风控健康检查
        self._scheduler.add_job(
            self._scheduled_risk_check,
            "interval",
            hours=1,
            id="risk_check",
            name="风控健康检查",
        )

        # 3. 每天 22:00 生成日报
        self._scheduler.add_job(
            self._scheduled_daily_report,
            "cron",
            hour=22,
            minute=0,
            id="daily_report",
            name="生成日报",
        )

        # 4. 每天 09:00 总裁制定策略
        self._scheduler.add_job(
            self._scheduled_strategy,
            "cron",
            hour=9,
            minute=0,
            id="daily_strategy",
            name="总裁制定策略",
        )

        # 5. 每4小时执行选品上架流水线（找货→比价→审核→包装上架）
        self._scheduler.add_job(
            self._scheduled_pipeline,
            "interval",
            hours=4,
            id="product_pipeline",
            name="选品上架流水线",
        )

        # 6. 每天凌晨00:01重置账号矩阵日计数
        self._scheduler.add_job(
            self._scheduled_daily_reset,
            "cron",
            hour=0, minute=1,
            id="daily_reset",
            name="账号矩阵日重置",
        )

        # 7. 每小时更新账号健康分数
        self._scheduler.add_job(
            self._scheduled_health_update,
            "interval",
            hours=1,
            id="health_update",
            name="账号健康度更新",
        )

        # 8. 每3分钟监控新订单 → 自动采购发货
        self._scheduler.add_job(
            self._scheduled_order_monitor,
            'interval',
            minutes=3,
            id='order_monitor',
            replace_existing=True,
            next_run_time=datetime.now()
        )

        logger.info(
            "[系统] 定时任务已配置: 回款检查(5min) / 风控检查(1h) / "
            "日报(22:00) / 策略(09:00) / 选品流水线(4h) / 矩阵重置(00:01) / 健康更新(1h) / 订单监控(3min)"
        )

    # ========== 选品上架流水线 ==========

    async def run_pipeline(self, keyword: str | None = None) -> dict[str, Any]:
        """
        执行完整的选品上架流水线：
        采购(找货) → 比价(算利润) → 审核(一审) → 复核(二审) → 包装上架

        :param keyword: 搜索关键词，None 时使用上次策略关键词
        :return: 流水线执行结果汇总
        """
        if keyword is None:
            keyword = self._last_strategy_keyword

        # 风控检查（模拟模式下允许手动触发）
        if RiskMonitor.is_paused(self.account_name) and not self.mock_mode:
            logger.warning("[流水线] 账号已暂停，跳过选品流水线")
            return {"skipped": True, "reason": "account_paused"}

        pipeline_start = datetime.now()
        logger.info("=" * 60)
        logger.info(f"  [流水线] 选品上架流水线启动 | 关键词: {keyword}")
        logger.info("=" * 60)

        result: dict[str, Any] = {
            "keyword": keyword,
            "started_at": pipeline_start.isoformat(),
            "steps": {},
        }

        # ===== Step 1: 采购 Agent 找货源 =====
        logger.info("[流水线] Step 1/5 采购Agent搜索货源...")
        try:
            candidates = await self.purchasing.find_sources(keyword, limit=20)
            result["steps"]["purchasing"] = {
                "status": "ok",
                "candidates_count": len(candidates),
                "candidates": candidates,
            }
            logger.info(f"[流水线] 采购完成: 找到 {len(candidates)} 条候选货源")
        except Exception as e:
            logger.error(f"[流水线] 采购失败: {e}")
            result["steps"]["purchasing"] = {"status": "error", "error": str(e)}
            result["completed"] = False
            result["error"] = f"采购阶段失败: {e}"
            return result

        if not candidates:
            logger.warning("[流水线] 无候选货源，流水线终止")
            result["completed"] = True
            result["note"] = "无候选货源"
            return result

        # ===== Step 2: 比价 Agent 算利润 =====
        logger.info("[流水线] Step 2/5 比价Agent核算利润...")
        try:
            pricing_result = await self.pricing.calculate_pricing(candidates)
            listable = pricing_result.get("listable", [])
            filtered = pricing_result.get("filtered", [])
            result["steps"]["pricing"] = {
                "status": "ok",
                "listable_count": len(listable),
                "filtered_count": len(filtered),
                "summary": pricing_result.get("summary", {}),
                "listable": listable,
            }
            logger.info(
                f"[流水线] 比价完成: 可上架 {len(listable)} 条，过滤 {len(filtered)} 条"
            )
        except Exception as e:
            logger.error(f"[流水线] 比价失败: {e}")
            result["steps"]["pricing"] = {"status": "error", "error": str(e)}
            result["completed"] = False
            result["error"] = f"比价阶段失败: {e}"
            return result

        if not listable:
            logger.warning("[流水线] 无可上架商品（利润不达标），流水线终止")
            result["completed"] = True
            result["note"] = "无可上架商品"
            return result

        # ===== Step 3: 审核 Agent 一审（合规检查） =====
        logger.info("[流水线] Step 3/5 审核Agent一审（合规检查）...")
        try:
            first_review_result = await self.review.first_review(listable)
            review_results = first_review_result.get("results", [])
            # 取一审通过的商品
            passed_items = [r for r in review_results if r.get("decision") == "pass"]
            # modify 项也带上（后续可修正后重提）
            modify_items = [r for r in review_results if r.get("decision") == "modify"]
            rejected_items = [r for r in review_results if r.get("decision") == "reject"]

            result["steps"]["first_review"] = {
                "status": "ok",
                "summary": first_review_result.get("summary", {}),
                "passed": len(passed_items),
                "modify": len(modify_items),
                "rejected": len(rejected_items),
            }
            logger.info(
                f"[流水线] 一审完成: 通过 {len(passed_items)}, "
                f"修改 {len(modify_items)}, 驳回 {len(rejected_items)}"
            )
        except Exception as e:
            logger.error(f"[流水线] 一审失败: {e}")
            result["steps"]["first_review"] = {"status": "error", "error": str(e)}
            result["completed"] = False
            result["error"] = f"一审阶段失败: {e}"
            return result

        if not passed_items:
            logger.warning("[流水线] 一审无通过商品，流水线终止")
            result["completed"] = True
            result["note"] = "一审无通过商品"
            return result

        # 合并一审通过的商品信息（从 listable 中匹配价格等信息）
        approved_for_second = []
        for r in passed_items:
            pid = r.get("product_id", "")
            matched = next((l for l in listable if l.get("product_id") == pid), None)
            if matched:
                approved_for_second.append({**matched, **r})
            else:
                approved_for_second.append(r)

        # ===== Step 4: 审核 Agent 复核（经营复核） =====
        logger.info("[流水线] Step 4/5 审核Agent复核（定价+重复检查）...")
        try:
            second_review_result = await self.review.second_review(
                approved_for_second, listable_items=listable
            )
            second_results = second_review_result.get("results", [])
            # 取复核批准的商品
            approved_items = [r for r in second_results if r.get("decision") == "approve"]
            rejected_items_2 = [r for r in second_results if r.get("decision") == "reject"]

            result["steps"]["second_review"] = {
                "status": "ok",
                "summary": second_review_result.get("summary", {}),
                "approved": len(approved_items),
                "rejected": len(rejected_items_2),
            }
            logger.info(
                f"[流水线] 复核完成: 批准 {len(approved_items)}, 驳回 {len(rejected_items_2)}"
            )
        except Exception as e:
            logger.error(f"[流水线] 复核失败: {e}")
            result["steps"]["second_review"] = {"status": "error", "error": str(e)}
            result["completed"] = False
            result["error"] = f"复核阶段失败: {e}"
            return result

        if not approved_items:
            logger.warning("[流水线] 复核无批准商品，流水线终止")
            result["completed"] = True
            result["note"] = "复核无批准商品"
            return result

        # 合并复核批准的商品完整信息（从 listable 中匹配）
        final_items = []
        for r in approved_items:
            pid = r.get("product_id", "")
            matched = next((l for l in listable if l.get("product_id") == pid), None)
            if matched:
                final_items.append({**matched, **r})
            else:
                final_items.append(r)

        # ===== Step 5: 包装上架 Agent 发布 =====
        logger.info(f"[流水线] Step 5/5 包装上架Agent发布 {len(final_items)} 件商品...")
        publish_results = []
        for i, item in enumerate(final_items):
            logger.info(f"[流水线] 包装上架 {i+1}/{len(final_items)}: {item.get('title','')[:30]}")
            try:
                pkg_result = await self.packaging.package_and_publish(item)
                publish_results.append({
                    "product_id": item.get("product_id", ""),
                    "title": pkg_result.get("packaged", {}).get("title", ""),
                    "price": pkg_result.get("packaged", {}).get("suggested_price", 0),
                    "publish_success": pkg_result.get("publish_result", {}).get("success", False),
                    "item_id": pkg_result.get("publish_result", {}).get("item_id", ""),
                    "mock": pkg_result.get("publish_result", {}).get("mock", False),
                })
            except Exception as e:
                logger.error(f"[流水线] 包装上架失败: {item.get('title','')[:30]} → {e}")
                publish_results.append({
                    "product_id": item.get("product_id", ""),
                    "title": item.get("title", ""),
                    "publish_success": False,
                    "error": str(e),
                })

        published_count = sum(1 for r in publish_results if r.get("publish_success"))
        result["steps"]["packaging"] = {
            "status": "ok",
            "total": len(final_items),
            "published": published_count,
            "failed": len(final_items) - published_count,
            "results": publish_results,
        }
        logger.info(f"[流水线] 包装上架完成: 成功 {published_count}/{len(final_items)}")

        # ===== 流水线汇总 =====
        pipeline_end = datetime.now()
        duration = (pipeline_end - pipeline_start).total_seconds()
        result["completed"] = True
        result["duration_sec"] = round(duration, 1)
        result["published_count"] = published_count

        logger.info("=" * 60)
        logger.info(f"  [流水线] 选品上架流水线完成！")
        logger.info(f"  关键词: {keyword}")
        logger.info(f"  采购候选: {len(candidates)} → 可上架: {len(listable)}")
        logger.info(f"  一审通过: {len(passed_items)} → 复核批准: {len(approved_items)}")
        logger.info(f"  成功发布: {published_count} 件")
        logger.info(f"  耗时: {duration:.1f}s")
        logger.info("=" * 60)

        return result

    # ========== 定时任务 ==========

    async def _scheduled_escrow_check(self):
        """定时检查担保交易回款"""
        if RiskMonitor.is_paused(self.account_name):
            return
        logger.info("[定时] 检查担保交易回款...")
        result = await self.accounting.check_escrow_status(self.account_name)
        if result["overdue"]:
            logger.warning(f"[定时] {len(result['overdue'])}笔订单超期未放款")

    async def _scheduled_risk_check(self):
        """定时风控检查"""
        logger.info("[定时] 风控健康检查...")
        await self.risk_control.daily_health_check(self.account_name)

    async def _scheduled_daily_report(self):
        """定时生成日报"""
        logger.info("[定时] 生成日报...")
        report = await self.accounting.generate_daily_report(self.account_name)
        logger.info(f"[定时] 日报:\n{report}")

        # 总裁审阅
        review = await self.ceo.review_daily_report()
        logger.info(f"[定时] 总裁批示:\n{review}")

    async def _scheduled_strategy(self):
        """定时制定策略"""
        logger.info("[定时] 总裁制定策略...")
        strategy = await self.ceo.make_strategy()
        if isinstance(strategy, dict):
            keyword = strategy.get("选品方向", "")
            if keyword:
                self._last_strategy_keyword = keyword.split("、")[0].split(",")[0].strip()
        logger.info(f"[定时] 选品方向更新为: {self._last_strategy_keyword}")

    async def _scheduled_pipeline(self):
        """定时执行选品上架流水线"""
        logger.info("[定时] 触发选品上架流水线...")
        try:
            await self.run_pipeline()
        except Exception as e:
            logger.error(f"[定时] 流水线执行异常: {e}")

    async def _scheduled_daily_reset(self):
        """每日重置账号矩阵"""
        logger.info("[定时] 重置账号矩阵日计数...")
        await self.account_manager.daily_reset()

    async def _scheduled_health_update(self):
        """更新账号健康分数"""
        logger.info("[定时] 更新账号健康分数...")
        await self.account_manager.update_health_scores()

    async def _scheduled_order_monitor(self):
        """定时监控新订单 → 自动采购低价货源 → 自动填写收货地址发货"""
        from loguru import logger

        self._last_order_check = datetime.now()

        for account in self.account_manager.get_active_accounts():
            try:
                # 1. 获取已付款的新订单（模拟模式下生成测试订单）
                if self.mock_mode:
                    # 模拟订单数据用于演示
                    orders = [
                        {
                            "order_id": f"MOCK_{datetime.now().strftime('%H%M%S')}",
                            "item_title": "演示商品-手机支架",
                            "price": 29.9,
                            "buyer_id": "mock_buyer_001",
                            "address": "上海市浦东新区演示路123号",
                            "phone": "13800138000",
                            "buyer_name": "演示买家",
                        }
                    ] if self._auto_fulfill_count < 3 else []
                else:
                    # 生产模式：需要真实WebClient（暂未初始化完整）
                    logger.debug("[订单监控] 生产模式暂未连接真实订单接口")
                    orders = []

                for order in orders:
                    order_id = order.get('order_id', '')

                    # 检查是否已处理过
                    if await self._order_repo.is_processed(order_id):
                        continue

                    logger.info(f"[自动发货] 收到新订单: {order_id} - {order.get('item_title','')}")

                    # 2. 调用运营Agent处理（搜索低价货源）
                    await self.operations.handle_order_paid(
                        order_id=order_id,
                        item_title=order.get('item_title', ''),
                        price=float(order.get('price', 0)),
                        buyer_id=order.get('buyer_id', ''),
                        buyer_address=order.get('address', ''),
                        buyer_phone=order.get('phone', ''),
                        buyer_name=order.get('buyer_name', '')
                    )

                    # 3. 标记订单已处理
                    await self._order_repo.mark_processed(order_id)
                    self._auto_fulfill_count += 1
                    logger.info(f"[自动发货] 订单 {order_id} 已自动处理完成 (累计: {self._auto_fulfill_count})")

            except Exception as e:
                logger.error(f"[订单监控] 检查失败: {e}")


def setup_logging():
    """配置日志"""
    config = get_config()
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        level=config.logging_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>",
        colorize=True,
    )
    logger.add(
        log_dir / "company.log",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function}:{line} | {message}",
    )
