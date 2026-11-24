"""端到端测试：CLI、报告产物、合成数据确定性。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ._base import ROOT  # noqa: F401

from pet_service_region_ops.cli import main as cli_main  # noqa: E402
from pet_service_region_ops.generator import generate  # noqa: E402


class DeterminismTests(unittest.TestCase):
    def test_generation_is_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate(a)
            generate(b)
            for path in sorted(Path(a).iterdir()):
                other = Path(b) / path.name
                self.assertEqual(
                    path.read_text(encoding="utf-8-sig"),
                    other.read_text(encoding="utf-8-sig"),
                    f"{path.name} 两次生成结果不一致",
                )


class CliEndToEndTests(unittest.TestCase):
    def test_run_all_produces_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            report_dir = Path(tmp) / "reports"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main([
                    "run-all", "--data-dir", str(data_dir),
                    "--report-dir", str(report_dir),
                ])
            self.assertEqual(rc, 0)
            md = report_dir / "经营分析报告.md"
            js = report_dir / "经营分析报告.json"
            self.assertTrue(md.exists())
            self.assertTrue(js.exists())

            payload = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["regions"]), 4)
            self.assertEqual(len(payload["decisions"]), 4)
            self.assertGreater(len(payload["obligation_ledger"]), 0)
            # 免责声明必须出现在机器可读报告中
            self.assertIn("合成", payload["disclaimer"])

            text = md.read_text(encoding="utf-8")
            self.assertIn("智能宠物产品", text)
            self.assertIn("扩展", text)
            self.assertIn("暂停", text)
            self.assertIn("不可比", text)

    def test_analyze_on_missing_dir_returns_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = cli_main([
                "analyze", "--data-dir", str(Path(tmp) / "nope"),
                "--report-dir", str(Path(tmp) / "reports"),
            ])
            self.assertEqual(rc, 2)

    def test_generate_then_analyze_as_separate_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            report_dir = Path(tmp) / "reports"
            self.assertEqual(
                cli_main(["generate", "--data-dir", str(data_dir),
                          "--report-dir", str(report_dir)]),
                0,
            )
            self.assertEqual(
                cli_main(["analyze", "--data-dir", str(data_dir),
                          "--report-dir", str(report_dir)]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
