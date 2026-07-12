import { useEffect, useRef, useState } from "react";
import { checkHealth } from "../api";
import { useTheme } from "../theme";

interface Props {
  onEngineReady: () => void;
  onComplete: (token: string) => void;
}

export function FirstRunWizard({ onEngineReady, onComplete }: Props) {
  const [step, setStep] = useState(0);
  const [token, setTokenInput] = useState("");
  const [ramTier, setRamTier] = useState<"16" | "24" | "unknown">("unknown");
  const [healthOk, setHealthOk] = useState(false);
  const notifiedReady = useRef(false);
  const { preferences, updatePreferences } = useTheme();

  useEffect(() => {
    const nav = navigator as Navigator & { deviceMemory?: number };
    const memory = nav.deviceMemory || 0;
    if (memory >= 24) setRamTier("24");
    else if (memory >= 8) setRamTier("16");

    let active = true;
    const poll = async () => {
      const ok = await checkHealth();
      if (!active) return;
      setHealthOk(ok);
      if (ok && !notifiedReady.current) {
        notifiedReady.current = true;
        onEngineReady();
      }
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [onEngineReady]);

  const finish = () => onComplete(token.trim() || "dev-token");

  const steps = [
    {
      title: "Your grading stays yours",
      eyebrow: "Welcome",
      content: (
        <div className="onboarding-copy">
          <p className="onboarding-lede">Grader prepares rubric-aligned drafts on this Mac so you can spend your attention on judgment, feedback, and exceptions.</p>
          <ul className="trust-list">
            <li><span aria-hidden="true">01</span><div><strong>Private by default</strong><p>Student work stays on your device.</p></div></li>
            <li><span aria-hidden="true">02</span><div><strong>You make the decision</strong><p>Every score remains a draft until you approve it.</p></div></li>
            <li><span aria-hidden="true">03</span><div><strong>Originals stay intact</strong><p>Annotations are written to a new copy.</p></div></li>
          </ul>
        </div>
      ),
    },
    {
      title: "Choose a comfortable view",
      eyebrow: "Appearance",
      content: (
        <div className="appearance-setup">
          <p className="step-explainer">These choices update the preview now and remain editable in Settings.</p>
          <fieldset className="choice-fieldset">
            <legend>Color mode</legend>
            <div className="preview-options preview-options--two">
              <label className="preview-choice preview-choice--light">
                <input type="radio" name="mode" checked={preferences.mode === "light"} onChange={() => updatePreferences({ mode: "light" })} aria-label="Light mode" />
                <span className="preview-swatch"><i /><i /><i /></span>
                <strong>Light</strong><small>Paper-like and bright</small>
              </label>
              <label className="preview-choice preview-choice--dark">
                <input type="radio" name="mode" checked={preferences.mode === "dark"} onChange={() => updatePreferences({ mode: "dark" })} aria-label="Dark mode" />
                <span className="preview-swatch"><i /><i /><i /></span>
                <strong>Dark</strong><small>Lower ambient light</small>
              </label>
            </div>
          </fieldset>
          <fieldset className="choice-fieldset">
            <legend>Reading space</legend>
            <div className="segmented-options">
              <label><input type="radio" name="density" checked={preferences.density === "comfortable"} onChange={() => updatePreferences({ density: "comfortable" })} aria-label="Comfortable density" /><span>Comfortable</span></label>
              <label><input type="radio" name="density" checked={preferences.density === "spacious"} onChange={() => updatePreferences({ density: "spacious" })} aria-label="Spacious density" /><span>Spacious</span></label>
            </div>
          </fieldset>
          <label className="check-choice">
            <input type="checkbox" checked={preferences.motion === "reduced"} onChange={event => updatePreferences({ motion: event.target.checked ? "reduced" : "system" })} aria-label="Reduce interface motion" />
            <span><strong>Reduce interface motion</strong><small>Turns off non-essential movement and animated transitions.</small></span>
          </label>
        </div>
      ),
    },
    {
      title: "Check this Mac",
      eyebrow: "Local readiness",
      content: (
        <div className="readiness-list">
          <div className="readiness-item">
            <span className={healthOk ? "readiness-icon readiness-icon--ready" : "readiness-icon readiness-icon--waiting"} aria-hidden="true">{healthOk ? "✓" : "…"}</span>
            <div><strong>Grading engine</strong><p>{healthOk ? "Connected on this Mac" : "Waiting for the local engine"}</p></div>
            <span className={`status-pill ${healthOk ? "status-confident" : "status-standard"}`}>{healthOk ? "Ready" : "Checking"}</span>
          </div>
          <div className="readiness-item">
            <span className="readiness-icon" aria-hidden="true">M</span>
            <div><strong>Memory tier</strong><p>{ramTier === "unknown" ? "Could not detect automatically" : `${ramTier} GB tier detected`}</p></div>
            <span className="status-pill status-info">Local</span>
          </div>
          <div className="model-note soft-panel">
            <strong>Recommended local pair</strong>
            <p><code>grader-text:8b</code> grades text. <code>grader-vision:1.3b</code> transcribes scans and handwriting before the text model grades.</p>
          </div>
        </div>
      ),
    },
    {
      title: "Connect the local engine",
      eyebrow: "Final step",
      content: (
        <div className="connection-step">
          <p className="step-explainer">Grader uses a per-launch token so only this desktop app can reach the local engine.</p>
          <label className="field-label" htmlFor="engine-token">Engine token</label>
          <input id="engine-token" type="text" value={token} onChange={event => setTokenInput(event.target.value)} placeholder="Token printed when the engine starts" className="field-control" />
          <p className="field-help">In development, leave this blank to use the local dev token.</p>
          <div className={`connection-status ${healthOk ? "connection-status--ready" : ""}`} role="status">
            <span aria-hidden="true">{healthOk ? "✓" : "…"}</span>
            <div><strong>{healthOk ? "Local engine connected" : "Waiting for the local engine"}</strong><p>{healthOk ? "You can start grading." : "Keep this window open while the engine starts."}</p></div>
          </div>
        </div>
      ),
    },
  ];

  const current = steps[step];
  return (
    <main className="onboarding-shell">
      <section className="onboarding-brand" aria-label="Grader privacy summary">
        <div className="onboarding-brand__top">
          <span className="brand-mark" aria-hidden="true">G</span>
          <div><p className="eyebrow">Grader</p><p className="onboarding-brand__name">A calmer review loop</p></div>
        </div>
        <blockquote>“Let the model prepare. Let the teacher decide.”</blockquote>
        <p>Local-first grading built around explicit approval, visible state, and recoverable work.</p>
        <div className="onboarding-brand__privacy"><span aria-hidden="true">⌂</span><strong>Student work remains on this Mac</strong></div>
      </section>

      <section className="onboarding-card" aria-labelledby="onboarding-title">
        <div className="onboarding-progress" aria-label={`Step ${step + 1} of ${steps.length}`}>
          <span>Step {step + 1} of {steps.length}</span>
          <div>{steps.map((_, index) => <i key={index} className={index <= step ? "is-active" : ""} />)}</div>
        </div>
        <div className="onboarding-content">
          <p className="eyebrow">{current.eyebrow}</p>
          <h1 id="onboarding-title" className="display-title">{current.title}</h1>
          {current.content}
        </div>
        <div className="onboarding-actions">
          {step > 0 ? <button type="button" className="button-quiet" onClick={() => setStep(step - 1)}>Back</button> : <span />}
          <div>
            {step === 1 && <button type="button" className="button-quiet" onClick={() => setStep(2)}>Skip appearance choices</button>}
            {step < steps.length - 1 ? (
              <button type="button" className="button-primary" onClick={() => setStep(step + 1)}>Continue</button>
            ) : (
              <button type="button" className="button-primary" onClick={finish} disabled={!healthOk}>{healthOk ? "Start grading" : "Waiting for engine…"}</button>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
