import type { ReactNode } from "react";

interface AppShellProps {
  eyebrow: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}

export function AppShell({ eyebrow, title, subtitle, actions, toolbar, children, footer, wide = true }: AppShellProps) {
  return (
    <div className="app-canvas">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="app-header" role="banner">
        <div className="app-header__brand" aria-label="Grader, private grading workspace">
          <span className="brand-mark" aria-hidden="true">G</span>
          <div>
            <span className="eyebrow">Private grading workspace</span>
            <p className="brand-name">Grader</p>
          </div>
        </div>
        {actions && <div className="app-header__actions">{actions}</div>}
      </header>
      {toolbar && <div className="app-toolbar">{toolbar}</div>}
      <main id="main-content" className={wide ? "app-main app-main--wide" : "app-main"}>
        <div className="page-intro">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h1 className="display-title">{title}</h1>
            {subtitle && <p className="page-subtitle">{subtitle}</p>}
          </div>
        </div>
        {children}
      </main>
      {footer && <footer className="app-footer">{footer}</footer>}
    </div>
  );
}
