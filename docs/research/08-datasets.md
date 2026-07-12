# Dataset report: HF/Kaggle sweep (sonnet agent, verified 2026-07-11 via HF API)

## 1. Essay scoring (human scores)
- PERSUADE 2.0: 25k+ argumentative essays gr6-12, 15 prompts, holistic 1-6 + discourse-element scores + demographics (ELL, race, econ, disability → BIAS SUITE GOLD). CC BY-NC-SA 4.0. Kaggle nbroad/persaude-corpus-2; GitHub scrosseye/persuade_corpus_2.0.
- ASAP-AES (2012 Hewlett): 8 sets, gr7-10, ~13k essays, resolved human scores. Kaggle comp; FREE HF mirrors no gating: TasfiaS/ASAP-AES, jjordanoc/asap-aes, nlpatunt/D_ASAP-AES.
- ASAP 2.0 (2024): ~24k essays, bias-mitigated sampling. CC BY. Kaggle lburleigh/asap-2-0; GitHub scrosseye/ASAP_2.0.
- HF zero-friction mirrors: tasksource/AES2-essay-scoring (CC BY-NC-4.0), jatinmehra/Automated-Essay-Scoring-2.0, abdlh/...EssayScore_and_24_textual_features (MIT).
- ELLIPSE: 6 analytic traits (cohesion/syntax/vocab/phraseology/grammar/conventions) — via Kaggle Feedback Prize ELL comp; no clean HF mirror.
- IELTS: chillies/IELTS-writing-task-2-evaluation (979 DL, NO LICENSE — verify).
- TOEFL11: ETS-restricted, not freely available.

## 2. Short-answer / STEM
- ⭐ nkazi/MohlerASAG — CC BY-4.0, 1-10k rows, schema: question, instructor_answer, student_answer, score_grader_1, score_grader_2, score_avg. TWO INDEPENDENT HUMAN GRADERS → inter-rater baseline for QWK gate. No barriers.
- ⭐ nkazi/SciEntsBank — CC BY-4.0, 10-100k rows, 5-way human labels (correct/contradictory/partially_correct_incomplete/irrelevant/non_domain). No barriers.
- ASAP-SAS: 10 sets, science/bio/ELA gr10, ~2200 responses each, 0-2/0-3. HF: nlpatunt/D_ASAP-SAS.
- SEACrowd/id_short_answer_grading: 534 respondents, 7 expert graders (multi-rater). License unspecified.
- Math ASAG: no clean public dataset found.

## 3. Rubric-paired
- Kaggle davidspencer/persuade-rubric-holistic-essay-scoring — actual rubric text (POV, evidence, organization, coherence, language, mechanics) + score-level descriptions, pairs with PERSUADE.
- TuringEnterprises/Rubric-Graded-Reasoning (HF, MIT, 150 tasks CS/DS/Chem) — weighted atomic rubrics + expert grading. Small but real.
- Generic LLM-judge rubric sets (vicgalle/creative-rubrics, rubricreward/*) = methodology only, not classroom.

## 4. Handwriting / handwritten exams
- ⭐⭐ Mendeley sf3kvjwknt/1 "A Dataset of Digitized Student Examination Papers, Answer Keys, and Manual Evaluations" — 50 university students, Data Science exam (35 Q / 50 marks). Contains RAW + TEACHER-ANNOTATED PDFs, Teacher_manual_marks_Anonymized.csv (item-level human scores Q1-Q35 × 50 students), answerkey.txt (rubric/answer key), Question.txt. CC BY 4.0, no DUA, Download All. → HANDWRITING + HUMAN SCORES + RUBRIC + ANNOTATED FEEDBACK IN ONE. Best single fixture for the whole app.
- JunaidMB/handwriting-ocr-images-dataset (HF, MIT, GCSE exam answers, 62/16) — gated: manual (click-through).
- IAM Handwriting DB — FKI registration, CC BY-NC-SA (non-commercial). HF mirror Voxel51/iam_handwriting_finevision (no gate, terms still apply).
- CROHME (handwritten math): HF mirrors free/no gating — Neeze/CROHME-full, Kitajiang/test2_CROHME201x.
- IEEE DataPort "Student Handwritten Exam Dataset": 21 students, 5,914 phone-scanned images — free IEEE account required.

## 5. Citation / hallucinated refs
- CiteCheck (GitHub xzy-xzy/CiteCheck, MIT): JSONL, human-annotated faithful/hallucinated citation labels. Downloadable.
- birdie0111/SciCiteVal (HF, CC BY-4.0): manual citation-verification annotations.
- allenai/scicite: 11k citation-INTENT labels (not hallucination).
- ⭐ Retraction Watch DB: full CSV ~40MB, api.labs.crossref.org/data/retractionwatch (free, mailto param) or GitLab crossref/retraction-watch-data; Kaggle mirrors. Ground truth for retracted-paper citations.
- GhostCite (arXiv 2602.06718): 13 LLMs × 40 domains × 375k citations, hallucination rates 14-95% — check paper's data-availability.
- HaluEval (pminervini/HaluEval, Apache-2.0) — generic hallucination fallback.

## 6. Document layout parsing
- ⭐ docling-project/DocLayNet-v1.1 (HF, 2436 DL, 10-100k pages, COCO-style, permissive CDLA) — matches our Docling choice.
- ⭐ opendatalab/OmniDocBench (HF, 22,801 DL — most-downloaded doc eval) — layout+text+table+formula GT, the modern benchmark (also the one MiniCPM/PaddleOCR papers report on).
- jordanparker6/publaynet (CDLA-Permissive-1.0).

## LICENSE FLAGS
- PERSUADE / AES2 family = CC BY-NC-SA / CC BY-NC-4.0 → NON-COMMERCIAL. Grader is Apache-2.0 OSS, so using them as dev/test fixtures is fine, but they must NOT be redistributed in the repo and can't ship as bundled app data.
- IAM = non-commercial + registration.
- Several HF essay/rubric repos have NO license field → treat as all-rights-reserved.
- Kaggle comps (ASAP, PERSUADE, ELLIPSE, AES2) need Kaggle account + rule acceptance (not a formal DUA).
