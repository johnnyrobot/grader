import { useEffect, useState } from "react";
import { getSettings, updateSettings, listRubrics, getRubric, saveRubric } from "../api";
import { AppShell } from "../components/AppShell";
import { useTheme } from "../theme";

interface Props { onClose: () => void; }
type SettingsTab = "general" | "appearance" | "models" | "rubrics";

const TABS: { id: SettingsTab; label: string; description: string }[] = [
  { id: "general", label: "General", description: "Canvas and review thresholds" },
  { id: "appearance", label: "Appearance", description: "Mode, spacing, and motion" },
  { id: "models", label: "Model defaults", description: "Local grading model tags" },
  { id: "rubrics", label: "Rubrics", description: "Saved and imported rubrics" },
];

export function SettingsScreen({ onClose }: Props) {
  const [settings, setSettings] = useState<Record<string, string | number>>({});
  const [rubrics, setRubrics] = useState<{ title: string; version: number }[]>([]);
  const [message, setMessage] = useState("");
  const [activeTab, setActiveTab] = useState<SettingsTab>("general");
  const [pasteText, setPasteText] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");

  const refresh = async () => {
    try {
      const [nextSettings, nextRubrics] = await Promise.all([getSettings(), listRubrics()]);
      setSettings(nextSettings);
      setRubrics(nextRubrics);
    } catch (error) {
      setMessage(`Settings could not be loaded. ${(error as Error).message}`);
    }
  };

  useEffect(() => { refresh(); }, []);

  const handleSave = async (updates: Record<string, string | number>) => {
    try {
      await updateSettings(updates);
      setMessage("Settings saved on this Mac.");
      await refresh();
    } catch (error) {
      setMessage(`Settings were not saved. ${(error as Error).message}`);
    }
  };

  const handlePasteRubric = async () => {
    if (!pasteText.trim() || !pasteTitle.trim()) {
      setMessage("Add both a title and rubric text before saving.");
      return;
    }
    try {
      await saveRubric({ text: pasteText, title: pasteTitle });
      setMessage(`Rubric “${pasteTitle}” saved.`);
      setPasteText("");
      setPasteTitle("");
      await refresh();
    } catch (error) {
      setMessage(`Rubric was not saved. ${(error as Error).message}`);
    }
  };

  const loadRubric = async (title: string) => {
    try {
      const rubric = await getRubric(title);
      setPasteTitle(rubric.title);
      setPasteText(JSON.stringify(rubric, null, 2));
      setMessage(`Loaded “${title}” into the editor.`);
    } catch (error) {
      setMessage(`Rubric could not be loaded. ${(error as Error).message}`);
    }
  };

  return (
    <AppShell
      eyebrow="Workspace preferences"
      title="Settings"
      subtitle="Keep the interface comfortable, the grading rules explicit, and every local connection understandable."
      actions={<button className="button-secondary" onClick={onClose}>← Back to queue</button>}
      toolbar={(
        <nav className="settings-tabs" role="tablist" aria-label="Settings sections">
          {TABS.map(tab => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-label={tab.label}
              aria-selected={activeTab === tab.id}
              aria-controls={`${tab.id}-panel`}
              onClick={() => setActiveTab(tab.id)}
            >
              <strong>{tab.label}</strong><span>{tab.description}</span>
            </button>
          ))}
        </nav>
      )}
      wide={false}
    >
      {message && <div className="settings-message" role="status" aria-live="polite">{message}</div>}
      <div className="settings-panel paper-panel" role="tabpanel" id={`${activeTab}-panel`} aria-label={`${TABS.find(tab => tab.id === activeTab)?.label} settings`}>
        {activeTab === "general" && <GeneralPanel settings={settings} onSave={handleSave} />}
        {activeTab === "appearance" && <AppearancePanel />}
        {activeTab === "models" && <ModelsPanel settings={settings} onSave={handleSave} />}
        {activeTab === "rubrics" && (
          <RubricsPanel rubrics={rubrics} pasteText={pasteText} setPasteText={setPasteText} pasteTitle={pasteTitle} setPasteTitle={setPasteTitle} onSave={handlePasteRubric} onLoad={loadRubric} />
        )}
      </div>
    </AppShell>
  );
}

function PanelHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="settings-panel__header"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></header>;
}

function AppearancePanel() {
  const { preferences, updatePreferences, resetPreferences } = useTheme();
  return (
    <div>
      <PanelHeader eyebrow="Reading comfort" title="A workspace that fits how you read" description="Changes preview immediately and are stored only on this Mac." />
      <div className="settings-section">
        <fieldset className="choice-fieldset">
          <legend>Color mode</legend>
          <p className="field-help">Light mode is the calm default. Dark mode remains available for lower-light rooms.</p>
          <div className="preview-options preview-options--two">
            <label className="preview-choice preview-choice--light"><input type="radio" name="settings-mode" checked={preferences.mode === "light"} onChange={() => updatePreferences({ mode: "light" })} aria-label="Light mode" /><span className="preview-swatch"><i /><i /><i /></span><strong>Light</strong><small>Paper-like and bright</small></label>
            <label className="preview-choice preview-choice--dark"><input type="radio" name="settings-mode" checked={preferences.mode === "dark"} onChange={() => updatePreferences({ mode: "dark" })} aria-label="Dark mode" /><span className="preview-swatch"><i /><i /><i /></span><strong>Dark</strong><small>Lower ambient light</small></label>
          </div>
        </fieldset>
      </div>
      <div className="settings-section settings-grid--two">
        <fieldset className="choice-fieldset">
          <legend>Reading space</legend>
          <p className="field-help">Increase whitespace without hiding important controls.</p>
          <div className="segmented-options"><label><input type="radio" name="settings-density" checked={preferences.density === "comfortable"} onChange={() => updatePreferences({ density: "comfortable" })} aria-label="Comfortable density" /><span>Comfortable</span></label><label><input type="radio" name="settings-density" checked={preferences.density === "spacious"} onChange={() => updatePreferences({ density: "spacious" })} aria-label="Spacious density" /><span>Spacious</span></label></div>
        </fieldset>
        <div>
          <p className="field-label">Motion</p>
          <label className="check-choice"><input type="checkbox" checked={preferences.motion === "reduced"} onChange={event => updatePreferences({ motion: event.target.checked ? "reduced" : "system" })} aria-label="Reduce interface motion" /><span><strong>Reduce interface motion</strong><small>Disables non-essential transitions.</small></span></label>
        </div>
      </div>
      <div className="settings-actions"><span className="saved-locally">✓ Appearance is saved automatically</span><button type="button" className="button-secondary" onClick={resetPreferences}>Restore appearance defaults</button></div>
    </div>
  );
}

function GeneralPanel({ settings, onSave }: { settings: Record<string, string | number>; onSave: (settings: Record<string, string | number>) => void }) {
  const [canvasToken, setCanvasToken] = useState("");
  const [canvasUrl, setCanvasUrl] = useState("");
  const [dataDir, setDataDir] = useState("");
  const [highConfidence, setHighConfidence] = useState(0.95);
  const [reviewThreshold, setReviewThreshold] = useState(0.7);
  useEffect(() => {
    setCanvasUrl(String(settings.canvas_url || ""));
    setDataDir(String(settings.data_dir || ""));
    setHighConfidence(Number(settings.high_confidence_threshold ?? 0.95));
    setReviewThreshold(Number(settings.review_first_threshold ?? 0.7));
  }, [settings]);
  return (
    <div>
      <PanelHeader eyebrow="Connections and review" title="General grading settings" description="Canvas credentials stay local. Confidence lanes organize review but never replace approval." />
      <section className="settings-section" aria-labelledby="canvas-settings-heading">
        <h3 id="canvas-settings-heading">Canvas connection</h3>
        <div className="settings-form-grid">
          <label><span className="field-label">Canvas URL</span><input className="field-control" value={canvasUrl} onChange={event => setCanvasUrl(event.target.value)} placeholder="https://canvas.instructure.com" /></label>
          <label><span className="field-label">Canvas token {settings.canvas_token_masked ? `(current: ${settings.canvas_token_masked})` : ""}</span><input type="password" className="field-control" value={canvasToken} onChange={event => setCanvasToken(event.target.value)} placeholder="Paste a new token" /><small>Leave blank to keep the current token.</small></label>
          <label className="settings-span-two"><span className="field-label">Canvas attachment directory</span><input className="field-control" value={dataDir} onChange={event => setDataDir(event.target.value)} placeholder="Use the default Grader folder" /><small>New Canvas downloads use this folder. Core databases and the durable post queue are selected when the local engine starts.</small></label>
        </div>
      </section>
      <section className="settings-section" aria-labelledby="threshold-heading">
        <h3 id="threshold-heading">Review-lane thresholds</h3>
        <div className="settings-form-grid">
          <label><span className="field-label">High-confidence threshold</span><input type="number" min="0.5" max="1" step="0.05" className="field-control" value={highConfidence} onChange={event => setHighConfidence(Number(event.target.value))} /><small>Drafts at or above this value enter the confident lane and still require your approval.</small></label>
          <label><span className="field-label">Needs-review threshold</span><input type="number" min="0" max="1" step="0.05" className="field-control" value={reviewThreshold} onChange={event => setReviewThreshold(Number(event.target.value))} /><small>Below this value, drafts move to review first.</small></label>
        </div>
      </section>
      <div className="settings-actions"><span /><button className="button-primary" onClick={() => onSave({ canvas_url: canvasUrl, canvas_token: canvasToken, data_dir: dataDir, high_confidence_threshold: highConfidence, review_first_threshold: reviewThreshold })}>Save general settings</button></div>
    </div>
  );
}

function ModelsPanel({ settings, onSave }: { settings: Record<string, string | number>; onSave: (settings: Record<string, string | number>) => void }) {
  const [textModel, setTextModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [embedModel, setEmbedModel] = useState("");
  useEffect(() => { setTextModel(String(settings.model_text || "grader-text:8b")); setVisionModel(String(settings.model_vision || "grader-vision:1.3b")); setEmbedModel(String(settings.model_embed || "nomic-embed-text:latest")); }, [settings]);
  return (
    <div>
      <PanelHeader eyebrow="Local AI defaults" title="Model configuration" description="Use capped Grader tags so local memory use stays predictable." />
      <section className="settings-section settings-form-stack">
        <label><span className="field-label">Text grading model</span><input className="field-control" value={textModel} onChange={event => setTextModel(event.target.value)} /><small>Grades rubric criteria and writes feedback.</small></label>
        <label><span className="field-label">Vision and handwriting model</span><input className="field-control" value={visionModel} onChange={event => setVisionModel(event.target.value)} /><small>Transcribes first; it never grades directly.</small></label>
        <label><span className="field-label">Embedding model</span><input className="field-control" value={embedModel} onChange={event => setEmbedModel(event.target.value)} /><small>Finds relevant teacher-approved exemplars.</small></label>
      </section>
      <div className="settings-actions"><span /><button className="button-primary" onClick={() => onSave({ model_text: textModel, model_vision: visionModel, model_embed: embedModel })}>Save model defaults</button></div>
    </div>
  );
}

function RubricsPanel({ rubrics, pasteText, setPasteText, pasteTitle, setPasteTitle, onSave, onLoad }: { rubrics: { title: string; version: number }[]; pasteText: string; setPasteText: (value: string) => void; pasteTitle: string; setPasteTitle: (value: string) => void; onSave: () => void; onLoad: (title: string) => void }) {
  return (
    <div>
      <PanelHeader eyebrow="Assessment language" title="Rubric library" description="Keep versions visible and review imported criteria before using them for grading." />
      <section className="settings-section">
        <h3>Saved rubrics</h3>
        {rubrics.length === 0 ? <div className="compact-empty">No saved rubrics yet. Import one below.</div> : <div className="rubric-list">{rubrics.map(rubric => <div key={rubric.title}><div><strong>{rubric.title}</strong><span>Version {rubric.version}</span></div><button className="button-secondary" onClick={() => onLoad(rubric.title)}>Load into editor</button></div>)}</div>}
      </section>
      <section className="settings-section settings-form-stack">
        <h3>Import or update a rubric</h3>
        <label><span className="field-label">Rubric title</span><input className="field-control" value={pasteTitle} onChange={event => setPasteTitle(event.target.value)} placeholder="Essay rubric" /></label>
        <label><span className="field-label">Rubric text</span><textarea className="field-control rubric-editor" rows={10} value={pasteText} onChange={event => setPasteText(event.target.value)} placeholder="Paste criteria from Canvas, a table, or JSON" /></label>
      </section>
      <div className="settings-actions"><span /><button className="button-primary" onClick={onSave}>Save rubric</button></div>
    </div>
  );
}
