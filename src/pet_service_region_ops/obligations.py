"""已承诺服务义务台账。

业务规则（对应需求）：
- 已接受的服务承诺在暂停/限量决策后仍必须履约，不能随经营调整撤销；
- 每条义务按其 **接受时的合同版本** 确定单价（历史账期按当时条件可追溯），
  按义务自带的 ``fx_version`` 折算美元，新合同版本不重算旧义务；
- 超过 ``due_at`` 仍未完成的义务标记为违约风险；
- 台账同时输出本币负债与美元负债，供全球运营委员会评估暂停成本。
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .fx import FxLedger
from .ingestion import DataBundle
from .models import (
    OBL_COMPLETE,
    OUTSTANDING_OBLIGATION_STATES,
    ObligationLedgerEntry,
    RegionSummary,
    ServiceObligation,
    _q2,
)


class ObligationLedger:
    """义务台账：合同版本 + 汇率版本双追溯。"""

    def __init__(self, bundle: DataBundle, fx: FxLedger):
        self.b = bundle
        self.fx = fx
        self.contracts = {
            (c.supplier_ref, c.contract_version): c for c in bundle.contracts
        }

    def _entry(self, ob: ServiceObligation) -> ObligationLedgerEntry:
        contract = self.contracts.get((ob.supplier_ref, ob.contract_version))
        if contract is None:
            # ingestion 已做外键校验，这里属于防御性分支
            raise KeyError(
                f"义务 {ob.obligation_id} 的合同版本 "
                f"{ob.supplier_ref}/{ob.contract_version} 不存在"
            )
        liability_local = contract.unit_cost_local
        converted = self.fx.convert(
            liability_local, ob.region, ob.fx_version
        )
        return ObligationLedgerEntry(
            obligation=ob,
            supplier_ref=ob.supplier_ref,
            service_type=ob.service_type,
            contract_version=ob.contract_version,
            unit_cost_local=liability_local,
            currency=contract.currency,
            fx_version=ob.fx_version,
            rate_to_usd=converted.rate_to_usd,
            liability_local=liability_local,
            liability_usd=converted.usd_amount,
            is_outstanding=ob.completion_state in OUTSTANDING_OBLIGATION_STATES,
            is_breached=ob.completion_state == "breached",
        )

    def entries(self) -> list[ObligationLedgerEntry]:
        """全量台账行，按地区与接受时间排序。"""
        rows = [self._entry(ob) for ob in self.b.obligations]
        return sorted(rows, key=lambda e: (
            e.obligation.region, e.obligation.accepted_at,
            e.obligation.obligation_id,
        ))

    def entries_for_region(self, region: str) -> list[ObligationLedgerEntry]:
        return [e for e in self.entries() if e.obligation.region == region]

    def annotate_summaries(self, summaries: list[RegionSummary]) -> None:
        """把义务数量与未履约负债回填到地区汇总。"""
        by_region: dict[str, list[ObligationLedgerEntry]] = defaultdict(list)
        for e in self.entries():
            by_region[e.obligation.region].append(e)
        for summary in summaries:
            rows = by_region.get(summary.region, [])
            outstanding = [e for e in rows if e.is_outstanding]
            summary.open_obligations = len(outstanding)
            summary.breached_obligations = sum(1 for e in rows if e.is_breached)
            summary.outstanding_liability_usd = _q2(sum(
                (e.liability_usd for e in outstanding), Decimal("0.00")
            ))

    # ------------------------------------------------------------------
    def supplier_capacity_view(self, region: str, period: str) -> dict:
        """供应商承接能力视图（地区服务负责人决策依据）。

        汇总该地区当前有效合同的容量，以及未结案义务占用情况；
        ``suspended`` 供应商不能承接新增服务。
        """
        contracts = [c for c in self.b.contracts
                     if c.region == region and c.valid_to == ""]
        view = {"suppliers": [], "total_capacity_hours": Decimal("0"),
                "available_hours": Decimal("0"), "blocked_suppliers": []}
        outstanding_hours: dict[str, Decimal] = defaultdict(Decimal)
        for e in self.entries_for_region(region):
            if e.is_outstanding:
                outstanding_hours[e.supplier_ref] += Decimal("1")
        for c in contracts:
            used = outstanding_hours.get(c.supplier_ref, Decimal("0"))
            available = c.capacity_hours - used
            row = {
                "supplier_ref": c.supplier_ref,
                "contract_version": c.contract_version,
                "service_type": c.service_type,
                "supplier_state": c.supplier_state,
                "capacity_hours": c.capacity_hours,
                "outstanding_orders": used,
                "available_hours": available,
            }
            view["suppliers"].append(row)
            if c.supplier_state == "suspended":
                view["blocked_suppliers"].append(c.supplier_ref)
            else:
                view["total_capacity_hours"] += c.capacity_hours
                view["available_hours"] += available
        return view
