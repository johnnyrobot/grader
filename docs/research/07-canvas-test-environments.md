# Canvas test-environment report (sonnet agent, 2026-07-11)

## BLOCKER: Canvas Free-for-Teacher is DEAD
- Instructure PERMANENTLY DISCONTINUED Free-for-Teacher after the April-May 2026 Canvas security breach (ShinyHunters/ransomware, traced to FFT accounts). New signups closed; existing FFT accounts in limbo.
- Instructure says a replacement product for former FFT users is "expected to launch this fall [2026]".
- → The obvious free-sandbox path no longer exists as of today.
- Sources: instructure.com/incident_update; community.instructure.com FFT threads.

## Institutional beta/test clusters (*.beta / *.test.instructure.com)
- Auto-provisioned per PAYING institution root account; NOT independently signup-able.
- Beta refreshed from production EVERY SATURDAY (wipes anything created); test cluster refresh less frequent/on-demand.
- API tokens work; developer keys can be scoped "test cluster only" to survive refreshes. Writes isolated from prod.
- Requires existing institutional Canvas admin/instructor rights. Not usable by a solo dev w/o affiliation. Also: ephemeral → bad for durable fixtures.

## Self-hosted canvas-lms (OSS) on Apple Silicon
- instructure/canvas-lms + ./script/docker_dev_setup.sh. Docker Desktop needs >=8GB allocated; realistically 16GB+ and tens of GB disk (Rails + Postgres + Redis + Node asset pipeline + optional Cassandra/RCS).
- ARM64/Apple Silicon: NO official confirmation either way. Real risk of --platform linux/amd64 Rosetta emulation for some containers. Docs imply the public docker path is second-tier maintained (Instructure staff use an internal `inst` CLI).
- instructure/canvas-self-hosted: ARCHIVED, alpha, explicitly "should not be relied upon" — not a better option.
- → Multi-hour-to-multi-day yak shave. Fallback only.

## Instructure Partner Program
- Hosted Canvas env + support but HAS A FEE. Not free/instant.

## Mocks
- Canvas publishes OpenAPI 3.0 spec → Postman/Prism mock useful for schema-contract checks only; cannot validate real write side effects (gradebook history, rubric-assessment persistence, SpeedGrader state).
- No general-purpose third-party "fake Canvas API" in active use.

## KEY LOCAL FINDING (from canvas-pp-cli repo)
- canvas-pp-cli is ALREADY live-configured against the user's OWN instance: https://canvas.johnnyrobot.ai (account "Project-CLU"), token in ~/.local/share/canvas-pp-cli/credentials.toml.
- HANDOFF.md flags an OPEN GAP: that instance has ZERO STUDENTS in any course → grade/roster/submission paths implemented but UNPROVEN on real data. Its own suggested fix = exactly our P3 need.
- No seeding scripts/fixtures exist in the repo.
- This session also has a canvas-lms MCP server connected (646 tools: create_course, create_user, enroll_user_in_course, create_assignment, create_rubric, submit_assignment, grade_submission, create_submission_comment...).

## SEEDING RECIPE (once any writable course exists)
1. POST /courses (create throwaway course).
2. Fake student — EASIEST: Canvas built-in "Test Student" via Student View: GET /courses/:id/student_view_student (canvas-pp-cli wraps it as `courses student-view-student`). Zero admin scope needed, designed exactly for exercising grading.
   For MULTIPLE fake students (needed for roster/at-risk joins): needs account-admin → POST /accounts/:id/users + POST /courses/:id/enrollments (enrollment_type=StudentEnrollment).
3. POST /courses/:id/assignments (submission_types: online_upload / online_text_entry).
4. POST /courses/:id/rubrics + rubric_association to the assignment.
5. Submit as student: POST /courses/:id/assignments/:id/submissions; for file attachments run Canvas 3-step upload first, then submission_type=online_upload + file ids.
6. GRADE WRITE-BACK (the thing under test): PUT /courses/:id/assignments/:id/submissions/:user_id with submission[posted_grade], rubric_assessment[...], comment[text_comment], comment[file_ids][].
- Rate limits: leaky-bucket (X-Request-Cost / X-Rate-Limit-Remaining, 403 on exhaustion) — trivial for single-threaded seeding.

## RECOMMENDATION
PRIMARY: Use the user's OWN instance (canvas.johnnyrobot.ai) — create a clearly-named THROWAWAY course, use built-in Test Student for submissions, exercise grade/rubric/comment write-back there. Zero new signup, isolated from real courses, and it closes canvas-pp-cli's own documented validation gap.
  ⚠️ Requires user's explicit OK — it's a write to a live system.
FALLBACK: self-host canvas-lms via Docker (budget a full day, expect amd64 emulation).
FUTURE: re-evaluate Instructure's fall-2026 FFT replacement.
