# FERPA & Student Data Privacy

**Grader is designed so that no student data ever leaves your device.** This document
explains how that works and what it means for your institution's FERPA compliance.

## The rule

Grader runs entirely on your Mac. Student submissions, grades, and feedback are stored
locally in SQLite. The only outbound network traffic is:

1. **Your Canvas instance** — to import submissions and post grades back (you authenticate
   with your own credentials; Grader never sees your password)
2. **Model downloads** — Ollama pulls model weights from the official CDN on first run
3. **Citation verification** — DOI resolution via doi.org, metadata via Crossref/Semantic
   Scholar/OpenAlex, and web fetches for non-academic sources

That's it. The allowlist is enforced and audited — see `engine/gates/egress.py`.

## What Grader does NOT do

- **Does not send student work to any cloud API.** All grading happens via local models
  (Ollama/MLX) running on your machine.
- **Does not send student work to any third party.** No OpenAI, no Anthropic, no Google.
- **Does not store student data in any cloud.** Everything is in local SQLite files.
- **Does not upload student data for "improvement" or telemetry.** There is no telemetry.
- **Does not post grades without your explicit approval.** Every grade requires
  `teacher_final.approved_at` before it can be posted to Canvas.

## What you control

- **Your Canvas token** — stored in your keychain, used only for your Canvas instance
- **Your rubrics** — versioned, stored locally, never shared
- **Your previously-graded work (anchors)** — stored locally as embeddings; these are
  your scored work, used to calibrate the AI. They never leave the device.
- **Your audit log** — every grading action is logged locally with timestamp and actor

## The network allowlist

Grader maintains a strict network allowlist. The egress gate (`engine/gates/egress.py`)
captures all network traffic during a grading batch and fails if any packet goes to a
non-allowlisted host. The allowlist:

| Host | Purpose |
|---|---|
| `localhost:11434` | Ollama (local model inference) |
| `canvas.*` | Your Canvas instance |
| `doi.org` | DOI resolution (citation verification) |
| `api.crossref.org` | Crossref metadata (citation verification) |
| `api.semanticscholar.org` | Semantic Scholar (citation enrichment) |
| `api.openalex.org` | OpenAlex (citation enrichment) |
| `html.duckduckgo.com` | DuckDuckGo (citation search — non-academic only) |
| CloudFront/Fastly CDN | CDN hosts for the above services |

If you add custom citation sources or Canvas instances, you must add them to the allowlist
in `engine/gates/egress.py`. Any unlisted host will fail the build.

## Institutional compliance

For your institution's compliance review:

- **Data residency:** All student data resides on the instructor's machine. No cloud
  storage, no external processing, no data transfer.
- **Data retention:** Data persists only as long as the instructor keeps it. Deleting
  the local database removes all student work. The instructor controls retention.
- **Access control:** Only the instructor has access. No shared accounts, no admin
  access from the vendor (there is no vendor — this is open source).
- **Audit trail:** Every grading action (import, grade, approve, override, post) is
  logged in a local SQLite audit table with timestamp and actor.
- **Right to access/correction:** The instructor can view, edit, and delete any student
  data at any time through the local database.

## Open source

Grader is Apache 2.0 licensed. The source code is available for review. There are no
hidden network calls, no bundled analytics, no telemetry. You can verify the allowlist
yourself by reading `engine/gates/egress.py`.

## Limitations

- **Citation verification** does fetch external URLs (doi.org, Crossref, etc.) but
  sends only the citation reference string — never the full submission text.
- **Canvas integration** sends grades and comments to your Canvas instance using your
  credentials. This is the same as posting grades manually through the Canvas web UI.
- **Model weights** are downloaded from Ollama's CDN. The weights themselves do not
  contain or transmit student data.

## Questions?

Read the source: `engine/gates/egress.py` (the allowlist), `engine/api.py` (all
endpoints), `engine/grader/canvas.py` (Canvas integration). Everything is auditable.