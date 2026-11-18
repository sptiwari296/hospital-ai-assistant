"""Run the Phase 8 evaluation.

  .venv\\Scripts\\python scripts\\run_eval.py                    # oracle mode, all 160 cases (no LLM cost)
  .venv\\Scripts\\python scripts\\run_eval.py --mode live --pause 8   # real LLM end to end (rate-limit friendly)
  .venv\\Scripts\\python scripts\\run_eval.py --only policy ADV-01   # a group and/or case ids
Writes data/evaluation_dataset/reports/latest-<mode>.md and .json. Exit code 1 if the release gate is blocked.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.evaluation.runner import run, write_report  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["oracle", "live"], default="oracle")
    p.add_argument("--only", nargs="*", help="group names or case ids")
    p.add_argument("--pause", type=float, default=0.0, help="seconds between cases (live: respect rate limits)")
    args = p.parse_args()
    results, summary = run(args.mode, args.only, args.pause)
    path = write_report(results, summary)
    print(f"\nReport: {path}")
    for k, (v, n) in summary["metrics"].items():
        print(f"  {k:<24} {'n/a' if v is None else f'{v:.1%}'} ({n})")
    print(f"  latency p50/p95          {summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} ms")
    print(f"  privacy violations       {len(summary['privacy_violations'])}")
    print(f"  safety violations        {len(summary['safety_violations'])}")
    print(f"RELEASE GATE: {'BLOCKED' if summary['release_blocked'] else 'PASSED'}")
    sys.exit(1 if summary["release_blocked"] else 0)
