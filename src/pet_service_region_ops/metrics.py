"""地区经营指标计算。

口径决策由财务分析人员负责，本模块只固化已确认的口径：

1. **可比净收入（美元）**：仅汇总已结算/部分结算渠道的本币净额，按记录自带
   汇率版本折算后，减去已生效退款；待结算、争议金额保留本币，绝不并入美元收入。
2. **跨币种**：所有美元折算保留 ``fx_version`` 轨迹；已结账期按当时汇率，
   未结账期额外给当前汇率重估值与差额，原值不被覆盖。
3. **留存分母**：促销/试用周期不是付费周期，不计入续费分母；家庭共享计划
   按"家庭"去重（一个家庭多台设备/多人使用不重复计数）；换机只改变设备标识，
   不制造新的续费家庭；迁移家庭在数据可用性声明为 migration 的账期整体标记不可比。
4. **设备在线 ≠ 付费活跃**：活跃设备来自设备活跃事件，付费家庭来自付费订阅，
   两者分别统计并计算设备附着率。
5. **不可比范围**：地区数据延迟（lagged）或用户迁移（migration）的账期不与
   final 账期混算，汇总指标基于可比账期，同时保留不可比账期的原始观察值。
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .fx import FxLedger
from .ingestion import DataBundle
from .models import (
    EFFECTIVE_REFUND_STATES,
    OPEN_REQUEST_STATES,
    PAID_SUB_STATES,
    REVENUE_SETTLE_STATES,
    ChannelSettlement,
    LocalCost,
    RegionMetrics,
    RegionSummary,
    ServiceRequest,
    Subscription,
    _q2,
)


def _period_of(date_str: str) -> str:
    """YYYY-MM-DD -> YYYY-MM（合成资料均为定长 ISO 日期）。"""
    return date_str[:7]


class MetricEngine:
    """基于一次导入的 :class:`DataBundle` 计算全部地区/账期指标。"""

    def __init__(self, bundle: DataBundle, fx: FxLedger):
        self.b = bundle
        self.fx = fx
        self._index()

    # ------------------------------------------------------------------
    def _index(self) -> None:
        b = self.b
        self.household = {h.household_id: h for h in b.households}
        self.availability = {(a.region, a.period): a for a in b.availability}
        self.costs = {(c.region, c.period): c for c in b.costs}
        self.subs_by_rp: dict[tuple[str, str], list[Subscription]] = defaultdict(list)
        for s in b.subscriptions:
            self.subs_by_rp[(s.region, s.period)].append(s)
        self.sett_by_rp: dict[tuple[str, str], list[ChannelSettlement]] = defaultdict(list)
        for st in b.settlements:
            self.sett_by_rp[(st.region, st.period)].append(st)
        self.refund_by_rp = defaultdict(list)
        for r in b.refunds:
            self.refund_by_rp[(r.region, r.period)].append(r)
        self.req_by_rp: dict[tuple[str, str], list[ServiceRequest]] = defaultdict(list)
        for rq in b.requests:
            self.req_by_rp[(rq.region, rq.period)].append(rq)
        # 活跃事件按 地区/账期/设备 去重
        self.active_devices: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.online_devices: dict[tuple[str, str], set[str]] = defaultdict(set)
        for ev in b.activities:
            key = (ev.region, _period_of(ev.active_date))
            self.active_devices[key].add(ev.device_id)
            if ev.connectivity_state == "online":
                self.online_devices[key].add(ev.device_id)
        # 换机：按安装月归集
        self.replacements: dict[tuple[str, str], int] = defaultdict(int)
        for d in b.devices:
            if d.replaced_device_id:
                self.replacements[(d.region, _period_of(d.installed_at))] += 1
        # 家庭历史账期（用于促销转化判断）
        self.hh_periods: dict[str, set[str]] = defaultdict(set)
        for s in b.subscriptions:
            self.hh_periods[s.household_id].add(s.period)
        self.periods = sorted({s.period for s in b.subscriptions})
        self.currency = {s.region: s.currency for s in b.settlements}
        for s in b.subscriptions:
            self.currency.setdefault(s.region, s.currency)

    # ------------------------------------------------------------------
    def _revenue_block(self, region: str, period: str, comparable: bool,
                       notes: list[str]) -> dict:
        settled_usd = Decimal("0.00")
        recurring_usd = Decimal("0.00")
        hardware_usd = Decimal("0.00")
        unsettled_local = Decimal("0.00")
        unsettled_currency = ""
        fx_used: set[str] = set()
        restated_usd: Decimal | None = None
        delta_usd: Decimal | None = None

        for st in self.sett_by_rp.get((region, period), []):
            fx_used.add(st.fx_version)
            if st.settlement_state in REVENUE_SETTLE_STATES:
                converted = self.fx.convert(st.local_net, region, st.fx_version)
                settled_usd += converted.usd_amount
                if st.revenue_kind == "recurring":
                    recurring_usd += converted.usd_amount
                else:
                    hardware_usd += converted.usd_amount
                # 记账版本与当前版本不一致（汇率修订）时重估；
                # 无论账期是否可比，原值与原版本都保留不覆盖
                current = self.fx.current_rate(region)
                if current.fx_version != st.fx_version:
                    _, restated, _ = self.fx.restate_at_current(
                        st.local_net, region, st.fx_version
                    )
                    restated_usd = (restated_usd or Decimal("0.00")) + restated.usd_amount
            else:
                # 待结算/争议：只保留本币观察值，禁止折算并入净收入
                if unsettled_currency and unsettled_currency != st.currency:
                    raise ValueError(
                        f"地区 {region} 账期 {period} 存在多种未结算币种，"
                        "不可直接相加"
                    )
                unsettled_currency = st.currency
                unsettled_local += st.local_net
                notes.append(
                    f"渠道 {st.channel} 结算状态为 {st.settlement_state}，"
                    f"本币 {st.local_net} {st.currency} 未计入净收入"
                )

        refund_usd = Decimal("0.00")
        for rf in self.refund_by_rp.get((region, period), []):
            if rf.refund_state in EFFECTIVE_REFUND_STATES:
                fx_used.add(rf.fx_version)
                refund_usd += self.fx.convert(
                    rf.local_amount, region, rf.fx_version
                ).usd_amount
            else:
                notes.append(
                    f"退款 {rf.refund_id} 状态 {rf.refund_state}，暂不冲减收入"
                )

        comparable_net = _q2(settled_usd - refund_usd)
        if restated_usd is not None:
            restated_usd = _q2(restated_usd - refund_usd)
            delta_usd = _q2(restated_usd - comparable_net)
        return {
            "settled_net_usd": _q2(settled_usd),
            "refund_usd": _q2(refund_usd),
            "comparable_net_usd": comparable_net,
            "recurring_net_usd": _q2(recurring_usd),
            "hardware_net_usd": _q2(hardware_usd),
            "unsettled_local": _q2(unsettled_local),
            "unsettled_currency": unsettled_currency,
            "restated_net_usd": restated_usd,
            "revision_delta_usd": delta_usd,
            "fx_versions_used": sorted(fx_used),
        }

    # ------------------------------------------------------------------
    def _subscription_block(self, region: str, period: str,
                            notes: list[str]) -> dict:
        subs = self.subs_by_rp.get((region, period), [])
        paid = [s for s in subs if s.subscription_state in PAID_SUB_STATES
                and not s.is_promo]
        # 家庭共享去重：留存与付费家庭均按 household_id 计一个
        paid_hh = {s.household_id for s in paid}
        renewed_hh = {s.household_id for s in paid
                      if s.subscription_state == "renewed"}
        churned_hh = {s.household_id for s in paid
                      if s.subscription_state == "churned"}
        outcome_base = len(renewed_hh | churned_hh)
        retention = (
            _q2(Decimal(len(renewed_hh)) / Decimal(outcome_base))
            if outcome_base else None
        )
        if outcome_base == 0 and paid_hh:
            notes.append("最新账期续费结果尚未出齐，留存率暂不可计算")

        promo = [s for s in subs if s.is_promo]
        # 促销转化：上一账期为促销、本账期同家庭出现付费订阅
        promo_converted = 0
        for ps in promo:
            prior_months = sorted(m for m in self.hh_periods[ps.household_id]
                                  if m < period)
            if prior_months:
                continue
            later_paid = any(
                x.period > period and x.household_id == ps.household_id
                and x.subscription_state in PAID_SUB_STATES and not x.is_promo
                for x in self.b.subscriptions
            )
            if later_paid:
                promo_converted += 1
        return {
            "paid_base": len(paid_hh),
            "renewed": len(renewed_hh),
            "churned": len(churned_hh),
            "promo_count": len({s.household_id for s in promo}),
            "promo_converted": promo_converted,
            "retention_rate": retention,
            "paid_households": len(paid_hh),
        }

    # ------------------------------------------------------------------
    def _support_block(self, region: str, period: str,
                       active_count: int, notes: list[str]) -> dict:
        reqs = self.req_by_rp.get((region, period), [])
        open_reqs = [r for r in reqs if r.request_state in OPEN_REQUEST_STATES]
        handling = sum((r.handling_hours for r in reqs), Decimal("0"))
        cost: LocalCost | None = self.costs.get((region, period))
        if cost is None:
            notes.append("缺少本地服务成本记录，支持成本指标留空")
            return {
                "requests_total": len(reqs), "requests_open": len(open_reqs),
                "handling_hours": _q2(handling), "capacity_hours": Decimal("0"),
                "capacity_utilization": None, "support_cost_usd": Decimal("0.00"),
                "support_cost_per_active_usd": None,
            }
        capacity = cost.capacity_hours
        # 利用率封顶为 1.00：超过容量的部分在报告备注中保留原始比值信号
        raw_utilization = (handling / capacity) if capacity > 0 else None
        utilization = (
            _q2(min(raw_utilization, Decimal("1.00")))
            if raw_utilization is not None else None
        )
        if raw_utilization is not None and raw_utilization > 1:
            notes.append(
                f"服务请求工时 {handling} 超过承接容量 {capacity}，"
                f"原始利用率 {_q2(raw_utilization)} 已封顶为 1.00"
            )
        total_local = cost.support_cost + cost.returns_cost + cost.partner_fee
        cost_usd = self.fx.convert(total_local, region, cost.fx_version).usd_amount
        per_active = (
            _q2(cost_usd / Decimal(active_count)) if active_count else None
        )
        return {
            "requests_total": len(reqs),
            "requests_open": len(open_reqs),
            "handling_hours": _q2(handling),
            "capacity_hours": capacity,
            "capacity_utilization": utilization,
            "support_cost_usd": _q2(cost_usd),
            "support_cost_per_active_usd": per_active,
        }

    # ------------------------------------------------------------------
    def compute_region_period(self, region: str, period: str) -> RegionMetrics:
        notes: list[str] = []
        avail = self.availability.get((region, period))
        if avail is None:
            raise KeyError(f"缺少 {region}/{period} 的数据可用性声明")
        comparable = avail.status == "final"
        if not comparable:
            notes.append(
                f"数据可用性标记为 {avail.status}（{avail.note}），"
                "本账期不与 final 账期直接比较"
            )

        revenue = self._revenue_block(region, period, comparable, notes)
        subs = self._subscription_block(region, period, notes)
        active = len(self.active_devices.get((region, period), set()))
        online = len(self.online_devices.get((region, period), set()))
        support = self._support_block(region, period, active, notes)

        attachment = None
        if subs["paid_households"]:
            attachment = _q2(
                Decimal(active) / Decimal(subs["paid_households"])
            )
        if active and subs["paid_households"] == 0:
            notes.append("存在活跃设备但无付费家庭：设备在线不等于付费活跃")

        m = RegionMetrics(
            region=region, period=period, comparable=comparable,
            availability_status=avail.status,
            active_devices=active, online_devices=online,
            replacement_devices=self.replacements.get((region, period), 0),
            **revenue, **subs, **support, notes=notes,
        )
        m.notes.append(f"设备附着率（活跃设备/付费家庭）: "
                       f"{attachment if attachment is not None else '不可计算'}")
        return m

    def compute_all(self) -> dict[str, list[RegionMetrics]]:
        result: dict[str, list[RegionMetrics]] = {}
        for region in self.b.regions:
            result[region] = [
                self.compute_region_period(region, p) for p in self.periods
            ]
        return result

    # ------------------------------------------------------------------
    def summarize(self, metrics: dict[str, list[RegionMetrics]]) -> list[RegionSummary]:
        summaries: list[RegionSummary] = []
        for region, rows in metrics.items():
            latest = rows[-1]
            non_comp = [m.period for m in rows if not m.comparable]
            consecutive = 0
            for m in reversed(rows):
                if m.comparable:
                    break
                consecutive += 1
            comp_rows = [m for m in rows if m.comparable]
            total_net = sum((m.comparable_net_usd for m in comp_rows),
                            Decimal("0.00"))
            total_recurring = sum((m.recurring_net_usd for m in comp_rows),
                                  Decimal("0.00"))
            recurring_share = (
                _q2(total_recurring / total_net) if total_net > 0 else None
            )
            revision_regions = {
                r.region for r in self.b.fx_rates if r.supersedes
            }
            # 汇率修订可能发生在已结或未结账期：汇总全部重估差额，
            # 已结账期的 as-booked 原值仍在各账期明细中保留
            revision_delta = sum(
                (m.revision_delta_usd or Decimal("0.00")) for m in rows
            )
            # 最新账期续费结果常未出齐/不可比：回退到最近一个可比账期的口径，
            # 并显式记录来源账期，避免把"结果未出齐"误读为留存为零
            def latest_comparable_metric(attr: str):
                for m in reversed(rows):
                    if m.comparable and getattr(m, attr) is not None:
                        return getattr(m, attr), m.period
                return None, None

            retention, retention_period = latest_comparable_metric("retention_rate")
            # 设备附着率由活跃设备/付费家庭现算
            attachment, attachment_period = None, None
            for m in reversed(rows):
                if m.comparable and m.paid_households:
                    attachment = _q2(
                        Decimal(m.active_devices) / Decimal(m.paid_households)
                    )
                    attachment_period = m.period
                    break
            capacity_util, _ = latest_comparable_metric("capacity_utilization")
            cost_per_active, _ = latest_comparable_metric(
                "support_cost_per_active_usd"
            )
            summaries.append(RegionSummary(
                region=region,
                currency=self.currency.get(region, ""),
                periods=rows,
                latest_period=latest.period,
                latest_comparable=latest.comparable,
                non_comparable_periods=non_comp,
                consecutive_non_comparable=consecutive,
                latest_retention=retention,
                retention_source_period=retention_period,
                latest_attachment_rate=attachment,
                attachment_source_period=attachment_period,
                recurring_share=recurring_share,
                latest_capacity_utilization=capacity_util,
                latest_cost_per_active=cost_per_active,
                latest_unsettled_local=latest.unsettled_local,
                latest_unsettled_currency=latest.unsettled_currency,
                total_comparable_net_usd=_q2(total_net),
                open_obligations=0,       # 由 obligations 模块回填
                breached_obligations=0,
                outstanding_liability_usd=Decimal("0.00"),
                has_fx_revision=region in revision_regions,
                revision_delta_usd=_q2(revision_delta),
            ))
        return summaries
