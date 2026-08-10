import { useCallback, useEffect, useState } from "react";
import { CalibrationHealth, fetchCalibration } from "../api";
import { AppShell } from "../components/AppShell";

/** Signed, as a fraction of criterion max — the convention the engine reports in.
 *  A true minus sign rather than a hyphen: this is a number, and it is read aloud. */
function signedPercent(fraction: number) {
  return `${fraction < 0 ? "−" : "+"}${(Math.abs(fraction) * 100).toFixed(1)}%`;
}

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function StatCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="triage-card paper-panel" role="group" aria-label={label}>
      <span className="eyebrow">{label}</span>
      <strong className="triage-count">{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

export function CalibrationScreen({ assignmentId, onClose }: { assignmentId: string; onClose: () => void }) {
  const [health, setHealth] = useState<CalibrationHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await fetchCalibration(assignmentId));
      setMessage("");
    } catch (error) {
      // A read that failed is not a calibration figure of zero. Say so, and keep
      // the panel empty rather than showing numbers nothing stands behind.
      setMessage(`Calibration could not be read. ${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [assignmentId]);

  useEffect(() => { refresh(); }, [refresh]);

  const criteria = Object.entries(health?.per_criterion || {});
  // Below the floor there is no direction to state — and no direction to infer.
  // This is the ordinary early state of an Assignment, so it reads as progress.
  const belowFloor = health !== null && health.draft_bias === null;
  const remaining = health ? Math.max(0, health.evidence_floor - health.n) : 0;

  return (
    <AppShell
      eyebrow="Calibration"
      title="Calibration health"
      subtitle={`How closely Grader's drafts agree with your own grading on ${assignmentId}, measured over every approval you have made — including the ones where you changed nothing.`}
      actions={(
        <>
          <button className="button-secondary" onClick={refresh} disabled={loading}>Refresh calibration</button>
          <button className="button-secondary" onClick={onClose}>← Back to queue</button>
        </>
      )}
      wide={false}
    >
      {message && <div className="settings-message" role="status" aria-live="polite">{message}</div>}

      {health === null ? (
        loading ? <div className="compact-empty">Reading your approvals on this Mac…</div> : null
      ) : (
        <>
          <section className="runtime-banner paper-panel" aria-labelledby="draft-bias-heading">
            <div>
              <p className="eyebrow">Draft bias</p>
              {belowFloor ? (
                <>
                  <h2 id="draft-bias-heading">Direction not reported yet</h2>
                  <p className="triage-count">{health.n} of {health.evidence_floor} approvals</p>
                  <p>
                    Grader reports which way drafts run once {health.evidence_floor} approvals are in on this
                    assignment. {plural(remaining, "more approval")} and the direction appears here.
                  </p>
                </>
              ) : (
                <>
                  <h2 id="draft-bias-heading">
                    {health.discloses
                      ? `Drafts run ${health.draft_bias! < 0 ? "below" : "above"} your grading`
                      : "Drafts and your grading agree closely"}
                  </h2>
                  <p className="triage-count">{signedPercent(health.draft_bias!)}</p>
                  <p>
                    of a criterion's maximum, averaged over {plural(health.n, "approval")}.
                    {health.discloses
                      ? " Grader discloses this gap and never changes a score for it."
                      : " Too small for Grader to name a direction, and it never changes a score."}
                  </p>
                  {health.demotes && (
                    <p>
                      <span className="status-pill status-review">Queue adjusted</span>{" "}
                      The gap is large enough that high-confidence drafts on this assignment are
                      routed for standard review. Scores are untouched.
                    </p>
                  )}
                </>
              )}
            </div>
          </section>

          <section className="queue-overview" aria-labelledby="measures-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">What the figure rests on</p>
                <h2 id="measures-heading">Your grading on this assignment</h2>
              </div>
            </div>
            <div className="triage-grid">
              <StatCard
                label="Approvals measured"
                value={String(health.n)}
                detail={belowFloor ? `of ${health.evidence_floor} needed before a direction is reported` : "every approval, changed or not"}
              />
              <StatCard
                label="Override rate"
                value={`${Math.round(health.override_rate * 100)}%`}
                // Exemplars, not approvals: every override produces one, so this is
                // the rate's own denominator. An approval you changed nothing on is
                // measured above but is not a correction.
                detail={`${health.override_count} of ${health.exemplar_count} exemplars came from an override`}
              />
              <StatCard
                label="Exemplars kept"
                value={String(health.exemplar_count)}
                detail="teacher-graded anchors available to the model"
              />
            </div>
          </section>

          <section className="submissions-section" aria-labelledby="per-criterion-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Where the gap sits</p>
                <h2 id="per-criterion-heading">Per-criterion disagreement</h2>
              </div>
              <p className="muted">{criteria.length} shown</p>
            </div>
            {criteria.length === 0 ? (
              <div className="compact-empty">
                Per-criterion disagreement appears alongside the direction, once{" "}
                {health.evidence_floor} approvals are in.
              </div>
            ) : (
              <div className="submission-table-wrap paper-panel">
                <table className="submission-table" aria-label="Per-criterion disagreement">
                  <thead>
                    <tr>
                      <th scope="col">Criterion</th>
                      <th scope="col">Direction</th>
                      <th scope="col">Typical gap</th>
                      <th scope="col">Approvals</th>
                    </tr>
                  </thead>
                  <tbody>
                    {criteria.map(([id, stats]) => (
                      <tr key={id}>
                        <th scope="row">{titleCase(id)}</th>
                        <td>
                          {signedPercent(stats.bias)}
                          <span className="muted"> {stats.bias < 0 ? "below you" : "above you"}</span>
                        </td>
                        <td>{(stats.abs_bias * 100).toFixed(1)}%</td>
                        <td>{stats.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <aside className="local-note soft-panel">
            <span aria-hidden="true">⌂</span>
            <div>
              <strong>Read on this Mac, from your own approvals</strong>
              <p>
                Every figure here comes from grades you approved locally. Grader shows you the gap;
                the score a student receives is always the one you approved.
              </p>
            </div>
          </aside>
        </>
      )}
    </AppShell>
  );
}
