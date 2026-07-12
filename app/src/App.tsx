import { useState, useEffect, useCallback } from "react";
import { checkHealth, setToken, fetchQueue, GradeItem, QueueData, connectWebSocket } from "./api";
import { QueueScreen } from "./screens/Queue";
import { SplitView } from "./screens/SplitView";
import { FirstRunWizard } from "./screens/FirstRun";
import { ModelManager } from "./screens/ModelManager";
import { SettingsScreen } from "./screens/Settings";

type Screen = "wizard" | "queue" | "split" | "models" | "settings";

async function getEngineToken(): Promise<string | null> {
  try {
    const tauri = await import("@tauri-apps/api/core");
    const info = await tauri.invoke("get_engine_info") as Record<string, string>;
    if (info.token && info.token !== "undefined") {
      localStorage.setItem("grader_engine_token", info.token);
      return info.token;
    }
  } catch {
    // Browser/dev mode — no Tauri runtime
  }
  return localStorage.getItem("grader_engine_token");
}

export default function App() {
  const [screen, setScreen] = useState<Screen>("wizard");
  const [engineReady, setEngineReady] = useState(false);
  const [queueData, setQueueData] = useState<QueueData | null>(null);
  const [currentItem, setCurrentItem] = useState<GradeItem | null>(null);
  const [assignmentId, setAssignmentId] = useState("default");
  const [selectedLane, setSelectedLane] = useState<string | undefined>(undefined);

  const checkEngine = useCallback(async () => {
    const t = await getEngineToken();
    if (t) setToken(t);

    for (let i = 0; i < 10; i++) {
      const ok = await checkHealth();
      if (ok) {
        setEngineReady(true);
        setScreen("queue");
        return;
      }
      await new Promise(r => setTimeout(r, 1000));
    }
  }, []);

  useEffect(() => {
    checkEngine();
    const ws = connectWebSocket((msg) => {
      if (msg.type === "job_done" || msg.type === "job_progress") {
        refreshQueue();
      }
    });
    return () => ws.close();
  }, []);

  const refreshQueue = useCallback(async () => {
    try {
      const data = await fetchQueue(assignmentId, selectedLane);
      setQueueData(data);
    } catch {
      // engine not ready yet
    }
  }, [assignmentId, selectedLane]);

  useEffect(() => {
    if (engineReady) refreshQueue();
  }, [engineReady, refreshQueue]);

  const handleWizardComplete = async (t: string) => {
    const engineT = await getEngineToken();
    setToken(engineT || t);
    setEngineReady(true);
    setScreen("queue");
  };

  const handleSelectItem = (item: GradeItem) => {
    setCurrentItem(item);
    setScreen("split");
  };

  const handleBack = () => {
    setScreen("queue");
    setCurrentItem(null);
    refreshQueue();
  };

  if (screen === "wizard" || !engineReady) {
    return <FirstRunWizard onEngineReady={checkEngine} onComplete={handleWizardComplete} />;
  }

  if (screen === "split" && currentItem) {
    return <SplitView item={currentItem} onBack={handleBack} />;
  }

  if (screen === "models") {
    return <ModelManager onClose={() => setScreen("queue")} />;
  }

  if (screen === "settings") {
    return <SettingsScreen onClose={() => setScreen("queue")} />;
  }

  return (
    <QueueScreen
      data={queueData}
      onSelectItem={handleSelectItem}
      assignmentId={assignmentId}
      setAssignmentId={setAssignmentId}
      selectedLane={selectedLane}
      setSelectedLane={setSelectedLane}
      onRefresh={refreshQueue}
      onOpenModels={() => setScreen("models")}
      onOpenSettings={() => setScreen("settings")}
    />
  );
}