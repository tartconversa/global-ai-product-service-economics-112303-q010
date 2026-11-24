"""义务台账与扩展决策测试。

覆盖需求：
- 义务按接受时合同版本与汇率版本双追溯，新版本不重算旧义务；
- 已接受义务在暂停决策后仍保留，负债按本币与美元双列；
- 逾期义务触发暂停；
- 决策三档（扩展/限量/暂停）与四类地区场景一一对应；
- 决策依据可解释，阈值集中可调整。
"""

from __future__ import annotations

from decimal import Decimal

from ._base import SyntheticDataTestCase
from pet_service_region_ops.decisions import (
    ACTION_EXPAND,
    ACTION_LIMIT,
    ACTION_SUSPEND,
    DecisionEngine,
    DecisionPolicy,
)


class ObligationLedgerTests(SyntheticDataTestCase):
    def test_every_obligation_traces_contract_and_fx(self):
        for entry in self.ledger.entries():
            self.assertTrue(entry.contract_version.startswith("CT-"))
            self.assertTrue(entry.fx_version.startswith("FX-"))
            self.assertEqual(entry.liability_local, entry.unit_cost_local)
            expected_usd = (
                entry.liability_local * entry.rate_to_usd
            ).quantize(Decimal("0.01"))
            self.assertEqual(entry.liability_usd, expected_usd)

    def test_historical_obligation_uses_version_at_acceptance(self):
        # US 存在 CT-V1 与 CT-V2 两版合同：10 月前义务必须挂 CT-V1
        us_entries = self.ledger.entries_for_region("US")
        v1 = [e for e in us_entries if e.contract_version == "CT-V1"]
        v2 = [e for e in us_entries if e.contract_version == "CT-V2"]
        self.assertGreater(len(v1), 0)
        self.assertGreater(len(v2), 0)
        for e in v1:
            self.assertLessEqual(
                e.obligation.accepted_at, "2026-09-30"
            )
        for e in v2:
            self.assertGreaterEqual(
                e.obligation.accepted_at, "2026-10-01"
            )

    def test_breached_obligation_present_in_jp(self):
        jp = self.summary("JP")
        self.assertGreaterEqual(jp.breached_obligations, 1)
        breached = [
            e for e in self.ledger.entries_for_region("JP") if e.is_breached
        ]
        self.assertTrue(breached)
        # 逾期义务仍计入未履约负债：暂停也不能撤销
        self.assertTrue(all(e.is_outstanding for e in breached))

    def test_outstanding_liability_is_sum_of_open_entries(self):
        for region in self.bundle.regions:
            s = self.summary(region)
            expected = sum(
                (e.liability_usd for e in self.ledger.entries_for_region(region)
                 if e.is_outstanding),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"))
            self.assertEqual(s.outstanding_liability_usd, expected)

    def test_suspended_supplier_blocks_capacity(self):
        view = self.ledger.supplier_capacity_view("JP", "2026-11")
        self.assertIn("SP-JP-2", view["blocked_suppliers"])
        # 被停用供应商的容量不计入可用容量
        suppliers = {r["supplier_ref"]: r for r in view["suppliers"]}
        self.assertLessEqual(
            view["available_hours"], view["total_capacity_hours"]
        )
        self.assertEqual(suppliers["SP-JP-2"]["available_hours"],
                         suppliers["SP-JP-2"]["capacity_hours"]
                         - suppliers["SP-JP-2"]["outstanding_orders"])


class DecisionEngineTests(SyntheticDataTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = DecisionEngine(cls.ledger)
        cls.decisions = {
            d.region: d
            for d in (cls.engine.evaluate(s, s.latest_period)
                      for s in cls.summaries)
        }

    def test_us_receives_expand(self):
        self.assertEqual(self.decisions["US"].action, ACTION_EXPAND)

    def test_jp_receives_suspend(self):
        self.assertEqual(self.decisions["JP"].action, ACTION_SUSPEND)
        reasons = " ".join(self.decisions["JP"].reasons)
        self.assertTrue("逾期" in reasons or "停用" in reasons)

    def test_de_limited_for_unsettled_exposure(self):
        self.assertEqual(self.decisions["DE"].action, ACTION_LIMIT)
        reasons = " ".join(self.decisions["DE"].reasons)
        self.assertIn("未结算", reasons)

    def test_au_limited_for_non_comparable_period(self):
        self.assertEqual(self.decisions["AU"].action, ACTION_LIMIT)
        reasons = " ".join(self.decisions["AU"].reasons)
        self.assertIn("不可比", reasons)

    def test_every_decision_has_explicit_reasons(self):
        for d in self.decisions.values():
            self.assertGreater(len(d.reasons), 0)
            self.assertEqual(d.decided_at, "2026-12-01")

    def test_suspend_still_keeps_obligations_in_ledger(self):
        # 决策为暂停的 JP，其义务台账不被清空
        jp_entries = self.ledger.entries_for_region("JP")
        self.assertGreater(len(jp_entries), 0)
        outstanding = [e for e in jp_entries if e.is_outstanding]
        self.assertGreater(len(outstanding), 0)

    def test_policy_thresholds_are_centralized_and_adjustable(self):
        # 把留存阈值调到极高，US 也应无法扩展
        strict = DecisionPolicy(min_retention=Decimal("0.999"))
        engine = DecisionEngine(self.ledger, strict)
        us_summary = self.summary("US")
        decision = engine.evaluate(us_summary, us_summary.latest_period)
        self.assertNotEqual(decision.action, ACTION_EXPAND)

    def test_finance_view_contains_source_periods(self):
        view = self.decisions["US"].finance_view
        self.assertIn("retention_source_period", view)
        self.assertIsNotNone(view["retention_source_period"])
        self.assertIn("latest_unsettled_local", view)


if __name__ == "__main__":
    import unittest
    unittest.main()
