"""Score the drafters: does each draft come out as a valid, answerable RTI?

Usage:
  python evals/run_drafter_eval.py            # offline drafter only (free)
  python evals/run_drafter_eval.py --ai       # also the Claude drafter (needs ANTHROPIC_API_KEY; ~15 API calls)

How a draft is scored (all automatic, using the rule checker as the judge):
  * clean      - the rendered letter has no error/warning from the checker
  * private    - for private-company needs, the draft is NOT addressed to the company and tells
                 the user which regulator to use
  * language   - Hindi needs get a Hindi letter
  * life       - detention/arrest needs claim the 48-hour deadline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rti_sahayak import Applicant, check, draft_offline, draft_with_ai, render_letter  # noqa: E402
from rti_sahayak.checker import ERROR, WARNING  # noqa: E402
from rti_sahayak.drafter import DraftError  # noqa: E402

NEEDS = Path(__file__).with_name("drafter_needs.jsonl")
APPLICANT = Applicant(
    name="Test Applicant",
    address="House No 1, Civil Lines, Bilaspur 495001",
    email="test@example.com",
    place="Bilaspur",
)
REGULATORS = ("rbi", "reserve bank", "trai", "telecom", "education", "dot", "department", "regulator", "rera")


def score(case: dict, draft) -> dict:
    letter = render_letter(draft, APPLICANT)
    report = check(letter)
    serious = [i.code for i in report.issues if i.severity in (ERROR, WARNING)]
    result = {"clean": not serious, "issues": serious}
    exp = case["expect"]
    if exp.get("private"):
        to_company = "PRIVATE_BODY" in check(draft.public_authority).codes
        notes = " ".join(draft.notes_for_user + [draft.public_authority]).lower()
        result["private"] = (not to_company) and any(r in notes for r in REGULATORS)
    if exp.get("hindi"):
        result["language"] = draft.language == "hi"
    if exp.get("life"):
        result["life"] = draft.life_or_liberty
    result["pass"] = all(v for k, v in result.items() if k not in ("issues",))
    return result


def run(name: str, fn, cases: list[dict]) -> list[dict]:
    rows = []
    for c in cases:
        t0 = time.time()
        try:
            d, _ = fn(c["need"], authority_hint=c["authority"])
            r = score(c, d)
        except DraftError as e:
            r = {"pass": False, "clean": False, "issues": [f"ERROR: {e}"]}
        r["id"], r["secs"] = c["id"], round(time.time() - t0, 1)
        rows.append(r)
    passed = sum(r["pass"] for r in rows)
    clean = sum(r["clean"] for r in rows)
    print(f"\n== {name}: {passed}/{len(rows)} pass · {clean}/{len(rows)} clean letters ==")
    for r in rows:
        mark = "✓" if r["pass"] else "✗"
        extra = {k: v for k, v in r.items() if k in ("private", "language", "life")}
        print(f"  {mark} {r['id']:<24} issues={r['issues'] or '-'} {extra or ''}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true", help="also run the Claude drafter")
    args = ap.parse_args()
    cases = [json.loads(line) for line in NEEDS.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = {"offline": run("Offline drafter (rules only)", draft_offline, cases)}
    if args.ai:
        results["ai"] = run("Claude drafter (with checker repair loop)", draft_with_ai, cases)
    out = Path(__file__).with_name("drafter_results.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSaved {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
