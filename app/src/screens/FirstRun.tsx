import { useState, useEffect } from "react";
import { checkHealth } from "../api";

interface Props {
  onEngineReady: () => void;
  onComplete: (token: string) => void;
}

export function FirstRunWizard({ onEngineReady, onComplete }: Props) {
  const [step, setStep] = useState(0);
  const [token, setTokenInput] = useState("");
  const [checking, setChecking] = useState(false);
  const [ramTier, setRamTier] = useState<"16" | "24" | "unknown">("unknown");
  const [healthOk, setHealthOk] = useState(false);

  useEffect(() => {
    const nav = (navigator as any);
    const ram = nav.deviceMemory || nav.hardwareConcurrency || 0;
    if (ram >= 8) setRamTier("16");
    else if (ram >= 4) setRamTier("unknown");
    else setRamTier("unknown");

    const interval = setInterval(async () => {
      const ok = await checkHealth();
      setHealthOk(ok);
      if (ok) {
        clearInterval(interval);
        onEngineReady();
      }
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const handleStart = () => {
    if (token.trim()) {
      onComplete(token.trim());
    } else {
      onComplete("dev-token");
    }
  };

  const steps = [
    {
      title: "Welcome to Grader",
      body: (
        <div className="space-y-3">
          <p className="text-gray-300">
            A private AI grading copilot that grades like <span className="font-semibold">you</span>.
          </p>
          <p className="text-sm text-gray-400">
            Student work never leaves your machine. You approve every score.
          </p>
        </div>
      ),
    },
    {
      title: "Hardware Check",
      body: (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${healthOk ? "bg-green-500" : "bg-yellow-500 animate-pulse"}`} />
            <span className="text-gray-300">
              Engine: {healthOk ? "Connected" : "Waiting for engine..."}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 rounded-full bg-blue-500" />
            <span className="text-gray-300">RAM tier: {ramTier === "unknown" ? "Detecting..." : `${ramTier} GB`}</span>
          </div>
          <p className="text-sm text-gray-400 mt-2">
            Recommended models: grader-text:8b (grading) + grader-vision:1.3b (handwriting)
          </p>
        </div>
      ),
    },
    {
      title: "Engine Connection",
      body: (
        <div className="space-y-4">
          <p className="text-gray-300">
            The engine is running locally. Enter the connection token (or skip for dev mode):
          </p>
          <input
            type="text"
            value={token}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Engine token (printed on engine startup)"
            className="w-full px-4 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none"
          />
          <button
            onClick={handleStart}
            disabled={!healthOk}
            className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium transition-colors"
          >
            {healthOk ? "Start Grading" : "Waiting for engine..."}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center">
      <div className="max-w-lg w-full bg-gray-850 rounded-2xl p-8 shadow-2xl">
        <h1 className="text-2xl font-bold text-white mb-1">Grader</h1>
        <div className="flex gap-2 mb-6">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full ${i <= step ? "bg-blue-500" : "bg-gray-700"}`}
            />
          ))}
        </div>
        <div className="mb-8">
          <h2 className="text-xl font-semibold text-white mb-3">{steps[step].title}</h2>
          {steps[step].body}
        </div>
        {step < steps.length - 1 && (
          <button
            onClick={() => setStep(step + 1)}
            disabled={step === 1 && !healthOk}
            className="px-6 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white transition-colors"
          >
            Next
          </button>
        )}
      </div>
    </div>
  );
}