#!/usr/bin/env python3
"""
闲鱼一人公司多智能体系统 - 主入口

用法:
  python main.py --web [端口]  # 启动Web管理界面（推荐）
  python main.py              # 启动系统（自动检测模拟/生产模式）
  python main.py --mock       # 强制模拟模式（测试，不需要闲鱼Cookie）
  python main.py --login      # 交互式登录闲鱼，导出Cookie
  python main.py --report     # 生成今日日报
  python main.py --health     # 检查账号健康状况
  python main.py --pipeline [关键词]  # 执行选品上架流水线（找货→比价→审核→上架）
  python main.py --stats      # 查看智能体操作统计
  python main.py --analyze    # 数据分析：运营表现评估+优化建议
  python main.py --dashboard  # 生成HTML可视化仪表盘
"""

import asyncio
import sys
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import get_config
from src.company import OnePersonCompany, setup_logging
from src.tools.xianyu_web import interactive_login
from src.agents.accounting import AccountingAgent
from src.agents.risk_control import RiskControlAgent
from loguru import logger


async def cmd_start(mock: bool = False):
    """启动系统"""
    config = get_config()
    # 自动判断是否需要模拟模式
    account = config.primary_account
    if not mock and account and not account.ws_token:
        logger.info("未检测到闲鱼 WebSocket Token，自动切换到模拟模式")
        logger.info("如需连接真实闲鱼，请配置 .env 中的 XIANYU_WS_TOKEN")
        mock = True

    company = OnePersonCompany(mock_mode=mock)
    await company.start()


async def cmd_login():
    """交互式登录闲鱼"""
    config = get_config()
    account = config.primary_account
    if not account:
        logger.error("请先在 config/settings.yaml 中配置闲鱼账号")
        return
    await interactive_login(account.name, account.cookie_file)


async def cmd_report():
    """生成日报"""
    from src.models.repo import get_db
    await get_db()
    accounting = AccountingAgent()
    report = await accounting.generate_daily_report("主号")
    print("\n" + "=" * 60)
    print("  今日运营日报")
    print("=" * 60)
    print(report)
    print("=" * 60 + "\n")


async def cmd_health():
    """检查账号健康"""
    from src.models.repo import get_db
    await get_db()
    risk = RiskControlAgent()
    result = await risk.daily_health_check("主号")
    print("\n" + "=" * 60)
    print("  账号健康检查")
    print("=" * 60)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 60 + "\n")


async def cmd_pipeline(keyword: str | None = None):
    """执行选品上架流水线"""
    from src.models.repo import get_db
    await get_db()
    company = OnePersonCompany(mock_mode=True)
    result = await company.run_pipeline(keyword)

    print("\n" + "=" * 60)
    print("  选品上架流水线结果")
    print("=" * 60)
    import json

    # 打印精简结果
    steps = result.get("steps", {})
    print(f"关键词: {result.get('keyword', '')}")
    print(f"完成: {result.get('completed', False)}")
    print(f"耗时: {result.get('duration_sec', 0)}s")
    print(f"发布数: {result.get('published_count', 0)}")
    print()

    for step_name, step_data in steps.items():
        status = step_data.get("status", "?")
        print(f"  [{step_name}] 状态: {status}")
        if step_name == "purchasing":
            print(f"    候选数: {step_data.get('candidates_count', 0)}")
        elif step_name == "pricing":
            print(f"    可上架: {step_data.get('listable_count', 0)} / 过滤: {step_data.get('filtered_count', 0)}")
        elif step_name == "first_review":
            print(f"    通过: {step_data.get('passed', 0)} / 修改: {step_data.get('modify', 0)} / 驳回: {step_data.get('rejected', 0)}")
        elif step_name == "second_review":
            print(f"    批准: {step_data.get('approved', 0)} / 驳回: {step_data.get('rejected', 0)}")
        elif step_name == "packaging":
            print(f"    发布: {step_data.get('published', 0)} / 失败: {step_data.get('failed', 0)}")
            for r in step_data.get("results", []):
                mock_tag = " [模拟]" if r.get("mock") else ""
                success_tag = "OK" if r.get("publish_success") else "FAIL"
                print(f"      [{success_tag}] {r.get('title','')[:35]} Y{r.get('price',0)}{mock_tag}")

    print("=" * 60 + "\n")

    # 保存完整结果到文件
    from src.config import PROJECT_ROOT
    output_path = PROJECT_ROOT / "data" / "pipeline_result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"完整结果已保存到: {output_path}")


async def cmd_stats():
    """查看智能体操作统计"""
    from src.models.repo import get_db, AgentLogRepo
    await get_db()

    stats = await AgentLogRepo.get_stats(hours=24)
    logs = await AgentLogRepo.get_recent(limit=20)

    print("\n" + "=" * 60)
    print("  智能体操作统计（近24小时）")
    print("=" * 60)

    if not stats:
        print("  暂无数据")
    else:
        print(f"  {'角色':<16} {'调用数':>6} {'Tokens':>8} {'费用(元)':>10} {'平均耗时':>8}")
        print("  " + "-" * 56)
        for role, data in sorted(stats.items()):
            print(f"  {role:<16} {data['count']:>6} {data['tokens']:>8} {data['cost_yuan']:>10.4f} {data['avg_duration_sec']:>7.2f}s")

    print("\n  最近 20 条操作日志:")
    print("  " + "-" * 56)
    for log in reversed(logs):
        status = "OK" if log.success else "FAIL"
        print(f"  [{status}] {log.created_at.strftime('%H:%M:%S')} | {log.role.value:<12} | {log.action:<20} | {log.output_summary[:40]}")

    print("=" * 60 + "\n")


async def cmd_analyze():
    """数据分析：运营表现评估+优化建议"""
    from src.models.repo import get_db
    await get_db()
    from src.agents.analytics import AnalyticsAgent

    analytics = AnalyticsAgent()
    report = await analytics.analyze_performance(days=7)

    print("\n" + "=" * 60)
    print("  运营数据分析报告")
    print("=" * 60)
    import json

    metrics = report.get("metrics", {})
    print(f"\n  关键指标（近{report.get('period_days',7)}天）:")
    print(f"    总发布: {metrics.get('total_published',0)} 件")
    print(f"    总订单: {metrics.get('total_orders',0)} 笔")
    print(f"    总收入: Y{metrics.get('total_revenue',0):.2f}")
    print(f"    净利润: Y{metrics.get('net_profit',0):.2f}")
    print(f"    平均毛利率: {metrics.get('avg_margin',0):.1f}%")
    print(f"    客服回复率: {metrics.get('reply_rate',0):.1f}%")

    analysis = report.get("analysis", {})
    print(f"\n  分析结论:")
    print(f"    整体评估: {analysis.get('整体评估', 'N/A')}")
    print(f"    选品建议: {analysis.get('选品优化建议', 'N/A')}")
    print(f"    定价建议: {analysis.get('定价优化建议', 'N/A')}")
    print(f"    客服评估: {analysis.get('客服效率评估', 'N/A')}")
    print(f"    风控评估: {analysis.get('风控状况评估', 'N/A')}")

    actions = analysis.get("下一步行动建议", [])
    if actions:
        print(f"\n  下一步行动:")
        for i, action in enumerate(actions, 1):
            print(f"    {i}. {action}")

    print("\n" + "=" * 60 + "\n")


async def cmd_dashboard():
    """生成HTML可视化仪表盘"""
    from src.models.repo import get_db
    await get_db()
    from src.tools.dashboard import DashboardGenerator

    gen = DashboardGenerator()
    filepath = await gen.generate("主号")

    print("\n" + "=" * 60)
    print("  HTML 仪表盘已生成")
    print("=" * 60)
    print(f"  文件路径: {filepath}")
    print(f"  可在浏览器中打开查看")
    print("=" * 60 + "\n")


def cmd_web(port: int = 8000):
    """启动 Web 管理界面"""
    import uvicorn
    print("\n" + "=" * 60)
    print("  闲鱼一人公司 - Web 管理界面")
    print("=" * 60)
    print(f"  地址: http://localhost:{port}")
    print(f"  在浏览器中打开上方地址即可使用")
    print("=" * 60 + "\n")

    uvicorn.run(
        "src.web.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )


def main():
    parser = argparse.ArgumentParser(description="闲鱼一人公司多智能体系统")
    parser.add_argument("--web", nargs="?", const=8000, type=int, metavar="端口", help="启动Web管理界面（推荐）")
    parser.add_argument("--mock", action="store_true", help="模拟模式（测试，不需闲鱼Cookie）")
    parser.add_argument("--login", action="store_true", help="交互式登录闲鱼，导出Cookie")
    parser.add_argument("--report", action="store_true", help="生成今日日报")
    parser.add_argument("--health", action="store_true", help="检查账号健康状况")
    parser.add_argument("--pipeline", nargs="?", const="", metavar="关键词", help="执行选品上架流水线（找货→比价→审核→上架）")
    parser.add_argument("--stats", action="store_true", help="查看智能体操作统计")
    parser.add_argument("--analyze", action="store_true", help="数据分析：运营表现评估+优化建议")
    parser.add_argument("--dashboard", action="store_true", help="生成HTML可视化仪表盘")
    args = parser.parse_args()

    setup_logging()

    if args.web is not None:
        cmd_web(port=args.web)
    elif args.login:
        asyncio.run(cmd_login())
    elif args.report:
        asyncio.run(cmd_report())
    elif args.health:
        asyncio.run(cmd_health())
    elif args.pipeline is not None:
        keyword = args.pipeline.strip() if args.pipeline else None
        asyncio.run(cmd_pipeline(keyword))
    elif args.stats:
        asyncio.run(cmd_stats())
    elif args.analyze:
        asyncio.run(cmd_analyze())
    elif args.dashboard:
        asyncio.run(cmd_dashboard())
    else:
        try:
            asyncio.run(cmd_start(mock=args.mock))
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止...")


if __name__ == "__main__":
    main()
