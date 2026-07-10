"""
HTML 仪表盘生成器
生成可视化运营报告，含图表和数据表格
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import PROJECT_ROOT
from src.models.repo import get_db, AgentLogRepo, StatsRepo, TransactionRepo, RiskRepo
from src.models.database import DailyStats, Transaction, RiskLog, AgentLog
from sqlalchemy import select, func


class DashboardGenerator:
    """HTML 仪表盘生成器"""

    def __init__(self):
        self.output_dir = PROJECT_ROOT / "data" / "dashboard"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, account_name: str = "主号") -> str:
        """
        生成完整 HTML 仪表盘
        :return: 生成的文件路径
        """
        logger.info("[仪表盘] 开始生成运营报告...")

        # 收集数据
        stats = await self._collect_stats(account_name, days=7)
        tx_summary = await TransactionRepo.get_daily_summary(account_name)
        agent_stats = await AgentLogRepo.get_stats(hours=24)
        recent_logs = await AgentLogRepo.get_recent(limit=30)
        risk_warnings = await RiskRepo.get_recent_warnings(hours=48)
        pipeline_result = self._load_pipeline_result()

        # 生成 HTML
        html = self._build_html(
            account_name=account_name,
            stats=stats,
            tx_summary=tx_summary,
            agent_stats=agent_stats,
            recent_logs=recent_logs,
            risk_warnings=risk_warnings,
            pipeline_result=pipeline_result,
        )

        # 保存文件
        filename = f"dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = self.output_dir / filename
        filepath.write_text(html, encoding="utf-8")

        # 同时保存一个 latest 版本
        latest = self.output_dir / "latest.html"
        latest.write_text(html, encoding="utf-8")

        logger.info(f"[仪表盘] 报告已生成: {filepath}")
        return str(filepath)

    async def _collect_stats(self, account_name: str, days: int) -> list[dict]:
        """收集近N天统计"""
        db = await get_db()
        cutoff = (date.today().replace(day=1) if date.today().day > days
                  else (date.today() - timedelta(days=days))).isoformat()

        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        async with db.session() as session:
            result = await session.execute(
                select(DailyStats)
                .where(DailyStats.stat_date >= cutoff)
                .order_by(DailyStats.stat_date)
            )
            rows = result.scalars().all()

        return [
            {
                "date": s.stat_date,
                "published": s.items_published,
                "polished": s.items_polished,
                "msg_received": s.messages_received,
                "msg_replied": s.messages_replied,
                "orders": s.orders_created,
                "revenue": s.revenue,
                "cost": s.cost,
                "profit": s.profit,
            }
            for s in rows
        ]

    def _load_pipeline_result(self) -> dict | None:
        """加载最近一次流水线结果"""
        path = PROJECT_ROOT / "data" / "pipeline_result.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _build_html(self, account_name: str, stats: list[dict], tx_summary: dict,
                    agent_stats: dict, recent_logs: list, risk_warnings: list,
                    pipeline_result: dict | None) -> str:
        """构建完整 HTML 页面"""

        # 准备图表数据
        dates = [s["date"] for s in stats] or ["今日"]
        published_data = [s["published"] for s in stats] or [0]
        revenue_data = [s["revenue"] for s in stats] or [0]
        profit_data = [s["profit"] for s in stats] or [0]

        # Agent 统计表格行
        agent_rows = ""
        if agent_stats:
            for role, data in sorted(agent_stats.items()):
                agent_rows += f"""
                <tr>
                    <td>{role}</td>
                    <td>{data['count']}</td>
                    <td>{data['tokens']:,}</td>
                    <td>¥{data['cost_yuan']:.4f}</td>
                    <td>{data['avg_duration_sec']:.2f}s</td>
                </tr>"""
        else:
            agent_rows = '<tr><td colspan="5" class="empty">暂无数据</td></tr>'

        # 最近日志行
        log_rows = ""
        for log in reversed(recent_logs[-15:]):
            status_class = "ok" if log.success else "fail"
            status_text = "OK" if log.success else "FAIL"
            log_rows += f"""
                <tr>
                    <td><span class="badge {status_class}">{status_text}</span></td>
                    <td>{log.created_at.strftime('%H:%M:%S')}</td>
                    <td>{log.role.value}</td>
                    <td>{log.action}</td>
                    <td class="truncate">{(log.output_summary or '')[:50]}</td>
                </tr>"""

        if not log_rows:
            log_rows = '<tr><td colspan="5" class="empty">暂无日志</td></tr>'

        # 风控告警
        risk_rows = ""
        if risk_warnings:
            for r in risk_warnings[-10:]:
                level_class = "critical" if r.level.value == "critical" else "warning"
                risk_rows += f"""
                <tr>
                    <td><span class="badge {level_class}">{r.level.value}</span></td>
                    <td>{r.created_at.strftime('%m-%d %H:%M')}</td>
                    <td>{r.category}</td>
                    <td class="truncate">{r.message[:60]}</td>
                </tr>"""
        else:
            risk_rows = '<tr><td colspan="4" class="empty">无告警</td></tr>'

        # 流水线摘要
        pipeline_html = ""
        if pipeline_result:
            steps = pipeline_result.get("steps", {})
            pipeline_html = f"""
            <div class="card">
                <h3>最近流水线执行</h3>
                <div class="pipeline-summary">
                    <div class="pipeline-item">
                        <span class="label">关键词</span>
                        <span class="value">{pipeline_result.get('keyword', '')}</span>
                    </div>
                    <div class="pipeline-item">
                        <span class="label">耗时</span>
                        <span class="value">{pipeline_result.get('duration_sec', 0)}s</span>
                    </div>
                    <div class="pipeline-item">
                        <span class="label">发布</span>
                        <span class="value">{pipeline_result.get('published_count', 0)} 件</span>
                    </div>
                </div>
                <div class="funnel">
                    {self._funnel_step('采购', steps.get('purchasing', {}).get('candidates_count', 0))}
                    {self._funnel_step('可上架', steps.get('pricing', {}).get('listable_count', 0))}
                    {self._funnel_step('一审通过', steps.get('first_review', {}).get('passed', 0))}
                    {self._funnel_step('复核批准', steps.get('second_review', {}).get('approved', 0))}
                    {self._funnel_step('已发布', steps.get('packaging', {}).get('published', 0), True)}
                </div>
            </div>"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>闲鱼一人公司 - 运营仪表盘</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; }}
.header {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; padding: 24px 32px; }}
.header h1 {{ font-size: 24px; margin-bottom: 4px; }}
.header .meta {{ font-size: 14px; opacity: 0.85; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 20px; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
.card h3 {{ font-size: 16px; color: #666; margin-bottom: 16px; }}
.stat-card {{ text-align: center; }}
.stat-card .number {{ font-size: 32px; font-weight: 700; color: #667eea; }}
.stat-card .label {{ font-size: 14px; color: #999; margin-top: 4px; }}
.stat-card.profit .number {{ color: #52c41a; }}
.stat-card.cost .number {{ color: #ff4d4f; }}
.chart-container {{ height: 300px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 10px 8px; border-bottom: 2px solid #f0f0f0; color: #999; font-weight: 600; }}
td {{ padding: 10px 8px; border-bottom: 1px solid #f5f5f5; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge.ok {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
.badge.fail {{ background: #fff2f0; color: #ff4d4f; border: 1px solid #ffccc7; }}
.badge.warning {{ background: #fffbe6; color: #faad14; border: 1px solid #ffe58f; }}
.badge.critical {{ background: #fff2f0; color: #ff4d4f; border: 1px solid #ffccc7; }}
.truncate {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.empty {{ text-align: center; color: #ccc; padding: 20px; }}
.pipeline-summary {{ display: flex; gap: 24px; margin-bottom: 16px; }}
.pipeline-item {{ display: flex; flex-direction: column; }}
.pipeline-item .label {{ font-size: 12px; color: #999; }}
.pipeline-item .value {{ font-size: 18px; font-weight: 600; color: #333; }}
.funnel {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.funnel-step {{ padding: 8px 16px; border-radius: 8px; font-size: 13px; }}
.funnel-step .count {{ font-weight: 700; font-size: 16px; }}
.funnel-arrow {{ color: #ccc; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; color: #333; }}
.footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; }}
</style>
</head>
<body>
<div class="header">
    <h1>闲鱼一人公司 - 运营仪表盘</h1>
    <div class="meta">账号: {account_name} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
</div>

<div class="container">
    <!-- 核心指标 -->
    <div class="grid">
        <div class="card stat-card">
            <div class="number">{sum(published_data)}</div>
            <div class="label">近7天发布 (件)</div>
        </div>
        <div class="card stat-card profit">
            <div class="number">¥{tx_summary.get('revenue', 0):.2f}</div>
            <div class="label">今日收入</div>
        </div>
        <div class="card stat-card cost">
            <div class="number">¥{tx_summary.get('cost', 0):.2f}</div>
            <div class="label">今日成本</div>
        </div>
        <div class="card stat-card profit">
            <div class="number">¥{tx_summary.get('net_profit', 0):.2f}</div>
            <div class="label">今日净利润</div>
        </div>
    </div>

    <!-- 图表 -->
    <div class="grid">
        <div class="card">
            <h3>发布趋势 (近7天)</h3>
            <div id="chart-publish" class="chart-container"></div>
        </div>
        <div class="card">
            <h3>收入与利润 (近7天)</h3>
            <div id="chart-revenue" class="chart-container"></div>
        </div>
    </div>

    <!-- 流水线 -->
    {pipeline_html}

    <!-- Agent 统计 -->
    <div class="section-title">智能体调用统计 (24h)</div>
    <div class="card">
        <table>
            <thead>
                <tr><th>角色</th><th>调用次数</th><th>Tokens</th><th>费用</th><th>平均耗时</th></tr>
            </thead>
            <tbody>{agent_rows}</tbody>
        </table>
    </div>

    <!-- 最近日志 -->
    <div class="section-title">最近操作日志</div>
    <div class="card">
        <table>
            <thead>
                <tr><th>状态</th><th>时间</th><th>角色</th><th>动作</th><th>输出摘要</th></tr>
            </thead>
            <tbody>{log_rows}</tbody>
        </table>
    </div>

    <!-- 风控告警 -->
    <div class="section-title">风控告警 (48h)</div>
    <div class="card">
        <table>
            <thead>
                <tr><th>等级</th><th>时间</th><th>类别</th><th>详情</th></tr>
            </thead>
            <tbody>{risk_rows}</tbody>
        </table>
    </div>
</div>

<div class="footer">闲鱼一人公司多智能体系统 | 演示模式数据仅供参考</div>

<script>
// 发布趋势图
var chart1 = echarts.init(document.getElementById('chart-publish'));
chart1.setOption({{
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{ type: 'category', data: {json.dumps(dates)} }},
    yAxis: {{ type: 'value' }},
    series: [{{
        name: '发布数', type: 'bar', data: {json.dumps(published_data)},
        itemStyle: {{ color: '#667eea' }}
    }}]
}});

// 收入利润图
var chart2 = echarts.init(document.getElementById('chart-revenue'));
chart2.setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['收入', '利润'] }},
    xAxis: {{ type: 'category', data: {json.dumps(dates)} }},
    yAxis: {{ type: 'value' }},
    series: [
        {{ name: '收入', type: 'line', data: {json.dumps(revenue_data)}, itemStyle: {{ color: '#52c41a' }} }},
        {{ name: '利润', type: 'line', data: {json.dumps(profit_data)}, itemStyle: {{ color: '#faad14' }} }}
    ]
}});

// 响应式
window.addEventListener('resize', function() {{
    chart1.resize();
    chart2.resize();
}});
</script>
</body>
</html>"""

    @staticmethod
    def _funnel_step(label: str, count: int, is_final: bool = False) -> str:
        color = "#52c41a" if is_final else "#667eea"
        return f"""
            <div class="funnel-step" style="background: {color}22; border: 1px solid {color};">
                <span style="color: {color};">{label}</span>
                <span class="count" style="color: {color};">{count}</span>
            </div>
            <span class="funnel-arrow">→</span>""" if not is_final else f"""
            <div class="funnel-step" style="background: {color}22; border: 1px solid {color};">
                <span style="color: {color};">{label}</span>
                <span class="count" style="color: {color};">{count}</span>
            </div>"""
