import { useState, useEffect } from "react";
import { getSettings, updateSettings, listRubrics, getRubric, saveRubric } from "../api";

interface Props {
  onClose: () => void;
}

export function SettingsScreen({ onClose }: Props) {
  const [settings, setSettings] = useState<Record<string, string | number>>({});
  const [rubrics, setRubrics] = useState<{ title: string; version: number }[]>([]);
  const [savedMsg, setSavedMsg] = useState("");
  const [activeTab, setActiveTab] = useState<"general" | "models" | "rubrics">("general");
  const [pasteText, setPasteText] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");

  const refresh = async () => {
    try {
      const s = await getSettings();
      setSettings(s);
      const r = await listRubrics();
      setRubrics(r);
    } catch (e) {
      setSavedMsg(`Error: ${(e as Error).message}`);
    }
  };

  useEffect(() => { refresh(); }, []);

  const handleSave = async (updates: Record<string, string | number>) => {
    try {
      await updateSettings(updates);
      setSavedMsg("Saved");
      setTimeout(() => setSavedMsg(""), 2000);
      refresh();
    } catch (e) {
      setSavedMsg(`Error: ${(e as Error).message}`);
    }
  };

  const handlePasteRubric = async () => {
    if (!pasteText.trim() || !pasteTitle.trim()) {
      setSavedMsg("Need both title and rubric text");
      return;
    }
    try {
      await saveRubric({ text: pasteText, title: pasteTitle });
      setSavedMsg("Rubric saved");
      setPasteText("");
      setPasteTitle("");
      refresh();
    } catch (e) {
      setSavedMsg(`Error: ${(e as Error).message}`);
    }
  };

  const loadRubric = async (title: string) => {
    try {
      const r = await getRubric(title);
      setPasteTitle(r.title);
      setPasteText(JSON.stringify(r, null, 2));
    } catch (e) {
      setSavedMsg(`Error: ${(e as Error).message}`);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between" role="banner">
        <h1 className="text-xl font-bold" role="heading" aria-level={1}>Settings</h1>
        <div className="flex items-center gap-4">
          {savedMsg && <span className="text-sm text-green-400" role="status" aria-live="polite">{savedMsg}</span>}
          <button
            onClick={onClose}
            className="px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
            aria-label="Back to queue"
          >
            Back
          </button>
        </div>
      </header>

      <div className="flex">
        <nav className="w-48 border-r border-gray-800 py-4 px-2" role="navigation" aria-label="Settings tabs">
          {(["general", "models", "rubrics"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`w-full text-left px-4 py-2 rounded-lg text-sm capitalize mb-1 ${
                activeTab === tab ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-800"
              }`}
              role="tab"
              aria-selected={activeTab === tab}
              aria-controls={`${tab}-panel`}
            >
              {tab}
            </button>
          ))}
        </nav>

        <main className="flex-1 p-6">
          {activeTab === "general" && (
            <div role="tabpanel" id="general-panel" aria-label="General settings">
              <GeneralPanel settings={settings} onSave={handleSave} />
            </div>
          )}
          {activeTab === "models" && (
            <div role="tabpanel" id="models-panel" aria-label="Model settings">
              <ModelsPanel settings={settings} onSave={handleSave} />
            </div>
          )}
          {activeTab === "rubrics" && (
            <div role="tabpanel" id="rubrics-panel" aria-label="Rubric management">
              <RubricsPanel
                rubrics={rubrics}
                pasteText={pasteText}
                setPasteText={setPasteText}
                pasteTitle={pasteTitle}
                setPasteTitle={setPasteTitle}
                onSave={handlePasteRubric}
                onLoad={loadRubric}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function GeneralPanel({ settings, onSave }: {
  settings: Record<string, string | number>;
  onSave: (s: Record<string, string | number>) => void;
}) {
  const [canvasToken, setCanvasToken] = useState("");
  const [canvasUrl, setCanvasUrl] = useState("");
  const [dataDir, setDataDir] = useState("");

  useEffect(() => {
    setCanvasUrl(String(settings.canvas_url || ""));
    setDataDir(String(settings.data_dir || ""));
  }, [settings]);

  return (
    <div className="space-y-6 max-w-2xl">
      <section>
        <h2 className="text-lg font-semibold mb-3" role="heading" aria-level={2}>Canvas Integration</h2>
        <label className="block text-sm text-gray-400 mb-1" htmlFor="canvas-url">Canvas URL</label>
        <input
          id="canvas-url"
          type="text"
          value={canvasUrl}
          onChange={(e) => setCanvasUrl(e.target.value)}
          placeholder="https://canvas.instructure.com"
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <label className="block text-sm text-gray-400 mb-1 mt-3" htmlFor="canvas-token">Canvas Token {settings.canvas_token_masked ? `(current: ${settings.canvas_token_masked})` : ""}</label>
        <input
          id="canvas-token"
          type="password"
          value={canvasToken}
          onChange={(e) => setCanvasToken(e.target.value)}
          placeholder="Paste new Canvas token"
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">Leave blank to keep existing token. Get your token from Canvas → Settings → Approved Integration Tokens.</p>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3" role="heading" aria-level={2}>Triage Thresholds</h2>
        <label className="block text-sm text-gray-400 mb-1" htmlFor="auto-thresh">Auto-approve threshold</label>
        <input
          id="auto-thresh"
          type="number"
          step="0.05"
          min="0.5"
          max="1.0"
          defaultValue={String(settings.auto_approve_threshold ?? 0.95)}
          className="w-32 px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">AI drafts above this confidence are marked high-confidence (still require your approval).</p>
        <label className="block text-sm text-gray-400 mb-1 mt-3" htmlFor="review-thresh">Review-first threshold</label>
        <input
          id="review-thresh"
          type="number"
          step="0.05"
          min="0.0"
          max="1.0"
          defaultValue={String(settings.review_first_threshold ?? 0.70)}
          className="w-32 px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">AI drafts below this confidence are flagged for review first.</p>
      </section>

      <button
        onClick={() => onSave({
          canvas_url: canvasUrl,
          canvas_token: canvasToken,
          data_dir: dataDir,
          auto_approve_threshold: Number((document.getElementById("auto-thresh") as HTMLInputElement).value),
          review_first_threshold: Number((document.getElementById("review-thresh") as HTMLInputElement).value),
        })}
        className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-sm font-medium"
      >
        Save Settings
      </button>
    </div>
  );
}

function ModelsPanel({ settings, onSave }: {
  settings: Record<string, string | number>;
  onSave: (s: Record<string, string | number>) => void;
}) {
  const [textModel, setTextModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [embedModel, setEmbedModel] = useState("");

  useEffect(() => {
    setTextModel(String(settings.model_text || "grader-text:8b"));
    setVisionModel(String(settings.model_vision || "grader-vision:1.3b"));
    setEmbedModel(String(settings.model_embed || "nomic-embed-text:latest"));
  }, [settings]);

  return (
    <div className="space-y-6 max-w-2xl">
      <section>
        <h2 className="text-lg font-semibold mb-3" role="heading" aria-level={2}>Model Configuration</h2>
        <label className="block text-sm text-gray-400 mb-1" htmlFor="text-model">Text grading model</label>
        <input
          id="text-model"
          type="text"
          value={textModel}
          onChange={(e) => setTextModel(e.target.value)}
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">Used for grading text submissions. Recommended: grader-text:8b (capped Qwen3 8B).</p>

        <label className="block text-sm text-gray-400 mb-1 mt-3" htmlFor="vision-model">Vision/handwriting model</label>
        <input
          id="vision-model"
          type="text"
          value={visionModel}
          onChange={(e) => setVisionModel(e.target.value)}
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">Used for transcribing handwritten submissions. Recommended: grader-vision:1.3b (capped MiniCPM-V).</p>

        <label className="block text-sm text-gray-400 mb-1 mt-3" htmlFor="embed-model">Embedding model</label>
        <input
          id="embed-model"
          type="text"
          value={embedModel}
          onChange={(e) => setEmbedModel(e.target.value)}
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-1">Used for exemplar retrieval. Recommended: nomic-embed-text.</p>
      </section>

      <button
        onClick={() => onSave({ model_text: textModel, model_vision: visionModel, model_embed: embedModel })}
        className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-sm font-medium"
      >
        Save Models
      </button>
    </div>
  );
}

function RubricsPanel({ rubrics, pasteText, setPasteText, pasteTitle, setPasteTitle, onSave, onLoad }: {
  rubrics: { title: string; version: number }[];
  pasteText: string;
  setPasteText: (s: string) => void;
  pasteTitle: string;
  setPasteTitle: (s: string) => void;
  onSave: () => void;
  onLoad: (title: string) => void;
}) {
  return (
    <div className="space-y-6 max-w-3xl">
      <section>
        <h2 className="text-lg font-semibold mb-3" role="heading" aria-level={2}>Saved Rubrics</h2>
        {rubrics.length === 0 ? (
          <p className="text-sm text-gray-500">No saved rubrics yet. Paste one below.</p>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-gray-500 border-b border-gray-800">
                <th className="py-2 px-2">Title</th>
                <th className="py-2 px-2">Version</th>
                <th className="py-2 px-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {rubrics.map((r) => (
                <tr key={r.title} className="border-b border-gray-800">
                  <td className="py-2 px-2 text-sm">{r.title}</td>
                  <td className="py-2 px-2 text-sm text-gray-400">v{r.version}</td>
                  <td className="py-2 px-2">
                    <button
                      onClick={() => onLoad(r.title)}
                      className="text-sm text-blue-400 hover:underline"
                    >
                      Load
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3" role="heading" aria-level={2}>Import Rubric</h2>
        <label className="block text-sm text-gray-400 mb-1" htmlFor="rubric-title">Title</label>
        <input
          id="rubric-title"
          type="text"
          value={pasteTitle}
          onChange={(e) => setPasteTitle(e.target.value)}
          placeholder="e.g. Essay Rubric v1"
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none mb-3"
        />
        <label className="block text-sm text-gray-400 mb-1" htmlFor="rubric-text">Rubric text (paste from Canvas, a table, or JSON)</label>
        <textarea
          id="rubric-text"
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder="Paste rubric criteria here..."
          rows={10}
          className="w-full px-3 py-2 bg-gray-800 rounded-lg text-sm border border-gray-700 focus:border-blue-500 focus:outline-none font-mono"
        />
        <button
          onClick={onSave}
          className="mt-3 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-sm font-medium"
        >
          Save Rubric
        </button>
      </section>
    </div>
  );
}