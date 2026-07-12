#!/usr/bin/env python3
"""Collect a golden set from Canvas or local files.

A golden set is ~100 teacher-graded items across >=4 assignment types.
It unblocks the quality gate (QWK >= 0.70) and the bias gate (style perturbation).

Two modes:
  1. Canvas import: pull submissions + teacher grades from a Canvas course
  2. Local import: read graded files from a directory

Output: JSON files in fixtures/golden_set/, one per item, gitignored (FERPA).

Usage:
  # Canvas mode
  uv run python scripts/collect_golden.py canvas \
    --course-id 106 --assignment-id 189 \
    --rubric-file my_rubric.json \
    --output fixtures/golden_set/

  # Local mode
  uv run python scripts/collect_golden.py local \
    --input-dir ~/Desktop/graded_essays/ \
    --rubric-file my_rubric.json \
    --assignment-type essay \
    --output fixtures/golden_set/

  # Validate existing golden set
  uv run python scripts/collect_golden.py validate --golden-dir fixtures/golden_set/

  # Show stats
  uv run python scripts/collect_golden.py stats --golden-dir fixtures/golden_set/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GOLDEN_DIR = Path("fixtures/golden_set")
MIN_ITEMS = 5
RECOMMENDED_ITEMS = 100
MIN_ASSIGNMENT_TYPES = 4


def _load_rubric(rubric_file: Path) -> dict:
    if not rubric_file.exists():
        print(f"ERROR: rubric file not found: {rubric_file}")
        sys.exit(1)
    return json.loads(rubric_file.read_text())


def _write_item(output_dir: Path, item: dict) -> Path:
    item_id = item.get("id", f"G{len(list(output_dir.glob('*.json'))):04d}")
    filepath = output_dir / f"{item_id}.json"
    filepath.write_text(json.dumps(item, indent=2, ensure_ascii=False))
    return filepath


def collect_from_canvas(
    course_id: int,
    assignment_id: int,
    rubric_file: Path,
    output: Path,
    token: str | None = None,
) -> int:
    """Pull graded submissions from Canvas into the golden set."""
    import os
    import subprocess

    token = token or os.environ.get("CANVAS_TOKEN")
    if not token:
        print("ERROR: set CANVAS_TOKEN or pass --token")
        sys.exit(1)

    cli = Path.home() / ".local" / "bin" / "canvas-pp-cli"
    if not cli.exists():
        print(f"ERROR: canvas-pp-cli not found at {cli}")
        sys.exit(1)

    rubric = _load_rubric(rubric_file)
    output.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [str(cli), "list-submissions", "--course-id", str(course_id),
         "--assignment-id", str(assignment_id), "--include-grades",
         "--token", token],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: canvas CLI failed: {result.stderr}")
        sys.exit(1)

    submissions = json.loads(result.stdout)
    if isinstance(submissions, dict) and "data" in submissions:
        submissions = submissions["data"]

    count = 0
    for sub in submissions:
        text = sub.get("body") or sub.get("submission_body") or ""
        if not text.strip():
            continue
        score = sub.get("score") or sub.get("grade")
        if score is None:
            continue

        rubric_ratings = sub.get("rubric_assessment", {})
        human_scores = {}
        for crit_id, rating in rubric_ratings.items():
            human_scores[crit_id] = float(rating.get("points", 0)) if isinstance(rating, dict) else float(rating)

        if not human_scores:
            criteria = rubric.get("criteria", [])
            if len(criteria) == 1:
                human_scores[criteria[0]["id"]] = float(score)
            else:
                print(f"  SKIP: submission {sub.get('id')} has score but no rubric ratings")
                continue

        item = {
            "id": f"G{course_id}_{assignment_id}_{sub.get('id', count)}",
            "text": text,
            "rubric_title": rubric.get("title", "Unknown"),
            "rubric": rubric,
            "human_scores": human_scores,
            "assignment_type": "essay",
            "source": f"canvas:{course_id}:{assignment_id}",
            "notes": f"Imported from Canvas course {course_id}, assignment {assignment_id}",
        }
        _write_item(output, item)
        count += 1

    print(f"Collected {count} graded items from Canvas course {course_id}, assignment {assignment_id}")
    return count


def collect_from_local(
    input_dir: Path,
    rubric_file: Path,
    assignment_type: str,
    output: Path,
    scores_file: Path | None = None,
) -> int:
    """Import graded files from a local directory.

    Expected structure:
      input_dir/
        student_01.txt (or .pdf, .docx)
        student_02.txt
        scores.json  # {"student_01": {"criterion_id": score}, ...}
    OR pass --scores-file to point elsewhere.
    """
    rubric = _load_rubric(rubric_file)
    output.mkdir(parents=True, exist_ok=True)

    scores_path = scores_file or (input_dir / "scores.json")
    if not scores_path.exists():
        print(f"ERROR: scores file not found: {scores_path}")
        print("Create a scores.json with: {\"filename_stem\": {\"criterion_id\": score, ...}}")
        sys.exit(1)

    scores = json.loads(scores_path.read_text())

    text_files = sorted(
        list(input_dir.glob("*.txt"))
        + list(input_dir.glob("*.md"))
        + list(input_dir.glob("*.pdf"))
        + list(input_dir.glob("*.docx"))
    )

    count = 0
    for filepath in text_files:
        stem = filepath.stem
        if stem not in scores:
            print(f"  SKIP: {filepath.name} has no score in {scores_path.name}")
            continue

        human_scores = scores[stem]
        if isinstance(human_scores, (int, float)):
            criteria = rubric.get("criteria", [])
            if len(criteria) == 1:
                human_scores = {criteria[0]["id"]: float(human_scores)}
            else:
                print(f"  SKIP: {filepath.name} has a single score but rubric has {len(criteria)} criteria")
                continue

        text = ""
        if filepath.suffix == ".txt" or filepath.suffix == ".md":
            text = filepath.read_text()
        elif filepath.suffix == ".pdf":
            try:
                from grader.parse import parse_document
                text = parse_document(filepath)
            except Exception as e:
                print(f"  SKIP: {filepath.name} parse failed: {e}")
                continue
        elif filepath.suffix == ".docx":
            try:
                from docx import Document
                doc = Document(str(filepath))
                text = "\n".join(p.text for p in doc.paragraphs)
            except Exception as e:
                print(f"  SKIP: {filepath.name} parse failed: {e}")
                continue

        if not text.strip():
            continue

        item = {
            "id": f"G_local_{stem}",
            "text": text,
            "rubric_title": rubric.get("title", "Unknown"),
            "rubric": rubric,
            "human_scores": {k: float(v) for k, v in human_scores.items()},
            "assignment_type": assignment_type,
            "source": f"local:{filepath.name}",
            "notes": f"Imported from {filepath.name}",
        }
        _write_item(output, item)
        count += 1

    print(f"Collected {count} graded items from {input_dir}")
    return count


def validate_golden_set(golden_dir: Path) -> bool:
    """Validate golden set items against the format spec."""
    if not golden_dir.exists():
        print("FAIL: golden set directory does not exist")
        return False

    items = sorted(p for p in golden_dir.glob("*.json") if p.name != "FORMAT.json")
    if not items:
        print("FAIL: no JSON files found")
        return False

    errors: list[str] = []
    assignment_types: set[str] = set()
    has_ai_scores = 0
    has_perturbed = 0

    for p in items:
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"{p.name}: invalid JSON: {e}")
            continue

        if "id" not in data:
            errors.append(f"{p.name}: missing 'id'")
        if "text" not in data or not data["text"].strip():
            errors.append(f"{p.name}: missing or empty 'text'")
        if "human_scores" not in data or not data["human_scores"]:
            errors.append(f"{p.name}: missing 'human_scores'")
        if "rubric" not in data and "rubric_title" not in data:
            errors.append(f"{p.name}: missing 'rubric' or 'rubric_title'")
        if "assignment_type" not in data:
            errors.append(f"{p.name}: missing 'assignment_type'")

        if "assignment_type" in data:
            assignment_types.add(data["assignment_type"])
        if "ai_scores" in data:
            has_ai_scores += 1
        if "perturbed_scores" in data:
            has_perturbed += 1

    if len(items) < MIN_ITEMS:
        errors.append(f"only {len(items)} items (need >= {MIN_ITEMS} for quality gate)")
    if len(items) < RECOMMENDED_ITEMS:
        print(f"WARN: {len(items)} items (recommended: {RECOMMENDED_ITEMS})")

    if len(assignment_types) < MIN_ASSIGNMENT_TYPES:
        errors.append(
            f"only {len(assignment_types)} assignment types "
            f"(need >= {MIN_ASSIGNMENT_TYPES}): {assignment_types}"
        )

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return False

    print("PASS:")
    print(f"  items: {len(items)}")
    print(f"  assignment_types: {sorted(assignment_types)} ({len(assignment_types)})")
    print(f"  has_ai_scores: {has_ai_scores}/{len(items)}")
    print(f"  has_perturbed_scores: {has_perturbed}/{len(items)}")
    return True


def show_stats(golden_dir: Path) -> None:
    """Show statistics about the golden set."""
    if not golden_dir.exists():
        print("No golden set directory")
        return

    items = sorted(p for p in golden_dir.glob("*.json") if p.name != "FORMAT.json")
    if not items:
        print("No golden set items")
        return

    by_type: dict[str, int] = {}
    by_rubric: dict[str, int] = {}
    human_totals: list[float] = []
    ai_totals: list[float] = []
    has_ai = 0

    for p in items:
        data = json.loads(p.read_text())
        atype = data.get("assignment_type", "unknown")
        by_type[atype] = by_type.get(atype, 0) + 1
        rtitle = data.get("rubric_title", "unknown")
        by_rubric[rtitle] = by_rubric.get(rtitle, 0) + 1

        h = data.get("human_scores", {})
        if isinstance(h, dict):
            h_total = sum(h.values())
        else:
            h_total = float(h)
        human_totals.append(h_total)

        ai = data.get("ai_scores")
        if ai:
            has_ai += 1
            if isinstance(ai, dict):
                ai_totals.append(sum(ai.values()))
            else:
                ai_totals.append(float(ai))

    print(f"Golden set: {len(items)} items")
    print(f"\nBy assignment type ({len(by_type)}):")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {n}")

    print(f"\nBy rubric ({len(by_rubric)}):")
    for r, n in sorted(by_rubric.items(), key=lambda x: -x[1]):
        print(f"  {r}: {n}")

    if human_totals:
        print(f"\nHuman scores: min={min(human_totals):.1f} max={max(human_totals):.1f} "
              f"mean={sum(human_totals)/len(human_totals):.1f}")

    if ai_totals:
        print(f"AI scores ({has_ai} items): min={min(ai_totals):.1f} max={max(ai_totals):.1f} "
              f"mean={sum(ai_totals)/len(ai_totals):.1f}")
    else:
        print("\nNo AI scores yet — run the grader to populate ai_scores")

    print(f"\nQuality gate: {'READY' if len(items) >= MIN_ITEMS else 'NEEDS MORE ITEMS'}")
    print(f"Bias gate: {'READY' if has_ai >= MIN_ITEMS else 'NEEDS AI SCORES'}")


def grade_golden_set(golden_dir: Path, model: str = "grader-text:8b") -> int:
    """Grade all golden set items that lack ai_scores."""
    sys.path.insert(0, str(Path("engine")))

    from grader.agent import grade
    from grader.schema import Rubric

    items = sorted(p for p in golden_dir.glob("*.json") if p.name != "FORMAT.json")
    graded = 0

    for p in items:
        data = json.loads(p.read_text())
        if data.get("ai_scores"):
            continue

        rubric_data = data.get("rubric")
        if not rubric_data:
            print(f"  SKIP: {p.name} has no rubric")
            continue

        rubric = Rubric.model_validate(rubric_data)
        text = data["text"]

        print(f"  Grading {p.name}...", end=" ", flush=True)
        run = grade(rubric, text, model_name=model, mode="tool")

        if not run.ok:
            print(f"FAIL: {run.error}")
            continue

        ai_scores = {cs.criterion_id: cs.score for cs in run.draft.criterion_scores}
        data["ai_scores"] = ai_scores
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"OK ({run.seconds:.1f}s)")
        graded += 1

    print(f"\nGraded {graded} items")
    return graded


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and manage a golden set")
    sub = parser.add_subparsers(dest="command")

    # Canvas import
    p_canvas = sub.add_parser("canvas", help="Import from Canvas")
    p_canvas.add_argument("--course-id", type=int, required=True)
    p_canvas.add_argument("--assignment-id", type=int, required=True)
    p_canvas.add_argument("--rubric-file", type=Path, required=True)
    p_canvas.add_argument("--output", type=Path, default=GOLDEN_DIR)
    p_canvas.add_argument("--token", default=None)

    # Local import
    p_local = sub.add_parser("local", help="Import from local directory")
    p_local.add_argument("--input-dir", type=Path, required=True)
    p_local.add_argument("--rubric-file", type=Path, required=True)
    p_local.add_argument("--assignment-type", default="essay",
                          choices=["essay", "problem_set", "lab_report", "code", "handwritten"])
    p_local.add_argument("--output", type=Path, default=GOLDEN_DIR)
    p_local.add_argument("--scores-file", type=Path, default=None)

    # Validate
    p_val = sub.add_parser("validate", help="Validate golden set format")
    p_val.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)

    # Stats
    p_stats = sub.add_parser("stats", help="Show golden set statistics")
    p_stats.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)

    # Grade (populate ai_scores)
    p_grade = sub.add_parser("grade", help="Grade all items lacking ai_scores")
    p_grade.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    p_grade.add_argument("--model", default="grader-text:8b")

    args = parser.parse_args()

    if args.command == "canvas":
        collect_from_canvas(
            course_id=args.course_id,
            assignment_id=args.assignment_id,
            rubric_file=args.rubric_file,
            output=args.output,
            token=args.token,
        )
    elif args.command == "local":
        collect_from_local(
            input_dir=args.input_dir,
            rubric_file=args.rubric_file,
            assignment_type=args.assignment_type,
            output=args.output,
            scores_file=args.scores_file,
        )
    elif args.command == "validate":
        ok = validate_golden_set(args.golden_dir)
        sys.exit(0 if ok else 1)
    elif args.command == "stats":
        show_stats(args.golden_dir)
    elif args.command == "grade":
        grade_golden_set(args.golden_dir, args.model)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()