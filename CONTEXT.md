# Grader

A local-first grading copilot for college instructors. Student work is graded on the teacher's own machine, and every AI-produced grade is a draft until the teacher explicitly approves it.

## Language

### The work being graded

**Assignment**:
A piece of coursework set to a class, graded against exactly one Rubric. Identified by its Canvas course and assignment pair.
_Avoid_: task, activity

**Submission**:
One student's response to an Assignment, as text extracted from whatever they turned in.
_Avoid_: paper, upload, attachment (an attachment is a file inside a Submission, not the Submission)

**Rubric**:
The scoring scheme an Assignment is graded against — a titled, versioned set of Criteria. A Rubric belongs to exactly one Assignment; grading a Submission against a different Assignment's Rubric is always an error.
_Avoid_: scheme, marking guide

**Criterion**:
One scored dimension of a Rubric, with a maximum score and a set of named levels.
_Avoid_: category, dimension, row

### Grading

**GradeRun**:
One attempt to grade one Submission against a Rubric. Succeeds with a GradeDraft or fails with a reason; a failed run is a recorded outcome, not an exception.
_Avoid_: grading job, attempt (a job covers many Submissions; a GradeRun covers one)

**GradeDraft**:
The AI's proposed scores, feedback, confidence and flags for one Submission. Never authoritative on its own.
_Avoid_: grade, result, prediction (a Grade is what a teacher approved; a Draft is what was proposed)

**Anchor**:
An Exemplar supplied to the model as a worked reference while grading, so scores stay aligned with how this teacher has graded before.
_Avoid_: example, few-shot sample

**Triage lane**:
Where a GradeDraft is routed for the teacher's attention — review-first, standard, or high-confidence. A lane is a recommendation about reading order; it never grants approval.
_Avoid_: bucket, priority, queue (the Queue holds Drafts across all lanes)

### Teacher authority

**Approval**:
A teacher's explicit acceptance of a grade for one Submission. Nothing reaches Canvas without one. Approval is a single event with a single moment of record, whether it followed an Override or not.
_Avoid_: sign-off, confirmation, acceptance

**Override**:
A teacher's correction of a GradeDraft's scores or feedback, recorded with the difference from what the AI proposed. An Override always carries an Approval; an Approval need not involve an Override.
_Avoid_: edit, correction, adjustment

**Exemplar**:
A teacher-graded Submission kept as ground truth, used to anchor later grading. Every Override produces one.
_Avoid_: sample, training example, gold item

**Postable**:
The property of an approved grade that is eligible to be sent to Canvas — approved, attributable to a Canvas student, and not already posted.
_Avoid_: ready, publishable

### Calibration

**Cold start**:
The state of an Assignment with too few Exemplars for the AI to draft at all. The teacher grades the first few themselves; drafting unlocks after that.
_Avoid_: warmup, bootstrap

**Calibration health**:
How closely current AI drafts agree with this teacher's own grading on this Assignment, measured over every Approval — including those where the teacher changed nothing.
_Avoid_: accuracy, quality score, model performance

**Draft bias**:
The signed direction and size of the gap between AI drafts and this teacher's approved grades on an Assignment. Negative means drafts run below the teacher.
_Avoid_: harshness, drift, error

### Canvas

**Post queue**:
Approved grades waiting to reach Canvas because it was unreachable when they were approved. Draining the queue is a retry, never a re-approval.
_Avoid_: outbox, retry buffer
