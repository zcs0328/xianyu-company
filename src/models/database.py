"""
数据模型定义
使用 SQLAlchemy 2.0 声明式映射，SQLite 存储
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, Boolean,
    ForeignKey, JSON, Enum as SAEnum, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


# ========== 枚举 ==========

class MessageDirection(str, PyEnum):
    """消息方向"""
    INCOMING = "incoming"   # 买家发来
    OUTGOING = "outgoing"   # 系统回复


class MessageIntent(str, PyEnum):
    """消息意图分类"""
    INQUIRY = "inquiry"           # 咨询商品
    BARGAIN = "bargain"           # 议价
    ORDER_INTENT = "order_intent" # 下单意向
    AFTER_SALE = "after_sale"     # 售后
    CHITCHAT = "chitchat"         # 闲聊
    UNKNOWN = "unknown"


class OrderStatus(str, PyEnum):
    """订单状态（对齐闲鱼担保交易流程）"""
    PENDING = "pending"           # 待付款
    PAID = "paid"                 # 已付款（资金在担保账户）
    SHIPPED = "shipped"           # 已发货
    RECEIVED = "received"         # 已确认收货
    COMPLETED = "completed"       # 已完成（资金到账）
    REFUNDED = "refunded"         # 已退款
    CLOSED = "closed"             # 已关闭


class TransactionType(str, PyEnum):
    """交易类型"""
    INCOME = "income"       # 收入（买家付款到账）
    COST = "cost"           # 成本（货源采购）
    SHIPPING = "shipping"   # 运费
    REFUND = "refund"       # 退款
    PLATFORM_FEE = "platform_fee"  # 平台手续费


class RiskLevel(str, PyEnum):
    """风控等级"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AgentRole(str, PyEnum):
    """智能体角色"""
    CEO = "ceo"
    PURCHASING = "purchasing"
    PRICING = "pricing"
    REVIEW = "review"
    PACKAGING = "packaging"
    OPERATIONS = "operations"
    ACCOUNTING = "accounting"
    RISK_CONTROL = "risk_control"


# ========== 模型 ==========

class XianyuMessage(Base):
    """闲鱼聊天消息"""
    __tablename__ = "xianyu_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), unique=True, nullable=False, comment="闲鱼消息ID")
    account_name = Column(String(32), nullable=False, comment="所属闲鱼账号名")
    buyer_id = Column(String(64), nullable=False, comment="买家用户ID")
    buyer_nickname = Column(String(128), default="", comment="买家昵称")
    item_id = Column(String(64), default="", comment="关联商品ID")
    item_title = Column(String(256), default="", comment="关联商品标题")
    direction = Column(SAEnum(MessageDirection), nullable=False)
    content = Column(Text, nullable=False, comment="消息内容")
    intent = Column(SAEnum(MessageIntent), default=MessageIntent.UNKNOWN)
    # AI 处理信息
    ai_processed = Column(Boolean, default=False, comment="是否已由AI处理")
    ai_reply = Column(Text, default="", comment="AI生成的回复")
    confidence = Column(Float, default=0.0, comment="AI回复置信度")
    # 元数据
    raw_data = Column(JSON, default=dict, comment="原始消息数据")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    replied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_msg_account_buyer", "account_name", "buyer_id"),
        Index("idx_msg_created", "created_at"),
    )


class XianyuOrder(Base):
    """闲鱼订单"""
    __tablename__ = "xianyu_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, nullable=False, comment="闲鱼订单号")
    account_name = Column(String(32), nullable=False)
    buyer_id = Column(String(64), nullable=False)
    buyer_nickname = Column(String(128), default="")
    item_id = Column(String(64), nullable=False)
    item_title = Column(String(256), default="")
    # 价格
    sell_price = Column(Float, nullable=False, comment="闲鱼售价")
    source_price = Column(Float, default=0.0, comment="货源采购价")
    shipping_cost = Column(Float, default=0.0, comment="运费")
    platform_fee = Column(Float, default=0.0, comment="平台手续费")
    profit = Column(Float, default=0.0, comment="利润")
    # 状态
    status = Column(SAEnum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    # 物流
    tracking_number = Column(String(64), default="", comment="快递单号")
    logistics_company = Column(String(32), default="")
    # 货源
    source_platform = Column(String(32), default="", comment="货源平台(pdd/1688)")
    source_order_id = Column(String(64), default="", comment="货源平台订单号")
    # 时间线
    paid_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    # 担保交易关键信息
    escrow_released = Column(Boolean, default=False, comment="担保资金是否已放款")
    # 元数据
    raw_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_order_status", "status"),
        Index("idx_order_account", "account_name"),
    )


class Transaction(Base):
    """交易记录（会计用）"""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), default="", comment="关联闲鱼订单号")
    account_name = Column(String(32), nullable=False)
    transaction_type = Column(SAEnum(TransactionType), nullable=False)
    amount = Column(Float, nullable=False, comment="金额（正数）")
    description = Column(String(512), default="")
    # 担保交易追踪
    escrow_status = Column(String(32), default="", comment="担保状态: frozen/released")
    # 元数据
    raw_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_tx_order", "order_id"),
        Index("idx_tx_type", "transaction_type"),
        Index("idx_tx_created", "created_at"),
    )


class RiskLog(Base):
    """风控日志"""
    __tablename__ = "risk_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_name = Column(String(32), default="", comment="关联账号")
    level = Column(SAEnum(RiskLevel), nullable=False)
    category = Column(String(64), nullable=False, comment="风险类别")
    message = Column(Text, nullable=False)
    action_taken = Column(String(256), default="", comment="已采取措施")
    auto_paused = Column(Boolean, default=False, comment="是否触发了自动暂停")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_risk_level", "level"),
        Index("idx_risk_created", "created_at"),
    )


class DailyStats(Base):
    """每日运营统计"""
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_name = Column(String(32), nullable=False)
    stat_date = Column(String(10), nullable=False, comment="日期 YYYY-MM-DD")
    # 发布
    items_published = Column(Integer, default=0)
    items_polished = Column(Integer, default=0)
    # 客服
    messages_received = Column(Integer, default=0)
    messages_replied = Column(Integer, default=0)
    avg_reply_time_sec = Column(Float, default=0.0)
    # 交易
    orders_created = Column(Integer, default=0)
    orders_completed = Column(Integer, default=0)
    revenue = Column(Float, default=0.0)
    cost = Column(Float, default=0.0)
    profit = Column(Float, default=0.0)
    # 风控
    risk_warnings = Column(Integer, default=0)
    auto_paused_count = Column(Integer, default=0)
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_stats_date", "stat_date"),
        Index("idx_stats_account_date", "account_name", "stat_date", unique=True),
    )


class AgentLog(Base):
    """智能体操作日志（审计用）"""
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(SAEnum(AgentRole), nullable=False)
    action = Column(String(128), nullable=False, comment="执行的动作")
    input_summary = Column(Text, default="", comment="输入摘要")
    output_summary = Column(Text, default="", comment="输出摘要")
    llm_model = Column(String(64), default="")
    tokens_used = Column(Integer, default=0)
    cost_yuan = Column(Float, default=0.0)
    duration_sec = Column(Float, default=0.0)
    success = Column(Boolean, default=True)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_agent_role", "role"),
        Index("idx_agent_created", "created_at"),
    )
