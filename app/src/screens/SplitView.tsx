import { useCallback, useEffect, useMemo, useState } from "react";
import { GradeItem, approveGrade, overrideGrade } from "../api";
import { AppShell } from "../components/AppShell";

interface Props {
  item: GradeItem;
  onBack: () => void;
}

const LANE_META: Record<string, { label: string; className: string }> = {
  review_first: { label: "Needs teacher review", className: "status-review" },
  standard: { label: "Standard review", className: "status-standard" },
  high_confidence: { label: "High confidence", className: "status-confident" },
};

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}

function total(scores: Record<string, number>) {
  return Object.values(scores).reduce((sum, score) => sum + score, 0);
}

export function SplitView({ item, onBack }: Props) {
  const initialScores = item.teacher_scores || item.ai_scores || {};
  const initialFeedback = item.teacher_feedback || item.ai_feedback || "";
  const [scores, setScores] = useState<Record<string, number>>(initialScores);
  const [feedback, setFeedback] = useState(initialFeedback);
  const [approved, setApproved] = useState(item.approved);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [feedbackFocused, setFeedbackFocused] = useState(false);

  useEffect(() => {
    setScores(item.teacher_scores || item.ai_scores || {});
    setFeedback(item.teacher_feedback || item.ai_feedback || "");
    setApproved(item.approved);
    setMessage("");
  }, [item]);

  const changed = useMemo(
    () => JSON.stringify(scores) !== JSON.stringify(initialScores) || feedback !== initialFeedback,
    [scores, feedback, initialScores, initialFeedback],
  );

  const handleApprove = useCallback(async () => {
    setSaving(true);
    setMessage("");
    try {
      await approveGrade(item.id);
      setApproved(true);
      setMessage("Grade approved. It is ready for your next posting step.");
    } catch (error) {
      setMessage(`Approval was not saved. ${(error as Error).message} Your edits are still here.`);
    } finally {
      setSaving(false);
    }
  }, [item.id]);

  const handleOverride = useCallback(async () => {
    setSaving(true);
    setMessage("");
    try {
      const result = await overrideGrade(item.id, scores, feedback);
      setApproved(true);
      if (result.refinement_proposals?.length > 0) {
        setMessage(`Your changes are approved. ${result.refinement_proposals.length} rubric refinement proposal${result.refinement_proposals.length === 1 ? " is" : "s are"} ready to review.`);
      } else {
        setMessage(`Your changes are approved. Score updated from ${result.ai_total} to ${result.teacher_total}.`);
      }
    } catch (error) {
      setMessage(`Your changes were not saved. ${(error as Error).message} Your scores and feedback are still here.`);
    } finally {
      setSaving(false);
    }
  }, [item.id, scores, feedback]);

  const handleKey = useCallback((event: KeyboardEvent) => {
    if (feedbackFocused) return;
    if (event.key.toLowerCase() === "a") {
      event.preventDefault();
      handleApprove();
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      changed ? handleOverride() : handleApprove();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onBack();
    }
    if (event.key.toLowerCase() === "e") {
      event.preventDefault();
      document.getElementById("feedback-editor")?.focus();
    }
  }, [changed, feedbackFocused, handleApprove, handleOverride, onBack]);

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  const lane = item.lane ? LANE_META[item.lane] : null;
  const criteriaIds = Object.keys(scores);
  const aiTotal = total(item.ai_scores || {});
  const currentTotal = total(scores);

  return (
    <AppShell
      eyebrow="Teacher decision"
      title={`Review ${item.submission_id}`}
      subtitle="Compare the draft with the rubric, make any changes, then approve only when the grade reflects your judgment."
      actions={(
        <>
          <button type="button" className="button-quiet" onClick={onBack}>← Back to queue</button>
          <button type="button" className="button-secondary" onClick={handleApprove} disabled={saving || approved || changed} aria-label="Approve AI draft" title={changed ? "Save your teacher changes instead" : undefined}>
            {approved && !changed ? "Approved" : "Approve AI draft"}
          </button>
        </>
      )}
      toolbar={(
        <div className="review-statusbar">
          <span className="status-pill status-info">AI suggestion — teacher decision required</span>
          {lane && <span className={`status-pill ${lane.className}`}>{lane.label}</span>}
          {item.confidence !== null && <span className="confidence-copy">{Math.round(item.confidence * 100)}% confidence</span>}
          <span className="privacy-copy">Processed locally · nothing posts without approval</span>
        </div>
      )}
      footer={(
        <p><strong>Shortcuts:</strong> <kbd>A</kbd> approve draft · <kbd>E</kbd> edit feedback · <kbd>⌘ Enter</kbd> save decision · <kbd>Esc</kbd> return</p>
      )}
    >
      {message && (
        <div className={message.includes("not saved") ? "decision-notice decision-notice--error" : "decision-notice"} role="status" aria-live="polite">
          {message}
        </div>
      )}

      <div className="review-grid">
        <section className="review-document" aria-labelledby="submission-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Source material</p>
              <h2 id="submission-heading">Submission</h2>
            </div>
            <span className="muted">Original remains unchanged</span>
          </div>
          <article className="submission-paper paper-panel">
            <div className="submission-paper__rule" aria-hidden="true" />
            <p className="submission-reference">Submission reference</p>
            <p className="submission-identity">{item.submission_id}</p>
            <p className="muted">The parsed submission content appears here when it is available from the grading engine.</p>
          </article>

          <article className="feedback-draft soft-panel" aria-labelledby="ai-feedback-heading">
            <div className="feedback-draft__header">
              <div>
                <p className="eyebrow">Draft, not final</p>
                <h2 id="ai-feedback-heading">AI feedback suggestion</h2>
              </div>
              <span className="status-pill status-info">Review before use</span>
            </div>
            <p>{item.ai_feedback || "No feedback suggestion was generated."}</p>
          </article>

          <section className="signals-panel" aria-labelledby="signals-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Review signals</p>
                <h2 id="signals-heading">What needs attention</h2>
              </div>
            </div>
            <div className="signal-list">
              {item.flags.length
                ? item.flags.map(flag => <span key={flag} className="signal-tag">{titleCase(flag)}</span>)
                : <p className="muted">No additional review signals were raised.</p>}
            </div>
          </section>
        </section>

        <aside className="rubric-inspector paper-panel" aria-labelledby="rubric-heading">
          <div className="rubric-inspector__header">
            <div>
              <p className="eyebrow">Your rubric</p>
              <h2 id="rubric-heading">Score and feedback</h2>
            </div>
            <div className="score-total" aria-label={`Current total ${currentTotal} points`}>
              <strong>{currentTotal}</strong>
              <span>points</span>
            </div>
          </div>
          {changed && <p className="unsaved-note" role="status">Teacher changes not saved yet.</p>}

          <div className="criteria-list">
            {criteriaIds.map(criterion => (
              <fieldset key={criterion} className="criterion-card soft-panel">
                <legend>{criterion}</legend>
                <div className="criterion-meta">
                  <span>AI suggested {item.ai_scores?.[criterion]?.toFixed(1) ?? "—"}</span>
                  <strong>{scores[criterion]?.toFixed(1) ?? "—"}</strong>
                </div>
                <div className="score-options">
                  {[0, 1, 2, 3, 4, 5].map(value => (
                    <label key={value} className="score-option">
                      <input
                        type="radio"
                        name={`score-${criterion}`}
                        value={value}
                        checked={scores[criterion] === value}
                        onChange={() => setScores(current => ({ ...current, [criterion]: value }))}
                        aria-label={`${value} points for ${criterion}`}
                        aria-checked={scores[criterion] === value}
                      />
                      <span>{value}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>

          <div className="feedback-editor-block">
            <label className="field-label" htmlFor="feedback-editor">Feedback for {item.submission_id}</label>
            <p className="field-help">Keep it specific: what worked, what to revise, and the next useful action.</p>
            <textarea
              id="feedback-editor"
              value={feedback}
              onChange={event => setFeedback(event.target.value)}
              onFocus={() => setFeedbackFocused(true)}
              onBlur={() => setFeedbackFocused(false)}
              rows={8}
              className="field-control feedback-textarea"
              aria-label={`Feedback for ${item.submission_id}`}
            />
          </div>

          <div className="score-comparison" aria-label="Score comparison">
            <span>AI draft <strong>{aiTotal}</strong></span>
            <span aria-hidden="true">→</span>
            <span>Your total <strong>{currentTotal}</strong></span>
          </div>
          <button type="button" className="button-primary review-save" onClick={handleOverride} disabled={saving} aria-label="Save changes and approve">
            {saving ? "Saving decision…" : "Save changes & approve"}
          </button>
        </aside>
      </div>
    </AppShell>
  );
}
