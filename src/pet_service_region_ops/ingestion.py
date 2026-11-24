"""多地区经营资料导入与校验。

设计要点：
- 只读取目录内约定的 12 个 CSV，UTF-8（带 BOM 也可）；
- 逐行做类型/枚举/必填校验，错误带文件名与行号，便于地区团队修正；
- 保留原始状态字符串，未知/处理中状态不会被当成零值或成功；
- 跨文件引用做轻量完整性检查（外键、汇率版本、合同版本）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    AVAILABILITY,
    CONNECTIVITY,
    ActivityEvent,
    ChannelSettlement,
    DataAvailability,
    Device,
    FxRate,
    Household,
    LocalCost,
    OBLIGATION_STATES,
    REFUND_STATES,
    REQUEST_STATES,
    Refund,
    ServiceObligation,
    ServiceRequest,
    Subscription,
    SUBSCRIPTION_STATES,
    SETTLEMENT_STATES,
    SUPPLIER_STATES,
    SupplierContract,
)


class IngestionError(ValueError):
    """资料导入错误：消息中包含文件名、行号与字段名。"""


@dataclass(frozen=True)
class DataBundle:
    """一次导入的全部资料，按类型索引。"""
    households: list[Household]
    devices: list[Device]
    activities: list[ActivityEvent]
    subscriptions: list[Subscription]
    refunds: list[Refund]
    settlements: list[ChannelSettlement]
    requests: list[ServiceRequest]
    contracts: list[SupplierContract]
    obligations: list[ServiceObligation]
    costs: list[LocalCost]
    fx_rates: list[FxRate]
    availability: list[DataAvailability]

    @property
    def regions(self) -> list[str]:
        return sorted({h.home_region for h in self.households})


# 文件名 -> (数据类, 字段顺序)
_SCHEMA: dict[str, tuple[type, tuple[str, ...]]] = {
    "households.csv": (
        Household,
        ("household_id", "home_region", "family_size", "migrated_from",
         "migrated_at", "migration_state"),
    ),
    "devices.csv": (
        Device,
        ("device_id", "household_id", "region", "sku", "installed_at",
         "replaced_device_id", "device_state"),
    ),
    "device_activity.csv": (
        ActivityEvent,
        ("device_id", "household_id", "region", "active_date",
         "activity_minutes", "connectivity_state", "firmware"),
    ),
    "subscriptions.csv": (
        Subscription,
        ("subscription_id", "household_id", "region", "plan", "currency",
         "period", "period_start", "period_end", "local_price",
         "is_family_plan", "is_promo", "subscription_state", "fx_version"),
    ),
    "refunds.csv": (
        Refund,
        ("refund_id", "subscription_id", "region", "currency", "period",
         "local_amount", "refund_state", "refunded_at", "fx_version"),
    ),
    "channel_settlements.csv": (
        ChannelSettlement,
        ("settlement_id", "channel", "region", "currency", "period",
         "local_gross", "local_fee", "local_net", "revenue_kind",
         "fx_version", "settlement_state"),
    ),
    "service_requests.csv": (
        ServiceRequest,
        ("request_id", "region", "household_id", "device_id", "period",
         "opened_at", "closed_at", "request_state", "handling_hours"),
    ),
    "supplier_contracts.csv": (
        SupplierContract,
        ("supplier_ref", "region", "contract_version", "valid_from",
         "valid_to", "service_type", "unit_cost_local", "currency",
         "capacity_hours", "supplier_state"),
    ),
    "service_obligations.csv": (
        ServiceObligation,
        ("obligation_id", "region", "household_id", "service_type",
         "supplier_ref", "contract_version", "accepted_at", "due_at",
         "completion_state", "fx_version"),
    ),
    "local_costs.csv": (
        LocalCost,
        ("region", "period", "support_cost", "returns_cost", "partner_fee",
         "capacity_hours", "cost_version", "currency", "fx_version"),
    ),
    "fx_rates.csv": (
        FxRate,
        ("region", "currency", "fx_version", "rate_to_usd", "valid_from",
         "valid_to", "supersedes"),
    ),
    "data_availability.csv": (
        DataAvailability,
        ("region", "period", "status", "note"),
    ),
}

_INT_FIELDS = frozenset({"family_size", "activity_minutes"})
_DECIMAL_FIELDS = frozenset({
    "local_price", "local_amount", "local_gross", "local_fee", "local_net",
    "handling_hours", "unit_cost_local", "capacity_hours", "support_cost",
    "returns_cost", "partner_fee", "rate_to_usd",
})
_BOOL_FIELDS = frozenset({"is_family_plan", "is_promo"})
_ENUM_FIELDS: dict[str, frozenset] = {
    "subscription_state": SUBSCRIPTION_STATES,
    "settlement_state": SETTLEMENT_STATES,
    "refund_state": REFUND_STATES,
    "request_state": REQUEST_STATES,
    "completion_state": OBLIGATION_STATES,
    "supplier_state": SUPPLIER_STATES,
    "connectivity_state": CONNECTIVITY,
    "status": AVAILABILITY,
}


def _parse_bool(raw: str, filename: str, line_no: int, field: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n"}:
        return False
    raise IngestionError(
        f"{filename} 第 {line_no} 行字段 {field} 不是布尔值: {raw!r}"
    )


def _parse_decimal(raw: str, filename: str, line_no: int, field: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise IngestionError(
            f"{filename} 第 {line_no} 行字段 {field} 不是数值: {raw!r}"
        ) from None
    if value < 0:
        raise IngestionError(
            f"{filename} 第 {line_no} 行字段 {field} 不允许负数: {raw!r}"
        )
    return value


def _parse_int(raw: str, filename: str, line_no: int, field: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        raise IngestionError(
            f"{filename} 第 {line_no} 行字段 {field} 不是整数: {raw!r}"
        ) from None


def _read_csv(filename: str, data_dir: Path) -> list[dict[str, str]]:
    path = data_dir / filename
    if not path.exists():
        raise IngestionError(f"缺少必需资料文件: {path}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        expected = list(_SCHEMA[filename][1])
        if reader.fieldnames != expected:
            raise IngestionError(
                f"{filename} 表头不匹配。\n  期望: {expected}\n  实际: {reader.fieldnames}"
            )
        rows: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):  # 表头占第 1 行
            if any(value is None for value in row.values()):
                raise IngestionError(f"{filename} 第 {line_no} 行存在缺列")
            if any(value.strip() == "" for key, value in row.items()
                   if key not in {"migrated_from", "migrated_at",
                                  "migration_state", "replaced_device_id",
                                  "closed_at", "valid_to", "supersedes",
                                  "note"}):
                raise IngestionError(f"{filename} 第 {line_no} 行存在必填空值")
            rows.append(row)
        return rows


def _build_rows(filename: str, data_dir: Path) -> list:
    cls, columns = _SCHEMA[filename]
    objects = []
    for line_no, row in enumerate(_read_csv(filename, data_dir), start=2):
        kwargs: dict[str, object] = {}
        for field in columns:
            raw = row[field].strip()
            if field in _BOOL_FIELDS:
                kwargs[field] = _parse_bool(raw, filename, line_no, field)
            elif field in _INT_FIELDS:
                kwargs[field] = _parse_int(raw, filename, line_no, field)
            elif field in _DECIMAL_FIELDS:
                kwargs[field] = _parse_decimal(raw, filename, line_no, field)
            elif field in _ENUM_FIELDS:
                allowed = _ENUM_FIELDS[field]
                if raw not in allowed:
                    raise IngestionError(
                        f"{filename} 第 {line_no} 行字段 {field} 取值非法: "
                        f"{raw!r}，允许值: {sorted(allowed)}"
                    )
                kwargs[field] = raw
            else:
                kwargs[field] = raw
        objects.append(cls(**kwargs))
    if not objects:
        raise IngestionError(f"{filename} 没有任何数据行")
    return objects


def _check_references(bundle: DataBundle) -> None:
    """跨文件引用完整性检查。"""
    filename = "cross-reference"
    household_ids = {h.household_id for h in bundle.households}
    device_ids = {d.device_id for d in bundle.devices}
    fx_keys = {(f.region, f.fx_version) for f in bundle.fx_rates}
    contract_keys = {(c.supplier_ref, c.contract_version) for c in bundle.contracts}
    sub_ids = {s.subscription_id for s in bundle.subscriptions}

    def need(condition: bool, message: str) -> None:
        if not condition:
            raise IngestionError(f"{filename}: {message}")

    for d in bundle.devices:
        need(d.household_id in household_ids,
             f"设备 {d.device_id} 引用了未知家庭 {d.household_id}")
        if d.replaced_device_id:
            need(d.replaced_device_id in device_ids,
                 f"设备 {d.device_id} 的换机来源 {d.replaced_device_id} 不存在")
    for a in bundle.activities:
        need(a.device_id in device_ids,
             f"活跃事件引用了未知设备 {a.device_id}")
    for s in bundle.subscriptions:
        need(s.household_id in household_ids,
             f"订阅 {s.subscription_id} 引用了未知家庭")
        need((s.region, s.fx_version) in fx_keys,
             f"订阅 {s.subscription_id} 引用了未知汇率版本 {s.fx_version}")
    for r in bundle.refunds:
        need(r.subscription_id in sub_ids,
             f"退款 {r.refund_id} 引用了未知订阅 {r.subscription_id}")
        need((r.region, r.fx_version) in fx_keys,
             f"退款 {r.refund_id} 引用了未知汇率版本 {r.fx_version}")
    for st in bundle.settlements:
        need((st.region, st.fx_version) in fx_keys,
             f"结算 {st.settlement_id} 引用了未知汇率版本 {st.fx_version}")
    for ob in bundle.obligations:
        need((ob.supplier_ref, ob.contract_version) in contract_keys,
             f"义务 {ob.obligation_id} 引用了未知合同版本 "
             f"{ob.supplier_ref}/{ob.contract_version}")
        need((ob.region, ob.fx_version) in fx_keys,
             f"义务 {ob.obligation_id} 引用了未知汇率版本 {ob.fx_version}")
    for c in bundle.costs:
        need((c.region, c.fx_version) in fx_keys,
             f"地区成本 {c.region}/{c.period} 引用了未知汇率版本")
    # 可用性声明必须覆盖实际出现的地区/账期组合
    avail_keys = {(a.region, a.period) for a in bundle.availability}
    used_keys = {(s.region, s.period) for s in bundle.subscriptions}
    missing = sorted(used_keys - avail_keys)
    need(not missing, f"以下地区/账期缺少数据可用性声明: {missing}")


def load_bundle(data_dir: str | Path) -> DataBundle:
    """从目录导入全部 CSV 并校验，返回 :class:`DataBundle`。"""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise IngestionError(f"资料目录不存在: {data_dir}")
    bundle = DataBundle(
        households=_build_rows("households.csv", data_dir),
        devices=_build_rows("devices.csv", data_dir),
        activities=_build_rows("device_activity.csv", data_dir),
        subscriptions=_build_rows("subscriptions.csv", data_dir),
        refunds=_build_rows("refunds.csv", data_dir),
        settlements=_build_rows("channel_settlements.csv", data_dir),
        requests=_build_rows("service_requests.csv", data_dir),
        contracts=_build_rows("supplier_contracts.csv", data_dir),
        obligations=_build_rows("service_obligations.csv", data_dir),
        costs=_build_rows("local_costs.csv", data_dir),
        fx_rates=_build_rows("fx_rates.csv", data_dir),
        availability=_build_rows("data_availability.csv", data_dir),
    )
    _check_references(bundle)
    return bundle
