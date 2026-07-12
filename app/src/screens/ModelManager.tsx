import { useEffect, useState } from "react";
import { fetchModels, installModel } from "../api";
import { AppShell } from "../components/AppShell";

interface ModelInfo { name: string; size: number; }

const RECOMMENDED = [
  { tag: "grader-text:8b", pull: "qwen3:8b", name: "Text grader", role: "Grades text submissions", description: "Applies rubric criteria and drafts feedback using the capped local context." },
  { tag: "grader-vision:1.3b", pull: "minicpm-v4.6:latest", name: "Handwriting reader", role: "Reads handwriting and scans", description: "Transcribes pages locally, then hands text to the normal grading model." },
  { tag: "nomic-embed-text:latest", pull: "nomic-embed-text:latest", name: "Exemplar finder", role: "Finds similar approved work", description: "Retrieves teacher-approved anchors without sending student work away." },
];

function formatSize(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

export function ModelManager({ onClose }: { onClose: () => void }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const refresh = async () => {
    try {
      const data = await fetchModels();
      setModels(data.available);
      setRunning(data.ollama_running);
    } catch (error) {
      setMessage(`Model status could not be loaded. ${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const handleInstall = async (tag: string, pull: string) => {
    setInstalling(tag);
    setMessage(`Downloading ${pull}. Keep Grader open; this can take several minutes.`);
    try {
      const result = await installModel(pull);
      if (!result.success) throw new Error(result.error || "download failed");
      setMessage(`${pull} downloaded. ${pull === tag ? "The model is ready." : `Run scripts/make_models.sh to create the capped ${tag} tag.`}`);
      await refresh();
    } catch (error) {
      setMessage(`${pull} was not installed. ${(error as Error).message}`);
    } finally {
      setInstalling(null);
    }
  };

  return (
    <AppShell
      eyebrow="On-device AI"
      title="Local models"
      subtitle="See what each model does, what is installed, and what Grader still needs—all without sending student work to a cloud service."
      actions={<button className="button-secondary" onClick={onClose}>← Back to queue</button>}
      wide={false}
    >
      <section className="runtime-banner paper-panel" aria-labelledby="runtime-heading">
        <span className={`runtime-mark ${running ? "runtime-mark--ready" : "runtime-mark--offline"}`} aria-hidden="true">{running ? "✓" : "!"}</span>
        <div><p className="eyebrow">Local runtime</p><h2 id="runtime-heading">{loading ? "Checking Ollama…" : running ? "Ollama is running" : "Ollama is not detected"}</h2><p>{running ? "Models can run privately on this Mac." : "Start Ollama, then refresh this screen before grading."}</p></div>
        <button className="button-secondary" onClick={refresh} disabled={loading}>Refresh status</button>
      </section>

      {message && <div className="settings-message" role="status" aria-live="polite">{message}</div>}

      <section className="model-section" aria-labelledby="recommended-models-heading">
        <div className="section-heading"><div><p className="eyebrow">Recommended set</p><h2 id="recommended-models-heading">Three focused local jobs</h2></div><p className="muted">Recommended for a 16 GB Mac</p></div>
        <div className="model-grid">
          {RECOMMENDED.map((model, index) => {
            const installed = models.some(item => item.name === model.tag);
            return (
              <article key={model.tag} className="model-card paper-panel">
                <div className="model-card__number" aria-hidden="true">0{index + 1}</div>
                <div className="model-card__heading"><div><p className="eyebrow">{model.name}</p><h3>{model.role}</h3></div><span className={`status-pill ${installed ? "status-confident" : "status-standard"}`}>{installed ? "Installed" : "Needed"}</span></div>
                <p>{model.description}</p>
                <code>{model.tag}</code>
                {installed ? (
                  <div className="installed-note"><span aria-hidden="true">✓</span> Ready for Grader</div>
                ) : (
                  <button className="button-primary model-install" onClick={() => handleInstall(model.tag, model.pull)} disabled={installing !== null || !running} aria-label={model.tag === "grader-vision:1.3b" ? "Install handwriting model" : `Install ${model.name.toLowerCase()} model`}>
                    {installing === model.tag ? "Downloading…" : `Install ${model.name.toLowerCase()}`}
                  </button>
                )}
              </article>
            );
          })}
        </div>
      </section>

      <section className="inventory-section paper-panel" aria-labelledby="inventory-heading">
        <div className="section-heading"><div><p className="eyebrow">Inventory</p><h2 id="inventory-heading">Installed Grader models</h2></div><span className="muted">{models.length} found</span></div>
        {models.length === 0 ? <div className="compact-empty">No Grader model tags were found. Install the recommended set above.</div> : <div className="model-inventory">{models.map(model => <div key={model.name}><span className="runtime-mark runtime-mark--ready" aria-hidden="true">✓</span><div><strong>{model.name}</strong><span>Stored locally</span></div><code>{formatSize(model.size)}</code></div>)}</div>}
      </section>

      <aside className="local-note soft-panel"><span aria-hidden="true">⌂</span><div><strong>Local-first boundary</strong><p>Vision transcribes. The text model grades. Student files and generated drafts remain on this Mac.</p></div></aside>
    </AppShell>
  );
}
