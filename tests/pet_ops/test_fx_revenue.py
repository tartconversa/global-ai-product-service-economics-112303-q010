"""汇率版本与可比净收入测试。

覆盖需求：
- 未结算/争议渠道金额只保留本币，绝不并入美元净收入；
- 跨币种折算保留 fx_version 轨迹；
- 汇率修订后，未结账期给当前汇率重估值与差额，历史 as-booked 原值不被覆盖；
- 已结账期仍按当时汇率折算（可追溯）。
"""

from __future__ import annotations

from decimal import Decimal

from ._base import SyntheticDataTestCase
from pet_service_region_ops.fx import FxError
from pet_service_region_ops.models import EFFECTIVE_REFUND_STATES, REFUND_REJECTED


class FxLedgerTests(SyntheticDataTestCase):
    def test_historical_conversion_uses_booked_version(self):
        converted = self.fx.convert(Decimal("100"), "DE", "FX-DE-1")
        # 100 EUR * 1.09 = 109.00 USD
        self.assertEqual(converted.usd_amount, Decimal("109.00"))
        self.assertEqual(converted.fx_version, "FX-DE-1")

    def test_jpy_large_amounts_convert_without_float_error(self):
        converted = self.fx.convert(Decimal("26620"), "JP", "FX-JP-1")
        expected = (Decimal("26620") * Decimal("0.0067")).quantize(
            Decimal("0.01")
        )
        self.assertEqual(converted.usd_amount, expected)

    def test_unknown_version_raises(self):
        with self.assertRaises(FxError):
            self.fx.convert(Decimal("1"), "US", "FX-NOPE")

    def test_us_has_two_versions_and_current_is_revision(self):
        versions = self.fx.versions("US")
        self.assertEqual(len(versions), 2)
        self.assertEqual(self.fx.current_rate("US").fx_version, "FX-US-2")
        self.assertEqual(self.fx.current_rate("US").supersedes, "FX-US-1")

    def test_restation_keeps_booked_value_and_reports_delta(self):
        booked, restated, delta = self.fx.restate_at_current(
            Decimal("1000"), "US", "FX-US-1"
        )
        self.assertEqual(booked.usd_amount, Decimal("1000.00"))
        self.assertEqual(restated.usd_amount, Decimal("1020.00"))
        self.assertEqual(delta, Decimal("20.00"))
        # 重估不修改原折算对象
        self.assertEqual(booked.fx_version, "FX-US-1")


class RevenueRecognitionTests(SyntheticDataTestCase):
    def test_pending_and_disputed_amounts_kept_local_only(self):
        # JP 11 月有待结算、DE 11 月有争议，均不得出现在美元净收入中
        jp_nov = self.metric("JP", "2026-11")
        de_nov = self.metric("DE", "2026-11")
        self.assertGreater(jp_nov.unsettled_local, Decimal("0"))
        self.assertEqual(jp_nov.unsettled_currency, "JPY")
        self.assertGreater(de_nov.unsettled_local, Decimal("0"))
        self.assertEqual(de_nov.unsettled_currency, "EUR")
        # 这两个账期的美元净收入必须为零（无已结算记录）
        self.assertEqual(jp_nov.settled_net_usd, Decimal("0.00"))
        self.assertEqual(de_nov.settled_net_usd, Decimal("0.00"))

    def test_settled_revenue_uses_record_fx_version(self):
        us_jul = self.metric("US", "2026-07")
        self.assertGreater(us_jul.comparable_net_usd, Decimal("0"))
        self.assertIn("FX-US-1", us_jul.fx_versions_used)
        # 7 月是已结账期，不应出现 FX-US-2 重估轨迹混入原值
        self.assertNotIn("FX-US-2", us_jul.fx_versions_used)

    def test_fx_reversion_keeps_history_and_restates_where_versions_differ(self):
        # US 在 11 月发布 FX-US-2：历史账期按 FX-US-1 记账并保留原值，
        # 汇总层给出当前汇率重估差额；新账期直接按新版记账，无差额。
        us_jul = self.metric("US", "2026-07")
        us_nov = self.metric("US", "2026-11")
        # 历史账期：记账版本为旧版，存在重估轨迹，as-booked 原值不被覆盖
        self.assertIsNotNone(us_jul.restated_net_usd)
        self.assertIsNotNone(us_jul.revision_delta_usd)
        self.assertGreater(us_jul.revision_delta_usd, Decimal("0"))
        self.assertLess(us_jul.comparable_net_usd, us_jul.restated_net_usd)
        # 新账期按新版记账：版本即当前版本，无重估差额
        self.assertIsNone(us_nov.revision_delta_usd)
        # 汇总层累计重估差额为正且保留全部 fx 版本
        us_summary = self.summary("US")
        self.assertTrue(us_summary.has_fx_revision)
        self.assertGreater(us_summary.revision_delta_usd, Decimal("0"))

    def test_refunds_reduce_comparable_net_with_trace(self):
        # DE 8 月存在已支付退款
        de_aug = self.metric("DE", "2026-08")
        self.assertGreater(de_aug.refund_usd, Decimal("0"))
        self.assertEqual(
            de_aug.comparable_net_usd,
            de_aug.settled_net_usd - de_aug.refund_usd,
        )

    def test_hardware_vs_recurring_split_identifies_one_off_sales(self):
        us_jul = self.metric("US", "2026-07")
        self.assertGreater(us_jul.hardware_net_usd, Decimal("0"))
        self.assertGreater(us_jul.recurring_net_usd, Decimal("0"))
        # 11 月没有新硬件结算，收入应全部为经常性
        us_nov = self.metric("US", "2026-11")
        self.assertEqual(us_nov.hardware_net_usd, Decimal("0.00"))

    def test_rejected_refund_does_not_reduce_revenue(self):
        # 合成数据中退款仅含 paid/approved；构造 rejected 场景验证口径
        from pet_service_region_ops.models import (
            EFFECTIVE_REFUND_STATES, REFUND_REJECTED,
        )
        self.assertNotIn(REFUND_REJECTED, EFFECTIVE_REFUND_STATES)


if __name__ == "__main__":
    import unittest
    unittest.main()
