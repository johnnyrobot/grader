import { useState, useEffect, useCallback } from "react";
import { GradeItem, approveGrade, overrideGrade, fetchQueue } from "../api";

interface Props {
  item: GradeItem;
  onBack: () => void;
}

const LANE_LABELS: Record<string, { label: string; color: string }> = {
  review_first: { label: "Review First", color: "text-red-400" },
  standard: { label: "Standard", color: "text-amber-400" },
  high_confidence: { label: "High Confidence", color: "text-green-400" },
};

export function SplitView({ item, onBack }: Props) {
  const [scores, setScores] = useState<Record<string, number>>(item.ai_scores || {});
  const [feedback, setFeedback] = useState(item.ai_feedback || "");
  const [approved, setApproved] = useState(item.approved);
  const [overridden, setOverridden] = useState(!!item.teacher_scores);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [feedbackFocused, setFeedbackFocused] = useState(false);

  useEffect(() => {
    setScores(item.teacher_scores || item.ai_scores || {});
    setFeedback(item.teacher_feedback || item.ai_feedback || "");
    setApproved(item.approved);
    setOverridden(!!item.teacher_scores);
  }, [item]);

  const handleApprove = useCallback(async () => {
    setSaving(true);
    try {
      await approveGrade(item.id);
      setApproved(true);
      setMsg("Approved");
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }, [item.id]);

  const handleOverride = useCallback(async () => {
    setSaving(true);
    try {
      const result = await overrideGrade(item.id, scores, feedback);
      setOverridden(true);
      setApproved(true);
      setMsg(`Overridden — AI ${result.ai_total} → Teacher ${result.teacher_total}`);
      if (result.refinement_proposals?.length > 0) {
        setMsg(`Overridden. ${result.refinement_proposals.length} rubric refinement proposal(s) available.`);
      }
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }, [item.id, scores, feedback]);

  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (feedbackFocused) return;
      if (e.key === "a" || e.key === "A") {
        e.preventDefault();
        handleApprove();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (overridden || !approved) handleOverride();
        else handleApprove();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        onBack();
      }
      if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        document.getElementById("feedback-editor")?.focus();
      }
    },
    [handleApprove, handleOverride, onBack, overridden, approved, feedbackFocused]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  const criteriaIds = Object.keys(scores);
  const lane = item.lane ? LANE_LABELS[item.lane] : null;

  return (
    <div className="min-h-screen bg-gray-900 text-white flex flex-col" role="application" aria-label="Grade review">
      <header className="border-b border-gray-800 px-6 py-3 flex items-center justify-between flex-shrink-0" role="banner">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
            aria-label="Back to queue"
          >
            ← Back
          </button>
          {lane && (
            <div className="flex items-center gap-2" role="status" aria-label={`Triage lane: ${lane.label}`}>
              <div className={`w-2 h-2 rounded-full ${lane.color.replace("text", "bg")}`} aria-hidden="true" />
              <span className={`text-sm ${lane.color}`}>{lane.label}</span>
            </div>
          )}
          {item.confidence !== null && (
            <span
              className={`text-sm ${item.confidence < 0.7 ? "text-red-400" : item.confidence >= 0.85 ? "text-green-400" : "text-amber-400"}`}
              role="status"
              aria-label={`AI confidence: ${(item.confidence * 100).toFixed(0)} percent`}
            >
              Confidence: {(item.confidence * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {msg && <span className="text-sm text-gray-400" role="status" aria-live="polite">{msg}</span>}
          {approved && <span className="text-sm text-green-400" role="status">✓ Approved</span>}
          <button
            onClick={handleApprove}
            disabled={saving || approved}
            className="px-4 py-1.5 rounded-lg bg-green-600 hover:bg-green-700 disabled:bg-gray-700 text-sm font-medium"
            aria-label={approved ? "Grade approved" : "Approve AI grade"}
            aria-disabled={saving || approved}
          >
            {approved ? "Approved" : "Approve"}
          </button>
          <button
            onClick={handleOverride}
            disabled={saving}
            className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-sm font-medium"
            aria-label="Override AI grade with your scores"
            aria-disabled={saving}
          >
            Override
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-auto p-6 border-r border-gray-800" role="region" aria-label="Student submission and AI feedback">
          <h3 className="text-sm text-gray-500 mb-3">Submission</h3>
          <div className="bg-gray-850 rounded-lg p-6 text-gray-300 whitespace-pre-wrap">
            {item.submission_id}
          </div>
          <div className="mt-4">
            <h3 className="text-sm text-gray-500 mb-2">AI Feedback Draft</h3>
            <div className="bg-gray-850 rounded-lg p-4 text-gray-400 text-sm whitespace-pre-wrap">
              {item.ai_feedback || "(no feedback)"}
            </div>
          </div>
          {item.flags.length > 0 && (
            <div className="mt-4" role="status" aria-label="AI flags">
              <h3 className="text-sm text-gray-500 mb-2">Flags</h3>
              <div className="flex gap-2">
                {item.flags.map((f) => (
                  <span key={f} className="text-xs px-2 py-1 rounded bg-yellow-900 text-yellow-200" role="badge">
                    {f.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="w-[420px] flex-shrink-0 overflow-auto p-6" role="region" aria-label="Rubric scoring">
          <h3 className="text-sm text-gray-500 mb-4" role="heading" aria-level={2}>Rubric Scorer</h3>
          <div className="space-y-4">
            {criteriaIds.map((cid) => (
              <div key={cid} className="bg-gray-850 rounded-xl p-4" role="group" aria-label={`Criterion: ${cid}`}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-gray-300">{cid}</span>
                  <span className="text-lg font-bold text-white" aria-label={`Score: ${scores[cid]?.toFixed(1) || "not set"}`}>
                    {scores[cid]?.toFixed(1) || "—"}
                  </span>
                </div>
                <div className="flex gap-1" role="radiogroup" aria-label={`Score for ${cid}`}>
                  {[0, 1, 2, 3, 4, 5].map((n) => (
                    <button
                      key={n}
                      onClick={() => setScores({ ...scores, [cid]: n })}
                      className={`flex-1 py-1.5 rounded text-sm ${
                        scores[cid] === n
                          ? "bg-blue-600 text-white"
                          : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                      }`}
                      role="radio"
                      aria-checked={scores[cid] === n}
                      aria-label={`${n} points`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6">
            <h3 className="text-sm text-gray-500 mb-2" role="heading" aria-level={2}>Feedback Editor</h3>
            <textarea
              id="feedback-editor"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              onFocus={() => setFeedbackFocused(true)}
              onBlur={() => setFeedbackFocused(false)}
              rows={6}
              className="w-full px-4 py-3 rounded-lg bg-gray-800 border border-gray-700 text-white text-sm focus:border-blue-500 focus:outline-none resize-none"
              placeholder="Edit feedback for the student..."
              aria-label="Feedback editor for student"
            />
          </div>

          <div className="mt-4 p-3 rounded-lg bg-gray-850 text-xs text-gray-500" role="note" aria-label="Keyboard shortcuts">
            <p>Keyboard: <kbd className="px-1 bg-gray-800 rounded">A</kbd> approve ·
            <kbd className="ml-1 px-1 bg-gray-800 rounded">E</kbd> edit feedback ·
            <kbd className="ml-1 px-1 bg-gray-800 rounded">⌘⏎</kbd> override+approve ·
            <kbd className="ml-1 px-1 bg-gray-800 rounded">Esc</kbd> back</p>
          </div>
        </div>
      </div>
    </div>
  );
}