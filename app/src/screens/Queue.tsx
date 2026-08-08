import { useCallback, useEffect, useState } from "react";
import { QueueData, GradeItem, gradeBatch, checkUpdates, canvasImport, canvasPost, canvasTargetOf, fetchPostQueue, flushPostQueue } from "../api";
import { AppShell } from "../components/AppShell";
import { checkDesktopUpdate, installPendingUpdate } from "../updater";

interface Props {
  data: QueueData | null;
  onSelectItem: (item: GradeItem) => void;
  assignmentId: string;
  setAssignmentId: (id: string) => void;
  selectedLane: string | undefined;
  setSelectedLane: (lane: string | undefined) => void;
  onRefresh: () => void;
  engineIssue?: string;
  recoveringEngine?: boolean;
  onRecoverEngine?: () => void;
  onOpenModels: () => void;
  onOpenSettings: () => void;
}

const LANE_META: Record<string, { summary: string; filter: string; description: string; className: string }> = {
  review_first: {
    summary: "Needs teacher review",
    filter: "Needs review",
    description: "Lower confidence or a signal needs your attention.",
    className: "status-review",
  },
  standard: {
    summary: "Standard review",
    filter: "Standard review",
    description: "A normal rubric check before approval.",
    className: "status-standard",
  },
  high_confidence: {
    summary: "High confidence",
    filter: "Confident drafts",
    description: "Still requires your explicit approval.",
    className: "status-confident",
  },
};

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}

function signalLabel(flag: string) {
  if (flag.toLowerCase().includes("citation")) return "Citation check (experimental)";
  return titleCase(flag);
}

export function QueueScreen({ data, onSelectItem, assignmentId, setAssignmentId, selectedLane, setSelectedLane, onRefresh, engineIssue = "", recoveringEngine = false, onRecoverEngine, onOpenModels, onOpenSettings }: Props) {
  const [grading, setGrading] = useState(false);
  const [gradeMsg, setGradeMsg] = useState("");
  const [updateVersion, setUpdateVersion] = useState("");
  const [updateStatus, setUpdateStatus] = useState("");
  const [installingUpdate, setInstallingUpdate] = useState(false);
  const [posting, setPosting] = useState(false);
  const [postReceipt, setPostReceipt] = useState<string[]>([]);
  const [importCourse, setImportCourse] = useState("");
  const [importAssignment, setImportAssignment] = useState("");
  const [importing, setImporting] = useState(false);
  const [pendingPosts, setPendingPosts] = useState(0);
  const [flushing, setFlushing] = useState(false);
  const stats = data?.stats;
  const items = data?.items || [];

  // Post is offered only for a batch imported from Canvas, and only once the
  // teacher has approved something. Approval remains the engine's to judge —
  // this just avoids offering an action that postable() would refuse outright.
  const canvasTarget = canvasTargetOf(assignmentId);
  const approvedIds = items.filter(item => item.approved).map(item => item.id);

  const refreshPostQueue = useCallback(async () => {
    try {
      setPendingPosts((await fetchPostQueue()).pending);
    } catch {
      // The engine being unreachable is already surfaced elsewhere; a failed
      // count must not replace the screen with an error.
    }
  }, []);

  useEffect(() => {
    refreshPostQueue();
  }, [refreshPostQueue, postReceipt]);

  async function handleFlush() {
    setFlushing(true);
    try {
      const result = await flushPostQueue();
      const lines = [`Retried the queue: ${result.posted} posted, ${result.remaining} still waiting.`];
      // An entry that burned its retries has stopped being attempted. Saying so
      // is the whole point of the bound — otherwise it just sits there.
      if (result.abandoned > 0) {
        lines.push(
          `${result.abandoned} gave up after repeated failures and need your attention.`,
        );
      }
      setPostReceipt(lines);
      onRefresh();
    } catch (error) {
      setPostReceipt([`Could not flush the queue: ${(error as Error).message}`]);
    } finally {
      setFlushing(false);
      refreshPostQueue();
    }
  }

  async function handleImport() {
    const course = Number(importCourse.trim());
    const assignment = Number(importAssignment.trim());
    if (!Number.isInteger(course) || !Number.isInteger(assignment) || !course || !assignment) {
      setPostReceipt(["Enter the numeric Canvas course and assignment ids."]);
      return;
    }
    setImporting(true);
    setPostReceipt([]);
    try {
      const result = await canvasImport(course, assignment);
      const lines = [
        `Imported ${result.imported} submission${result.imported === 1 ? "" : "s"}.`,
      ];
      // A file that produced no readable text is reported per student rather
      // than quietly graded as empty.
      for (const failure of result.failures || []) {
        lines.push(`Student ${failure.user_id}: ${failure.error}`);
      }
      setPostReceipt(lines);
      setAssignmentId(`canvas_${course}_${assignment}`);
      onRefresh();
    } catch (error) {
      setPostReceipt([`Could not import: ${(error as Error).message}`]);
    } finally {
      setImporting(false);
    }
  }

  async function handlePost() {
    if (!canvasTarget || approvedIds.length === 0) return;
    setPosting(true);
    setPostReceipt([]);
    try {
      const result = await canvasPost(
        canvasTarget.courseId, canvasTarget.assignmentId, approvedIds,
      );
      const lines: string[] = [];
      if (result.posted > 0) {
        lines.push(`Posted ${result.posted} grade${result.posted === 1 ? "" : "s"} to Canvas.`);
        for (const [student, score] of Object.entries(result.posted_scores || {})) {
          lines.push(`Student ${student}: ${score}`);
        }
      }
      // Queued is not a failure. Canvas was unreachable; the approvals are kept
      // and will go out on the next flush.
      if (result.queued > 0) {
        lines.push(
          `Canvas was unreachable, so ${result.queued} approved grade` +
          `${result.queued === 1 ? " is" : "s are"} queued and will post when it is back.`,
        );
      }
      for (const skipped of result.skipped || []) lines.push(`Skipped: ${skipped}`);
      if (lines.length === 0) lines.push("Nothing to post.");
      setPostReceipt(lines);
      onRefresh();
    } catch (error) {
      setPostReceipt([`Could not post: ${(error as Error).message}`]);
    } finally {
      setPosting(false);
    }
  }

  useEffect(() => {
    checkDesktopUpdate().then(update => {
      if (update) {
        setUpdateVersion(update.version);
        return undefined;
      }
      return checkUpdates().then(update => {
        if (update.update_available) setUpdateVersion(update.latest_version || "new version");
      });
    }).catch(() => {});
  }, []);

  const handleInstallUpdate = async () => {
    setInstallingUpdate(true);
    setUpdateStatus("Preparing the signed update…");
    try {
      await installPendingUpdate(progress => {
        if (progress.phase === "started") {
          setUpdateStatus("Downloading the signed update…");
        } else if (progress.phase === "progress") {
          const mb = progress.downloaded / 1024 / 1024;
          const total = progress.contentLength ? ` of ${(progress.contentLength / 1024 / 1024).toFixed(1)} MB` : "";
          setUpdateStatus(`Downloaded ${mb.toFixed(1)} MB${total}.`);
        } else {
          setUpdateStatus("Installing update and restarting Grader…");
        }
      });
    } catch (error) {
      setUpdateStatus(`Update could not be installed. ${(error as Error).message}`);
      setInstallingUpdate(false);
    }
  };

  const handleGrade = async () => {
    setGrading(true);
    setGradeMsg("Starting a local grading pass…");
    try {
      const result = await gradeBatch(assignmentId);
      setGradeMsg(`${result.total} submissions queued. You can keep working while drafts are prepared.`);
      window.setTimeout(onRefresh, 1200);
    } catch (error) {
      setGradeMsg(`Grading could not start. ${(error as Error).message}`);
    } finally {
      setGrading(false);
    }
  };

  const statValue = (key: string) => key === "review_first" ? stats?.review_first : key === "standard" ? stats?.standard : stats?.high_confidence;

  const toolbar = (
    <div className="queue-toolbar">
      <label className="assignment-field">
        <span>Assignment</span>
        <input
          value={assignmentId}
          onChange={event => setAssignmentId(event.target.value)}
          placeholder="Assignment ID"
          className="field-control"
        />
      </label>
      <div className="filter-group" role="group" aria-label="Import a batch from Canvas">
        <label className="assignment-field">
          <span>Canvas course</span>
          <input
            value={importCourse}
            onChange={event => setImportCourse(event.target.value)}
            placeholder="106"
            inputMode="numeric"
            className="field-control"
          />
        </label>
        <label className="assignment-field">
          <span>Canvas assignment</span>
          <input
            value={importAssignment}
            onChange={event => setImportAssignment(event.target.value)}
            placeholder="2841"
            inputMode="numeric"
            className="field-control"
          />
        </label>
        <button
          type="button"
          className="button-secondary"
          onClick={handleImport}
          disabled={importing}
        >
          {importing ? "Importing…" : "Import from Canvas"}
        </button>
      </div>
      <div className="filter-group" role="group" aria-label="Filter submissions by review lane">
        <button
          type="button"
          onClick={() => setSelectedLane(undefined)}
          aria-pressed={!selectedLane}
          className="filter-chip"
        >
          All submissions
        </button>
        {Object.entries(LANE_META).map(([key, lane]) => (
          <button
            type="button"
            key={key}
            onClick={() => setSelectedLane(key)}
            aria-pressed={selectedLane === key}
            className="filter-chip"
          >
            {lane.filter}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <AppShell
      eyebrow="Teacher review"
      title="Grading queue"
      subtitle="Start with the drafts that need judgment, then approve each grade when it reflects your rubric."
      toolbar={toolbar}
      actions={(
        <>
          <button className="button-quiet" onClick={onOpenSettings}>Settings</button>
          <button className="button-quiet" onClick={onOpenModels}>Local models</button>
          <button className="button-secondary" onClick={onRefresh}>Refresh queue</button>
          <button className="button-primary" onClick={handleGrade} disabled={grading || !assignmentId.trim()}>
            {grading ? "Starting…" : "Grade submissions"}
          </button>
          {pendingPosts > 0 && (
            <button
              className="button-secondary"
              onClick={handleFlush}
              disabled={flushing}
            >
              {flushing ? "Retrying…" : `Retry ${pendingPosts} queued post${pendingPosts === 1 ? "" : "s"}`}
            </button>
          )}
          {canvasTarget && (
            <button
              className="button-primary"
              onClick={handlePost}
              disabled={posting || approvedIds.length === 0}
              title={approvedIds.length === 0 ? "Approve a grade first" : undefined}
            >
              {posting ? "Posting…" : `Post ${approvedIds.length} approved to Canvas`}
            </button>
          )}
        </>
      )}
      footer={(
        <p><strong>Keyboard ready:</strong> open a submission, then use <kbd>A</kbd> to approve, <kbd>E</kbd> to edit feedback, or <kbd>Esc</kbd> to return.</p>
      )}
    >
      {postReceipt.length > 0 && (
        <div className="notice-row" role="status" aria-live="polite">
          <div>
            <h2>Canvas post receipt</h2>
            <ul>
              {postReceipt.map(line => <li key={line}>{line}</li>)}
            </ul>
          </div>
        </div>
      )}
      {(gradeMsg || updateVersion) && (
        <div className="notice-row" role="status" aria-live="polite">
          {gradeMsg && <p>{gradeMsg}</p>}
          {updateVersion && (
            <>
              <p>Grader {updateVersion} is available when you are ready to update. {updateStatus}</p>
              <button className="button-secondary" type="button" onClick={handleInstallUpdate} disabled={installingUpdate}>
                {installingUpdate ? "Installing update…" : "Install update and restart"}
              </button>
            </>
          )}
        </div>
      )}

      {engineIssue && (
        <div className="decision-notice decision-notice--error" role="alert">
          <div>
            <strong>Local engine needs attention</strong>
            <p>{engineIssue}</p>
          </div>
          {onRecoverEngine && (
            <button type="button" className="button-secondary" onClick={onRecoverEngine} disabled={recoveringEngine}>
              {recoveringEngine ? "Restarting…" : "Restart local engine"}
            </button>
          )}
        </div>
      )}

      {stats && (
        <section aria-labelledby="queue-overview-title" className="queue-overview">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Work at a glance</p>
              <h2 id="queue-overview-title">Choose where to begin</h2>
            </div>
            <p className="muted">{stats.summary}</p>
          </div>
          <div className="triage-grid">
            {Object.entries(LANE_META).map(([key, lane]) => (
              <article key={key} className="triage-card paper-panel">
                <span className={`status-pill ${lane.className}`}>{lane.summary}</span>
                <strong className="triage-count">{statValue(key) ?? 0}</strong>
                <p>{lane.description}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="submissions-section" aria-labelledby="submissions-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current assignment</p>
            <h2 id="submissions-title">Submissions</h2>
          </div>
          <p className="muted">{items.length} shown</p>
        </div>

        {items.length === 0 ? (
          <div className="empty-state paper-panel">
            <span className="empty-state__mark" aria-hidden="true">✓</span>
            <h2>No submissions to review yet</h2>
            <p>Import submissions, then choose Grade submissions to prepare rubric-aligned drafts on this Mac.</p>
          </div>
        ) : (
          <div className="submission-table-wrap paper-panel">
            <table className="submission-table" aria-label="Graded submissions">
              <thead>
                <tr>
                  <th scope="col">Submission</th>
                  <th scope="col">Review lane</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Proposed score</th>
                  <th scope="col">Signals</th>
                  <th scope="col">Decision</th>
                </tr>
              </thead>
              <tbody>
                {items.map(item => {
                  const lane = item.lane ? LANE_META[item.lane] : null;
                  const aiTotal = item.ai_scores ? Object.values(item.ai_scores).reduce((a, b) => a + b, 0) : null;
                  const decision = item.approved ? "Approved" : item.teacher_scores ? "Teacher override" : "Draft — review required";
                  return (
                    <tr key={item.id}>
                      <td>
                        <button type="button" className="submission-link" onClick={() => onSelectItem(item)}>
                          {item.submission_id}
                          <span>Open review <span aria-hidden="true">→</span></span>
                        </button>
                      </td>
                      <td>{lane ? <span className={`status-pill ${lane.className}`}>{lane.summary}</span> : "Not triaged"}</td>
                      <td>{item.confidence === null ? "Not available" : `${Math.round(item.confidence * 100)}%`}</td>
                      <td>{aiTotal === null ? "Not scored" : aiTotal.toFixed(1)}</td>
                      <td>
                        <div className="signal-list">
                          {item.flags.length ? item.flags.map(flag => <span key={flag} className="signal-tag">{signalLabel(flag)}</span>) : <span className="muted">No flags</span>}
                        </div>
                      </td>
                      <td><span className={item.approved ? "decision-approved" : "muted"}>{decision}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </AppShell>
  );
}
