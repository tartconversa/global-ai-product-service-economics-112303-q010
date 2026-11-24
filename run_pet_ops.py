#!/usr/bin/env python3
"""便捷入口：以隔离模式（-I 等价保障：仅使用标准库、不依赖当前目录注入）
运行智能宠物产品多地区经营分析。

用法：
    python3 run_pet_ops.py generate     # 生成合成资料到 data/pet_ops
    python3 run_pet_ops.py analyze      # 分析并输出报告到 reports/
    python3 run_pet_ops.py run-all      # 生成 + 分析（默认）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pet_service_region_ops.cli import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("run-all")
    raise SystemExit(main())
