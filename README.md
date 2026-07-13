# Grader

A **fully-local** macOS desktop app that helps college instructors grade anything — essays,
problem sets, lab reports, code, scanned handwriting — with an AI that learns to grade **the way
you grade**.

Student work never leaves your machine.

---

## Why

Grading is the largest unautomated time sink in teaching. Cloud AI graders exist, but they
(a) require sending student work to third parties — a FERPA problem most institutions prohibit,
(b) grade like a generic model rather than like *you*, and (c) position the AI as the authority.

Grader inverts all three. Local models. Your rubric, your source materials, and your own
previously-graded work as the source of truth. And **you approve every score** — the AI drafts,
you decide.

## How the calibration works

You grade a few papers. Grader embeds them. From then on, every draft grade is conditioned on
**your most similar previously-graded work**, injected as anchor examples:

> *"I scored this a 4 on Evidence because it resembles these three you scored 4 — same pattern of
> citing a source without integrating it."*

This is how human raters calibrate, and it's what the research supports. It works from your first
graded paper, improves every time you grade one more, and it's explainable — no black box, no
per-teacher model to train.

## Status

🚧 **Developer-assisted alpha; not ready for an unattended public beta.** Engine, desktop,
Canvas, document, citation, and release-gate plumbing exist, but public beta requires passing
public-proxy quality/fairness evidence, explicit citation labeling, a guarded live Canvas app
round trip, signed/notarized clean-machine testing, and updater validation.

| | |
|---|---|
| Engine (grading, calibration, triage) | 🟢 Working |
| Calibration validation | 🟡 P1 built (anchors +0.135 QWK at k=6) |
| Desktop app (Tauri + React) | 🟡 Built — sidecar, queue, split-view, model manager, settings |
| Canvas integration | 🟡 Built — import, post grades, offline queue; app-level live round trip still required |
| Document parsing + handwriting | 🟡 Built — Docling + Apple Vision + grader-vision |
| Citation verification | 🟡 Built — current fixture result is experimental until ≥90% and 3/3 retractions pass |
| CI gates | 🟡 Privacy/perf built; public-proxy quality + fairness profiles added |
| Signing + notarization | ⬜ Scripts built; signed/notarized clean-machine test still required |
| Updates | 🟡 Tauri updater integrated, manifest generator built |
| Beta rollout | ⬜ Blocked until release-readiness report returns `supervised_public_beta` |

See [`docs/HANDOFF.md`](docs/HANDOFF.md) for exact status and [`docs/phases/`](docs/phases/) for
per-phase detail.

## Design commitments

1. **No student PII leaves the device.** The only outbound traffic is your own Canvas, model
   downloads, and *cited-source* lookups (a DOI, a title, a URL). Enforced by an allowlist and a
   CI test that fails the build on any stray packet.
2. **Nothing posts without your approval.** Enforced in the data layer, not just the UI.
3. **Show your work.** Every score cites the rubric criterion, the evidence, and the anchors that
   informed it.
4. **Formative first.** Research consistently finds people accept AI *feedback* but distrust AI
   *scores* — so you always own the number.

## Stack

Tauri + React shell · Python engine (FastAPI) · **Pydantic AI v2** · **Ollama** (→ MLX) ·
**Qwen3-VL** (vision) + **Qwen3** (text) · **Docling** (parsing) · SQLite + local embeddings ·
Canvas via [canvas-pp-cli](https://github.com/mvanhorn/printing-press-library)

Every choice is justified with citations in [`RESEARCH.md`](RESEARCH.md).

## Development

```bash
uv sync                                              # deps
ollama pull qwen3:8b && ollama pull qwen3-vl:4b      # models (16 GB tier)

uv run python engine/bench.py                        # which model/output-mode works
uv run python engine/calibrate.py --n-test 20        # do teacher anchors actually help?
```

## Public beta gate commands

```bash
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness
```

The committed `fixtures/public_proxy/` set is a tiny mechanics fixture. Real public-proxy datasets
must be downloaded into gitignored local storage and reported separately from teacher-local data.

The calibration run uses [Mohler ASAG](https://huggingface.co/datasets/nkazi/MohlerASAG)
(CC BY 4.0) — 2,273 short answers, each scored by **two independent human graders**, so we can
report agreement against the *human ceiling* rather than against an arbitrary target.

## Docs

| | |
|---|---|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | **Start here** — status, gotchas, next command |
| [`docs/INSTALL.md`](docs/INSTALL.md) | Installation guide (from source + release builds) |
| [`docs/PRD.md`](docs/PRD.md) | Product requirements |
| [`docs/SPEC.md`](docs/SPEC.md) | Technical spec sheet |
| [`docs/DATA-PLAN.md`](docs/DATA-PLAN.md) | Datasets, licenses, access paths |
| [`docs/FERPA.md`](docs/FERPA.md) | FERPA privacy one-pager |
| [`docs/RUBRIC_AUTHORING.md`](docs/RUBRIC_AUTHORING.md) | Rubric authoring guide |
| [`docs/BETA_ROLLOUT.md`](docs/BETA_ROLLOUT.md) | Beta program guide + feedback form |
| [`RESEARCH.md`](RESEARCH.md) | The cited architecture report |
| [`docs/research/`](docs/research/) | Raw research (adversarially verified) |

## License

Apache 2.0.
