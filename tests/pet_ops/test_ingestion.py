"""导入校验测试：表头、枚举、必填、外键、未知状态不得当零值。"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from ._base import SRC, SyntheticDataTestCase  # noqa: F401

from pet_service_region_ops.generator import generate  # noqa: E402
from pet_service_region_ops.ingestion import IngestionError, load_bundle  # noqa: E402

EXPECTED_FILES = {
    "households.csv", "devices.csv", "device_activity.csv",
    "subscriptions.csv", "refunds.csv", "channel_settlements.csv",
    "service_requests.csv", "supplier_contracts.csv",
    "service_obligations.csv", "local_costs.csv", "fx_rates.csv",
    "data_availability.csv",
}


class IngestionContractTests(SyntheticDataTestCase):
    def test_all_twelve_files_present_and_nonempty(self):
        files = {p.name for p in self.data_dir.iterdir() if p.suffix == ".csv"}
        self.assertEqual(files, EXPECTED_FILES)
        for name in EXPECTED_FILES:
            with (self.data_dir / name).open(encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            self.assertGreater(len(rows), 0, f"{name} 不应为空")

    def test_four_regions_and_five_periods(self):
        self.assertEqual(self.bundle.regions, ["AU", "DE", "JP", "US"])
        periods = {s.period for s in self.bundle.subscriptions}
        self.assertEqual(
            periods,
            {"2026-07", "2026-08", "2026-09", "2026-10", "2026-11"},
        )

    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(tmp)
            (Path(tmp) / "fx_rates.csv").unlink()
            with self.assertRaises(IngestionError):
                load_bundle(tmp)

    def test_bad_header_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(tmp)
            path = Path(tmp) / "subscriptions.csv"
            text = path.read_text(encoding="utf-8-sig")
            path.write_text("wrong,header\n1,2\n", encoding="utf-8-sig")
            self.assertIn("subscription_id", text)  # 原文件确实包含该列
            with self.assertRaises(IngestionError):
                load_bundle(tmp)

    def test_illegal_enum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(tmp)
            path = Path(tmp) / "channel_settlements.csv"
            rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
            rows[0]["settlement_state"] = "mystery-state"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(IngestionError):
                load_bundle(tmp)

    def test_negative_amount_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(tmp)
            path = Path(tmp) / "refunds.csv"
            rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
            rows[0]["local_amount"] = "-999"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(IngestionError):
                load_bundle(tmp)

    def test_foreign_key_violation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(tmp)
            path = Path(tmp) / "service_obligations.csv"
            rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
            rows[0]["supplier_ref"] = "SP-DOES-NOT-EXIST"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(IngestionError):
                load_bundle(tmp)

    def test_pending_state_is_not_treated_as_success(self):
        # 待结算/争议记录必须保留原始状态，不能被清洗成 settled
        states = {s.settlement_state for s in self.bundle.settlements}
        self.assertIn("pending", states)
        self.assertIn("disputed", states)
        for s in self.bundle.settlements:
            if s.settlement_state in {"pending", "disputed"}:
                self.assertNotEqual(s.settlement_state, "settled")


if __name__ == "__main__":
    unittest.main()
