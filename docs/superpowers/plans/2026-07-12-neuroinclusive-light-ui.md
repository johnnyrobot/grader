# Neuroinclusive Light UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the complete Grader desktop interface as a calm, light-first, neuroinclusive workflow while preserving teacher approval and local-first safety behavior.

**Architecture:** Add a small persisted presentation-preference boundary that applies semantic CSS tokens at the document root. Rebuild the existing React screens around shared semantic classes and explicit information hierarchy, leaving all grading, Canvas, model, and settings API boundaries unchanged. Add component tests for preferences and critical workflows, then exercise the complete rendered app with mocked loopback APIs in Playwright.

**Tech Stack:** React 18, TypeScript 5.6, Tailwind CSS 3, Vitest, Testing Library, Playwright, Tauri 2, FastAPI.

## Global Constraints

- Light mode is the default; dark mode remains an optional persisted preference.
- Student work stays on-device and no grade posts without explicit teacher approval.
- Use only semantic presentation tokens in feature UI; do not fork components by theme.
- Status is never communicated by color alone.
- Respect `prefers-reduced-motion` and an explicit reduced-motion preference.
- Preserve keyboard operation, visible focus, plain-language recovery states, and existing engine/API contracts.
- Do not touch or commit `.omo/run-continuation/` operational state or unrelated root `AGENTS.md` edits.

---

### Task 1: Presentation preferences and test foundation

**Files:**
- Create: `app/src/theme.tsx`
- Create: `app/src/theme.test.tsx`
- Create: `app/src/test/setup.ts`
- Modify: `app/src/main.tsx`
- Modify: `app/src/index.css`
- Modify: `app/vite.config.ts`
- Modify: `app/package.json`
- Modify: `app/package-lock.json`

**Interfaces:**
- Produces: `ThemeProvider`, `useTheme()`, `ThemePreferences`, and `DEFAULT_THEME`.
- Persists: `grader_theme_preferences` in `localStorage`.
- Applies: `data-mode`, `data-density`, and `data-motion` to `document.documentElement`.

- [ ] **Step 1: Install Vitest, jsdom, and Testing Library development dependencies.**

Run: `cd app && npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event`

- [ ] **Step 2: Write failing preference tests.**

Test light defaults, local-storage restoration, root data attributes, and updates through `useTheme()`.

- [ ] **Step 3: Run the focused test and verify RED.**

Run: `cd app && npm test -- --run src/theme.test.tsx`
Expected: FAIL because `ThemeProvider` does not exist.

- [ ] **Step 4: Implement the preference boundary and semantic token foundation.**

Use this public contract:

```ts
export type ColorMode = "light" | "dark";
export type Density = "comfortable" | "spacious";
export type MotionPreference = "system" | "reduced";
export interface ThemePreferences { mode: ColorMode; density: Density; motion: MotionPreference }
export const DEFAULT_THEME: ThemePreferences = { mode: "light", density: "comfortable", motion: "system" };
export function ThemeProvider({ children }: { children: React.ReactNode }): JSX.Element;
export function useTheme(): { preferences: ThemePreferences; updatePreferences(next: Partial<ThemePreferences>): void; resetPreferences(): void };
```

- [ ] **Step 5: Run the focused test and verify GREEN.**

Run: `cd app && npm test -- --run src/theme.test.tsx`
Expected: PASS.

### Task 2: Light-first app shell and queue

**Files:**
- Create: `app/src/components/AppShell.tsx`
- Create: `app/src/screens/Queue.test.tsx`
- Modify: `app/src/App.tsx`
- Modify: `app/src/screens/Queue.tsx`
- Modify: `app/src/index.css`

**Interfaces:**
- `AppShell` consumes a page title, eyebrow, optional back action, and action content.
- Queue keeps the existing `QueueScreen` props and API calls.
- App stores onboarding completion in `grader_onboarding_complete` and does not skip first-run setup on a healthy engine.

- [ ] **Step 1: Write failing queue tests.**

Assert that lane controls expose pressed/selected state, every status has text, the primary action is “Grade submissions,” and an empty state explains the next action.

- [ ] **Step 2: Run the queue test and verify RED.**

Run: `cd app && npm test -- --run src/screens/Queue.test.tsx`
Expected: FAIL on the new labels and semantics.

- [ ] **Step 3: Implement the shared shell and queue redesign.**

Create a light paper-like workspace with a stable header, explicit assignment context, three semantic triage summaries, visible filters, responsive submission rows, and specific progress/error copy. Keep grading, refresh, settings, and model callbacks unchanged.

- [ ] **Step 4: Run queue and theme tests and verify GREEN.**

Run: `cd app && npm test -- --run src/theme.test.tsx src/screens/Queue.test.tsx`
Expected: PASS.

### Task 3: Grade review workflow

**Files:**
- Create: `app/src/screens/SplitView.test.tsx`
- Modify: `app/src/screens/SplitView.tsx`
- Modify: `app/src/index.css`

**Interfaces:**
- Preserve `SplitView({ item, onBack })`.
- Preserve `approveGrade(id)` and `overrideGrade(id, scores, feedback)` calls.

- [ ] **Step 1: Write failing review tests.**

Assert visible AI-draft labeling, text equivalents for flags/confidence, accessible score radios, editable feedback, approval confirmation, and the existing keyboard path.

- [ ] **Step 2: Run the focused review test and verify RED.**

Run: `cd app && npm test -- --run src/screens/SplitView.test.tsx`
Expected: FAIL on the redesigned structure or copy.

- [ ] **Step 3: Implement the review workspace.**

Use a responsive submission canvas and sticky rubric inspector, make the teacher decision the clear final step, show score totals, preserve input on errors, and keep Approve versus Save override consequences explicit.

- [ ] **Step 4: Run the focused review test and verify GREEN.**

Run: `cd app && npm test -- --run src/screens/SplitView.test.tsx`
Expected: PASS.

### Task 4: Onboarding, settings, and model manager

**Files:**
- Create: `app/src/screens/FirstRun.test.tsx`
- Create: `app/src/screens/Settings.test.tsx`
- Create: `app/src/screens/ModelManager.test.tsx`
- Modify: `app/src/screens/FirstRun.tsx`
- Modify: `app/src/screens/Settings.tsx`
- Modify: `app/src/screens/ModelManager.tsx`
- Modify: `app/src/index.css`

**Interfaces:**
- First run consumes `useTheme()` for live presentation choices.
- Settings gains an `appearance` tab while keeping general, models, and rubrics.
- Model manager keeps `fetchModels()` and `installModel()` behavior.

- [ ] **Step 1: Write failing tests for the three secondary surfaces.**

Assert one-decision-per-step onboarding with Back/Skip paths, live appearance controls and reset, explicit save/error status, model running state, install progress, and recovery copy.

- [ ] **Step 2: Run the focused tests and verify RED.**

Run: `cd app && npm test -- --run src/screens/FirstRun.test.tsx src/screens/Settings.test.tsx src/screens/ModelManager.test.tsx`
Expected: FAIL on new appearance and instructional behavior.

- [ ] **Step 3: Implement all three redesigned surfaces.**

Add a calm split-panel onboarding sequence: orientation, comfort choices, hardware readiness, and local-engine connection. Add an appearance settings panel with immediate preview and reset. Convert Model Manager into a full consistent workspace with human-readable model roles and local-processing explanation.

- [ ] **Step 4: Run the focused tests and verify GREEN.**

Run: `cd app && npm test -- --run src/screens/FirstRun.test.tsx src/screens/Settings.test.tsx src/screens/ModelManager.test.tsx`
Expected: PASS.

### Task 5: End-to-end evaluation and release checks

**Files:**
- Create: `app/e2e/neuroinclusive-ui.spec.ts`
- Modify: `app/package.json`
- Modify: `app/package-lock.json`
- Modify: `app/vite.config.ts`

**Interfaces:**
- Playwright mocks `http://127.0.0.1:8756` responses but exercises the real compiled React application.
- Screenshots are diagnostic artifacts only and are not committed.

- [ ] **Step 1: Add Playwright and axe development dependencies, then inspect the helper CLI.**

Run: `cd app && npm install --save-dev @playwright/test @axe-core/playwright`
Run: `python /Users/laccd/.codex/skills/anthropics__skills__webapp-testing/scripts/with_server.py --help`

- [ ] **Step 2: Write an end-to-end test before any E2E-specific production adjustment.**

Cover onboarding appearance selection, queue filtering, opening review, changing a criterion, saving an override, returning to queue, settings appearance persistence, model-manager status, keyboard focus visibility, no serious/critical axe findings, 200% zoom, and reduced motion.

- [ ] **Step 3: Run E2E and address only failures proven by the test.**

Run: `cd app && npx playwright test e2e/neuroinclusive-ui.spec.ts`
Expected: PASS.

- [ ] **Step 4: Run fresh complete verification.**

Run: `cd app && npm test -- --run`
Run: `cd app && npm run build`
Run: `cd app/src-tauri && cargo check`
Run: `uv run pytest engine/tests/ -q`
Expected: all commands exit 0; Python retains 26 passed and 12 skipped unless unrelated test counts changed.

- [ ] **Step 5: Review the final diff and requirements line by line.**

Run: `git status --short && git diff --check && git diff --stat`
Expected: only intended app, test, lockfile, and plan changes; no student data or unrelated operational state.
