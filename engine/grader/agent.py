"""The grading agent: rubric + submission -> validated GradeDraft.

Runs against any OpenAI-compatible local endpoint. Ollama today; an MLX server later
(same interface, per RESEARCH.md §3) — which is why nothing here imports Ollama directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.output import NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from grader.schema import GradeDraft, GradeRun, OutputMode, Rubric

# Grade at temperature 0. Same-verdict reproducibility is >95% at temp 0 and falls to ~70%
# at temp 1 (arXiv 2603.28304); temperature mainly buys variance, not accuracy. Uncertainty
# is measured separately, by sampling — not by making the grade itself noisy.
GRADING_SETTINGS = ModelSettings(temperature=0.0)

SYSTEM_PROMPT = """\
You are an experienced instructor grading student work. You are drafting a grade for a \
human instructor to review — they are the final authority, and your job is to save them \
time without ever substituting your judgment for theirs.

Rules:
1. Grade ONLY against the rubric criteria given. Never invent criteria, and never let an \
unlisted quality (length, vocabulary, formatting flair) move a score.
2. Judge CONTENT, not style. Informal tone, grammatical slips, or non-native phrasing must \
not reduce a content score unless a criterion explicitly assesses writing mechanics.
3. Cite specific evidence from the submission in every rationale. "Weak thesis" is useless; \
"the thesis in paragraph 1 asserts X but never states a position on Y" is useful.
4. Feedback is for the STUDENT. Make it actionable: name the concrete next step. Feedback \
that cannot be acted on is noise.
5. Report confidence honestly. If the work is ambiguous, off-rubric, or unlike anything \
you've been shown, say so with a LOW confidence value. Routing hard cases to a human is \
the correct outcome, not a failure.
"""


def build_model(model_name: str, base_url: str = "http://localhost:11434/v1") -> OpenAIChatModel:
    """Any OpenAI-compatible local server. Ollama needs no real key."""
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key="local-no-key-needed"),
    )


def build_agent(model_name: str, mode: OutputMode = "tool", *, base_url: str | None = None) -> Agent:
    """Construct the grading agent in one of Pydantic AI's three structured-output modes.

    The mode matters: 'tool' (the default) presents the schema as an output tool's
    parameters, so it depends on the model's tool-calling ability — the known weak spot for
    small quantized models. 'prompted' just asks for JSON and validates. P0 measures which
    one these models can actually sustain; see bench.py.
    """
    model = build_model(model_name, base_url or "http://localhost:11434/v1")

    output_spec: object
    if mode == "tool":
        output_spec = ToolOutput(GradeDraft)
    elif mode == "native":
        output_spec = NativeOutput(GradeDraft)
    elif mode == "prompted":
        output_spec = PromptedOutput(GradeDraft)
    else:  # pragma: no cover
        raise ValueError(f"unknown output mode: {mode}")

    return Agent(
        model,
        output_type=output_spec,
        system_prompt=SYSTEM_PROMPT,
        model_settings=GRADING_SETTINGS,
        retries=3,  # schema failures re-prompt; see the rubric-level check below
    )


def _user_prompt(rubric: Rubric, submission: str, *, anchors: str = "", materials: str = "") -> str:
    parts = [rubric.render()]
    if materials:
        parts += ["# Source materials (ground truth for this assignment)", materials, ""]
    if anchors:
        # Anchor exemplars are THE calibration mechanism (RESEARCH.md §8): they are how human
        # raters calibrate, and they measurably close the LLM-human gap. P1 wires up retrieval.
        parts += [
            "# Previously graded examples (the instructor's own scoring — match this standard)",
            anchors,
            "",
        ]
    parts += [
        "# Student submission",
        submission,
        "",
        "Grade this submission against every rubric criterion.",
    ]
    return "\n".join(parts)


def grade(
    rubric: Rubric,
    submission: str,
    *,
    model_name: str = "grader-text:8b",
    mode: OutputMode = "tool",
    anchors: str = "",
    materials: str = "",
    base_url: str | None = None,
) -> GradeRun:
    """Grade one submission. Never raises — a failure is a result the queue must surface."""
    agent = build_agent(model_name, mode, base_url=base_url)
    retries = 0

    @agent.output_validator
    def _check(draft: GradeDraft) -> GradeDraft:
        # Constraints that need the rubric (unknown ids, out-of-range scores, missing
        # criteria) can't live in the schema, so enforce them here. ModelRetry hands the
        # problems back to the model to repair rather than accepting a broken grade.
        nonlocal retries
        problems = draft.validate_against(rubric)
        if problems:
            retries += 1
            raise ModelRetry("Fix these problems and return the corrected grade:\n- " + "\n- ".join(problems))
        return draft

    prompt = _user_prompt(rubric, submission, anchors=anchors, materials=materials)

    t0 = time.perf_counter()
    try:
        result = agent.run_sync(prompt)
        return GradeRun(result.output, mode, model_name, time.perf_counter() - t0, retries)
    except Exception as exc:  # noqa: BLE001 - a failed grade is data, not a crash
        return GradeRun(None, mode, model_name, time.perf_counter() - t0, retries, error=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class PydanticAIJudge:
    """The production Judge: pydantic_ai against an OpenAI-compatible local server.

    Frozen, and the model is fixed at construction — there is no setter for a
    caller to reach for mid-batch.
    """

    model_name: str = "grader-text:8b"
    mode: OutputMode = "tool"
    base_url: str | None = None

    def grade(
        self,
        rubric: Rubric,
        submission: str,
        *,
        anchors: str = "",
        materials: str = "",
    ) -> GradeRun:
        return grade(
            rubric, submission,
            model_name=self.model_name, mode=self.mode,
            anchors=anchors, materials=materials, base_url=self.base_url,
        )
