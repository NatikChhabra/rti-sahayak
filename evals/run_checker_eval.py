"""Measure the rule checker against hand-labelled RTI applications.

Usage:
  python evals/run_checker_eval.py                       # dev set (used while building the checker)
  python evals/run_checker_eval.py heldout_cases.jsonl   # held-out set (written independently, never tuned on)

Each case in checker_cases.jsonl lists the issue codes a careful human reviewer
would raise. We compare, per code: true positives, false positives, false negatives.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rti_sahayak import check  # noqa: E402

CASES = Path(__file__).with_name("checker_cases.jsonl")
MIN_PRECISION = 0.9
MIN_RECALL = 0.9


def main() -> int:
    path = Path(__file__).with_name(sys.argv[1]) if len(sys.argv) > 1 else CASES
    print(f"Cases: {path.name}")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tp, fp, fn = Counter(), Counter(), Counter()
    exact = 0
    failures = []
    for c in cases:
        got, want = set(check(c["text"]).codes), set(c["expected"])
        for code in got & want:
            tp[code] += 1
        for code in got - want:
            fp[code] += 1
        for code in want - got:
            fn[code] += 1
        if got == want:
            exact += 1
        else:
            failures.append((c["id"], sorted(got - want), sorted(want - got)))

    codes = sorted(set(tp) | set(fp) | set(fn))
    print(f"{'code':<16}{'TP':>4}{'FP':>4}{'FN':>4}{'precision':>11}{'recall':>8}")
    for code in codes:
        p = tp[code] / (tp[code] + fp[code]) if tp[code] + fp[code] else 1.0
        r = tp[code] / (tp[code] + fn[code]) if tp[code] + fn[code] else 1.0
        print(f"{code:<16}{tp[code]:>4}{fp[code]:>4}{fn[code]:>4}{p:>11.2f}{r:>8.2f}")
    T, F, N = sum(tp.values()), sum(fp.values()), sum(fn.values())
    precision, recall = T / (T + F), T / (T + N)
    print(
        f"\n{len(cases)} cases · exact match {exact}/{len(cases)} · "
        f"overall precision {precision:.2f} · recall {recall:.2f}"
    )
    for cid, extra, missed in failures:
        print(f"  ✗ {cid}: false alarms {extra or '-'} · missed {missed or '-'}")
    return 0 if precision >= MIN_PRECISION and recall >= MIN_RECALL else 1


if __name__ == "__main__":
    sys.exit(main())
