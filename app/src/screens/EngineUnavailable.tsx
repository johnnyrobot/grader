export interface EngineFailure {
  kind: string;
  message: string;
  detail: string;
  logPath: string;
}

interface Props {
  error: EngineFailure;
  retrying: boolean;
  onRetry: () => void;
}

const FALLBACK_MESSAGE = "The local engine did not become ready.";
const FALLBACK_DETAIL =
  "Grader started, but its engine never answered on this Mac. Try starting it again. If it keeps failing, reinstall Grader from the signed release.";

/**
 * Shown when the engine cannot start. An engine failure used to reach only stderr,
 * which is invisible to anyone who launched Grader from Finder — the app simply waited
 * (#23). This screen is the difference between a hang and an error.
 */
export function EngineUnavailable({ error, retrying, onRetry }: Props) {
  const message = error.message || FALLBACK_MESSAGE;
  const detail = error.detail || FALLBACK_DETAIL;

  return (
    <main className="onboarding-shell">
      <section className="onboarding-brand" aria-label="Grader">
        <div className="onboarding-brand__top">
          <span className="brand-mark" aria-hidden="true">G</span>
          <div><p className="eyebrow">Grader</p><p className="onboarding-brand__name">A calmer review loop</p></div>
        </div>
        <p>Grading runs on this Mac, so Grader needs its local engine before it can show you any work.</p>
        <div className="onboarding-brand__privacy"><span aria-hidden="true">⌂</span><strong>No student work has left this Mac</strong></div>
      </section>

      <section className="onboarding-card" aria-labelledby="engine-failure-title">
        <div className="onboarding-content">
          <p className="eyebrow">Local engine</p>
          <h1 id="engine-failure-title" className="display-title">The grading engine did not start</h1>

          <div className="engine-failure" role="alert">
            <p className="engine-failure__message">{message}</p>
            <p className="engine-failure__detail">{detail}</p>
          </div>

          {error.logPath && (
            <div className="soft-panel engine-failure__log">
              <strong>Engine log</strong>
              <p>Include this file if you report the problem.</p>
              <code>{error.logPath}</code>
            </div>
          )}
        </div>

        <div className="onboarding-actions">
          <span />
          <div>
            <button type="button" className="button-primary" onClick={onRetry} disabled={retrying}>
              {retrying ? "Starting…" : "Try starting it again"}
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
