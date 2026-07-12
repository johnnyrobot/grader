"""Generate a sample golden set with synthetic data — for testing the gates.

Real golden sets contain real student work (FERPA, gitignored). This script creates
synthetic items that exercise the quality and bias gates without any real student data.

Usage:
  python fixtures/golden_set/generate_sample.py --count 10
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SAMPLE_RUBRIC = {
    "title": "Sample Essay Rubric",
    "criteria": [
        {"id": "content", "title": "Content Quality", "max_points": 5,
         "criterion_type": "open_ended", "levels": []},
        {"id": "evidence", "title": "Use of Evidence", "max_points": 5,
         "criterion_type": "analytic", "levels": []},
        {"id": "clarity", "title": "Clarity & Organization", "max_points": 5,
         "criterion_type": "analytic", "levels": []},
        {"id": "mechanics", "title": "Mechanics", "max_points": 5,
         "criterion_type": "open_ended", "levels": []},
    ],
}

SAMPLE_TEXTS = [
    ("The experiment demonstrated that working memory capacity is positively correlated with academic performance. Participants with higher working memory scores achieved better grades across all tested subjects. This finding supports the hypothesis that working memory serves as a foundation for complex cognitive processes necessary in educational settings."),
    ("Research on attention deficit disorders has shown that environmental factors play a significant role in symptom severity. Studies conducted in controlled laboratory settings reveal that noise, lighting, and room temperature all impact attention performance. These results suggest that classroom design should consider environmental modifications to support students with attention challenges."),
    ("The analysis of student feedback mechanisms reveals three key patterns: immediate feedback improves retention, delayed feedback promotes deeper reflection, and peer feedback builds critical evaluation skills. When combined, these approaches create a comprehensive feedback ecosystem that addresses both cognitive and social dimensions of learning."),
    ("Machine learning applications in education have grown substantially, but concerns about algorithmic bias remain. This paper examines three case studies where automated grading systems produced systematically different scores for different demographic groups. The findings underscore the importance of human oversight in AI-assisted grading workflows."),
    ("The relationship between sleep quality and academic performance was examined in a sample of 200 undergraduate students. Results indicated that students reporting consistent sleep schedules achieved significantly higher GPAs than those with irregular sleep patterns. The effect was most pronounced in courses requiring complex problem-solving."),
    ("Collaborative learning environments produce mixed results depending on group composition. This study found that heterogeneous groups (mixed ability levels) generated more creative solutions but took longer to reach consensus. Homogeneous groups were more efficient but produced less innovative outcomes."),
    ("Formative assessment practices in higher education remain underutilized despite strong evidence of their effectiveness. Barriers include time constraints, large class sizes, and institutional reward structures that prioritize summative evaluation. This paper proposes a framework for integrating low-stakes formative assessments into existing curricula."),
    ("The impact of classroom layout on student engagement was measured across three configurations: traditional rows, collaborative clusters, and flexible seating. Engagement was highest in flexible seating arrangements, particularly for courses involving group work. Traditional rows showed best results for lecture-based instruction."),
    ("Digital literacy skills are increasingly essential for academic success, yet significant disparities exist across socioeconomic backgrounds. This study surveyed 500 first-year students and found that prior technology exposure predicted digital literacy scores more strongly than current access to devices."),
    ("Metacognitive strategy instruction improved problem-solving performance in a controlled intervention study. Students who received explicit instruction in planning, monitoring, and evaluating their thinking processes outperformed control groups on transfer tasks. The effect persisted three months after the intervention ended."),
]

SAMPLE_SCORES = [
    {"content": 4, "evidence": 3, "clarity": 4, "mechanics": 5},
    {"content": 3, "evidence": 4, "clarity": 3, "mechanics": 4},
    {"content": 5, "evidence": 4, "clarity": 5, "mechanics": 4},
    {"content": 4, "evidence": 5, "clarity": 4, "mechanics": 3},
    {"content": 3, "evidence": 3, "clarity": 4, "mechanics": 4},
    {"content": 4, "evidence": 4, "clarity": 3, "mechanics": 5},
    {"content": 5, "evidence": 5, "clarity": 5, "mechanics": 4},
    {"content": 3, "evidence": 2, "clarity": 4, "mechanics": 3},
    {"content": 4, "evidence": 4, "clarity": 4, "mechanics": 5},
    {"content": 5, "evidence": 4, "clarity": 5, "mechanics": 4},
]


def generate(count: int, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    n = min(count, len(SAMPLE_TEXTS))
    for i in range(n):
        item = {
            "id": f"SAMPLE-{i+1:03d}",
            "text": SAMPLE_TEXTS[i],
            "rubric_title": SAMPLE_RUBRIC["title"],
            "rubric": SAMPLE_RUBRIC,
            "human_scores": SAMPLE_SCORES[i],
            "assignment_type": "essay",
            "notes": "synthetic sample — not real student work",
        }
        path = output_dir / f"sample_{i+1:03d}.json"
        path.write_text(json.dumps(item, indent=2))
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample golden set items")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("fixtures/golden_set"))
    args = parser.parse_args()
    n = generate(args.count, args.output_dir)
    print(f"Generated {n} sample items in {args.output_dir}")
    print("These are SYNTHETIC — real golden sets need teacher-graded student work.")


if __name__ == "__main__":
    main()