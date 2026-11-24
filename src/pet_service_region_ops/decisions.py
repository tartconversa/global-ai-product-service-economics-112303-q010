"""服务扩展判断（扩展 / 限量 / 暂停）。

职责分离（对应需求）：
- 财务分析人员决定可比净收入与留存口径——由 :class:`~pet_service_region_ops.metrics.MetricEngine` 固化；
- 地区服务负责人决定支持容量与供应商承接——由供应商容量视图提供；
- 全球运营委员会结合二者与合同义务做最终决策——本引擎给出可解释的建议与依据，
  所有阈值集中在 :class:`DecisionPolicy`，委员会可调整后复算。

判断优先级（自上而下，命中即定）：
1. 连续两个及以上账期不可比（数据延迟/迁移）→ 暂停新服务（先恢复数据口径）；
2. 存在违约义务或供应商被停用、容量打满 → 暂停扩展并先履约；
3. 最新账期不可比或发生汇率修订 → 限量（重估完成前不扩展）；
4. 财务与容量同时达标 → 扩展；
5. 其余情况 → 限量，并列出未达标项。

即使决策为暂停，未完成义务仍保留在台账中（见 obligations 模块）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .models import DecisionRecord, RegionSummary
from .obligations import ObligationLedger

ACTION_EXPAND = "expand"
ACTION_LIMIT = "limit"
ACTION_SUSPEND = "suspend"
ACTION_LABELS = {
    ACTION_EXPAND: "扩展本地支持",
    ACTION_LIMIT: "限量运营",
    ACTION_SUSPEND: "暂停新服务",
}


@dataclass(frozen=True)
class DecisionPolicy:
    """委员会可调的决策阈值。"""
    min_retention: Decimal = Decimal("0.80")          # 最新可比续费留存率
    min_attachment: Decimal = Decimal("0.70")         # 活跃设备/付费家庭
    min_recurring_share: Decimal = Decimal("0.70")    # 经常性收入占比
    max_capacity_utilization: Decimal = Decimal("0.85")
    max_cost_per_active_usd: Decimal = Decimal("120.00")
    max_consecutive_non_comparable: int = 1           # 超过即暂停
    max_fx_revision_delta_abs: Decimal = Decimal("200.00")


@dataclass
class DecisionEngine:
    ledger: ObligationLedger
    policy: DecisionPolicy = field(default_factory=DecisionPolicy)
    decided_at: str = "2026-12-01"

    def evaluate(self, summary: RegionSummary, period: str) -> DecisionRecord:
        p = self.policy
        reasons: list[str] = []
        capacity_view = self.ledger.supplier_capacity_view(summary.region, period)

        finance_view = self._finance_view(summary)
        capacity_block = self._capacity_view(summary, capacity_view)
        contract_view = self._contract_view(summary, capacity_view)

        # --- 规则 1：连续不可比账期 ---
        if summary.consecutive_non_comparable > p.max_consecutive_non_comparable:
            reasons.append(
                f"连续 {summary.consecutive_non_comparable} 个账期不可比"
                f"（{', '.join(summary.non_comparable_periods)}），"
                "需先恢复数据口径再评估扩展"
            )
            return self._record(summary, ACTION_SUSPEND, reasons,
                                finance_view, capacity_block, contract_view)

        # --- 规则 2：履约/供应商风险 ---
        if summary.breached_obligations:
            reasons.append(
                f"存在 {summary.breached_obligations} 项逾期违约义务，"
                "暂停扩展并优先履约"
            )
            return self._record(summary, ACTION_SUSPEND, reasons,
                                finance_view, capacity_block, contract_view)
        if capacity_view["blocked_suppliers"]:
            reasons.append(
                "供应商已被停用: "
                f"{', '.join(capacity_view['blocked_suppliers'])}，"
                "本地承接能力不足"
            )
            return self._record(summary, ACTION_SUSPEND, reasons,
                                finance_view, capacity_block, contract_view)
        if capacity_view["available_hours"] <= 0:
            reasons.append("供应商可用承接容量为零，无法支撑服务扩展")
            return self._record(summary, ACTION_SUSPEND, reasons,
                                finance_view, capacity_block, contract_view)

        # --- 规则 3：最新账期不可比 / 汇率修订 / 未结算敞口 ---
        if not summary.latest_comparable:
            reasons.append(
                f"最新账期 {summary.latest_period} 数据不可比，"
                "结论限定为观察，不做扩展投入"
            )
            return self._record(summary, ACTION_LIMIT, reasons,
                                finance_view, capacity_block, contract_view)
        if summary.latest_unsettled_local > 0:
            reasons.append(
                f"最新账期存在未结算/争议渠道金额 "
                f"{summary.latest_unsettled_local} "
                f"{summary.latest_unsettled_currency}，"
                "该本币敞口不能并入美元净收入，结算确认前限量运营"
            )
            return self._record(summary, ACTION_LIMIT, reasons,
                                finance_view, capacity_block, contract_view)
        if summary.has_fx_revision:
            delta = summary.revision_delta_usd
            if abs(delta) > p.max_fx_revision_delta_abs:
                reasons.append(
                    f"汇率版本修订重估差额 {delta:+.2f} 美元超过委员会容忍阈值 "
                    f"{p.max_fx_revision_delta_abs} 美元，重估确认前禁止扩展"
                )
                return self._record(summary, ACTION_LIMIT, reasons,
                                    finance_view, capacity_block, contract_view)
            reasons.append(
                f"汇率修订重估差额 {delta:+.2f} 美元处于容忍阈值内，"
                "历史账期按当时汇率保留，继续按可比口径评估"
            )

        # --- 规则 4：财务与容量双达标 ---
        finance_ok, finance_reasons = self._finance_checks(summary)
        capacity_ok, capacity_reasons = self._capacity_checks(summary)
        reasons.extend(finance_reasons)
        reasons.extend(capacity_reasons)
        if finance_ok and capacity_ok:
            reasons.append("财务口径与支持容量同时达标，建议扩展本地支持")
            return self._record(summary, ACTION_EXPAND, reasons,
                                finance_view, capacity_block, contract_view)

        # --- 规则 5：兜底限量 ---
        reasons.append("部分门槛未达标，维持限量运营并按上述缺口整改")
        return self._record(summary, ACTION_LIMIT, reasons,
                            finance_view, capacity_block, contract_view)

    # ------------------------------------------------------------------
    def _finance_view(self, s: RegionSummary) -> dict:
        return {
            "latest_period": s.latest_period,
            "latest_comparable": s.latest_comparable,
            "latest_retention": _num(s.latest_retention),
            "retention_source_period": s.retention_source_period,
            "latest_attachment_rate": _num(s.latest_attachment_rate),
            "attachment_source_period": s.attachment_source_period,
            "recurring_share": _num(s.recurring_share),
            "total_comparable_net_usd": _num(s.total_comparable_net_usd),
            "latest_cost_per_active_usd": _num(s.latest_cost_per_active),
            "latest_unsettled_local": _num(s.latest_unsettled_local),
            "latest_unsettled_currency": s.latest_unsettled_currency,
            "fx_revision_delta_usd": _num(s.revision_delta_usd),
            "non_comparable_periods": s.non_comparable_periods,
        }

    def _capacity_view(self, s: RegionSummary, view: dict) -> dict:
        return {
            "latest_capacity_utilization": _num(s.latest_capacity_utilization),
            "total_capacity_hours": _num(view["total_capacity_hours"]),
            "available_hours": _num(view["available_hours"]),
            "blocked_suppliers": view["blocked_suppliers"],
        }

    def _contract_view(self, s: RegionSummary, view: dict) -> dict:
        return {
            "open_obligations": s.open_obligations,
            "breached_obligations": s.breached_obligations,
            "outstanding_liability_usd": _num(s.outstanding_liability_usd),
            "active_contracts": [
                {
                    "supplier_ref": row["supplier_ref"],
                    "contract_version": row["contract_version"],
                    "service_type": row["service_type"],
                    "supplier_state": row["supplier_state"],
                    "available_hours": _num(row["available_hours"]),
                }
                for row in view["suppliers"]
            ],
        }

    def _finance_checks(self, s: RegionSummary) -> tuple[bool, list[str]]:
        p = self.policy
        ok = True
        reasons: list[str] = []
        if s.latest_retention is None or s.latest_retention < p.min_retention:
            ok = False
            reasons.append(
                f"续费留存率 {_num(s.latest_retention)}"
                f"（口径来源账期 {s.retention_source_period or '无'}）"
                f"低于阈值 {p.min_retention}"
            )
        if s.latest_attachment_rate is None or s.latest_attachment_rate < p.min_attachment:
            ok = False
            reasons.append(
                f"设备附着率 {_num(s.latest_attachment_rate)}"
                f"（口径来源账期 {s.attachment_source_period or '无'}）"
                f"低于阈值 {p.min_attachment}"
            )
        if s.recurring_share is None or s.recurring_share < p.min_recurring_share:
            ok = False
            reasons.append(
                f"经常性收入占比 {_num(s.recurring_share)} 低于阈值 "
                f"{p.min_recurring_share}，增长可能来自一次性设备销售"
            )
        if (s.latest_cost_per_active is not None
                and s.latest_cost_per_active > p.max_cost_per_active_usd):
            ok = False
            reasons.append(
                f"单活跃设备支持成本 {s.latest_cost_per_active} 美元"
                f"高于阈值 {p.max_cost_per_active_usd} 美元"
            )
        return ok, reasons

    def _capacity_checks(self, s: RegionSummary) -> tuple[bool, list[str]]:
        p = self.policy
        ok = True
        reasons: list[str] = []
        if (s.latest_capacity_utilization is None
                or s.latest_capacity_utilization > p.max_capacity_utilization):
            ok = False
            reasons.append(
                f"支持容量利用率 {_num(s.latest_capacity_utilization)} "
                f"高于阈值 {p.max_capacity_utilization}"
            )
        return ok, reasons

    def _record(self, summary: RegionSummary, action: str, reasons: list[str],
                finance: dict, capacity: dict, contract: dict) -> DecisionRecord:
        return DecisionRecord(
            region=summary.region,
            action=action,
            reasons=reasons,
            finance_view=finance,
            capacity_view=capacity,
            contract_view=contract,
            decided_at=self.decided_at,
        )


def _num(value: Decimal | None) -> str | None:
    """Decimal 转字符串，避免 JSON 序列化丢失精度；None 透传。"""
    return None if value is None else str(value)
