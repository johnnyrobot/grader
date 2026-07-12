import { useState, useEffect } from "react";
import { fetchModels, installModel } from "../api";

interface ModelInfo {
  name: string;
  size: number;
}

const RECOMMENDED_16GB = ["grader-text:8b", "grader-vision:1.3b", "nomic-embed-text:latest"];

export function ModelManager({ onClose }: { onClose: () => void }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [running, setRunning] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  const refresh = async () => {
    const data = await fetchModels();
    setModels(data.available);
    setRunning(data.ollama_running);
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleInstall = async (name: string) => {
    setInstalling(name);
    setMsg(`Pulling ${name}...`);

    let modelName = name;
    if (name.startsWith("grader-text:")) {
      modelName = "qwen3:8b";
    } else if (name.startsWith("grader-vision:")) {
      modelName = "minicpm-v4.6:latest";
    }

    const result = await installModel(modelName);
    if (result.success) {
      if (modelName !== name) {
        setMsg(`${modelName} pulled. Run scripts/make_models.sh to bake the capped variant.`);
      } else {
        setMsg(`${name} installed successfully.`);
      }
    } else {
      setMsg(`Failed: ${result.error}`);
    }
    setInstalling(null);
    refresh();
  };

  const formatSize = (bytes: number) => {
    if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
    if (bytes > 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
    return `${bytes} B`;
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-850 rounded-2xl p-6 max-w-md w-full border border-gray-700">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Model Manager</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white">✕</button>
        </div>

        <div className="mb-4 flex items-center gap-2">
          <div className={`w-2.5 h-2.5 rounded-full ${running ? "bg-green-500" : "bg-red-500"}`} />
          <span className="text-sm text-gray-400">Ollama: {running ? "Running" : "Not detected"}</span>
        </div>

        <div className="space-y-2 mb-4">
          <h3 className="text-sm text-gray-500">Installed Grader Models</h3>
          {models.length === 0 ? (
            <p className="text-sm text-gray-600">No grader models found. Install recommended models below.</p>
          ) : (
            models.map((m) => (
              <div key={m.name} className="flex items-center justify-between bg-gray-800 rounded-lg px-4 py-2">
                <span className="text-sm text-white">{m.name}</span>
                <span className="text-xs text-gray-500">{formatSize(m.size)}</span>
              </div>
            ))
          )}
        </div>

        <div className="space-y-2">
          <h3 className="text-sm text-gray-500">Recommended (16 GB tier)</h3>
          {RECOMMENDED_16GB.map((name) => {
            const installed = models.some((m) => m.name === name);
            return (
              <div key={name} className="flex items-center justify-between bg-gray-800 rounded-lg px-4 py-2">
                <span className="text-sm text-white">{name}</span>
                {installed ? (
                  <span className="text-xs text-green-400">Installed</span>
                ) : (
                  <button
                    onClick={() => handleInstall(name)}
                    disabled={installing !== null}
                    className="px-3 py-1 rounded text-xs bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-white"
                  >
                    {installing === name ? "Pulling..." : "Install"}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {msg && <p className="mt-4 text-sm text-gray-400">{msg}</p>}
      </div>
    </div>
  );
}