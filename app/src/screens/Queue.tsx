import { QueueData, GradeItem, fetchQueue, gradeBatch, checkUpdates } from "../api";
import { useState, useEffect, useCallback } from "react";

interface Props {
  data: QueueData | null;
  onSelectItem: (item: GradeItem) => void;
  assignmentId: string;
  setAssignmentId: (id: string) => void;
  selectedLane: string | undefined;
  setSelectedLane: (lane: string | undefined) => void;
  onRefresh: () => void;
  onOpenModels: () => void;
  onOpenSettings: () => void;
}

const LANE_LABELS: Record<string, { label: string; color: string; dot: string }> = {
  review_first: { label: "Review First", color: "text-red-400", dot: "bg-red-500" },
  standard: { label: "Standard", color: "text-amber-400", dot: "bg-amber-500" },
  high_confidence: { label: "High Confidence", color: "text-green-400", dot: "bg-green-500" },
};

export function QueueScreen({ data, onSelectItem, assignmentId, setAssignmentId, selectedLane, setSelectedLane, onRefresh, onOpenModels, onOpenSettings }: Props) {
  const [grading, setGrading] = useState(false);
  const [gradeMsg, setGradeMsg] = useState("");
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [updateVersion, setUpdateVersion] = useState("");

  const stats = data?.stats;
  const items = data?.items || [];

  useEffect(() => {
    checkUpdates().then(u => {
      if (u.update_available) {
        setUpdateAvailable(true);
        setUpdateVersion(u.latest_version || "");
      }
    }).catch(() => {});
  }, []);

  const handleGrade = async () => {
    setGrading(true);
    setGradeMsg("Starting grading batch...");
    try {
      const result = await gradeBatch(assignmentId);
      setGradeMsg(`Grading ${result.total} submissions...`);
    } catch (e: any) {
      setGradeMsg(`Error: ${e.message}`);
    } finally {
      setGrading(false);
    }
  };

  useEffect(() => {
    if (grading) {
      const t = setInterval(() => {
        onRefresh();
        setGrading(false);
        setGradeMsg("");
      }, 10000);
      return () => clearInterval(t);
    }
  }, [grading]);

  return (
    <div className="min-h-screen bg-gray-900 text-white" role="application" aria-label="Grading queue">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between" role="banner">
        <div className="flex items-center gap-6">
          <h1 className="text-xl font-bold">Grader</h1>
          <input
            value={assignmentId}
            onChange={(e) => setAssignmentId(e.target.value)}
            placeholder="Assignment ID"
            className="px-3 py-1.5 rounded-lg bg-gray-800 border border-gray-700 text-sm w-48 focus:border-blue-500 focus:outline-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() => setSelectedLane(undefined)}
              className={`px-3 py-1.5 rounded-lg text-sm ${!selectedLane ? "bg-blue-600" : "bg-gray-800 hover:bg-gray-700"}`}
            >
              All
            </button>
            {Object.entries(LANE_LABELS).map(([key, { label }]) => (
              <button
                key={key}
                onClick={() => setSelectedLane(key)}
                className={`px-3 py-1.5 rounded-lg text-sm ${selectedLane === key ? "bg-blue-600" : "bg-gray-800 hover:bg-gray-700"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-4">
          {updateAvailable && (
            <span className="text-sm text-green-400">
              Update available: v{updateVersion}
            </span>
          )}
          {gradeMsg && <span className="text-sm text-gray-400">{gradeMsg}</span>}
          <button
            onClick={handleGrade}
            disabled={grading}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-sm font-medium"
          >
            {grading ? "Grading..." : "Grade Batch"}
          </button>
          <button
            onClick={onOpenSettings}
            className="px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
            aria-label="Open settings"
          >
            Settings
          </button>
          <button
            onClick={onOpenModels}
            className="px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
            aria-label="Open model manager"
          >
            Models
          </button>
          <button
            onClick={onRefresh}
            className="px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
          >
            Refresh
          </button>
        </div>
      </header>

      {stats && (
        <div className="px-6 py-3 bg-gray-850 border-b border-gray-800">
          <p className="text-sm text-gray-400">{stats.summary}</p>
        </div>
      )}

      <div className="px-6 py-4">
        {stats && (
          <div className="grid grid-cols-3 gap-4 mb-6">
            {Object.entries(LANE_LABELS).map(([key, { label, dot }]) => (
              <div key={key} className="bg-gray-850 rounded-xl p-4 border border-gray-800">
                <div className="flex items-center gap-2 mb-2">
                  <div className={`w-2.5 h-2.5 rounded-full ${dot}`} />
                  <span className="text-sm text-gray-400">{label}</span>
                </div>
                <span className="text-2xl font-bold">
                  {key === "review_first" ? stats.review_first : key === "standard" ? stats.standard : stats.high_confidence}
                </span>
              </div>
            ))}
          </div>
        )}

        {items.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-500 text-lg">No graded items yet</p>
            <p className="text-gray-600 text-sm mt-2">Import submissions and click "Grade Batch" to start</p>
          </div>
        ) : (
          <table className="w-full" role="table" aria-label="Graded submissions">
            <thead>
              <tr className="text-left text-sm text-gray-500 border-b border-gray-800" role="row">
                <th className="py-3 px-2" scope="col">Lane</th>
                <th className="py-3 px-2" scope="col">Confidence</th>
                <th className="py-3 px-2" scope="col">AI Score</th>
                <th className="py-3 px-2" scope="col">Flags</th>
                <th className="py-3 px-2" scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const lane = item.lane ? LANE_LABELS[item.lane] : null;
                const aiTotal = item.ai_scores ? Object.values(item.ai_scores).reduce((a, b) => a + b, 0) : null;
                return (
                  <tr
                    key={item.id}
                    onClick={() => onSelectItem(item)}
                    className="border-b border-gray-800 hover:bg-gray-850 cursor-pointer transition-colors"
                  >
                    <td className="py-3 px-2">
                      {lane && (
                        <div className="flex items-center gap-2">
                          <div className={`w-2 h-2 rounded-full ${lane.dot}`} />
                          <span className={`text-sm ${lane.color}`}>{lane.label}</span>
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-2">
                      {item.confidence !== null && (
                        <span className={`text-sm ${item.confidence < 0.7 ? "text-red-400" : item.confidence >= 0.85 ? "text-green-400" : "text-amber-400"}`}>
                          {(item.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-2 text-sm">
                      {aiTotal !== null ? aiTotal.toFixed(1) : "—"}
                    </td>
                    <td className="py-3 px-2">
                      <div className="flex gap-1">
                        {item.flags.map((f) => (
                          <span key={f} className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300">
                            {f.replace(/_/g, " ")}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-2">
                      {item.approved ? (
                        <span className="text-sm text-green-400">Approved</span>
                      ) : item.teacher_scores ? (
                        <span className="text-sm text-blue-400">Overridden</span>
                      ) : (
                        <span className="text-sm text-gray-500">Draft</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <footer className="fixed bottom-0 left-0 right-0 px-6 py-2 bg-gray-850 border-t border-gray-800 text-xs text-gray-600">
        <kbd className="px-1.5 py-0.5 bg-gray-800 rounded">J</kbd>/
        <kbd className="px-1.5 py-0.5 bg-gray-800 rounded">K</kbd> navigate ·
        <kbd className="ml-2 px-1.5 py-0.5 bg-gray-800 rounded">A</kbd> approve ·
        <kbd className="ml-2 px-1.5 py-0.5 bg-gray-800 rounded">1-5</kbd> score ·
        <kbd className="ml-2 px-1.5 py-0.5 bg-gray-800 rounded">E</kbd> feedback ·
        <kbd className="ml-2 px-1.5 py-0.5 bg-gray-800 rounded">⌘⏎</kbd> approve+next
      </footer>
    </div>
  );
}