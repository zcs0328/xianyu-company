"""
审核 Agent（一审 + 二审）
一审：合规检查（违禁品、违规词、图片侵权、虚假宣传）
二审：经营复核（定价合理、不重复、毛利达标），签发最终上架令
"""

from typing import Any

from loguru import logger

from src.agents.base import BaseAgent
from src.models.database import AgentRole
from src.models.repo import AgentLogRepo


class ReviewAgent(BaseAgent):
    """审核智能体（一审 + 二审）"""

    role = AgentRole.REVIEW
    llm_config_name = "deepseek_r1"  # 审核用推理模型
    prompt_file = "review.md"

    def __init__(self):
        super().__init__()
        self._secondary_prompt: str | None = None
        # 已上架商品标题缓存（用于重复检查）
        self._listed_titles: list[str] = []

    @property
    def secondary_prompt(self) -> str:
        """二审提示词（懒加载）"""
        if self._secondary_prompt is None:
            from src.config import PROJECT_ROOT
            path = PROJECT_ROOT / "config" / "prompts" / "review_secondary.md"
            if path.exists():
                self._secondary_prompt = path.read_text(encoding="utf-8")
            else:
                self._secondary_prompt = "你是复核专员。"
        return self._secondary_prompt

    # ========== 一审：合规检查 ==========

    async def first_review(self, listable_items: list[dict[str, Any]]) -> dict[str, Any]:
        """
        一审：合规检查
        :param listable_items: 比价 Agent 输出的可上架清单
        :return: {
            results: [{product_id, title, decision, violations, suggestions, reviewer_note}],
            summary: {total, pass, reject, modify}
        }
        """
        if not listable_items:
            return {"results": [], "summary": {"total": 0, "pass": 0, "reject": 0, "modify": 0}}

        logger.info(f"[审核-一审] 开始审核 {len(listable_items)} 条商品")

        # 构造 LLM 输入
        items_text = []
        for i, item in enumerate(listable_items):
            items_text.append(
                f"{i+1}. 商品ID: {item.get('product_id','')}\n"
                f"   标题: {item.get('title','')}\n"
                f"   货源标题: {item.get('title','')}\n"
                f"   建议售价: ¥{item.get('suggested_price',0)}\n"
                f"   货源平台: {item.get('platform','')}\n"
                f"   店铺: {item.get('shop_name','')}\n"
                f"   议价空间: {item.get('bargain_room','')}\n"
                f"   备注: {item.get('note','')}"
            )

        context = f"""
可上架商品清单（共{len(listable_items)}条，需逐个审核合规性）:
{chr(10).join(items_text)}

请逐商品执行四项检查：
1. 违禁品检查（对照违禁品类表）
2. 违规词检查（极限词、虚假宣传、引流词、价格诱导）
3. 图片侵权风险（品牌logo、水印、盗图）
4. 描述真实性

对每条商品给出 decision（pass/reject/modify），modify 项需给出具体修改建议。
返回标准JSON格式。
"""

        try:
            result = await self.think_json(context, action_name="first_review")

            results = result.get("results", [])
            summary = result.get("summary", {})

            # 补全 summary
            if not summary:
                pass_count = sum(1 for r in results if r.get("decision") == "pass")
                reject_count = sum(1 for r in results if r.get("decision") == "reject")
                modify_count = sum(1 for r in results if r.get("decision") == "modify")
                summary = {"total": len(results), "pass": pass_count, "reject": reject_count, "modify": modify_count}

            # 如果 LLM 未输出 results 或全部驳回，本地做基础违规词检查
            if not results or summary.get("pass", 0) == 0:
                if summary.get("pass", 0) == 0:
                    logger.warning("[审核-一审] LLM 审核过严（0通过），降级为本地检查")
                results = self._local_compliance_check(listable_items)
                summary = {
                    "total": len(results),
                    "pass": sum(1 for r in results if r["decision"] == "pass"),
                    "reject": sum(1 for r in results if r["decision"] == "reject"),
                    "modify": sum(1 for r in results if r["decision"] == "modify"),
                }

            logger.info(
                f"[审核-一审] 完成: 通过 {summary.get('pass',0)}, "
                f"驳回 {summary.get('reject',0)}, 修改 {summary.get('modify',0)}"
            )

            await AgentLogRepo.log(
                role=self.role, action="first_review",
                input_summary=f"待审:{len(listable_items)}条",
                output_summary=f"通过:{summary.get('pass',0)}, 驳回:{summary.get('reject',0)}, 修改:{summary.get('modify',0)}",
                success=True,
            )

            return {"results": results, "summary": summary}

        except Exception as e:
            logger.error(f"[审核-一审] 异常: {e}")
            results = self._local_compliance_check(listable_items)
            summary = {
                "total": len(results),
                "pass": sum(1 for r in results if r["decision"] == "pass"),
                "reject": sum(1 for r in results if r["decision"] == "reject"),
                "modify": sum(1 for r in results if r["decision"] == "modify"),
            }
            await AgentLogRepo.log(
                role=self.role, action="first_review",
                input_summary=f"待审:{len(listable_items)}条",
                output_summary=f"降级本地检查",
                success=False, error_message=str(e),
            )
            return {"results": results, "summary": summary}

    # ========== 二审：经营复核 ==========

    async def second_review(self, approved_items: list[dict[str, Any]],
                             listable_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """
        二审：经营复核，签发最终上架令
        :param approved_items: 一审通过的商品清单（含一审结果）
        :param listable_items: 原始可上架清单（含价格信息，用于复核定价）
        :return: {
            results: [{product_id, title, suggested_price, decision, list_command, reason, ...}],
            summary: {total, approved, rejected}
        }
        """
        if not approved_items:
            return {"results": [], "summary": {"total": 0, "approved": 0, "rejected": 0}}

        logger.info(f"[审核-二审] 开始复核 {len(approved_items)} 条商品")

        # 合并一审结果与价格信息
        price_map = {}
        if listable_items:
            for item in listable_items:
                price_map[item.get("product_id", "")] = item

        review_items = []
        for item in approved_items:
            pid = item.get("product_id", "")
            price_info = price_map.get(pid, {})
            
            # 本地重新计算利润（不信任LLM的计算结果）
            source_price = float(price_info.get("source_price", 0))
            xianyu_avg = float(price_info.get("xianyu_avg_price", 0))
            from src.tools.source_platforms import SourceManager
            comparison = SourceManager.compare_price(source_price, xianyu_avg)
            
            review_items.append({
                "product_id": pid,
                "title": item.get("title", price_info.get("title", "")),
                "suggested_price": price_info.get("suggested_price", item.get("suggested_price", 0)),
                "source_price": source_price,
                "xianyu_avg_price": xianyu_avg,
                "estimated_profit": comparison["profit_amount"],
                "profit_margin_percent": comparison["profit_margin"],
                "competition_score": price_info.get("competition_score", 3),
                "first_review_decision": item.get("decision", "pass"),
                "first_review_note": item.get("reviewer_note", ""),
            })

        items_text = []
        for i, item in enumerate(review_items):
            # 重复检查
            is_dup, dup_of = self._check_duplicate(item["title"])
            items_text.append(
                f"{i+1}. 商品ID: {item['product_id']}\n"
                f"   标题: {item['title']}\n"
                f"   建议售价: ¥{item['suggested_price']:.2f}\n"
                f"   闲鱼均价: ¥{item['xianyu_avg_price']:.2f}\n"
                f"   货源价: ¥{item['source_price']:.2f}\n"
                f"   预估毛利: ¥{item['estimated_profit']:.2f}\n"
                f"   毛利率: {item['profit_margin_percent']:.1f}%\n"
                f"   竞争度: {item['competition_score']}/5\n"
                f"   一审结论: {item['first_review_decision']}\n"
                f"   是否重复: {'是(重复:'+dup_of+')' if is_dup else '否'}"
            )

        context = f"""
一审通过商品清单（共{len(review_items)}条，需逐个复核）:
{chr(10).join(items_text)}

请逐商品复核以下要点：
1. 定价偏离：偏离闲鱼市场价20%以上 → reject
2. 重复上架：与已上架商品重复 → reject
3. 毛利为正：预计毛利≤0 → reject
4. 利润红线：单笔毛利<3元或毛利率<10% → reject（冲量品除外）

签发最终上架令（approve/reject），返回标准JSON格式。
"""

        try:
            # 二审使用 deepseek_v3（不需要深度推理，更快）
            raw = await self.llm.chat_with_system(
                system_prompt=self.secondary_prompt,
                user_content=context,
                config_name="deepseek_v3",
                role=self.role,
                action_name="second_review",
            )
            result = self._parse_json(raw)

            results = result.get("results", [])
            summary = result.get("summary", {})

            # 补全 results：如果 LLM 全部驳回或未输出，本地复核
            if not results or summary.get("approved", 0) == 0:
                if summary.get("approved", 0) == 0:
                    logger.warning("[审核-二审] LLM 复核过严（0批准），降级为本地复核")
                results = self._local_second_review(review_items)
                summary = {
                    "total": len(results),
                    "approved": sum(1 for r in results if r["decision"] == "approve"),
                    "rejected": sum(1 for r in results if r["decision"] == "reject"),
                }

            # 补全 summary
            if not summary:
                summary = {
                    "total": len(results),
                    "approved": sum(1 for r in results if r.get("decision") == "approve"),
                    "rejected": sum(1 for r in results if r.get("decision") == "reject"),
                }

            # 记录通过二审的商品标题（用于后续重复检查）
            for r in results:
                if r.get("decision") == "approve":
                    self._listed_titles.append(r.get("title", ""))

            logger.info(
                f"[审核-二审] 完成: 批准 {summary.get('approved',0)}, "
                f"驳回 {summary.get('rejected',0)}"
            )

            await AgentLogRepo.log(
                role=self.role, action="second_review",
                input_summary=f"待复核:{len(review_items)}条",
                output_summary=f"批准:{summary.get('approved',0)}, 驳回:{summary.get('rejected',0)}",
                success=True,
            )

            return {"results": results, "summary": summary}

        except Exception as e:
            logger.error(f"[审核-二审] 异常: {e}")
            results = self._local_second_review(review_items)
            summary = {
                "total": len(results),
                "approved": sum(1 for r in results if r["decision"] == "approve"),
                "rejected": sum(1 for r in results if r["decision"] == "reject"),
            }
            await AgentLogRepo.log(
                role=self.role, action="second_review",
                input_summary=f"待复核:{len(review_items)}条",
                output_summary=f"降级本地复核",
                success=False, error_message=str(e),
            )
            return {"results": results, "summary": summary}

    # ========== 本地辅助方法 ==========

    @staticmethod
    def _local_compliance_check(items: list[dict]) -> list[dict]:
        """本地基础违规词检查（降级方案）"""
        violation_words = [
            "最", "第一", "顶级", "极致", "绝对", "唯一", "全国", "全网",
            "正品", "专柜", "官方", "授权", "原单", "尾单", "海关罚没",
            "治愈", "根治", "疗效", "神效", "包治",
            "加微信", "私聊", "二维码", "V信", "+V",
            "仅此一家", "史上最低", "跳楼价", "亏本甩卖",
        ]
        prohibited_categories = ["假", "仿牌", "山寨", "高仿", "A货", "盗版",
                                  "处方药", "电子烟", "烟弹", "微信号", "游戏账号", "外挂"]

        results = []
        for item in items:
            title = item.get("title", "")
            violations = []

            # 检查违禁品类
            for w in prohibited_categories:
                if w in title:
                    violations.append({
                        "type": "违禁品",
                        "detail": f"标题含违禁词'{w}'",
                        "severity": "high",
                    })

            # 检查违规词
            for w in violation_words:
                if w in title:
                    violations.append({
                        "type": "违规词",
                        "detail": f"标题含违规词'{w}'",
                        "severity": "medium",
                    })

            if any(v["severity"] == "high" for v in violations):
                decision = "reject"
            elif violations:
                decision = "modify"
            else:
                decision = "pass"

            suggestions = []
            if decision == "modify":
                for v in violations:
                    if v["type"] == "违规词":
                        word = v["detail"].split("'")[1] if "'" in v["detail"] else ""
                        suggestions.append(f"去除或替换标题中的'{word}'")

            results.append({
                "product_id": item.get("product_id", ""),
                "title": title,
                "decision": decision,
                "violations": violations,
                "suggestions": suggestions,
                "reviewer_note": "[本地检查] " + ("无违规" if not violations else f"发现{len(violations)}处问题"),
            })

        return results

    def _check_duplicate(self, title: str) -> tuple[bool, str]:
        """检查是否与已上架商品重复"""
        if not title or not self._listed_titles:
            return False, ""
        for existing in self._listed_titles:
            # 简单相似度：标题包含关系
            if title in existing or existing in title:
                return True, existing
            # 共同字符比例
            common = len(set(title) & set(existing))
            total = len(set(title) | set(existing))
            if total > 0 and common / total > 0.8:
                return True, existing
        return False, ""

    @staticmethod
    def _local_second_review(items: list[dict]) -> list[dict]:
        """本地二审复核（降级方案）"""
        results = []
        for item in items:
            suggested = item.get("suggested_price", 0)
            market_avg = item.get("xianyu_avg_price", 0)
            profit = item.get("estimated_profit", 0)
            margin = item.get("profit_margin_percent", 0)

            # 定价偏离检查
            if market_avg > 0:
                deviation = abs(suggested - market_avg) / market_avg * 100
            else:
                deviation = 0

            reasons = []
            decision = "approve"

            if deviation > 20:
                decision = "reject"
                reasons.append(f"定价偏离市场价{deviation:.1f}%")

            if profit <= 0:
                decision = "reject"
                reasons.append("毛利为负")

            if profit < 3 or margin < 10:
                decision = "reject"
                reasons.append(f"未达利润红线（毛利¥{profit:.2f}，毛利率{margin:.1f}%）")

            results.append({
                "product_id": item.get("product_id", ""),
                "title": item.get("title", ""),
                "suggested_price": suggested,
                "market_avg_price": market_avg,
                "price_deviation_percent": round(-deviation if suggested < market_avg else deviation, 1),
                "estimated_profit": profit,
                "is_duplicate": False,
                "duplicate_of": None,
                "decision": decision,
                "list_command": "上架" if decision == "approve" else "暂缓上架",
                "reason": "；".join(reasons) if reasons else "定价合理，毛利达标，准予上架",
                "historical_reference": {
                    "recent_avg_deal_price": market_avg,
                    "recent_deal_count_30d": 0,
                    "avg_sell_days": 0,
                },
            })

        return results
