"""中文经营报告与机器可读 JSON 输出。

报告面向三个角色：
- 财务分析人员：可比净收入、退款、汇率版本与重估差额、留存口径；
- 地区服务负责人：请求积压、容量利用率、供应商承接；
- 全球运营委员会：扩展/限量/暂停建议、依据与未履约义务台账。

所有不可比账期明确标注区间与原因；未结算金额只展示本币，不折算混入美元收入。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from .decisions import ACTION_LABELS, DecisionRecord
from .models import ObligationLedgerEntry, RegionMetrics, RegionSummary

REPORT_TITLE = "智能宠物产品多地区服务可持续经营报告（本地合成数据）"
DISCLAIMER = (
    "说明：本报告基于本地确定性合成数据，国家/地区数量、用户规模与金额均为虚构"
    "案例，不对应任何真实主体，不构成业务数据或效果承诺。"
)


def _money(value: Decimal | None, currency: str = "USD") -> str:
    if value is None:
        return "不可计算"
    return f"{value:,.2f} {currency}"


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "不可计算"
    return f"{value * 100:.1f}%"


# ---------------------------------------------------------------------------
def render_text(
    summaries: list[RegionSummary],
    decisions: list[DecisionRecord],
    ledger_entries: list[ObligationLedgerEntry],
) -> str:
    lines: list[str] = [REPORT_TITLE, "=" * 64, DISCLAIMER, ""]
    decision_by_region = {d.region: d for d in decisions}

    for s in summaries:
        lines.append(f"【地区 {s.region}｜记账本币 {s.currency}】")
        lines.append("-" * 64)
        lines.extend(_render_metrics_table(s.periods))
        lines.append("")
        lines.extend(_render_region_summary(s))
        decision = decision_by_region[s.region]
        lines.append(f"运营建议：{ACTION_LABELS[decision.action]}（{decision.action}）")
        for reason in decision.reasons:
            lines.append(f"  - {reason}")
        lines.append("")
        lines.extend(_render_ledger(s.region, ledger_entries))
        lines.append("")

    lines.append("口径与责任")
    lines.append("-" * 64)
    lines.append("1. 可比净收入与留存口径由财务分析人员确认：仅已结算渠道净额")
    lines.append("   按记录汇率版本折美元并扣减生效退款；待结算/争议金额保留本币。")
    lines.append("2. 支持容量与供应商承接由地区服务负责人确认；已接受义务即使")
    lines.append("   暂停服务也必须履约，历史义务按当时合同版本与汇率可追溯。")
    lines.append("3. 全球运营委员会结合财务、容量与合同义务做扩展/限量/暂停决策。")
    lines.append("4. 退款或汇率修订后重估当前表现；已完成服务承诺与历史账期")
    lines.append("   仍按当时条件保留，不被重写。")
    return "\n".join(lines)


def _render_metrics_table(rows: list[RegionMetrics]) -> list[str]:
    header = (
        f"{'账期':<9}{'可比性':<8}{'可比净收入(USD)':>18}"
        f"{'留存率':>9}{'付费家庭':>9}{'活跃设备':>9}{'容量利用':>9}"
    )
    out = [header]
    for m in rows:
        out.append(
            f"{m.period:<9}{m.availability_status:<8}"
            f"{m.comparable_net_usd:>18,.2f}"
            f"{_pct(m.retention_rate):>9}{m.paid_households:>9}"
            f"{m.active_devices:>9}{_pct(m.capacity_utilization):>9}"
        )
        if m.unsettled_local:
            out.append(
                f"    ↳ 未结算/争议金额（不并入净收入）: "
                f"{m.unsettled_local:,.2f} {m.unsettled_currency}"
            )
        if m.restated_net_usd is not None:
            out.append(
                f"    ↳ 当前汇率重估净收入 {m.restated_net_usd:,.2f} USD"
                f"（差额 {m.revision_delta_usd:+.2f} USD，原值保留）"
            )
        for note in m.notes:
            out.append(f"    ↳ {note}")
    return out


def _render_region_summary(s: RegionSummary) -> list[str]:
    out = [
        f"累计可比净收入：{_money(s.total_comparable_net_usd)}",
        f"最新账期 {s.latest_period} 可比：{'是' if s.latest_comparable else '否'}",
        f"不可比账期：{', '.join(s.non_comparable_periods) or '无'}"
        f"（期末连续 {s.consecutive_non_comparable} 个）",
        f"最新续费留存率：{_pct(s.latest_retention)}"
        f"（口径来源账期：{s.retention_source_period or '无'}；"
        "促销周期不计分母，家庭共享按家庭去重，换机不新增分母）",
        f"设备附着率：{_pct(s.latest_attachment_rate)}"
        f"（口径来源账期：{s.attachment_source_period or '无'}；"
        "设备在线不等于付费活跃）",
        f"经常性收入占比：{_pct(s.recurring_share)}",
        f"最新容量利用率：{_pct(s.latest_capacity_utilization)}",
        f"最新单活跃设备支持成本：{_money(s.latest_cost_per_active)}",
        f"最新未结算/争议敞口："
        f"{s.latest_unsettled_local:,.2f} {s.latest_unsettled_currency or '无'}"
        "（本币保留，不并入美元净收入）",
        f"未履约义务：{s.open_obligations} 项（其中逾期 {s.breached_obligations} 项），"
        f"待履约负债 {_money(s.outstanding_liability_usd)}",
    ]
    if s.has_fx_revision:
        out.append(
            f"汇率修订重估差额：{s.revision_delta_usd:+.2f} USD"
            "（历史账期按当时汇率保留）"
        )
    return out


def _render_ledger(region: str, entries: list[ObligationLedgerEntry]) -> list[str]:
    rows = [e for e in entries if e.obligation.region == region]
    if not rows:
        return ["义务台账：无记录"]
    out = ["义务台账（按接受时合同版本与汇率追溯）："]
    for e in rows:
        flag = "逾期" if e.is_breached else (
            "未完成" if e.is_outstanding else "已完成"
        )
        out.append(
            f"  - {e.obligation.obligation_id} {e.service_type} [{flag}] "
            f"合同 {e.contract_version}｜本币 {e.liability_local} {e.currency}"
            f"｜汇率 {e.fx_version}（1 {e.currency}={e.rate_to_usd} USD）"
            f"｜折美元 {e.liability_usd:,.2f}"
        )
    return out


# ---------------------------------------------------------------------------
def build_json_payload(
    summaries: list[RegionSummary],
    decisions: list[DecisionRecord],
    ledger_entries: list[ObligationLedgerEntry],
) -> dict:
    """构造机器可读 JSON（Decimal 统一转字符串保精度）。"""
    return {
        "disclaimer": DISCLAIMER,
        "regions": [_summary_to_dict(s) for s in summaries],
        "decisions": [_decision_to_dict(d) for d in decisions],
        "obligation_ledger": [
            {
                "obligation_id": e.obligation.obligation_id,
                "region": e.obligation.region,
                "service_type": e.service_type,
                "state": e.obligation.completion_state,
                "supplier_ref": e.supplier_ref,
                "contract_version": e.contract_version,
                "unit_cost_local": str(e.unit_cost_local),
                "currency": e.currency,
                "fx_version": e.fx_version,
                "rate_to_usd": str(e.rate_to_usd),
                "liability_usd": str(e.liability_usd),
                "is_outstanding": e.is_outstanding,
                "is_breached": e.is_breached,
            }
            for e in ledger_entries
        ],
    }


def _summary_to_dict(s: RegionSummary) -> dict:
    return {
        "region": s.region,
        "currency": s.currency,
        "latest_period": s.latest_period,
        "latest_comparable": s.latest_comparable,
        "non_comparable_periods": s.non_comparable_periods,
        "consecutive_non_comparable": s.consecutive_non_comparable,
        "latest_retention": _d(s.latest_retention),
        "retention_source_period": s.retention_source_period,
        "latest_attachment_rate": _d(s.latest_attachment_rate),
        "attachment_source_period": s.attachment_source_period,
        "recurring_share": _d(s.recurring_share),
        "latest_capacity_utilization": _d(s.latest_capacity_utilization),
        "latest_cost_per_active_usd": _d(s.latest_cost_per_active),
        "latest_unsettled_local": _d(s.latest_unsettled_local),
        "latest_unsettled_currency": s.latest_unsettled_currency,
        "total_comparable_net_usd": _d(s.total_comparable_net_usd),
        "revision_delta_usd": _d(s.revision_delta_usd),
        "open_obligations": s.open_obligations,
        "breached_obligations": s.breached_obligations,
        "outstanding_liability_usd": _d(s.outstanding_liability_usd),
        "periods": [_metric_to_dict(m) for m in s.periods],
    }


def _metric_to_dict(m: RegionMetrics) -> dict:
    return {
        "period": m.period,
        "comparable": m.comparable,
        "availability_status": m.availability_status,
        "settled_net_usd": _d(m.settled_net_usd),
        "refund_usd": _d(m.refund_usd),
        "comparable_net_usd": _d(m.comparable_net_usd),
        "recurring_net_usd": _d(m.recurring_net_usd),
        "hardware_net_usd": _d(m.hardware_net_usd),
        "unsettled_local": _d(m.unsettled_local),
        "unsettled_currency": m.unsettled_currency,
        "restated_net_usd": _d(m.restated_net_usd),
        "revision_delta_usd": _d(m.revision_delta_usd),
        "fx_versions_used": m.fx_versions_used,
        "paid_base": m.paid_base,
        "renewed": m.renewed,
        "churned": m.churned,
        "promo_count": m.promo_count,
        "promo_converted": m.promo_converted,
        "retention_rate": _d(m.retention_rate),
        "active_devices": m.active_devices,
        "online_devices": m.online_devices,
        "paid_households": m.paid_households,
        "replacement_devices": m.replacement_devices,
        "requests_total": m.requests_total,
        "requests_open": m.requests_open,
        "handling_hours": str(m.handling_hours),
        "capacity_hours": str(m.capacity_hours),
        "capacity_utilization": _d(m.capacity_utilization),
        "support_cost_usd": _d(m.support_cost_usd),
        "support_cost_per_active_usd": _d(m.support_cost_per_active_usd),
        "notes": m.notes,
    }


def _decision_to_dict(d: DecisionRecord) -> dict:
    return {
        "region": d.region,
        "action": d.action,
        "action_label": ACTION_LABELS[d.action],
        "reasons": d.reasons,
        "finance_view": d.finance_view,
        "capacity_view": d.capacity_view,
        "contract_view": d.contract_view,
        "decided_at": d.decided_at,
    }


def _d(value: Decimal | None):
    return None if value is None else str(value)


def write_report(
    out_dir: str | Path,
    summaries: list[RegionSummary],
    decisions: list[DecisionRecord],
    ledger_entries: list[ObligationLedgerEntry],
) -> tuple[Path, Path]:
    """写出 Markdown 文本报告与 JSON 报告，返回两个文件路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "经营分析报告.md"
    json_path = out_dir / "经营分析报告.json"
    md_path.write_text(render_text(summaries, decisions, ledger_entries),
                       encoding="utf-8")
    json_path.write_text(
        json.dumps(build_json_payload(summaries, decisions, ledger_entries),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path, json_path
