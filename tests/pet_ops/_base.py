"""测试共用夹具：在临时目录生成合成资料并加载。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
# unittest discover -s tests 在 -I 模式下只会把 tests 目录放入 sys.path，
# 这里同时补上项目根与 src，保证 tests.pet_ops 包与业务包都可导入。
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

# 保证 tests.pet_ops 可作为包被相对导入
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_service_region_ops.fx import FxLedger  # noqa: E402
from pet_service_region_ops.generator import generate  # noqa: E402
from pet_service_region_ops.ingestion import load_bundle  # noqa: E402
from pet_service_region_ops.metrics import MetricEngine  # noqa: E402
from pet_service_region_ops.obligations import ObligationLedger  # noqa: E402


class SyntheticDataTestCase(unittest.TestCase):
    """基于完整合成资料的集成测试基类。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._tmp.name)
        generate(cls.data_dir)
        cls.bundle = load_bundle(cls.data_dir)
        cls.fx = FxLedger(cls.bundle.fx_rates)
        cls.engine = MetricEngine(cls.bundle, cls.fx)
        cls.metrics = cls.engine.compute_all()
        cls.summaries = cls.engine.summarize(cls.metrics)
        cls.ledger = ObligationLedger(cls.bundle, cls.fx)
        cls.ledger.annotate_summaries(cls.summaries)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def metric(self, region: str, period: str):
        return next(m for m in self.metrics[region] if m.period == period)

    def summary(self, region: str):
        return next(s for s in self.summaries if s.region == region)
