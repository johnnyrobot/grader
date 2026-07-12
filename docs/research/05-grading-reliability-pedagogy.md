# Gap report: LLM grading reliability + pedagogy literature (sonnet agent)

## 1. SURE (MDPI 2504-4990/8/3/74, ~Mar 2026)
Method: each answer scored 20x by repeated LLM prompting; majority vote = grade; dispersion = uncertainty signal; low-certainty flagged to human regrade. Models: gpt-5, codestral-25.01, llama-3.3-70b + 3-model ensemble.
- Targeted human regrading of flagged subset recovered most accuracy loss, cutting manual grading time 40-90%.
- 3-model ensemble: 70-90% of LLM grades fell inside human-grader range.
- Newer reasoning models (gpt-5) may shrink human-oversight need.

## 2. Three arXiv papers
**2603.29559 "When Can We Trust LLM Graders?"** — 7 LLMs (4B-120B), 3 edu datasets. Self-reported confidence beat self-consistency voting on calibration (ECE 0.166 vs 0.229; voting 38% worse and 5x costlier). Larger models calibrate better (GPT-OSS-120B ECE 0.100). Caveat: confidence top-skewed ("confidence floor").
**2603.18765 "Implicit Grading Bias"** — 180 responses (math/programming/essay) perturbed for grammar/informal/non-native style, content constant; LLaMA 3.3 70B + Qwen 2.5 72B. Essay grading: significant bias for EVERY perturbation (p<0.05); informal-language penalty −1.90/−1.20 pts on /10; non-native −1.35/−0.90. Math/programming minimal bias. Bias persisted despite "ignore style" prompting.
**2606.12422 "K-12 GenAI Assessment Graders"** — MCAS data, QWK/PRMSE; Claude Sonnet 4, Haiku 4.5, GPT-5, GPT-5 Mini. Substantial human agreement in math/science; ELA inconsistent. Teachers/students accept AI narrative feedback but skeptical of AI numerical scores → formative not summative.

## 3. Broader findings
- Rubric-conditioned LLM-judge κ 0.765-0.873 vs humans when rubrics explicit, analytic (criterion-separated), calibrated. Human κ target ≥0.7. Binary MET/UNMET most reliable; 3-5 level anchored scales recommended. (futureagi.com/glossary/rubric/; arXiv 2603.00077 Autorubric; arXiv 2601.08843)
- 0-5 scale strongest LLM-human agreement; 0-10 weakest, holds under temperature perturbation (arXiv 2601.03444).
- Few-shot anchors: including graded examples substantially reduces bias vs human scores (ScienceDirect S1075293526000413 "Anchor is the key"); diminishing returns past ~8-12 exemplars; RAG-style similarity retrieval of prior graded examples > random selection; verdict-balanced sampling avoids base-rate bias. Also arXiv 2502.13337 "LMs are Few-Shot Graders"; arXiv 2412.06910 (Grading Confidence Index + personalized feedback).
- Biases: verbosity bias (arXiv 2410.02736, 2604.23178); position bias >10pt swings (arXiv 2310.01432); leniency bias (fine-tuned GPT-3.5 78.3% agreement but overrated weak essays); AI-text favoritism; consistency-bias paradox — reliability ≠ fairness (arXiv 2606.19544).
- Temperature: >95% same-verdict at temp 0, ~70% at temp 1; affects variance not mean accuracy → grade at temp 0 (arXiv 2603.28304).
- Small vs frontier: 1.5-3B poor at grading (50-60% on Grades 1-5); 7-8B often comparable to larger; Qwen2.5-14B/Mistral-Small-24B consistently strong (EduAdapt arXiv 2510.17389). Task-specific small models can match frontier on narrow scoped grading (arize.com; forbes.com 2026-06-25).

## 4. Pedagogy/feedback
- Systematic review (ScienceDirect S2666557326000455, 47 studies 2023-2026): LLM formative feedback positive for writing quality/self-regulation/motivation when task-aligned + iterative; risks: hallucinated feedback, overreliance, fairness/trust.
- Feedback uptake is the bottleneck (ResearchGate 395196355) → make feedback actionable/optional.
- Second-LLM feedback-quality evaluator as QA layer (arXiv 2511.04213; 2508.05952 "Dean of LLM Tutors").
- Hattie & Timperley: feedback must be goal-linked, thinking-causing, understandable, actionable → encode as checklist for AI-generated comments.
- Teacher feedback literacy w/ automated tools is scaffoldable by UI (surface WHY a score was given) (Springer s41239-023-00410-9).
- arXiv 2506.07955 + Tandfonline 02602938.2025.2487495: LLMs best as formative, revisable feedback generators, not autonomous summative graders.

## 5. HITL UX patterns
- Confidence-based routing (Braintrust, Galileo). CHiLLGrader (arXiv 2603.11957): auto-grades 35-65% at expert quality via confidence thresholds.
- Disagreement → adjudication with justifications; persistent disagreement patterns feed rubric refinement (Comet).
- Audit trails: full provenance, who/what decided, confidence+justification attached (Velt). FERPA/state audit relevant.
- Async review queues: AI batch-scores + flags; teacher works flagged queue at own pace.
- Precedents: Label Studio, SuperAnnotate.

## FERPA/COPPA
- "School official" exception = dominant legal basis; requires school "direct control" over data → local inference strengthens footing.
- FERPA has no explicit security requirements — voluntarily exceeding baseline (encryption at rest, local-only, no PII in vendor training, data-flow docs) is a differentiator.
- Cloud LLM vendor default terms may allow training on inputs → PII must stay local or be contractually excluded.
- 2025 COPPA amendments: opt-in default for under-13, written security programs.
(concentric.ai; schoolai.com; fpf.org Ed_AI_legal_compliance PDF; flywire.com)

## Agent's synthesis notes
1. SURE vs 2603.29559 disagree on self-consistency voting — combine both signals (low confidence AND low consistency → high-priority flag).
2. Bias risk subject-dependent: essays need more human-review weight than math/code.
3. Grade at temp 0; separate pass for uncertainty signal.
4. Teacher examples as retrieved (similarity, verdict-balanced) few-shot anchors — empirically supported.
5. Separate trust levels: AI narrative feedback (more autonomous) vs numerical score (teacher always approves).
6. Hattie's 4 conditions as feedback-quality checklist.
