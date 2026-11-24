"""输入资料的数据类与业务状态常量。

所有金额字段统一使用 :class:`decimal.Decimal`，避免浮点误差；
日期/时间字段保留为 ISO 字符串，比较时按字典序（合成资料均为定长 ISO 格式）。

状态常量集中定义，未知/处理中状态一律不得解释为零值或成功（见《资料契约》）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# ---------------------------------------------------------------------------
# 状态常量：与合成资料、资料契约保持一致
# ---------------------------------------------------------------------------

# 订阅状态
SUB_ACTIVE = "active"          # 当期有效
SUB_RENEWED = "renewed"        # 已续费（期末进入下一周期）
SUB_CHURNED = "churned"        # 到期未续
SUB_REFUNDED = "refunded"      # 已退款，订阅作废
SUB_PROMO = "promo"            # 促销/试用周期（不计入付费留存分母）
SUB_CANCELLED = "cancelled"    # 主动取消

SUBSCRIPTION_STATES = frozenset(
    {SUB_ACTIVE, SUB_RENEWED, SUB_CHURNED, SUB_REFUNDED, SUB_PROMO, SUB_CANCELLED}
)
# 构成"付费订阅基数"的状态（促销期不是付费周期，退款/取消已不成立）
PAID_SUB_STATES = frozenset({SUB_ACTIVE, SUB_RENEWED, SUB_CHURNED})

# 渠道结算状态
SETTLE_SETTLED = "settled"     # 已结算：可计入可比净收入
SETTLE_PENDING = "pending"     # 待结算：金额未结算，不能并入净收入
SETTLE_PARTIAL = "partial"     # 部分结算
SETTLE_DISPUTED = "disputed"   # 争议中
SETTLEMENT_STATES = frozenset(
    {SETTLE_SETTLED, SETTLE_PENDING, SETTLE_PARTIAL, SETTLE_DISPUTED}
)
# 可计入可比净收入的结算状态
REVENUE_SETTLE_STATES = frozenset({SETTLE_SETTLED, SETTLE_PARTIAL})

# 退款状态
REFUND_PAID = "paid"
REFUND_APPROVED = "approved"   # 已批准未支付：冲减待生效
REFUND_REJECTED = "rejected"
REFUND_STATES = frozenset({REFUND_PAID, REFUND_APPROVED, REFUND_REJECTED})
# 已实际冲减收入的退款
EFFECTIVE_REFUND_STATES = frozenset({REFUND_PAID, REFUND_APPROVED})

# 服务请求状态
REQ_OPEN = "open"
REQ_IN_PROGRESS = "in-progress"
REQ_RESOLVED = "resolved"
REQ_ESCALATED = "escalated"
REQUEST_STATES = frozenset(
    {REQ_OPEN, REQ_IN_PROGRESS, REQ_RESOLVED, REQ_ESCALATED}
)
# 未结案状态（计算积压量时使用）
OPEN_REQUEST_STATES = frozenset({REQ_OPEN, REQ_IN_PROGRESS, REQ_ESCALATED})

# 服务义务状态
OBL_ACCEPTED = "accepted"
OBL_IN_PROGRESS = "in-progress"
OBL_COMPLETE = "complete"
OBL_BREACHED = "breached"     # 超过 due_at 未完成
OBLIGATION_STATES = frozenset(
    {OBL_ACCEPTED, OBL_IN_PROGRESS, OBL_COMPLETE, OBL_BREACHED}
)
# 仍需履约的义务（暂停服务也不能撤销）
OUTSTANDING_OBLIGATION_STATES = frozenset(
    {OBL_ACCEPTED, OBL_IN_PROGRESS, OBL_BREACHED}
)

# 供应商承接状态
SUPPLIER_ACTIVE = "active"
SUPPLIER_AT_RISK = "at-risk"
SUPPLIER_SUSPENDED = "suspended"
SUPPLIER_STATES = frozenset(
    {SUPPLIER_ACTIVE, SUPPLIER_AT_RISK, SUPPLIER_SUSPENDED}
)

# 设备连接与数据可用性
CONNECTIVITY = frozenset({"online", "partial", "offline"})
AVAILABILITY = frozenset({"final", "lagged", "migration"})

# 家庭迁移状态
MIGRATION_DONE = "done"
MIGRATION_PENDING = "pending"


def D(value: str | Decimal | int) -> Decimal:
    """构造 Decimal 的简写。"""
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    """金额保留两位小数。"""
    return value.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 输入资料数据类（字段与 data/pet_ops 下的 CSV 一一对应）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Household:
    household_id: str
    home_region: str
    family_size: int
    migrated_from: str          # 空串表示无迁移
    migrated_at: str
    migration_state: str


@dataclass(frozen=True)
class Device:
    device_id: str
    household_id: str
    region: str
    sku: str
    installed_at: str
    replaced_device_id: str     # 空串表示非换机
    device_state: str           # active | returned | replaced


@dataclass(frozen=True)
class ActivityEvent:
    device_id: str
    household_id: str
    region: str
    active_date: str
    activity_minutes: int
    connectivity_state: str
    firmware: str


@dataclass(frozen=True)
class Subscription:
    subscription_id: str
    household_id: str
    region: str
    plan: str
    currency: str
    period: str                 # YYYY-MM 账期
    period_start: str
    period_end: str
    local_price: Decimal
    is_family_plan: bool
    is_promo: bool
    subscription_state: str
    fx_version: str


@dataclass(frozen=True)
class Refund:
    refund_id: str
    subscription_id: str
    region: str
    currency: str
    period: str
    local_amount: Decimal
    refund_state: str
    refunded_at: str
    fx_version: str


@dataclass(frozen=True)
class ChannelSettlement:
    settlement_id: str
    channel: str
    region: str
    currency: str
    period: str
    local_gross: Decimal
    local_fee: Decimal
    local_net: Decimal
    revenue_kind: str           # recurring | hardware
    fx_version: str
    settlement_state: str


@dataclass(frozen=True)
class ServiceRequest:
    request_id: str
    region: str
    household_id: str
    device_id: str
    period: str
    opened_at: str
    closed_at: str              # 空串表示未关闭
    request_state: str
    handling_hours: Decimal


@dataclass(frozen=True)
class SupplierContract:
    supplier_ref: str
    region: str
    contract_version: str
    valid_from: str
    valid_to: str               # 空串表示仍有效
    service_type: str
    unit_cost_local: Decimal
    currency: str
    capacity_hours: Decimal
    supplier_state: str


@dataclass(frozen=True)
class ServiceObligation:
    obligation_id: str
    region: str
    household_id: str
    service_type: str
    supplier_ref: str
    contract_version: str
    accepted_at: str
    due_at: str
    completion_state: str
    fx_version: str


@dataclass(frozen=True)
class LocalCost:
    region: str
    period: str
    support_cost: Decimal
    returns_cost: Decimal
    partner_fee: Decimal
    capacity_hours: Decimal
    cost_version: str
    currency: str
    fx_version: str


@dataclass(frozen=True)
class FxRate:
    region: str
    currency: str
    fx_version: str
    rate_to_usd: Decimal        # 1 单位本币 = rate_to_usd 美元
    valid_from: str
    valid_to: str               # 空串表示当前有效
    supersedes: str             # 被替代的旧版本，空串表示首个版本


@dataclass(frozen=True)
class DataAvailability:
    region: str
    period: str
    status: str                 # final | lagged | migration
    note: str


# ---------------------------------------------------------------------------
# 计算结果数据类
# ---------------------------------------------------------------------------

@dataclass
class RegionMetrics:
    """单个地区单个账期的可比经营指标（金额统一为美元）。"""
    region: str
    period: str
    comparable: bool
    availability_status: str
    # 收入（美元，已结算口径）
    settled_net_usd: Decimal = Decimal("0.00")
    refund_usd: Decimal = Decimal("0.00")
    comparable_net_usd: Decimal = Decimal("0.00")
    recurring_net_usd: Decimal = Decimal("0.00")
    hardware_net_usd: Decimal = Decimal("0.00")
    # 未结算渠道金额（本币原值，不得并入美元净收入）
    unsettled_local: Decimal = Decimal("0.00")
    unsettled_currency: str = ""
    # 若为未结账期，按当前汇率重估的美元净额与差额
    restated_net_usd: Decimal | None = None
    revision_delta_usd: Decimal | None = None
    fx_versions_used: list[str] = field(default_factory=list)
    # 订阅留存
    paid_base: int = 0
    renewed: int = 0
    churned: int = 0
    promo_count: int = 0
    promo_converted: int = 0
    replacement_devices: int = 0
    retention_rate: Decimal | None = None
    # 活跃与设备
    active_devices: int = 0
    online_devices: int = 0
    paid_households: int = 0
    # 支持成本
    requests_total: int = 0
    requests_open: int = 0
    handling_hours: Decimal = Decimal("0.00")
    capacity_hours: Decimal = Decimal("0.00")
    capacity_utilization: Decimal | None = None
    support_cost_usd: Decimal = Decimal("0.00")
    support_cost_per_active_usd: Decimal | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RegionSummary:
    """地区级汇总（跨账期）。"""
    region: str
    currency: str
    periods: list[RegionMetrics]
    latest_period: str
    latest_comparable: bool
    non_comparable_periods: list[str]
    consecutive_non_comparable: int
    latest_retention: Decimal | None
    retention_source_period: str | None
    latest_attachment_rate: Decimal | None
    attachment_source_period: str | None
    recurring_share: Decimal
    latest_capacity_utilization: Decimal | None
    latest_cost_per_active: Decimal | None
    latest_unsettled_local: Decimal
    latest_unsettled_currency: str
    total_comparable_net_usd: Decimal
    open_obligations: int
    breached_obligations: int
    outstanding_liability_usd: Decimal
    has_fx_revision: bool
    revision_delta_usd: Decimal
    notes: list[str] = field(default_factory=list)


@dataclass
class DecisionRecord:
    """全球运营委员会的地区决策记录。"""
    region: str
    action: str                 # expand | limit | suspend
    reasons: list[str]
    finance_view: dict
    capacity_view: dict
    contract_view: dict
    decided_at: str


@dataclass
class ObligationLedgerEntry:
    """义务台账行：历史义务按当时合同与汇率可追溯。"""
    obligation: ServiceObligation
    supplier_ref: str
    service_type: str
    contract_version: str
    unit_cost_local: Decimal
    currency: str
    fx_version: str
    rate_to_usd: Decimal
    liability_local: Decimal
    liability_usd: Decimal
    is_outstanding: bool
    is_breached: bool
