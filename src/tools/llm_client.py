"""
LLM 调用工具
统一封装 DeepSeek / Qwen / Ollama(本地) 的 OpenAI 兼容接口调用
无 API Key 时自动降级为演示模式（返回预设响应）
支持智能路由：简单任务用本地模型降本，复杂任务用云端模型保质量
"""

import time
import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from src.config import get_config, LLMConfig
from src.models.database import AgentRole
from src.models.repo import AgentLogRepo


# ========== 演示模式预设响应 ==========

DEMO_RESPONSES = {
    "handle_message": {
        "intent": "inquiry",
        "reply": "亲，这个有货的哦～全新包邮，直接拍下就行😊",
        "confidence": 0.85,
        "needs_human": False,
    },
    "daily_health_check": {
        "level": "info",
        "category": "health_check",
        "message": "账号状态正常，今日操作量在安全范围内",
        "action": "continue",
        "recommendation": "继续保持当前节奏运营",
    },
    "make_strategy": {
        "选品方向": "厨房收纳用品、家居小件、母婴易耗品",
        "利润目标": "单笔毛利不低于10元，毛利率不低于15%",
        "注意事项": "新号养号期每日发布不超过2条，注意擦亮节奏",
    },
    "review_daily_report": "今日运营正常，利润率达标。建议持续关注厨房收纳品类，该品类转化率较好。注意控制发布频率，保持养号节奏。",
    "generate_daily_report": """今日运营概况：
- 发布商品: 0件
- 收到消息: 0条
- 订单: 0笔
- 收入: ¥0.00
- 成本: ¥0.00
- 利润: ¥0.00

资金状态: 暂无冻结资金
异常提醒: 无
建议: 系统刚启动，建议先跑通客服流程，积累对话数据后再批量上架。""",
    "handle_order_paid": {
        "estimated_source_price": 0,
        "search_keywords": "同款商品关键词",
        "buyer_notice": "亲，已收到订单，正在为您安排发货，请耐心等待～",
    },
    # ========== 阶段二 Agent 演示响应 ==========
    "find_sources": [
        {
            "product_id": "PDD_190023456003",
            "title": "[DEMO]冰箱收纳盒透明带盖保鲜盒 食品级特大号储物盒",
            "price": 5.90,
            "platform": "pinduoduo",
            "image_url": "https://img.pddpic.com/goods/images/2023-06/demo-bingxiang-1.jpg",
            "reason": "低重量刚需品，货源¥5.9比闲鱼均价低70%，月销15万+好评率98%",
            "xianyu_avg_price": 19.90,
            "price_gap_percent": -70.0,
            "monthly_sales": 156800,
            "has_freight_insurance": True,
            "ship_within_hours": 24,
        },
        {
            "product_id": "1688_660123456004",
            "title": "[DEMO]可调节抽屉分隔收纳盒 厨房桌面杂物整理盒源头工厂",
            "price": 2.80,
            "platform": "alibaba1688",
            "image_url": "https://cbu01.alicdn.com/img/ibank/2023/demo-chouti-1.jpg",
            "reason": "极低价刚需品，货源¥2.8比闲鱼均价低86%，月销24万+",
            "xianyu_avg_price": 19.90,
            "price_gap_percent": -86.0,
            "monthly_sales": 245000,
            "has_freight_insurance": True,
            "ship_within_hours": 48,
        },
        {
            "product_id": "1688_660123456010",
            "title": "[DEMO]厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架一件代发",
            "price": 2.20,
            "platform": "alibaba1688",
            "image_url": "https://cbu01.alicdn.com/img/ibank/2023/demo-lajidai-1.jpg",
            "reason": "极低价刚需品，货源¥2.2比闲鱼均价低89%，月销89万+爆款",
            "xianyu_avg_price": 19.90,
            "price_gap_percent": -89.0,
            "monthly_sales": 890000,
            "has_freight_insurance": True,
            "ship_within_hours": 24,
        },
    ],
    "calculate_pricing": {
        "listable": [
            {
                "product_id": "1688_660123456010",
                "title": "[DEMO]厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架一件代发",
                "source_price": 2.20,
                "xianyu_avg_price": 19.90,
                "suggested_price": 9.90,
                "estimated_profit": 4.64,
                "profit_margin_percent": 46.9,
                "freight": 3.00,
                "platform_fee": 0.06,
                "competition_score": 3,
                "pricing_stage": "新号冲量",
                "bargain_room": "可小刀",
                "note": "货源极低¥2.2，建议售价9.9冲销量，预留3元砍价空间",
            },
            {
                "product_id": "1688_660123456004",
                "title": "[DEMO]可调节抽屉分隔收纳盒 厨房桌面杂物整理盒源头工厂",
                "source_price": 2.80,
                "xianyu_avg_price": 19.90,
                "suggested_price": 9.90,
                "estimated_profit": 4.04,
                "profit_margin_percent": 40.8,
                "freight": 3.00,
                "platform_fee": 0.06,
                "competition_score": 3,
                "pricing_stage": "新号冲量",
                "bargain_room": "可小刀",
                "note": "货源¥2.8，建议售价9.9冲销量",
            },
            {
                "product_id": "PDD_190023456003",
                "title": "[DEMO]冰箱收纳盒透明带盖保鲜盒 食品级特大号储物盒",
                "source_price": 5.90,
                "xianyu_avg_price": 19.90,
                "suggested_price": 14.90,
                "estimated_profit": 5.91,
                "profit_margin_percent": 39.7,
                "freight": 3.00,
                "platform_fee": 0.09,
                "competition_score": 4,
                "pricing_stage": "新号冲量",
                "bargain_room": "可小刀",
                "note": "货源¥5.9，建议售价14.9比均价低25%冲销量",
            },
        ],
        "filtered": [],
        "summary": {
            "total_candidates": 3,
            "listable_count": 3,
            "filtered_count": 0,
            "avg_margin_percent": 42.5,
        },
    },
    "first_review": {
        "results": [
            {
                "product_id": "1688_660123456010",
                "title": "[DEMO]厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架一件代发",
                "decision": "pass",
                "violations": [],
                "suggestions": [],
                "reviewer_note": "无违禁品、无违规词、无侵权风险，通过",
            },
            {
                "product_id": "1688_660123456004",
                "title": "[DEMO]可调节抽屉分隔收纳盒 厨房桌面杂物整理盒源头工厂",
                "decision": "pass",
                "violations": [],
                "suggestions": [],
                "reviewer_note": "无违禁品、无违规词、无侵权风险，通过",
            },
            {
                "product_id": "PDD_190023456003",
                "title": "[DEMO]冰箱收纳盒透明带盖保鲜盒 食品级特大号储物盒",
                "decision": "pass",
                "violations": [],
                "suggestions": [],
                "reviewer_note": "无违禁品、无违规词、无侵权风险，通过",
            },
        ],
        "summary": {"total": 3, "pass": 3, "reject": 0, "modify": 0},
    },
    "second_review": {
        "results": [
            {
                "product_id": "1688_660123456010",
                "title": "[DEMO]厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架一件代发",
                "suggested_price": 9.90,
                "market_avg_price": 19.90,
                "price_deviation_percent": -50.3,
                "estimated_profit": 4.64,
                "is_duplicate": False,
                "duplicate_of": None,
                "decision": "approve",
                "list_command": "上架",
                "reason": "定价低于市场价冲销量，无重复，毛利为正，准予上架",
                "historical_reference": {"recent_avg_deal_price": 12.50, "recent_deal_count_30d": 15, "avg_sell_days": 3},
            },
            {
                "product_id": "1688_660123456004",
                "title": "[DEMO]可调节抽屉分隔收纳盒 厨房桌面杂物整理盒源头工厂",
                "suggested_price": 9.90,
                "market_avg_price": 19.90,
                "price_deviation_percent": -50.3,
                "estimated_profit": 4.04,
                "is_duplicate": False,
                "duplicate_of": None,
                "decision": "approve",
                "list_command": "上架",
                "reason": "定价低于市场价冲销量，无重复，毛利为正，准予上架",
                "historical_reference": {"recent_avg_deal_price": 11.80, "recent_deal_count_30d": 8, "avg_sell_days": 4},
            },
            {
                "product_id": "PDD_190023456003",
                "title": "[DEMO]冰箱收纳盒透明带盖保鲜盒 食品级特大号储物盒",
                "suggested_price": 14.90,
                "market_avg_price": 19.90,
                "price_deviation_percent": -25.1,
                "estimated_profit": 5.91,
                "is_duplicate": False,
                "duplicate_of": None,
                "decision": "approve",
                "list_command": "上架",
                "reason": "定价合理偏离25%，无重复，毛利为正，准予上架",
                "historical_reference": {"recent_avg_deal_price": 17.20, "recent_deal_count_30d": 12, "avg_sell_days": 5},
            },
        ],
        "summary": {"total": 3, "approved": 3, "rejected": 0},
    },
    "package": {
        "title": "厨房垃圾袋收纳盒壁挂免打孔全新包邮搬家急转",
        "description": "【品名】厨房挂式垃圾袋收纳盒\n【规格】免打孔壁挂式，PP材质，白色\n【成色】全新未拆封\n【发货说明】下单后24小时内发货，全国包邮（新疆西藏补差价）\n【售后说明】支持7天无理由，质量问题包退换，签收前请验货",
        "suggested_price": 9.90,
        "image_strategy": {
            "main_image": "正面主图，干净背景，加'实拍''全新''包邮'文字标签",
            "detail_images": [
                "细节特写：壁挂卡扣结构",
                "侧面/背面图",
                "尺寸对比图：与手掌对比",
                "使用场景图：挂在厨房墙面收纳垃圾袋",
            ],
            "text_tags": ["实拍", "全新", "包邮"],
            "shot_count": 5,
        },
    },
    "default": "演示模式：这是一个预设的LLM响应。请配置真实的API Key以启用智能回复。",
    # ========== 阶段三 Agent 演示响应 ==========
    "analyze_performance": {
        "整体评估": "良好",
        "选品优化建议": "厨房收纳品类转化率较高，建议持续深耕。家居小件利润空间大但竞争激烈，可考虑差异化包装",
        "定价优化建议": "当前9.9元冲量策略效果良好，建议稳定期可逐步提价至12.9-14.9测试转化率变化",
        "客服效率评估": "回复率达标，建议持续关注高峰时段响应速度",
        "风控状况评估": "无异常告警，账号健康度良好",
        "下一步行动建议": [
            "继续执行厨房收纳品类选品流水线，每日2次",
            "测试家居小件品类，观察转化率",
            "稳定期尝试提价至12.9元，观察7天转化率变化",
        ],
    },
}


class LLMClient:
    """统一 LLM 客户端"""

    # 智能路由：简单任务路由到本地模型降本
    # key=action_name, value=本地模型可替代 (True=可降级到本地)
    LOCAL_ROUTABLE = {
        "handle_message": True,          # 客服回复：模板化，本地可处理
        "handle_order_paid": True,       # 订单通知：简单文本生成
        "generate_daily_report": True,   # 日报：数据聚合，不需要深度推理
        "daily_health_check": True,      # 健康检查：规则化判断
        "check_before_publish": True,    # 发布前检查：规则化
        "check_before_polish": True,     # 擦亮前检查：规则化
        # 以下任务需要云端模型保质量
        "find_sources": False,           # 采购选品：需要市场理解
        "calculate_pricing": False,      # 比价定价：需要数学推理
        "first_review": False,           # 合规审核：需要判断力
        "second_review": False,          # 经营复核：需要推理
        "package": False,                # 包装文案：需要创意
        "make_strategy": False,          # 战略制定：需要深度分析
        "analyze_performance": False,    # 数据分析：需要推理
    }

    def __init__(self, config_name: str = "deepseek_v3"):
        self.config_name = config_name
        self._clients: dict[str, AsyncOpenAI] = {}
        self._demo_mode: bool | None = None
        self._ollama_available: bool | None = None

    @property
    def is_demo_mode(self) -> bool:
        """检查是否为演示模式（无有效API Key）"""
        if self._demo_mode is None:
            config = get_config()
            llm_config = config.get_llm(self.config_name)
            api_key = llm_config.api_key
            self._demo_mode = not api_key or "YOUR_" in api_key or api_key.endswith("_KEY")
        return self._demo_mode

    @property
    def is_ollama_available(self) -> bool:
        """检查 Ollama 本地模型是否可用"""
        if self._ollama_available is None:
            config = get_config()
            ollama = config.get_llm("ollama_local")
            self._ollama_available = bool(ollama and ollama.api_key == "ollama")
        return self._ollama_available

    def _should_use_local(self, action_name: str) -> bool:
        """判断是否应使用本地模型（降本路由）"""
        if not self.is_ollama_available:
            return False
        return self.LOCAL_ROUTABLE.get(action_name, False)

    def _get_client(self, config_name: str) -> tuple[AsyncOpenAI, LLMConfig]:
        config = get_config()
        llm_config = config.get_llm(config_name)
        if config_name not in self._clients:
            self._clients[config_name] = AsyncOpenAI(
                api_key=llm_config.api_key or "dummy",
                base_url=llm_config.base_url,
                timeout=60.0,  # 60秒超时
            )
        return self._clients[config_name], llm_config

    async def chat(
        self,
        messages: list[dict[str, str]],
        config_name: str | None = None,
        role: AgentRole | None = None,
        action_name: str = "",
        temperature: float | None = None,
    ) -> str:
        """
        调用 LLM 对话
        无 API Key 时自动降级为演示模式
        """
        cfg_name = config_name or self.config_name

        # 演示模式：返回预设响应
        if self.is_demo_mode:
            demo_key = action_name or "default"
            demo_resp = DEMO_RESPONSES.get(demo_key, DEMO_RESPONSES["default"])
            if isinstance(demo_resp, (dict, list)):
                return json.dumps(demo_resp, ensure_ascii=False)
            logger.info(f"[LLM-演示] {action_name or 'chat'} → 预设响应")
            return demo_resp

        # 智能路由：简单任务降级到本地模型降本
        if self._should_use_local(action_name):
            logger.info(f"[LLM-路由] {action_name} → 本地模型(降本)")
            cfg_name = "ollama_local"

        # 真实模式：调用 API
        client, llm_config = self._get_client(cfg_name)

        start_time = time.time()
        success = True
        error_msg = ""
        tokens = 0

        try:
            response = await client.chat.completions.create(
                model=llm_config.model,
                messages=messages,
                temperature=temperature if temperature is not None else llm_config.temperature,
                max_tokens=llm_config.max_tokens,
            )
            content = response.choices[0].message.content or ""
            tokens = response.usage.total_tokens if response.usage else 0

            # 估算成本（元）
            cost_rates = {
                "deepseek_v3": 0.000005,
                "deepseek_r1": 0.00001,
                "qwen_turbo": 0.000004,
                "qwen_plus": 0.0000014,
            }
            cost = tokens * cost_rates.get(cfg_name, 0.000005)

            duration = time.time() - start_time
            logger.info(
                f"[LLM] {llm_config.model} | tokens={tokens} | cost≈¥{cost:.4f} | {duration:.1f}s"
            )

            if role:
                await AgentLogRepo.log(
                    role=role, action=action_name or "chat",
                    input_summary=messages[-1]["content"][:200] if messages else "",
                    output_summary=content[:200],
                    llm_model=llm_config.model, tokens_used=tokens,
                    cost_yuan=cost, duration_sec=duration, success=True,
                )

            return content

        except Exception as e:
            success = False
            error_msg = str(e)
            duration = time.time() - start_time
            logger.error(f"[LLM] 调用失败: {e}")

            # 降级到演示模式响应
            logger.warning("[LLM] 降级为演示模式响应")
            demo_key = action_name or "default"
            demo_resp = DEMO_RESPONSES.get(demo_key, DEMO_RESPONSES["default"])
            if isinstance(demo_resp, (dict, list)):
                return json.dumps(demo_resp, ensure_ascii=False)
            return demo_resp

    async def chat_with_system(self, system_prompt: str, user_content: str,
                                config_name: str | None = None,
                                role: AgentRole | None = None,
                                action_name: str = "") -> str:
        """便捷方法：系统提示 + 用户输入"""
        return await self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            config_name=config_name,
            role=role,
            action_name=action_name,
        )


# 全局单例
_llm_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
