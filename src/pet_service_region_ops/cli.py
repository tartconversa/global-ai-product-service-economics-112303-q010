"""命令行入口。

子命令：
- generate：生成本地确定性合成资料（默认 data/pet_ops）；
- analyze：导入资料 → 指标 → 义务台账 → 扩展决策 → 中文报告（默认 reports/）；
- run-all：先生成再分析，便于一键演示与测试。

所有命令均可通过 ``python -I -m pet_service_region_ops.cli ...`` 以隔离模式运行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .decisions import ACTION_LABELS, DecisionEngine, DecisionPolicy
from .fx import FxLedger
from .generator import generate
from .ingestion import IngestionError, load_bundle
from .metrics import MetricEngine
from .obligations import ObligationLedger
from .report import write_report

DEFAULT_DATA_DIR = Path("data/pet_ops")
DEFAULT_REPORT_DIR = Path("reports")


def cmd_generate(args: argparse.Namespace) -> int:
    written = generate(args.data_dir)
    print(f"已生成 {len(written)} 个合成资料文件到 {args.data_dir}：")
    for path in written:
        print(f"  - {path.name}")
    print("提示：全部为虚构数据，不对应真实主体，不构成效果承诺。")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    try:
        bundle = load_bundle(args.data_dir)
    except IngestionError as exc:
        print(f"资料导入失败：{exc}", file=sys.stderr)
        return 2

    fx = FxLedger(bundle.fx_rates)
    engine = MetricEngine(bundle, fx)
    metrics = engine.compute_all()
    summaries = engine.summarize(metrics)

    ledger = ObligationLedger(bundle, fx)
    ledger.annotate_summaries(summaries)
    entries = ledger.entries()

    policy = DecisionPolicy()
    decision_engine = DecisionEngine(ledger, policy)
    decisions = [
        decision_engine.evaluate(s, s.latest_period) for s in summaries
    ]

    md_path, json_path = write_report(
        args.report_dir, summaries, decisions, entries
    )
    print(f"已导入 {len(bundle.regions)} 个地区、"
          f"{len(bundle.subscriptions)} 条订阅、"
          f"{len(bundle.settlements)} 条渠道结算、"
          f"{len(bundle.obligations)} 条服务义务。")
    print("地区决策摘要：")
    for d in decisions:
        print(f"  - {d.region}: {ACTION_LABELS[d.action]}（{d.action}，"
              f"依据 {len(d.reasons)} 条，详见报告）")
    print(f"中文报告：{md_path}")
    print(f"机器可读报告：{json_path}")
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    rc = cmd_generate(args)
    if rc:
        return rc
    return cmd_analyze(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pet-service-region-ops",
        description="智能宠物产品多地区服务可持续经营分析（本地合成数据）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"资料目录（默认 {DEFAULT_DATA_DIR}）")
    common.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR,
                        help=f"报告输出目录（默认 {DEFAULT_REPORT_DIR}）")

    p_gen = sub.add_parser("generate", parents=[common],
                           help="生成本地确定性合成资料")
    p_gen.set_defaults(func=cmd_generate)

    p_an = sub.add_parser("analyze", parents=[common],
                          help="导入资料并输出经营分析报告")
    p_an.set_defaults(func=cmd_analyze)

    p_all = sub.add_parser("run-all", parents=[common],
                           help="生成资料后立即分析")
    p_all.set_defaults(func=cmd_run_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
