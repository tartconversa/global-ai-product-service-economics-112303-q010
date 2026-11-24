"""本地确定性合成数据生成器。

生成的资料仅用于测试与演示，标识和数值均不对应真实主体（见 README 免责声明）。
固定随机种子保证可复现；生成结果直接满足 ingestion 的表头、枚举与外键约束。

场景设计（4 个虚构地区 × 2026-07 至 2026-11 五个账期）：
- US：留存健康、经常性收入为主、容量充裕 → 扩展候选，但 11 月有汇率修订重估；
- DE：留存达标但 11 月出现争议结算、容量利用率偏高 → 限量；
- JP：留存下滑、11 月数据延迟（lagged）且供应商被停用 → 暂停；
- AU：促销占比高、家庭共享与换机集中、11 月发生家庭跨区迁移（migration）→ 限量。
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

PERIODS = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]

# region -> (币种, 基础汇率(本币->USD), 家庭数, 月费本币, 在线率, 流失率)
REGION_SPEC = {
    "US": ("USD", Decimal("1.0000"), 24, Decimal("19.99"),  0.90, 0.07),
    "DE": ("EUR", Decimal("1.0900"), 28, Decimal("18.99"),  0.82, 0.12),
    "JP": ("JPY", Decimal("0.0067"), 26, Decimal("2200"),   0.70, 0.28),
    "AU": ("AUD", Decimal("0.6600"), 22, Decimal("21.99"),  0.75, 0.24),
}
# 11 月汇率修订（仅 US 发布新版本，演示当前表现重估）
FX_REVISION = {"US": ("FX-US-2", Decimal("1.0200"))}
REVISED_REGIONS = {"US"}

PLANS = ["basic", "premium", "health"]
CHANNELS = ["direct", "retail", "app-store"]
DEVICE_SKUS = ["PET-FEEDER", "PET-COLLAR", "PET-LITTER"]


@dataclass
class GenConfig:
    seed: int = 20261124
    periods: tuple[str, ...] = tuple(PERIODS)


# ---------------------------------------------------------------------------
def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)


def _month_end(period: str) -> str:
    # 合成数据统一以每月 1 日起、当月最后一天止
    import calendar
    year, month = (int(x) for x in period.split("-"))
    last = calendar.monthrange(year, month)[1]
    return f"{period}-{last:02d}"


# ---------------------------------------------------------------------------
def generate(data_dir: str | Path, config: GenConfig | None = None) -> list[Path]:
    cfg = config or GenConfig()
    rng = random.Random(cfg.seed)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    periods = list(cfg.periods)

    households_rows: list[dict] = []
    devices_rows: list[dict] = []
    activity_rows: list[dict] = []
    sub_rows: list[dict] = []
    refund_rows: list[dict] = []
    settlement_rows: list[dict] = []
    request_rows: list[dict] = []
    contract_rows: list[dict] = []
    obligation_rows: list[dict] = []
    cost_rows: list[dict] = []
    fx_rows: list[dict] = []
    availability_rows: list[dict] = []

    # ---- 1. 汇率版本（每地区基础版 FX-<R>-1，US 在 11 月发布修订版）----
    for region, (currency, base_rate, _, _, _, _) in REGION_SPEC.items():
        if region in REVISED_REGIONS:
            new_version, new_rate = FX_REVISION[region]
            fx_rows.append({
                "region": region, "currency": currency,
                "fx_version": f"FX-{region}-1", "rate_to_usd": str(base_rate),
                "valid_from": f"{periods[0]}-01",
                "valid_to": f"{periods[-1]}-01", "supersedes": "",
            })
            fx_rows.append({
                "region": region, "currency": currency,
                "fx_version": new_version, "rate_to_usd": str(new_rate),
                "valid_from": f"{periods[-1]}-01",
                "valid_to": "", "supersedes": f"FX-{region}-1",
            })
        else:
            fx_rows.append({
                "region": region, "currency": currency,
                "fx_version": f"FX-{region}-1", "rate_to_usd": str(base_rate),
                "valid_from": f"{periods[0]}-01", "valid_to": "",
                "supersedes": "",
            })

    def fx_version_for(region: str, period: str) -> str:
        if region in REVISED_REGIONS and period == periods[-1]:
            return FX_REVISION[region][0]
        return f"FX-{region}-1"

    # ---- 2. 家庭（AU 末尾 3 个家庭在 11 月迁移到 US）----
    migrating_hh: set[str] = set()
    hh_counter = 0
    for region, (currency, _rate, target_n, _price, _online, _churn) in REGION_SPEC.items():
        for i in range(1, target_n + 1):
            hh_counter += 1
            hh_id = f"HH-{hh_counter:03d}"
            if region == "AU" and i >= target_n - 2:
                migrating_hh.add(hh_id)
            family_size = rng.choice([1, 1, 2, 2, 3, 4])
            households_rows.append({
                "household_id": hh_id, "home_region": region,
                "family_size": family_size,
                "migrated_from": "", "migrated_at": "",
                "migration_state": "",
            })

    # 迁移家庭：11 月起 home_region 改为 US，并登记迁移轨迹
    for hh_id in sorted(migrating_hh):
        for row in households_rows:
            if row["household_id"] == hh_id:
                row["migrated_from"] = "AU"
                row["migrated_at"] = f"{periods[-1]}-03"
                row["migration_state"] = "done"
                row["home_region"] = "US"

    # ---- 3. 设备（每家庭一台；AU 10 月发生 3 台换机；US 1 台退货）----
    device_by_hh: dict[str, str] = {}
    dev_counter = 0
    for hh in households_rows:
        dev_counter += 1
        dev_id = f"DEV-{dev_counter:04d}"
        device_by_hh[hh["household_id"]] = dev_id
        devices_rows.append({
            "device_id": dev_id, "household_id": hh["household_id"],
            "region": hh["home_region"],
            "sku": rng.choice(DEVICE_SKUS),
            "installed_at": f"{periods[0]}-{rng.randint(1, 10):02d}",
            "replaced_device_id": "", "device_state": "active",
        })
    # AU 换机（10 月）
    au_hh = [h["household_id"] for h in households_rows
             if h["home_region"] == "AU" and h["household_id"] not in migrating_hh]
    replaced_pairs: list[tuple[str, str]] = []
    for hh_id in rng.sample(au_hh, 3):
        old_dev = device_by_hh[hh_id]
        dev_counter += 1
        new_dev = f"DEV-{dev_counter:04d}"
        device_by_hh[hh_id] = new_dev
        replaced_pairs.append((old_dev, new_dev))
        for row in devices_rows:
            if row["device_id"] == old_dev:
                row["device_state"] = "replaced"
        devices_rows.append({
            "device_id": new_dev, "household_id": hh_id, "region": "AU",
            "sku": rng.choice(DEVICE_SKUS),
            "installed_at": f"{periods[-2]}-08",
            "replaced_device_id": old_dev, "device_state": "active",
        })
    # US 一台退货（9 月）
    us_hh = [h["household_id"] for h in households_rows if h["home_region"] == "US"]
    returned_hh = rng.choice(us_hh)
    for row in devices_rows:
        if row["device_id"] == device_by_hh[returned_hh]:
            row["device_state"] = "returned"

    # ---- 4. 订阅链（按月；促销、家庭共享、迁移缺口、流失）----
    # 口径说明：上月订阅在期末要么 renewed（同家庭出现下月付费订阅），
    # 要么 churned；最新账期结果未出齐，统一为 active。
    sub_counter = 0
    # AU 选 3 个家庭在 7 月做促销试用，8 月起转化为付费
    promo_hh = set(rng.sample(
        [h["household_id"] for h in households_rows
         if h["home_region"] == "AU" and h["household_id"] not in migrating_hh], 3))
    refund_pending: list[tuple] = []  # 稍后生成退款记录
    for hh in households_rows:
        hh_id = hh["household_id"]
        region = hh["home_region"]
        currency, _rate, _n, price, _online, churn_p = REGION_SPEC[region]
        is_family = hh["family_size"] >= 3
        active_chain = True       # 该家庭订阅链是否仍在存续
        for idx, period in enumerate(periods):
            # 迁移家庭在 11 月的原地区订阅终止，迁入地不补建（迁移账期不可比）
            if hh_id in migrating_hh and period == periods[-1]:
                continue
            if not active_chain:
                continue
            is_promo = hh_id in promo_hh and idx == 0
            if is_promo:
                state = "promo"
            elif idx == len(periods) - 1:
                state = "active"          # 最新账期续费结果未出齐
            elif idx > 0 and rng.random() < churn_p:
                state = "churned"
                active_chain = False
            else:
                state = "renewed"
            local_price = _money(float(price) * (1.25 if is_family else 1.0)
                                 * (0.0 if is_promo else 1.0))
            sub_counter += 1
            sub_id = f"SUB-{sub_counter:05d}"
            sub_rows.append({
                "subscription_id": sub_id,
                "household_id": hh_id, "region": region,
                "plan": rng.choice(PLANS), "currency": currency,
                "period": period,
                "period_start": f"{period}-01",
                "period_end": _month_end(period),
                "local_price": str(local_price),
                "is_family_plan": str(is_family).lower(),
                "is_promo": str(is_promo).lower(),
                "subscription_state": state,
                "fx_version": fx_version_for(region, period),
            })
            # 退款：DE 8 月 1 笔已支付退款，JP 10 月 1 笔已批准未支付
            if region == "DE" and period == periods[1] and rng.random() < 0.06:
                refund_pending.append((sub_id, region, currency, period, price, "paid"))
            if region == "JP" and period == periods[-2] and rng.random() < 0.06:
                refund_pending.append((sub_id, region, currency, period, price, "approved"))
    for j, (sub_id, region, currency, period, price, state) in enumerate(refund_pending, 1):
        refund_rows.append({
            "refund_id": f"REF-{j:04d}", "subscription_id": sub_id,
            "region": region, "currency": currency, "period": period,
            "local_amount": str(price), "refund_state": state,
            "refunded_at": f"{period}-15",
            "fx_version": fx_version_for(region, period),
        })

    # ---- 5. 渠道结算（每地区每账期一条经常性 + 前两账期一条硬件）----
    set_counter = 0
    for region, (currency, _rate, _n, price, _online, _churn) in REGION_SPEC.items():
        region_subs = [s for s in sub_rows if s["region"] == region]
        for period in periods:
            fxv = fx_version_for(region, period)
            period_subs = [s for s in region_subs if s["period"] == period]
            gross = sum((Decimal(s["local_price"]) for s in period_subs),
                        Decimal("0"))
            if gross > 0:
                set_counter += 1
                fee = _money(float(gross) * 0.12)
                state = "settled"
                # JP 11 月待结算、DE 11 月争议
                if region == "JP" and period == periods[-1]:
                    state = "pending"
                if region == "DE" and period == periods[-1]:
                    state = "disputed"
                settlement_rows.append({
                    "settlement_id": f"SET-{set_counter:04d}",
                    "channel": rng.choice(CHANNELS), "region": region,
                    "currency": currency, "period": period,
                    "local_gross": str(_money(float(gross))),
                    "local_fee": str(fee),
                    "local_net": str(_money(float(gross) - float(fee))),
                    "revenue_kind": "recurring",
                    "fx_version": fxv, "settlement_state": state,
                })
            # 硬件收入只出现在前两个账期（演示一次性设备销售与留存的区分）
            if period in periods[:2]:
                set_counter += 1
                # 硬件占比按地区差异化：US 经常性收入占比高，JP/AU 硬件占比偏高
                hw_span = {"US": (300, 550), "DE": (500, 800),
                           "JP": (900, 1300), "AU": (850, 1200)}[region]
                hw_gross = _money(rng.uniform(*hw_span))
                hw_fee = _money(float(hw_gross) * 0.08)
                settlement_rows.append({
                    "settlement_id": f"SET-{set_counter:04d}",
                    "channel": rng.choice(CHANNELS), "region": region,
                    "currency": currency, "period": period,
                    "local_gross": str(hw_gross),
                    "local_fee": str(hw_fee),
                    "local_net": str(_money(float(hw_gross) - float(hw_fee))),
                    "revenue_kind": "hardware",
                    "fx_version": fxv, "settlement_state": "settled",
                })

    # ---- 6. 设备活跃事件（在用设备每账期一条；流失后活跃度衰减）----
    active_devices = {d["device_id"]: d for d in devices_rows
                      if d["device_state"] == "active"}
    # 各家庭的流失账期（该账期起不再产生活跃事件）
    churned_at: dict[str, str] = {}
    for s in sub_rows:
        if s["subscription_state"] == "churned":
            churned_at[s["household_id"]] = s["period"]
    for dev in active_devices.values():
        region = dev["region"]
        online_p = REGION_SPEC[region][4]
        for period in periods:
            if dev["household_id"] in migrating_hh and period == periods[-1]:
                continue
            # 流失家庭的设备自流失账期起停用：设备在线不等于付费活跃
            if dev["household_id"] in churned_at and period >= churned_at[dev["household_id"]]:
                continue
            roll = rng.random()
            if roll < online_p:
                state = "online"
            elif roll < online_p + 0.12:
                state = "partial"
            else:
                state = "offline"
            activity_rows.append({
                "device_id": dev["device_id"],
                "household_id": dev["household_id"], "region": region,
                "active_date": f"{period}-{rng.randint(5, 25):02d}",
                "activity_minutes": rng.randint(0 if state == 'offline' else 20, 480),
                "connectivity_state": state,
                "firmware": f"FW-{rng.randint(1, 3)}",
            })

    # ---- 7. 服务请求（含未结案积压；JP 利用率高、DE 11 月积压）----
    req_counter = 0
    for region in REGION_SPEC:
        region_devs = [d["device_id"] for d in devices_rows if d["region"] == region]
        for period in periods:
            n_req = {"US": 3, "DE": 5, "JP": 7, "AU": 4}[region]
            if region == "DE" and period == periods[-1]:
                n_req += 3
            for _ in range(n_req):
                req_counter += 1
                dev_id = rng.choice(region_devs)
                hh_id = next(d["household_id"] for d in devices_rows
                             if d["device_id"] == dev_id)
                if period == periods[-1]:
                    state = rng.choices(
                        ["open", "in-progress", "resolved"],
                        weights=[3, 3, 4])[0]
                else:
                    state = rng.choices(
                        ["resolved", "escalated", "open"],
                        weights=[8, 1, 1])[0]
                closed_at = "" if state in {"open", "in-progress"} else f"{period}-20"
                # 单请求工时按地区差异化：US 高效、DE/JP 偏重，形成容量利用率差异
                hour_span = {"US": (1.0, 2.5), "DE": (3.0, 5.5),
                             "JP": (3.5, 6.0), "AU": (2.0, 4.0)}[region]
                request_rows.append({
                    "request_id": f"REQ-{req_counter:04d}",
                    "region": region, "household_id": hh_id,
                    "device_id": dev_id, "period": period,
                    "opened_at": f"{period}-{rng.randint(1, 12):02d}",
                    "closed_at": closed_at, "request_state": state,
                    "handling_hours": str(_money(rng.uniform(*hour_span))),
                })
    # ---- 8. 供应商合同（JP 新供应商被停用；DE 容量偏紧）----
    service_types = ["support-call", "device-replacement", "health-review"]
    for region, (currency, _r, _n, _p, _o, _c) in REGION_SPEC.items():
        unit_base = {"USD": 45, "EUR": 42, "JPY": 5200, "AUD": 60}[currency]
        for k, stype in enumerate(service_types, 1):
            supplier = f"SP-{region}-{k}"
            # 容量：DE 偏紧，JP 第二版合同供应商停用，其余充裕
            if region == "DE":
                cap = Decimal("26")
            elif region == "JP":
                cap = Decimal("40")
            else:
                cap = Decimal("60")
            common = {
                "supplier_ref": supplier, "region": region,
                "service_type": stype, "currency": currency,
                "unit_cost_local": str(_money(unit_base + k * 5)),
                "capacity_hours": str(cap),
            }
            if region == "JP" and k == 2:
                # 旧合同 10 月底终止，新合同供应商被停用 → 本地无法承接
                contract_rows.append({
                    **common,
                    "contract_version": "CT-V1",
                    "valid_from": f"{periods[0]}-01",
                    "valid_to": f"{periods[-2]}-{_month_end(periods[-2])[-2:]}",
                    "supplier_state": "active",
                })
                contract_rows.append({
                    **common,
                    "contract_version": "CT-V2",
                    "valid_from": f"{periods[-1]}-01", "valid_to": "",
                    "supplier_state": "suspended",
                })
            elif region == "US":
                # US 演示合同版本升级：旧版 9 月底终止，10 月起新版
                contract_rows.append({
                    **common,
                    "contract_version": "CT-V1",
                    "valid_from": f"{periods[0]}-01",
                    "valid_to": f"{periods[2]}-{_month_end(periods[2])[-2:]}",
                    "supplier_state": "active",
                })
                contract_rows.append({
                    **common,
                    "contract_version": "CT-V2",
                    "valid_from": f"{periods[3]}-01", "valid_to": "",
                    "supplier_state": "active",
                })
            else:
                contract_rows.append({
                    **common,
                    "contract_version": "CT-V1",
                    "valid_from": f"{periods[0]}-01", "valid_to": "",
                    "supplier_state": "active",
                })

    def contract_for(region: str, period: str, stype: str) -> str:
        candidates = [c for c in contract_rows
                      if c["region"] == region and c["service_type"] == stype
                      and c["valid_from"] <= f"{period}-15"
                      and (c["valid_to"] == ""
                           or c["valid_to"] >= f"{period}-15")]
        return sorted(candidates, key=lambda c: c["valid_from"])[-1]["contract_version"]

    # ---- 9. 服务义务（每地区每账期 2~3 条；JP 11 月固定一条逾期）----
    ob_counter = 0
    for region in REGION_SPEC:
        hh_ids = [h["household_id"] for h in households_rows
                  if h["home_region"] == region]
        for idx, period in enumerate(periods):
            for _ in range(rng.randint(2, 3)):
                ob_counter += 1
                stype = rng.choice(service_types)
                k = service_types.index(stype) + 1
                supplier = f"SP-{region}-{k}"
                ct_version = contract_for(region, period, stype)
                if period == periods[-1]:
                    state = rng.choice(["accepted", "in-progress"])
                else:
                    state = rng.choices(
                        ["complete", "accepted", "in-progress"],
                        weights=[7, 2, 1])[0]
                obligation_rows.append({
                    "obligation_id": f"OB-{ob_counter:04d}",
                    "region": region,
                    "household_id": rng.choice(hh_ids),
                    "service_type": stype, "supplier_ref": supplier,
                    "contract_version": ct_version,
                    "accepted_at": f"{period}-05",
                    "due_at": f"{period}-28",
                    "completion_state": state,
                    "fx_version": fx_version_for(region, period),
                })
            # JP 11 月固定补一条换机逾期义务：演示暂停服务也不能撤销已承诺义务
            if region == "JP" and period == periods[-1]:
                ob_counter += 1
                stype = "device-replacement"
                supplier = f"SP-JP-{service_types.index(stype) + 1}"
                obligation_rows.append({
                    "obligation_id": f"OB-{ob_counter:04d}",
                    "region": "JP",
                    "household_id": rng.choice(hh_ids),
                    "service_type": stype, "supplier_ref": supplier,
                    "contract_version": contract_for("JP", period, stype),
                    "accepted_at": f"{period}-02",
                    "due_at": f"{period}-20",
                    "completion_state": "breached",
                    "fx_version": fx_version_for("JP", period),
                })

    # ---- 10. 本地服务成本（US 成本低、DE 容量偏紧成本高，JP 容量打满）----
    for region, (currency, _r, _n, _p, _o, _c) in REGION_SPEC.items():
        base_support = {"US": 1500, "DE": 3600, "JP": 4800, "AU": 2600}[region]
        for idx, period in enumerate(periods):
            support = _money(base_support * (1 + 0.03 * idx) + rng.uniform(-80, 80))
            returns = _money(200 + rng.random() * 180)
            partner = _money(120 + rng.random() * 120)
            # 与供应商合同容量呼应：DE/JP 偏紧，US/AU 充裕
            capacity = Decimal(
                {"DE": "26", "JP": "34", "US": "70", "AU": "52"}[region]
            )
            cost_rows.append({
                "region": region, "period": period,
                "support_cost": str(support),
                "returns_cost": str(returns),
                "partner_fee": str(partner),
                "capacity_hours": str(capacity),
                "cost_version": f"CC-{region}-{idx + 1}",
                "currency": currency,
                "fx_version": fx_version_for(region, period),
            })

    # ---- 11. 数据可用性声明 ----
    for region in REGION_SPEC:
        for period in periods:
            status, note = "final", "已结账期，按当时汇率与合同口径冻结"
            if region == "JP" and period == periods[-1]:
                status, note = "lagged", "地区数据延迟，渠道与活跃事件尚未回传完整"
            if region == "AU" and period == periods[-1]:
                status, note = "migration", "3 个家庭迁移至 US，迁移账期标记为不可比"
            availability_rows.append({
                "region": region, "period": period,
                "status": status, "note": note,
            })

    # ---- 落盘 ----
    schema = [
        ("households.csv",
         ("household_id", "home_region", "family_size", "migrated_from",
          "migrated_at", "migration_state"), households_rows),
        ("devices.csv",
         ("device_id", "household_id", "region", "sku", "installed_at",
          "replaced_device_id", "device_state"), devices_rows),
        ("device_activity.csv",
         ("device_id", "household_id", "region", "active_date",
          "activity_minutes", "connectivity_state", "firmware"), activity_rows),
        ("subscriptions.csv",
         ("subscription_id", "household_id", "region", "plan", "currency",
          "period", "period_start", "period_end", "local_price",
          "is_family_plan", "is_promo", "subscription_state", "fx_version"),
         sub_rows),
        ("refunds.csv",
         ("refund_id", "subscription_id", "region", "currency", "period",
          "local_amount", "refund_state", "refunded_at", "fx_version"),
         refund_rows),
        ("channel_settlements.csv",
         ("settlement_id", "channel", "region", "currency", "period",
          "local_gross", "local_fee", "local_net", "revenue_kind",
          "fx_version", "settlement_state"), settlement_rows),
        ("service_requests.csv",
         ("request_id", "region", "household_id", "device_id", "period",
          "opened_at", "closed_at", "request_state", "handling_hours"),
         request_rows),
        ("supplier_contracts.csv",
         ("supplier_ref", "region", "contract_version", "valid_from",
          "valid_to", "service_type", "unit_cost_local", "currency",
          "capacity_hours", "supplier_state"), contract_rows),
        ("service_obligations.csv",
         ("obligation_id", "region", "household_id", "service_type",
          "supplier_ref", "contract_version", "accepted_at", "due_at",
          "completion_state", "fx_version"), obligation_rows),
        ("local_costs.csv",
         ("region", "period", "support_cost", "returns_cost", "partner_fee",
          "capacity_hours", "cost_version", "currency", "fx_version"),
         cost_rows),
        ("fx_rates.csv",
         ("region", "currency", "fx_version", "rate_to_usd", "valid_from",
          "valid_to", "supersedes"), fx_rows),
        ("data_availability.csv",
         ("region", "period", "status", "note"), availability_rows),
    ]
    written = []
    for filename, columns, rows in schema:
        path = data_dir / filename
        _write_csv(path, columns, rows)
        written.append(path)
    return written
