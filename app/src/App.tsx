import { useState, useEffect, useCallback } from "react";
import { checkHealth, setBaseUrl, setToken, fetchQueue, GradeItem, QueueData, connectWebSocket } from "./api";
import { QueueScreen } from "./screens/Queue";
import { SplitView } from "./screens/SplitView";
import { FirstRunWizard } from "./screens/FirstRun";
import { ModelManager } from "./screens/ModelManager";
import { SettingsScreen } from "./screens/Settings";
import { CalibrationScreen } from "./screens/Calibration";

type Screen = "wizard" | "queue" | "split" | "models" | "settings" | "calibration";
type EngineInfo = { token: string | null; port: string | null };

function applyEngineInfo(info: Record<string, string>): EngineInfo {
  if (info.port && info.port !== "undefined") {
    localStorage.setItem("grader_engine_port", info.port);
    setBaseUrl(`http://127.0.0.1:${info.port}`);
  }
  if (info.token && info.token !== "undefined") {
    localStorage.setItem("grader_engine_token", info.token);
  }
  return {
    token: info.token && info.token !== "undefined" ? info.token : null,
    port: info.port && info.port !== "undefined" ? info.port : null,
  };
}

async function getEngineInfo(): Promise<EngineInfo> {
  try {
    const tauri = await import("@tauri-apps/api/core");
    const info = await tauri.invoke("get_engine_info") as Record<string, string>;
    return applyEngineInfo(info);
  } catch {
    // Browser/dev mode — no Tauri runtime
  }
  const storedPort = localStorage.getItem("grader_engine_port");
  if (storedPort) setBaseUrl(`http://127.0.0.1:${storedPort}`);
  return { token: localStorage.getItem("grader_engine_token"), port: storedPort };
}

export default function App() {
  const onboardingComplete = localStorage.getItem("grader_onboarding_complete") === "true";
  const [screen, setScreen] = useState<Screen>(onboardingComplete ? "queue" : "wizard");
  const [engineReady, setEngineReady] = useState(false);
  const [queueData, setQueueData] = useState<QueueData | null>(null);
  const [currentItem, setCurrentItem] = useState<GradeItem | null>(null);
  const [assignmentId, setAssignmentId] = useState("default");
  const [selectedLane, setSelectedLane] = useState<string | undefined>(undefined);
  const [engineIssue, setEngineIssue] = useState("");
  const [recoveringEngine, setRecoveringEngine] = useState(false);

  const checkEngine = useCallback(async () => {
    const info = await getEngineInfo();
    if (info.token) setToken(info.token);

    for (let i = 0; i < 10; i++) {
      const ok = await checkHealth();
      if (ok) {
        setEngineReady(true);
        setEngineIssue("");
        if (onboardingComplete) setScreen("queue");
        return;
      }
      await new Promise(r => setTimeout(r, 1000));
    }
  }, []);

  const refreshQueue = useCallback(async () => {
    try {
      const data = await fetchQueue(assignmentId, selectedLane);
      setQueueData(data);
      setEngineIssue("");
    } catch (error) {
      setEngineIssue(`Local engine connection failed. ${(error as Error).message}`);
    }
  }, [assignmentId, selectedLane]);

  useEffect(() => {
    checkEngine();
  }, []);

  useEffect(() => {
    if (!engineReady) return;
    const ws = connectWebSocket((msg) => {
      if (msg.type === "job_done" || msg.type === "job_progress") {
        refreshQueue();
      }
    });
    return () => ws.close();
  }, [engineReady, refreshQueue]);

  useEffect(() => {
    if (engineReady) refreshQueue();
  }, [engineReady, refreshQueue]);

  useEffect(() => {
    if (!engineReady) return;
    const interval = window.setInterval(async () => {
      const ok = await checkHealth();
      if (!ok) setEngineIssue("Local engine connection lost. Restart the engine, then continue reviewing.");
    }, 5000);
    return () => window.clearInterval(interval);
  }, [engineReady]);

  const recoverEngine = useCallback(async () => {
    setRecoveringEngine(true);
    setEngineIssue("Restarting the local engine…");
    try {
      const tauri = await import("@tauri-apps/api/core");
      const info = await tauri.invoke("restart_engine") as Record<string, string>;
      const applied = applyEngineInfo(info);
      if (applied.token) setToken(applied.token);
      for (let i = 0; i < 10; i++) {
        if (await checkHealth()) {
          setEngineReady(true);
          setEngineIssue("");
          await refreshQueue();
          return;
        }
        await new Promise(r => setTimeout(r, 1000));
      }
      setEngineIssue("The local engine restarted, but it did not become ready in time.");
    } catch (error) {
      setEngineIssue(`Engine restart failed. ${(error as Error).message}`);
    } finally {
      setRecoveringEngine(false);
    }
  }, [refreshQueue]);

  const handleWizardComplete = async (t: string) => {
    const info = await getEngineInfo();
    setToken(info.token || t);
    localStorage.setItem("grader_onboarding_complete", "true");
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

  if (screen === "calibration") {
    return <CalibrationScreen assignmentId={assignmentId} onClose={() => setScreen("queue")} />;
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
      engineIssue={engineIssue}
      recoveringEngine={recoveringEngine}
      onRecoverEngine={recoverEngine}
      onOpenModels={() => setScreen("models")}
      onOpenSettings={() => setScreen("settings")}
      onOpenCalibration={() => setScreen("calibration")}
    />
  );
}
