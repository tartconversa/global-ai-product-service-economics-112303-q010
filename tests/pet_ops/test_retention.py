"""留存口径与不可比范围测试。

覆盖需求：
- 促销/试用不计入付费续费分母；
- 家庭共享按家庭去重，一个家庭只计一个续费单位；
- 换机不制造新的续费分母；
- 迁移账期标记为不可比，不与 final 账期混算；
- 数据延迟账期同样标记不可比并给出范围；
- 设备在线不等于付费活跃。
"""

from __future__ import annotations

from decimal import Decimal

from ._base import SyntheticDataTestCase


class RetentionScopeTests(SyntheticDataTestCase):
    def test_promo_subscriptions_excluded_from_paid_base(self):
        # AU 7 月有 3 个促销家庭，付费基数应排除它们
        au_jul = self.metric("AU", "2026-07")
        promo_hh = {
            s.household_id for s in self.bundle.subscriptions
            if s.region == "AU" and s.period == "2026-07" and s.is_promo
        }
        self.assertEqual(len(promo_hh), 3)
        all_au_hh = {
            s.household_id for s in self.bundle.subscriptions
            if s.region == "AU" and s.period == "2026-07"
        }
        self.assertEqual(au_jul.paid_base, len(all_au_hh) - len(promo_hh))
        self.assertEqual(au_jul.promo_count, 3)

    def test_promo_conversion_counted_separately(self):
        # 3 个促销家庭在 8 月转化为付费
        au_jul = self.metric("AU", "2026-07")
        self.assertEqual(au_jul.promo_converted, 3)

    def test_family_plan_deduplicated_by_household(self):
        # 付费基数按 household_id 去重：同一家庭即使多台设备也只计一次
        for region in self.bundle.regions:
            for m in self.metrics[region]:
                paid_subs = [
                    s for s in self.bundle.subscriptions
                    if s.region == region and s.period == m.period
                    and not s.is_promo
                    and s.subscription_state in {"active", "renewed", "churned"}
                ]
                unique_hh = len({s.household_id for s in paid_subs})
                self.assertEqual(m.paid_base, unique_hh,
                                 f"{region}/{m.period} 家庭去重口径错误")

    def test_replacement_devices_do_not_inflate_base(self):
        # AU 10 月有 3 台换机，但订阅按家庭计，换机不产生新的付费家庭
        au_oct = self.metric("AU", "2026-10")
        self.assertEqual(au_oct.replacement_devices, 3)
        au_sep = self.metric("AU", "2026-09")
        # 换机只可能减少或维持家庭数，不可能增加
        self.assertLessEqual(au_oct.paid_base, au_sep.paid_base + 0)

    def test_retention_rate_between_zero_and_one(self):
        for region in self.bundle.regions:
            for m in self.metrics[region]:
                if m.retention_rate is not None:
                    self.assertGreaterEqual(m.retention_rate, Decimal("0"))
                    self.assertLessEqual(m.retention_rate, Decimal("1"))

    def test_latest_open_period_retention_is_none_with_note(self):
        # 最新账期续费结果未出齐时，账期级留存为 None，但汇总回退到最近可比账期
        us_nov = self.metric("US", "2026-11")
        self.assertIsNone(us_nov.retention_rate)
        us_summary = self.summary("US")
        self.assertIsNotNone(us_summary.latest_retention)
        self.assertEqual(us_summary.retention_source_period, "2026-10")

    def test_churned_household_devices_stop_activity(self):
        # 流失家庭自流失账期起不再产生活跃事件（在线≠付费活跃的反向约束）
        churn = {
            (s.household_id, s.period): s
            for s in self.bundle.subscriptions if s.subscription_state == "churned"
        }
        activity_by_hh_period: dict[tuple[str, str], int] = {}
        for ev in self.bundle.activities:
            key = (ev.household_id, ev.active_date[:7])
            activity_by_hh_period[key] = activity_by_hh_period.get(key, 0) + 1
        for (hh_id, period) in churn:
            later = [p for p in {"2026-07", "2026-08", "2026-09",
                                 "2026-10", "2026-11"} if p >= period]
            for p in later:
                self.assertEqual(
                    activity_by_hh_period.get((hh_id, p), 0), 0,
                    f"流失家庭 {hh_id} 在 {p} 不应再有活跃事件",
                )


class ComparabilityTests(SyntheticDataTestCase):
    def test_lagged_and_migration_periods_flagged(self):
        jp_nov = self.metric("JP", "2026-11")
        au_nov = self.metric("AU", "2026-11")
        self.assertFalse(jp_nov.comparable)
        self.assertEqual(jp_nov.availability_status, "lagged")
        self.assertFalse(au_nov.comparable)
        self.assertEqual(au_nov.availability_status, "migration")

    def test_final_periods_are_comparable(self):
        for region in ("US", "DE"):
            for m in self.metrics[region]:
                self.assertTrue(m.comparable, f"{region}/{m.period} 应为可比")

    def test_non_comparable_periods_listed_as_ranges(self):
        jp = self.summary("JP")
        au = self.summary("AU")
        self.assertEqual(jp.non_comparable_periods, ["2026-11"])
        self.assertEqual(au.non_comparable_periods, ["2026-11"])
        self.assertEqual(jp.consecutive_non_comparable, 1)
        self.assertEqual(au.consecutive_non_comparable, 1)

    def test_total_net_uses_comparable_periods_only(self):
        for region in self.bundle.regions:
            s = self.summary(region)
            expected = sum(
                (m.comparable_net_usd for m in s.periods if m.comparable),
                Decimal("0.00"),
            )
            self.assertEqual(s.total_comparable_net_usd, expected)

    def test_migrating_households_absent_from_november_subscriptions(self):
        migrated = {
            h.household_id for h in self.bundle.households
            if h.migration_state == "done"
        }
        self.assertEqual(len(migrated), 3)
        nov_subs = {
            s.household_id for s in self.bundle.subscriptions
            if s.period == "2026-11" and s.household_id in migrated
        }
        self.assertEqual(nov_subs, set())


if __name__ == "__main__":
    import unittest
    unittest.main()
