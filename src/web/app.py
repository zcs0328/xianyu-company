"""
FastAPI Web 服务器
提供 REST API + WebSocket 实时推送
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from loguru import logger

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, LLMConfig
from src.models.repo import get_db, AgentLogRepo, StatsRepo
from src.models.database import AgentRole
from src.agents.analytics import AnalyticsAgent
from src.tools.dashboard import DashboardGenerator
from src.tools.account_manager import AccountManager


# ========== 全局状态 ==========

app = FastAPI(title="闲鱼一人公司", description="多智能体自动化运营系统")

# 系统状态
_system_state: dict[str, Any] = {
    "running": False,
    "mock_mode": True,
    "company": None,
    "company_task": None,
    "pipeline_running": False,
}

# WebSocket 连接管理
_ws_clients: list[WebSocket] = []

# 日志缓冲（最近100条）
_log_buffer: list[dict] = []
_MAX_BUFFER = 100


def push_log(level: str, source: str, message: str):
    """推送日志到 WebSocket 客户端"""
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "source": source,
        "message": message,
    }
    _log_buffer.append(entry)
    if len(_log_buffer) > _MAX_BUFFER:
        _log_buffer.pop(0)
    # 异步推送
    asyncio.create_task(_broadcast_log(entry))


async def _broadcast_log(entry: dict):
    """广播日志到所有 WebSocket 客户端"""
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json({"type": "log", "data": entry})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


# ========== 自定义日志 Sink ==========

class WebLogSink:
    """loguru 日志 -> WebSocket 推送"""
    def __init__(self):
        self._loop = None

    def __call__(self, message):
        record = message.record
        level = record["level"].name.lower()
        source = record["name"].split(".")[-1] if record["name"] else "system"
        msg = str(record["message"])

        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                return

        # 只推送 INFO 以上
        if record["level"].no >= 20:
            push_log(level, source, msg)


# 安装日志 sink
logger.add(WebLogSink(), level="INFO", format="{message}")


# ========== REST API ==========

@app.get("/")
async def index():
    """返回前端页面"""
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>前端文件未找到</h1>", status_code=404)


@app.get("/api/status")
async def get_status():
    """获取系统状态"""
    await get_db()
    config = get_config()

    # 检查 LLM 模式
    llm_config = config.get_llm("deepseek_v3")
    is_demo = not llm_config.api_key or "YOUR_" in llm_config.api_key

    # 获取账号矩阵状态
    if _system_state["company"]:
        matrix = _system_state["company"].account_manager.get_matrix_status()
    else:
        manager = AccountManager()
        matrix = manager.get_matrix_status()

    # 定时任务
    scheduler_jobs = []
    if _system_state["company"] and _system_state["company"]._scheduler.running:
        for job in _system_state["company"]._scheduler.get_jobs():
            scheduler_jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })

    return {
        "running": _system_state["running"],
        "mock_mode": _system_state["mock_mode"],
        "demo_mode": is_demo,
        "agents": [
            {"name": "总裁", "role": "ceo", "desc": "制定策略、审阅日报、风控决策"},
            {"name": "采购", "role": "purchasing", "desc": "拼多多/1688搜索货源"},
            {"name": "比价", "role": "pricing", "desc": "核算利润、心理定价"},
            {"name": "审核", "role": "review", "desc": "合规检查+经营复核"},
            {"name": "包装上架", "role": "packaging", "desc": "爆款标题、图片策略、发布"},
            {"name": "运营", "role": "operations", "desc": "客服议价、发货协调"},
            {"name": "会计", "role": "accounting", "desc": "记账对账、资金监控"},
            {"name": "风控", "role": "risk_control", "desc": "频率控制、异常检测"},
            {"name": "数据分析", "role": "analytics", "desc": "运营分析、选品优化"},
        ],
        "account_matrix": matrix,
        "scheduler_jobs": scheduler_jobs,
        "llm_models": {
            name: {
                "model": cfg.model,
                "has_key": bool(cfg.api_key) and "YOUR_" not in cfg.api_key,
                "base_url": cfg.base_url,
            }
            for name, cfg in config.llm.items()
        },
    }


@app.post("/api/start")
async def start_system(mock: bool = True):
    """启动系统"""
    if _system_state["running"]:
        return {"ok": False, "error": "系统已在运行中"}

    from src.company import OnePersonCompany

    _system_state["mock_mode"] = mock
    company = OnePersonCompany(mock_mode=mock)
    _system_state["company"] = company

    async def run_company():
        try:
            await company.start()
        except Exception as e:
            logger.error(f"系统运行异常: {e}")
            _system_state["running"] = False

    _system_state["company_task"] = asyncio.create_task(run_company())
    _system_state["running"] = True

    push_log("info", "web", f"系统已启动 (模拟={mock})")
    return {"ok": True, "message": "系统启动中..."}


@app.post("/api/stop")
async def stop_system():
    """停止系统"""
    if not _system_state["running"] or not _system_state["company"]:
        return {"ok": False, "error": "系统未运行"}

    company = _system_state["company"]
    try:
        await company.stop()
    except Exception as e:
        logger.error(f"停止异常: {e}")

    if _system_state["company_task"]:
        _system_state["company_task"].cancel()
        _system_state["company_task"] = None

    _system_state["running"] = False
    _system_state["company"] = None
    push_log("info", "web", "系统已停止")
    return {"ok": True}


@app.post("/api/pipeline")
async def run_pipeline(body: dict = None):
    """执行选品上架流水线"""
    if _system_state["pipeline_running"]:
        return {"ok": False, "error": "流水线正在执行中"}

    keyword = (body or {}).get("keyword", "") if body else ""

    _system_state["pipeline_running"] = True
    push_log("info", "web", f"触发流水线: {keyword or '(默认关键词)'}")

    try:
        await get_db()
        from src.company import OnePersonCompany

        if _system_state["company"]:
            company = _system_state["company"]
        else:
            company = OnePersonCompany(mock_mode=True)

        result = await company.run_pipeline(keyword.strip() if keyword.strip() else None)

        _system_state["pipeline_running"] = False
        push_log("info", "web", f"流水线完成: 发布{result.get('published_count',0)}件")
        return {"ok": True, "result": _serialize(result)}

    except Exception as e:
        logger.error(f"流水线异常: {e}")
        _system_state["pipeline_running"] = False
        return {"ok": False, "error": str(e)}


@app.get("/api/pipeline/result")
async def get_pipeline_result():
    """获取最近一次流水线结果"""
    path = PROJECT_ROOT / "data" / "pipeline_result.json"
    if not path.exists():
        return {"ok": False, "error": "暂无流水线结果"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"ok": True, "result": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/orders")
async def get_orders():
    """获取订单列表"""
    from src.models.repo import get_db
    from src.models.database import XianyuOrder
    from sqlalchemy import select, desc

    try:
        db = await get_db()
        async with db.session() as session:
            result = await session.execute(
                select(XianyuOrder).order_by(desc(XianyuOrder.created_at)).limit(50)
            )
            orders = result.scalars().all()

        return {
            "ok": True,
            "orders": [
                {
                    "order_id": o.order_id,
                    "item_title": o.item_title,
                    "price": o.sell_price,
                    "buyer_name": o.buyer_nickname,
                    "status": str(o.status),
                    "created_at": str(o.created_at) if o.created_at else "",
                    "auto_fulfilled": o.source_platform != ""  # 如果已有货源平台则视为已自动处理
                }
                for o in orders
            ]
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "orders": []}


@app.get("/api/auto-fulfill")
async def get_auto_fulfill_status():
    """获取自动发货状态"""
    company = _get_company()
    return {
        "ok": True,
        "enabled": company is not None,
        "fulfill_count": company._auto_fulfill_count if company and hasattr(company, '_auto_fulfill_count') else 0,
        "last_check": company._last_order_check if company and hasattr(company, '_last_order_check') else None
    }


@app.post("/api/auto-fulfill/toggle")
async def toggle_auto_fulfill(enabled: bool = True):
    """启用/禁用自动发货"""
    # This would toggle the order_monitor scheduled job
    company = _get_company()
    if company:
        job = company._scheduler.get_job('order_monitor')
        if job:
            if enabled:
                job.resume()
            else:
                job.pause()
        return {"ok": True, "enabled": enabled}
    return {"ok": False, "error": "系统未运行"}


@app.post("/api/unpause")
async def unpause_account():
    """恢复账号运行（清理风控暂停状态）"""
    from src.tools.risk_control import RiskMonitor
    from src.models.repo import RiskRepo, StatsRepo
    from sqlalchemy import delete
    from src.models.database import RiskLog, DailyStats

    try:
        db = await get_db()
        async with db.session() as session:
            # 清理风险日志
            await session.execute(delete(RiskLog))
            # 重置今日统计
            from sqlalchemy import update
            await session.execute(
                update(DailyStats).values(risk_warnings=0, auto_paused_count=0)
            )
            await session.commit()

        # 恢复内存中的暂停状态
        RiskMonitor.resume("主号")

        # 恢复账号管理器中的状态
        company = _get_company()
        if company:
            company.account_manager.resume_account("主号")

        push_log("info", "web", "账号已手动恢复，风控记录已清理")
        return {"ok": True, "message": "账号已恢复，可继续操作"}
    except Exception as e:
        logger.error(f"恢复账号失败: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/analyze")
async def run_analyze(days: int = 7):
    """数据分析"""
    try:
        await get_db()
        analytics = AnalyticsAgent()
        report = await analytics.analyze_performance(days=days)
        return {"ok": True, "report": _serialize(report)}
    except Exception as e:
        logger.error(f"分析异常: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/agent-stats")
async def get_agent_stats():
    """获取智能体调用统计"""
    await get_db()
    stats = await AgentLogRepo.get_stats(hours=24)
    logs = await AgentLogRepo.get_recent(limit=50)

    return {
        "ok": True,
        "stats": {k: _serialize(v) if isinstance(v, dict) else v for k, v in stats.items()},
        "logs": [
            {
                "id": log.id,
                "role": log.role.value,
                "action": log.action,
                "success": log.success,
                "output_summary": log.output_summary or "",
                "input_summary": log.input_summary or "",
                "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_sec": log.duration_sec,
                "tokens_used": log.tokens_used,
                "cost_yuan": log.cost_yuan,
            }
            for log in logs
        ],
        "buffer": _log_buffer[-50:],
    }


@app.get("/api/dashboard-html")
async def get_dashboard_html():
    """生成并返回 HTML 仪表盘"""
    await get_db()
    gen = DashboardGenerator()
    filepath = await gen.generate("主号")
    html = Path(filepath).read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/config")
async def get_config_api():
    """获取当前配置"""
    config = get_config()
    return {
        "ok": True,
        "llm": {
            name: {
                "model": cfg.model,
                "base_url": cfg.base_url,
                "has_key": bool(cfg.api_key) and "YOUR_" not in cfg.api_key,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
            }
            for name, cfg in config.llm.items()
        },
        "xianyu_accounts": [
            {
                "name": acc.name,
                "has_cookie": bool(acc.cookie_file),
                "has_ws_token": bool(acc.ws_token),
                "has_user_id": bool(acc.user_id),
                "max_daily_publish": acc.max_daily_publish,
                "max_daily_polish": acc.max_daily_polish,
            }
            for acc in config.xianyu
        ],
        "risk_control": {
            "global_action_interval": config.risk_control.global_action_interval,
            "daily_action_limit": config.risk_control.daily_action_limit,
            "auto_pause_on_warning": config.risk_control.auto_pause_on_warning,
        },
    }


@app.get("/api/logs")
async def get_logs():
    """获取日志缓冲"""
    return {"ok": True, "logs": _log_buffer[-100:]}


@app.get("/download")
async def download_project():
    """下载完整项目压缩包"""
    import subprocess
    zip_path = PROJECT_ROOT.parent / "xianyu-company.zip"
    # 重新打包（确保最新）
    subprocess.run([
        "zip", "-r", str(zip_path), "xianyu-company/",
        "-x", "*/__pycache__/*", "-x", "*.pyc",
        "-x", "*/venv/*", "-x", "*/data/company.db",
        "-x", "*/data/logs/*", "-x", "*/data/dashboard/*",
    ], cwd=str(PROJECT_ROOT.parent), capture_output=True)

    return FileResponse(
        path=str(zip_path),
        filename="xianyu-company.zip",
        media_type="application/zip",
    )


@app.get("/api/health")
async def health_check():
    """健康检查"""
    await get_db()
    from src.agents.risk_control import RiskControlAgent

    risk = RiskControlAgent()
    result = await risk.daily_health_check("主号")
    return {"ok": True, "result": _serialize(result)}


@app.get("/api/matrix")
async def get_matrix():
    """获取账号矩阵状态"""
    if _system_state["company"]:
        matrix = _system_state["company"].account_manager.get_matrix_status()
    else:
        manager = AccountManager()
        matrix = manager.get_matrix_status()
    return {"ok": True, "matrix": matrix}


# ========== WebSocket ==========

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 实时推送"""
    await ws.accept()
    _ws_clients.append(ws)

    # 发送历史日志
    await ws.send_json({"type": "history", "data": _log_buffer[-50:]})

    try:
        while True:
            # 接收客户端消息（心跳）
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _ws_clients.remove(ws)
    except Exception:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ========== 辅助 ==========

def _get_company():
    """获取当前运行的公司实例"""
    return _system_state.get("company")

def _serialize(obj: Any) -> Any:
    """序列化对象为 JSON 兼容格式"""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize(v) for v in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif hasattr(obj, "__dict__"):
        return _serialize(obj.__dict__)
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)


# 挂载静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
