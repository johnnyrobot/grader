"""Score the citation verifier against the 29 fixtures.

Reports accuracy by verdict, false-positive rate separately, and per-layer breakdown.

Usage:
  uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader.citations import verify, CitationCheck


def decide_citation_mode(summary: dict) -> dict:
    """Decide whether citation verification can ship as enabled or experimental."""
    failures: list[str] = []
    if summary.get("accuracy", 0) < 0.90:
        failures.append("accuracy below 90%")
    if summary.get("false_positives", 0) > 0:
        failures.append(f"{summary['false_positives']} false positive citation verdict(s)")

    retraction = summary.get("retraction_check", {})
    retraction_total = int(retraction.get("total", 0) or 0)
    retraction_caught = int(retraction.get("caught", 0) or 0)
    if retraction_total > 0 and retraction_caught < retraction_total:
        missed = retraction_total - retraction_caught
        failures.append(f"retraction check missed {missed}/{retraction_total}")

    return {
        "mode": "enabled" if not failures else "experimental",
        "release_status": "PASS" if not failures else "FAIL",
        "experimental_required": bool(failures),
        "failures": failures,
    }


def score_fixtures(fixtures_path: Path, *, check_claim: bool = True, verbose: bool = False) -> dict:
    """Run the verifier against all 29 fixtures and score results."""
    data = json.loads(fixtures_path.read_text())
    fixtures = data["fixtures"]

    results: list[dict] = []
    correct = 0
    false_positives = 0  # the worst error: calling a real citation "fabricated"
    total = len(fixtures)

    for fx in fixtures:
        expected = fx["verdict"]
        ref_text = fx["reference_text"]
        claim = fx.get("in_text_claim", "")
        expected_flags = fx.get("flags", [])

        try:
            result = verify(ref_text, claim, check_claim=check_claim)
        except Exception as e:
            result = CitationCheck(
                verdict="not_found", confidence=0.0,
                resolution_layer="error",
                error=str(e)[:200],
            )

        is_correct = result.verdict == expected
        if is_correct:
            correct += 1

        # False positive: expected verified/mismatch/unreachable but got not_found
        if expected in ("verified", "metadata_mismatch", "unreachable") and result.verdict == "not_found":
            false_positives += 1

        # Check retraction flag
        retraction_caught = "retracted" in result.flags if "retracted" in expected_flags else None

        results.append({
            "id": fx["id"],
            "expected": expected,
            "got": result.verdict,
            "correct": is_correct,
            "confidence": result.confidence,
            "resolution_layer": result.resolution_layer,
            "expected_layer": fx.get("expected_resolution_layer", ""),
            "flags": result.flags,
            "retraction_caught": retraction_caught,
            "error": result.error,
        })

        if verbose:
            status = "✓" if is_correct else "✗"
            print(f"  {status} {fx['id']}: expected={expected} got={result.verdict} "
                  f"conf={result.confidence:.2f} layer={result.resolution_layer}")

    accuracy = correct / total if total else 0
    fp_rate = false_positives / total if total else 0

    # Per-verdict breakdown
    by_verdict: dict[str, dict] = {}
    for r in results:
        v = r["expected"]
        if v not in by_verdict:
            by_verdict[v] = {"total": 0, "correct": 0}
        by_verdict[v]["total"] += 1
        if r["correct"]:
            by_verdict[v]["correct"] += 1

    # Retraction check
    retraction_fixtures = [r for r in results if r["retraction_caught"] is not None]
    retraction_caught_count = sum(1 for r in retraction_fixtures if r["retraction_caught"])

    summary = {
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 3),
        "false_positives": false_positives,
        "false_positive_rate": round(fp_rate, 3),
        "by_verdict": {
            v: {"total": d["total"], "correct": d["correct"],
                "accuracy": round(d["correct"] / d["total"], 3) if d["total"] else 0}
            for v, d in by_verdict.items()
        },
        "retraction_check": {
            "total": len(retraction_fixtures),
            "caught": retraction_caught_count,
            "accuracy": round(retraction_caught_count / len(retraction_fixtures), 3)
                        if retraction_fixtures else 0,
        },
        "results": results,
    }
    mode = decide_citation_mode(summary)
    summary["citation_mode"] = mode
    summary["mode"] = mode["mode"]
    summary["release_status"] = mode["release_status"]
    summary["experimental_required"] = mode["experimental_required"]
    summary["failures"] = mode["failures"]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Score citation verifier against fixtures")
    parser.add_argument("--fixtures", default="fixtures/citations/citation_fixtures.json",
                        help="Path to citation_fixtures.json")
    parser.add_argument("--verbose", action="store_true", help="Print per-fixture results")
    parser.add_argument("--no-claim-check", action="store_true",
                        help="Skip L3 claim checking (faster, but misses claim_unsupported)")
    parser.add_argument("--output", default=None, help="Write results JSON to this path")
    args = parser.parse_args()

    fixtures_path = Path(args.fixtures)
    if not fixtures_path.exists():
        print(f"Fixtures not found: {fixtures_path}")
        sys.exit(1)

    print(f"Scoring against {fixtures_path}...")
    summary = score_fixtures(
        fixtures_path,
        check_claim=not args.no_claim_check,
        verbose=args.verbose,
    )

    print(f"\n=== Citation Verification Score ===")
    print(f"Total: {summary['total']}")
    print(f"Correct: {summary['correct']}/{summary['total']} ({summary['accuracy']:.1%})")
    print(f"False positives (real→fabricated): {summary['false_positives']} "
          f"({summary['false_positive_rate']:.1%})")
    print(f"\nPer verdict:")
    for v, d in sorted(summary["by_verdict"].items()):
        print(f"  {v}: {d['correct']}/{d['total']} ({d['accuracy']:.0%})")
    print(f"\nRetraction check: {summary['retraction_check']['caught']}/"
          f"{summary['retraction_check']['total']} caught")

    # Pass criteria
    passed = True
    if summary["accuracy"] < 0.90:
        print("\n✗ FAIL: accuracy < 90%")
        passed = False
    if summary["false_positives"] > 0:
        print(f"\n✗ FAIL: {summary['false_positives']} false positive(s) — zero tolerance")
        passed = False
    if summary["retraction_check"]["total"] > 0 and summary["retraction_check"]["caught"] < summary["retraction_check"]["total"]:
        print(f"\n✗ FAIL: retraction check missed {summary['retraction_check']['total'] - summary['retraction_check']['caught']}")
        passed = False

    if summary.get("experimental_required"):
        print("\nCitation beta mode: experimental")
        for failure in summary.get("failures", []):
            print(f"  - {failure}")
    else:
        print("\nCitation beta mode: enabled")

    if passed:
        print("\n✓ ALL CHECKS PASSED")
    else:
        print("\n✗ SOME CHECKS FAILED")

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2))
        print(f"\nResults written to {args.output}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
