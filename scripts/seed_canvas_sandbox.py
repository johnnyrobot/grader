#!/usr/bin/env python3
"""Seed a throwaway Canvas course for Grader's grade-write-back integration tests.

Builds the full round-trip surface P3 needs, then exercises it:

    course -> assignment (+file upload type) -> rubric -> Test Student submission
           -> grade + rubric assessment + comment + comment-file attachment -> read back

Why the built-in Test Student: Canvas provisions a hidden "Student View" student per
course (GET /courses/:id/student_view_student). It needs no account-admin scope and
exists precisely to exercise the student side of grading. Creating real fake users
would require admin rights on the account.

SAFETY
  * Dry run by default. Pass --apply to write.
  * Refuses to operate on any course whose name lacks the sandbox marker, so it cannot
    be pointed at a real course by accident.
  * Only ever creates its own course; never mutates an existing one unless you pass
    --course-id explicitly (and even then the name must carry the marker).
  * --teardown deletes the course it made.

Auth: CANVAS_BASE_URL + CANVAS_API_TOKEN, or reuse canvas-pp-cli's credentials.toml.

    python scripts/seed_canvas_sandbox.py                 # dry run — prints the plan
    python scripts/seed_canvas_sandbox.py --apply         # create + exercise
    python scripts/seed_canvas_sandbox.py --teardown --course-id 123 --apply
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Every sandbox course name must contain this. The guard is the whole point.
SANDBOX_MARKER = "ZZ-GRADER-TEST"
DEFAULT_COURSE_NAME = f"{SANDBOX_MARKER}-SANDBOX (safe to delete)"

# canvas-pp-cli splits these deliberately: secrets (0600) in the data dir, non-secret
# config in the config dir. Read both.
CREDS_PATHS = [
    Path.home() / ".local/share/canvas-pp-cli/credentials.toml",
    Path.home() / ".config/canvas-pp-cli/credentials.toml",
]
CONFIG_PATHS = [
    Path.home() / ".config/canvas-pp-cli/config.toml",
    Path.home() / ".local/share/canvas-pp-cli/config.toml",
]


class CanvasError(RuntimeError):
    pass


class Canvas:
    def __init__(self, base_url: str, token: str, *, apply: bool) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.apply = apply

    def _req(self, method: str, path: str, *, data: Any = None, raw: bytes | None = None,
             content_type: str | None = None, absolute: bool = False) -> Any:
        url = path if absolute else f"{self.base}/api/v1{path}"

        if not self.apply:
            # Stub every write, and any read whose URL depends on an id we never created.
            if method != "GET" or "<dry-run" in url:
                print(f"    [dry-run] {method} {url}")
                if data:
                    print(f"              {json.dumps(data)[:200]}")
                return {"id": f"<dry-run-{path.strip('/').split('/')[-1] or 'id'}>", "_dry_run": True}

        body: bytes | None = raw
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if raw is not None:
            if content_type:
                headers["Content-Type"] = content_type
        elif data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                if not payload:
                    return {}
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return {"_raw": payload.decode("utf-8", "replace")}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise CanvasError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc

    def get(self, path: str, **kw: Any) -> Any:
        return self._req("GET", path, **kw)

    def post(self, path: str, data: dict) -> Any:
        return self._req("POST", path, data=data)

    def put(self, path: str, data: dict) -> Any:
        return self._req("PUT", path, data=data)

    def delete(self, path: str, data: dict | None = None) -> Any:
        return self._req("DELETE", path, data=data or {})


def _scan(paths: list[Path], keys: tuple[str, ...]) -> tuple[str | None, Path | None]:
    """First matching key across a set of TOML files, checking top level and any table."""
    for p in paths:
        if not p.exists():
            continue
        try:
            data = tomllib.loads(p.read_text())
        except tomllib.TOMLDecodeError:
            continue
        for scope in [data, *(v for v in data.values() if isinstance(v, dict))]:
            for k in keys:
                if scope.get(k):
                    return str(scope[k]), p
    return None, None


def load_credentials() -> tuple[str, str]:
    base = os.environ.get("CANVAS_BASE_URL")
    token = os.environ.get("CANVAS_API_TOKEN") or os.environ.get("CANVAS_ACCESS_TOKEN")

    if not token:
        token, src = _scan(CREDS_PATHS, ("access_token", "token", "api_token"))
        if token:
            print(f"  token     <- {src}")
    if not base:
        base, src = _scan(CONFIG_PATHS + CREDS_PATHS, ("base_url", "url", "host"))
        if base:
            print(f"  base_url  <- {src}")

    if base and token:
        return base, token

    missing = ", ".join(x for x, v in (("base_url", base), ("token", token)) if not v)
    raise SystemExit(
        f"No Canvas credentials found (missing: {missing}).\n"
        "Set CANVAS_BASE_URL / CANVAS_API_TOKEN, or ensure canvas-pp-cli is configured:\n"
        "  token:    " + "\n            ".join(str(p) for p in CREDS_PATHS) + "\n"
        "  base_url: " + "\n            ".join(str(p) for p in CONFIG_PATHS)
    )


def guard_sandbox(name: str) -> None:
    if SANDBOX_MARKER not in name:
        raise SystemExit(
            f"REFUSING TO PROCEED: course name {name!r} does not contain {SANDBOX_MARKER!r}.\n"
            "This script only touches courses explicitly marked as throwaway sandboxes."
        )


def upload_file(cv: Canvas, path_prefix: str, filename: str, content: bytes,
                extra: dict | None = None, as_user_id: Any = None) -> Any:
    """Canvas's 3-step upload: request a target, POST the bytes, confirm.

    as_user_id masquerades. Required when writing into a *student's* file storage:
    a teacher POSTing to /users/:sid/files as themselves gets 403 even for the built-in
    Test Student. Needs the account-level `become_user` permission.
    """
    path = path_prefix if as_user_id is None else f"{path_prefix}?as_user_id={as_user_id}"
    step1 = cv.post(path, {
        "name": filename,
        "size": len(content),
        "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        **(extra or {}),
    })
    if not cv.apply or step1.get("_dry_run"):
        print(f"    [dry-run] would upload {filename} ({len(content)} bytes) via 3-step flow")
        return {"id": "<dry-run-file>", "_dry_run": True}

    upload_url = step1["upload_url"]
    params = step1.get("upload_params", {})

    boundary = "----GraderSeedBoundary7MA4YWxkTrZu0gW"
    parts: list[bytes] = []
    for k, v in params.items():  # Canvas requires these BEFORE the file part
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    step2 = cv._req(  # noqa: SLF001 - deliberate: upload_url is off the /api/v1 base
        "POST", upload_url, raw=body,
        content_type=f"multipart/form-data; boundary={boundary}", absolute=True,
    )
    # Canvas may 201 with the file JSON directly, or hand back a Location to confirm.
    if isinstance(step2, dict) and step2.get("id"):
        return step2
    if isinstance(step2, dict) and step2.get("location"):
        return cv.get(step2["location"], absolute=True)
    return step2


def seed(cv: Canvas, account_id: str, course_name: str, students: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}

    print("\n[1/7] Sandbox course")
    guard_sandbox(course_name)
    course = None
    if cv.apply:  # reuse rather than pile up duplicate sandboxes on re-runs
        existing = cv.get(
            f"/accounts/{account_id}/courses?per_page=100"
            f"&search_term={urllib.parse.quote(SANDBOX_MARKER)}"
        )
        for c in existing if isinstance(existing, list) else []:
            if SANDBOX_MARKER in c.get("name", "") and c.get("workflow_state") != "deleted":
                course = c
                print(f"    reusing existing course id={c['id']} name={c['name']!r}")
                break
    if course is None:
        course = cv.post(f"/accounts/{account_id}/courses", {
            "course[name]": course_name,
            "course[course_code]": SANDBOX_MARKER,
            "course[is_public]": "false",
            "offer": "true",  # publish, else submissions can't be made
        })
        print(f"    created course id={course['id']} name={course_name!r}")
    cid = course["id"]
    out["course"] = course

    print("\n[2/7] Assignment (online_upload + online_text_entry)")
    assignment = None
    if cv.apply:
        for a in cv.get(f"/courses/{cid}/assignments") or []:
            if "GRADER TEST" in a.get("name", ""):
                assignment = a
                print(f"    reusing assignment id={a['id']}")
                break
    if assignment is None:
        assignment = cv.post(f"/courses/{cid}/assignments", {
            "assignment[name]": "Essay 1 — Feedback and Learning (GRADER TEST)",
            "assignment[description]": "Sandbox assignment used to exercise Grader's write-back path.",
            "assignment[submission_types][]": ["online_upload", "online_text_entry"],
            "assignment[points_possible]": 20,
            "assignment[grading_type]": "points",
            "assignment[published]": "true",
        })
        print(f"    created assignment id={assignment['id']}")
    aid = assignment["id"]
    out["assignment"] = assignment

    print("\n[3/7] Rubric (4 criteria x 0-5, analytic) + association")
    criteria = {
        "0": ("Thesis & Argument", "Clarity and defensibility of the central claim"),
        "1": ("Use of Evidence", "Relevance, integration, and accuracy of supporting evidence"),
        "2": ("Organization", "Logical structure and coherence across paragraphs"),
        "3": ("Mechanics & Citation", "Grammar, style, and correctly formatted citations"),
    }
    ratings = [(5, "Exemplary"), (4, "Proficient"), (3, "Developing"), (2, "Beginning"), (0, "Missing")]
    data: dict[str, Any] = {
        "rubric[title]": "Essay Rubric (GRADER TEST)",
        "rubric[free_form_criterion_comments]": "true",
        "rubric_association[association_id]": aid,
        "rubric_association[association_type]": "Assignment",
        "rubric_association[use_for_grading]": "true",
        "rubric_association[purpose]": "grading",
    }
    for idx, (title, desc) in criteria.items():
        data[f"rubric[criteria][{idx}][description]"] = title
        data[f"rubric[criteria][{idx}][long_description]"] = desc
        data[f"rubric[criteria][{idx}][points]"] = 5
        for r_idx, (pts, label) in enumerate(ratings):
            data[f"rubric[criteria][{idx}][ratings][{r_idx}][description]"] = label
            data[f"rubric[criteria][{idx}][ratings][{r_idx}][points]"] = pts

    rubric = None
    if cv.apply:
        for r in cv.get(f"/courses/{cid}/rubrics") or []:
            if "GRADER TEST" in r.get("title", ""):
                # The list endpoint omits criteria; fetch the full record for its criterion ids.
                rubric = cv.get(f"/courses/{cid}/rubrics/{r['id']}")
                print(f"    reusing rubric id={r['id']}")
                break
    if rubric is None:
        rubric = cv.post(f"/courses/{cid}/rubrics", data)
        print(f"    created rubric id={(rubric.get('rubric') or rubric).get('id')}")
    out["rubric"] = rubric
    print("    (4 criteria x 5 pts = 20)")

    print("\n[4/7] Fetching built-in Test Student (Student View)")
    test_student = cv.get(f"/courses/{cid}/student_view_student")
    sid = test_student["id"]
    out["test_student"] = test_student
    print(f"    test student id={sid} name={test_student.get('name', '<dry-run>')!r}")

    print("\n[5/7] Submitting as Test Student (text entry + file attachment)")
    essay = Path("fixtures/citations/sample_paper.md")
    content = essay.read_bytes() if essay.exists() else b"# Sample submission\n\nPlaceholder essay body.\n"
    # Submission attachments must live in the *student's* files, so masquerade to upload.
    uploaded = upload_file(
        cv, f"/users/{sid}/files", "essay1_submission.md", content,
        extra={"parent_folder_path": "submissions"}, as_user_id=sid,
    )
    fid = uploaded.get("id")
    submission = cv.post(f"/courses/{cid}/assignments/{aid}/submissions?as_user_id={sid}", {
        "submission[submission_type]": "online_upload",
        "submission[file_ids][]": [fid],
    })
    out["submission"] = submission
    print(f"    submitted file id={fid} for user id={sid}")

    print("\n[6/7] Writing back: grade + rubric assessment + comment + comment file")
    feedback = (
        "Strong central claim, and your evidence is well chosen. Two things to work on: "
        "the third paragraph asserts a causal link the source doesn't support, and reference "
        "[4] could not be located — please double-check it. Revise and resubmit for reassessment."
    ).encode()
    comment_file = upload_file(
        cv, f"/courses/{cid}/assignments/{aid}/submissions/{sid}/comments/files",
        "annotated_feedback.md", b"# Annotated feedback\n\n" + feedback,
    )
    cfid = comment_file.get("id")

    grade_payload = {
        "submission[posted_grade]": 16,
        "comment[text_comment]": feedback.decode(),
        "comment[file_ids][]": [cfid],
    }
    # Rubric assessment is keyed by the live criterion ids Canvas assigned.
    rubric_criteria = (rubric.get("rubric") or rubric).get("data") or []
    scores = [4, 4, 5, 3]
    notes = [
        "Clear, defensible thesis.",
        "Evidence is relevant; one source is over-claimed.",
        "Excellent logical flow.",
        "Citation [4] could not be verified.",
    ]
    for i, crit in enumerate(rubric_criteria[:4]):
        key = crit.get("id")
        grade_payload[f"rubric_assessment[{key}][points]"] = scores[i]
        grade_payload[f"rubric_assessment[{key}][comments]"] = notes[i]
    if not rubric_criteria and not cv.apply:
        print("    [dry-run] rubric_assessment keys resolve from live criterion ids at apply time")

    graded = cv.put(f"/courses/{cid}/assignments/{aid}/submissions/{sid}", grade_payload)
    out["graded"] = graded
    print(f"    posted grade=16/20 + rubric assessment + comment (file id={cfid})")

    print("\n[7/7] Reading it back (the assertion that matters)")
    verify = cv.get(
        f"/courses/{cid}/assignments/{aid}/submissions/{sid}"
        "?include[]=rubric_assessment&include[]=submission_comments"
    )
    out["verify"] = verify
    if cv.apply:
        score = verify.get("score")
        n_comments = len(verify.get("submission_comments") or [])
        has_rubric = bool(verify.get("rubric_assessment"))
        print(f"    score={score} comments={n_comments} rubric_assessment={'yes' if has_rubric else 'NO'}")
        ok = score == 16 and n_comments >= 1 and has_rubric
        print(f"\n  {'PASS' if ok else 'FAIL'}: single-submission round-trip "
              f"{'verified' if ok else 'INCOMPLETE — inspect above'}")
        out["round_trip_ok"] = ok
    else:
        print("    [dry-run] would assert score==16, >=1 comment, rubric_assessment present")

    # ---- Cohort: the multi-student paths the Test Student cannot reach -------------------
    if students > 0:
        print("\n" + "=" * 70)
        print(f"COHORT: {students} enrolled students (roster / at-risk / bulk-update paths)")
        print("=" * 70)
        cohort = ensure_students(cv, account_id, cid, students)
        submit_for_cohort(cv, cid, aid, cohort)
        bulk_ok = bulk_grade(cv, cid, aid, cohort, rubric)
        cohort_ok = verify_cohort(cv, cid, aid, cohort) and bulk_ok
        out["cohort"] = [{"id": s.get("id"), "name": s.get("name")} for s in cohort]
        out["cohort_ok"] = cohort_ok

    return out


# --------------------------------------------------------------------------------------
# Cohort: real enrollments, for the paths the single Test Student cannot reach
# (roster, at-risk, standings, and submissions bulk-update).
# --------------------------------------------------------------------------------------

STUDENT_PREFIX = "ZZGraderTest"
# .invalid is reserved by RFC 2606 and can never resolve — no mail can ever reach these.
STUDENT_DOMAIN = "zz-grader-test.invalid"

# Submissions are deliberately spread across quality tiers so grading has real signal to
# find, and so the confidence lanes have something to actually separate.
QUALITY_TIERS = [
    ("strong", 5,
     "The evidence for feedback timing is strongest in Hattie's synthesis, though the effect "
     "sizes vary by task complexity. I want to argue that immediacy matters less than "
     "actionability: a delayed comment a student can act on outperforms an instant score they "
     "cannot. Three studies complicate this view, and I take them seriously below."),
    ("middling", 3,
     "Feedback is important for learning. Many studies show that students who get feedback do "
     "better than students who do not. Hattie says feedback has a large effect size. In this "
     "essay I will explain why feedback matters and give some examples from class."),
    ("weak", 1,
     "Feedback helps students learn. Teachers should give feedback. It is good because it "
     "helps. Studies prove feedback always works for every student in every situation."),
    ("empty_ish", 0,
     "i didnt really understand the prompt but i think feedback is when the teacher tells you "
     "what you did wrong"),
]


def ensure_students(cv: Canvas, account_id: str, cid: Any, n: int) -> list[dict]:
    """Create (or reuse) N fake students and enroll them. Requires account-admin scope."""
    print(f"\n[C1] Provisioning {n} fake students")

    existing: dict[str, dict] = {}
    if cv.apply:
        found = cv.get(f"/accounts/{account_id}/users?per_page=100&search_term={STUDENT_PREFIX}")
        for u in found if isinstance(found, list) else []:
            existing[u.get("name", "")] = u

    students: list[dict] = []
    for i in range(1, n + 1):
        name = f"{STUDENT_PREFIX} Student {i:02d}"
        if name in existing:
            students.append(existing[name])
            print(f"    reusing {name} (id={existing[name]['id']})")
            continue
        user = cv.post(f"/accounts/{account_id}/users", {
            "user[name]": name,
            "user[skip_registration]": "true",
            "user[terms_of_use]": "true",
            "pseudonym[unique_id]": f"{STUDENT_PREFIX.lower()}-{i:02d}@{STUDENT_DOMAIN}",
            "pseudonym[send_confirmation]": "false",
            "communication_channel[skip_confirmation]": "true",
        })
        students.append(user)
        print(f"    created {name} (id={user.get('id')})")

    print(f"\n[C2] Enrolling {len(students)} students in course {cid}")
    enrolled_ids: set[Any] = set()
    if cv.apply:
        rolls = cv.get(f"/courses/{cid}/enrollments?per_page=100&type[]=StudentEnrollment")
        enrolled_ids = {e["user"]["id"] for e in rolls if isinstance(e, dict) and e.get("user")}

    for s in students:
        sid = s.get("id")
        if sid in enrolled_ids:
            print(f"    already enrolled: id={sid}")
            continue
        cv.post(f"/courses/{cid}/enrollments", {
            "enrollment[user_id]": sid,
            "enrollment[type]": "StudentEnrollment",
            "enrollment[enrollment_state]": "active",  # skip the invitation dance
            "enrollment[notify]": "false",
        })
        print(f"    enrolled id={sid}")
    return students


def submit_for_cohort(cv: Canvas, cid: Any, aid: Any, students: list[dict]) -> None:
    """Each student submits work of varying quality (masqueraded text entries)."""
    print(f"\n[C3] Submitting work for {len(students)} students (varied quality tiers)")
    for i, s in enumerate(students):
        sid = s.get("id")
        tier, _, body = QUALITY_TIERS[i % len(QUALITY_TIERS)]
        cv.post(f"/courses/{cid}/assignments/{aid}/submissions?as_user_id={sid}", {
            "submission[submission_type]": "online_text_entry",
            "submission[body]": f"<p>{body}</p>",
        })
        print(f"    id={sid} submitted ({tier})")


def bulk_grade(cv: Canvas, cid: Any, aid: Any, students: list[dict], rubric: dict) -> bool:
    """Exercise submissions bulk-update — the path Grader uses to post a whole batch.

    Canvas answers this ASYNCHRONOUSLY with a Progress object; the grades are NOT live when
    the call returns. Any importer that skips the poll will read stale scores.
    """
    print(f"\n[C4] Bulk-grading {len(students)} submissions (grade_data + rubric assessments)")

    criteria = (rubric.get("rubric") or rubric).get("data") or []
    payload: dict[str, Any] = {}
    for i, s in enumerate(students):
        sid = s.get("id")
        tier, per_criterion, _ = QUALITY_TIERS[i % len(QUALITY_TIERS)]
        total = per_criterion * len(criteria) if criteria else per_criterion * 4
        payload[f"grade_data[{sid}][posted_grade]"] = total
        payload[f"grade_data[{sid}][text_comment]"] = f"Bulk-graded in the {tier} tier by the Grader harness."
        for crit in criteria[:4]:
            key = crit.get("id")
            payload[f"grade_data[{sid}][rubric_assessment][{key}][points]"] = per_criterion

    progress = cv.post(f"/courses/{cid}/assignments/{aid}/submissions/update_grades", payload)
    if not cv.apply:
        print("    [dry-run] would POST update_grades, then poll the returned Progress to completion")
        return True

    pid = progress.get("id")
    print(f"    progress id={pid} state={progress.get('workflow_state')} — polling...")
    state, tries = progress.get("workflow_state"), 0
    while state in {"queued", "running"} and tries < 30:
        time.sleep(1)
        p = cv.get(f"/progress/{pid}")
        state, tries = p.get("workflow_state"), tries + 1
    print(f"    progress -> {state} after {tries}s")
    return state == "completed"


def verify_cohort(cv: Canvas, cid: Any, aid: Any, students: list[dict]) -> bool:
    """Read every submission back and confirm the bulk grades actually landed."""
    print(f"\n[C5] Verifying {len(students)} graded submissions")
    if not cv.apply:
        print("    [dry-run] would assert every student has a score + rubric assessment")
        return True

    subs = cv.get(
        f"/courses/{cid}/assignments/{aid}/submissions"
        "?per_page=100&include[]=rubric_assessment&include[]=submission_comments"
    )
    by_user = {s["user_id"]: s for s in subs if isinstance(s, dict)}
    ok = True
    for s in students:
        sid = s.get("id")
        sub = by_user.get(sid)
        if not sub:
            print(f"    id={sid} MISSING submission")
            ok = False
            continue
        score = sub.get("score")
        has_rubric = bool(sub.get("rubric_assessment"))
        n_comments = len(sub.get("submission_comments") or [])
        flag = "ok " if score is not None and has_rubric else "FAIL"
        print(f"    [{flag}] id={sid} score={score} rubric={'y' if has_rubric else 'n'} comments={n_comments}")
        if score is None or not has_rubric:
            ok = False

    graded = sum(1 for s in students if (by_user.get(s.get("id")) or {}).get("score") is not None)
    print(f"\n  {'PASS' if ok else 'FAIL'}: {graded}/{len(students)} students graded via bulk-update")
    return ok


def teardown(cv: Canvas, course_id: str, account_id: str = "self", delete_users: bool = False) -> None:
    course = cv.get(f"/courses/{course_id}")
    name = course.get("name", "")
    print(f"  course {course_id} name={name!r}")
    guard_sandbox(name)
    cv.delete(f"/courses/{course_id}", {"event": "delete"})
    print(f"  deleted course {course_id}")

    if not delete_users:
        print(f"  (fake {STUDENT_PREFIX} users left in place; pass --delete-users to remove them)")
        return

    found = cv.get(f"/accounts/{account_id}/users?per_page=100&search_term={STUDENT_PREFIX}")
    for u in found if isinstance(found, list) else []:
        if not u.get("name", "").startswith(STUDENT_PREFIX):  # never touch a real user
            continue
        cv.delete(f"/accounts/{account_id}/users/{u['id']}")
        print(f"  deleted user {u['id']} ({u['name']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--account-id", default="self", help="Canvas account id to create the course under")
    ap.add_argument("--course-name", default=DEFAULT_COURSE_NAME)
    ap.add_argument("--students", type=int, default=0, metavar="N",
                    help="also provision N fake enrolled students, submit varied work for each, "
                         "and exercise the bulk-update grading path (needs account-admin scope)")
    ap.add_argument("--teardown", action="store_true", help="delete the sandbox course")
    ap.add_argument("--delete-users", action="store_true",
                    help="with --teardown, also delete the fake ZZGraderTest users")
    ap.add_argument("--course-id", help="course id (required with --teardown)")
    ap.add_argument("--out", type=Path, default=Path("fixtures/canvas_sandbox.json"))
    args = ap.parse_args()

    base, token = load_credentials()
    print(f"Canvas: {base}")
    print(f"Mode:   {'APPLY (writes!)' if args.apply else 'DRY RUN (no writes)'}")

    cv = Canvas(base, token, apply=args.apply)

    if args.teardown:
        if not args.course_id:
            raise SystemExit("--teardown requires --course-id")
        teardown(cv, args.course_id, args.account_id, args.delete_users)
        return

    result = seed(cv, args.account_id, args.course_name, students=args.students)

    if args.apply:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        ids = {
            "base_url": base,
            "course_id": result["course"]["id"],
            "assignment_id": result["assignment"]["id"],
            "test_student_id": result["test_student"]["id"],
            "round_trip_ok": result.get("round_trip_ok"),
        }
        if "cohort" in result:
            ids["cohort"] = result["cohort"]
            ids["cohort_ok"] = result.get("cohort_ok")
        args.out.write_text(json.dumps(ids, indent=2))
        print(f"\n  wrote ids -> {args.out}")
        print(f"  teardown:  python {sys.argv[0]} --teardown --course-id {ids['course_id']} --apply")


if __name__ == "__main__":
    main()
