import { useState, useEffect, useCallback } from "react";
import { checkHealth, setBaseUrl, setToken, fetchQueue, GradeItem, QueueData, connectWebSocket } from "./api";
import { QueueScreen } from "./screens/Queue";
import { SplitView } from "./screens/SplitView";
import { FirstRunWizard } from "./screens/FirstRun";
import { ModelManager } from "./screens/ModelManager";
import { SettingsScreen } from "./screens/Settings";
import { CalibrationScreen } from "./screens/Calibration";
import { EngineUnavailable, EngineFailure } from "./screens/EngineUnavailable";

type Screen = "wizard" | "queue" | "split" | "models" | "settings" | "calibration";
type EngineInfo = { token: string | null; port: string | null };

/**
 * How long the engine gets to answer before the wait is reported as a failure.
 *
 * Generous on purpose. A first launch on an unfamiliar Mac pays for Gatekeeper verifying
 * every signed binary in the bundle and for the engine importing a large dependency tree
 * from cold storage. Calling that a failure after a few seconds would report a working
 * engine as broken — the opposite of the #23 defect, and just as misleading.
 */
const ENGINE_STARTUP_BUDGET_MS = 120_000;
const ENGINE_POLL_INTERVAL_MS = 1_000;
/** How often to keep looking after the wait has already been reported as a failure. */
const ENGINE_RECHECK_INTERVAL_MS = 3_000;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * The engine's own account of why it is not running, if it has one. A failure that
 * reaches only stderr is invisible to anyone who launched Grader from Finder (#23),
 * so the shell hands it to the window.
 */
function readEngineFailure(info: Record<string, string>): EngineFailure {
  return {
    kind: info.error_kind || "",
    message: info.error_message || "",
    detail: info.error_detail || "",
    logPath: info.log_path || "",
  };
}

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

async function getEngineInfoMap(): Promise<Record<string, string>> {
  try {
    const tauri = await import("@tauri-apps/api/core");
    return await tauri.invoke("get_engine_info") as Record<string, string>;
  } catch {
    // Browser/dev mode — no Tauri runtime
    return {};
  }
}

/**
 * Wait for the engine to answer. Resolves `null` once it does.
 *
 * Returns a failure early when the shell already knows the engine cannot start — an
 * absent bundle will not fix itself, so there is nothing to wait for. Otherwise it waits
 * out the whole budget before reporting that the engine never became ready.
 */
async function awaitEngine(budgetMs: number): Promise<EngineFailure | null> {
  const deadline = Date.now() + budgetMs;
  for (;;) {
    if (await checkHealth()) return null;

    const failure = readEngineFailure(await getEngineInfoMap());
    if (failure.kind) return failure;
    if (Date.now() >= deadline) return failure;

    await sleep(ENGINE_POLL_INTERVAL_MS);
  }
}

async function getEngineInfo(): Promise<EngineInfo> {
  const info = await getEngineInfoMap();
  if (info.port || info.token) return applyEngineInfo(info);

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
  const [engineFailure, setEngineFailure] = useState<EngineFailure | null>(null);

  /** Take up a live engine: adopt its token, clear any failure, and show the app. */
  const adoptEngine = useCallback(async () => {
    const info = await getEngineInfo();
    if (info.token) setToken(info.token);
    setEngineReady(true);
    setEngineIssue("");
    setEngineFailure(null);
    if (onboardingComplete) setScreen("queue");
  }, [onboardingComplete]);

  const checkEngine = useCallback(async () => {
    const info = await getEngineInfo();
    if (info.token) setToken(info.token);

    const failure = await awaitEngine(ENGINE_STARTUP_BUDGET_MS);
    if (!failure) {
      await adoptEngine();
      return;
    }

    // The engine did not answer. Say why — an unexplained spinner is what made #23
    // present as a hang rather than an error.
    setEngineFailure(failure);
  }, [adoptEngine]);

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

  // Keep looking after the wait has been reported as a failure. An engine that was merely
  // slow still becomes usable, and the teacher should not have to press anything for that.
  useEffect(() => {
    if (!engineFailure || recoveringEngine) return;
    let watching = true;
    const interval = window.setInterval(async () => {
      if (!watching) return;
      if (await checkHealth()) await adoptEngine();
    }, ENGINE_RECHECK_INTERVAL_MS);
    return () => {
      watching = false;
      window.clearInterval(interval);
    };
  }, [engineFailure, recoveringEngine, adoptEngine]);

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
      // Never tear down an engine that is answering. A slow start is indistinguishable
      // from a failure here, and restarting would kill the process being waited on.
      if (await checkHealth()) {
        await adoptEngine();
        await refreshQueue();
        return;
      }

      const tauri = await import("@tauri-apps/api/core");
      const info = await tauri.invoke("restart_engine") as Record<string, string>;
      const applied = applyEngineInfo(info);
      if (applied.token) setToken(applied.token);

      const failure = await awaitEngine(ENGINE_STARTUP_BUDGET_MS);
      if (!failure) {
        await adoptEngine();
        await refreshQueue();
        return;
      }
      setEngineIssue("The local engine restarted, but it did not become ready in time.");
      setEngineFailure(failure);
    } catch (error) {
      // `restart_engine` rejects with the engine's own explanation, as a plain string.
      const detail = error instanceof Error ? error.message : String(error);
      setEngineIssue(`Engine restart failed. ${detail}`);
      setEngineFailure(previous => ({
        kind: "restart_failed",
        message: "Grader could not restart its grading engine.",
        detail,
        logPath: previous?.logPath ?? "",
      }));
    } finally {
      setRecoveringEngine(false);
    }
  }, [refreshQueue, adoptEngine]);

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

  // A stated failure takes precedence over any waiting state, including onboarding.
  if (engineFailure) {
    return <EngineUnavailable error={engineFailure} retrying={recoveringEngine} onRetry={recoverEngine} />;
  }

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
