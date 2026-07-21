import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const instructorFixture = JSON.parse(readFileSync(join(__dirname, "fixtures/instructor-beta.json"), "utf8"));
const queue = instructorFixture.queue;
const visualEvidenceDir = join(__dirname, "..", "..", "artifacts", "release-evidence", "visual-sweep");
const visualEvidenceJson = join(__dirname, "..", "..", "artifacts", "release-evidence", "visual-sweep.json");

async function expectNoSeriousAccessibilityViolations(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = result.violations.filter(violation => ["serious", "critical"].includes(violation.impact || ""));
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}

async function collectControlInventory(page: Page) {
  return page.locator("button, input, textarea, select, a[href], [role='tab']").evaluateAll(elements => elements.map((element, index) => {
    const el = element as HTMLElement;
    const input = element as HTMLInputElement;
    const label = input.labels?.[0]?.textContent?.replace(/\s+/g, " ").trim();
    const rect = el.getBoundingClientRect();
    return {
      index,
      tag: element.tagName.toLowerCase(),
      type: input.type || null,
      role: el.getAttribute("role"),
      id: el.id || null,
      label: label || el.getAttribute("aria-label") || el.textContent?.replace(/\s+/g, " ").trim() || input.placeholder || null,
      ariaPressed: el.getAttribute("aria-pressed"),
      ariaSelected: el.getAttribute("aria-selected"),
      disabled: input.disabled || el.getAttribute("aria-disabled") === "true",
      visible: rect.width > 0 && rect.height > 0,
    };
  }));
}

async function captureVisualScreen(page: Page, name: string) {
  mkdirSync(visualEvidenceDir, { recursive: true });
  const screenshot = join(visualEvidenceDir, `${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  const controls = await collectControlInventory(page);
  return {
    name,
    screenshot,
    control_count: controls.length,
    visible_control_count: controls.filter(control => control.visible).length,
    controls,
  };
}

test.beforeEach(async ({ page }) => {
  await page.routeWebSocket("ws://127.0.0.1:8756/ws/jobs", () => {});
  await page.route("http://127.0.0.1:8756/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (value: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });

    if (url.pathname === "/health") return json({ ok: true });
    if (url.pathname === "/api/queue") return json(queue);
    if (url.pathname === "/api/updates/check") return json({ update_available: false, current_version: "0.1.0" });
    if (url.pathname === "/api/grades/grade-1/override") return json({ ai_total: 7, teacher_total: 8, refinement_proposals: [] });
    if (url.pathname === "/api/grades/grade-1/approve") return json({ success: true });
    if (url.pathname === "/api/grades/grade-3/override") return json({ ai_total: 6, teacher_total: 7, refinement_proposals: [] });
    if (url.pathname === "/api/grades/grade-3/approve") return json({ success: true });
    if (url.pathname === "/api/settings" && request.method() === "GET") return json(instructorFixture.settings);
    if (url.pathname === "/api/settings" && request.method() === "PUT") return json({ success: true });
    if (url.pathname === "/api/rubrics" && request.method() === "GET") return json(instructorFixture.rubrics);
    if (url.pathname.startsWith("/api/rubrics/") && request.method() === "GET") return json(instructorFixture.rubricDetail);
    if (url.pathname === "/api/rubrics" && request.method() === "POST") return json({ id: 12 });
    if (url.pathname === "/api/models") return json(instructorFixture.models);
    if (url.pathname === "/api/models/install") return json({ success: true });
    if (url.pathname === "/api/grade/batch") return json({ job_id: "job-1", total: 3 });
    return json({});
  });
});

test("complete neuroinclusive grading workflow", async ({ page }, testInfo) => {
  const consoleErrors: string[] = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });

  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Your grading stays yours" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-mode", "light");
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: testInfo.outputPath("onboarding-light.png"), fullPage: true });

  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("radio", { name: "Spacious density" }).check();
  await page.getByRole("checkbox", { name: "Reduce interface motion" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-density", "spacious");
  await expect(page.locator("html")).toHaveAttribute("data-motion", "reduced");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Check this Mac" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Local engine connected")).toBeVisible();
  await page.getByRole("button", { name: "Start grading" }).click();

  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();
  await expect(page.getByText("Needs teacher review").first()).toBeVisible();
  await page.getByRole("button", { name: "Needs review" }).click();
  await expect(page.getByRole("button", { name: "Needs review" })).toHaveAttribute("aria-pressed", "true");

  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: testInfo.outputPath("queue-light.png"), fullPage: true });

  await page.getByRole("button", { name: /Student 1042/ }).click();
  await expect(page.getByRole("heading", { name: "Review Student 1042" })).toBeVisible();
  await page.getByRole("radio", { name: "5 points for Thesis" }).check();
  const feedback = page.getByRole("textbox", { name: "Feedback for Student 1042" });
  await feedback.fill("Strong claim. Add one more cited example.");
  await page.getByRole("button", { name: "Save changes and approve" }).click();
  await expect(page.getByText("Your changes are approved. Score updated from 7 to 8.")).toBeVisible();
  await expect(page.getByText("Teacher changes not saved yet.")).toBeHidden();
  await expectNoSeriousAccessibilityViolations(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("review-light.png"), fullPage: true });
  await page.getByRole("button", { name: /Back to queue/ }).click();

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "Appearance" }).click();
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: testInfo.outputPath("settings-light.png"), fullPage: true });
  await page.getByRole("radio", { name: "Dark mode" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-mode", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-mode", "dark");
  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: testInfo.outputPath("queue-dark.png"), fullPage: true });

  await page.setViewportSize({ width: 720, height: 900 });
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const pageOverflows = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(pageOverflows).toBe(false);
  await page.evaluate(() => { document.documentElement.style.fontSize = ""; });
  await page.getByRole("button", { name: "Local models" }).click();
  await expect(page.getByRole("heading", { name: "Local models" })).toBeVisible();
  await expect(page.getByText("Ollama is running")).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: testInfo.outputPath("models-dark-responsive.png"), fullPage: true });

  expect(consoleErrors).toEqual([]);
});

test("instructor exercises every core control surface", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  await expect(page.getByRole("heading", { name: "Your grading stays yours" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Choose a comfortable view" })).toBeVisible();
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page.getByRole("heading", { name: "Your grading stays yours" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("radio", { name: "Dark mode" }).check();
  await page.getByRole("radio", { name: "Light mode" }).check();
  await page.getByRole("radio", { name: "Spacious density" }).check();
  await page.getByRole("radio", { name: "Comfortable density" }).check();
  await page.getByRole("checkbox", { name: "Reduce interface motion" }).check();
  await page.getByRole("button", { name: "Skip appearance choices" }).click();
  await expect(page.getByRole("heading", { name: "Check this Mac" })).toBeVisible();
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page.getByRole("heading", { name: "Choose a comfortable view" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Start grading" }).click();

  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();
  const assignment = page.getByPlaceholder("Assignment ID");
  await assignment.fill("");
  await expect(page.getByRole("button", { name: "Grade submissions" })).toBeDisabled();
  await assignment.fill("default");
  await page.getByRole("button", { name: "All submissions" }).click();
  await page.getByRole("button", { name: "Needs review" }).click();
  await page.getByRole("button", { name: "Standard review" }).click();
  await page.getByRole("button", { name: "Confident drafts" }).click();
  await page.getByRole("button", { name: "Refresh queue" }).click();
  await page.getByRole("button", { name: "Grade submissions" }).click();
  await expect(page.getByText("3 submissions queued.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("queue-all-controls.png"), fullPage: true });

  await page.getByRole("button", { name: /Student 1042/ }).click();
  await page.getByRole("button", { name: "Approve AI draft" }).click();
  await expect(page.getByText("Grade approved. It is ready for your next posting step.")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();

  await page.getByRole("button", { name: /Student 1204/ }).click();
  await page.keyboard.press("e");
  await expect(page.getByRole("textbox", { name: "Feedback for Student 1204" })).toBeFocused();
  await page.getByRole("radio", { name: "4 points for Thesis" }).check();
  await page.getByRole("textbox", { name: "Feedback for Student 1204" }).fill("Good structure. Add a clearer source connection.");
  await expect(page.getByText("Teacher changes not saved yet.")).toBeVisible();
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText("Your changes are approved. Score updated from 6 to 7.")).toBeVisible();
  await expect(page.getByText("Teacher changes not saved yet.")).toBeHidden();
  await page.getByRole("button", { name: /Back to queue/ }).click();

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "General" }).click();
  await page.getByLabel("Canvas URL").fill("https://canvas.example.invalid");
  await page.getByPlaceholder("Paste a new token").fill("synthetic-token");
  await page.getByLabel("Canvas attachment directory").fill("/tmp/grader-synthetic");
  await page.getByLabel("High-confidence threshold").fill("0.9");
  await page.getByLabel("Needs-review threshold").fill("0.65");
  await page.getByRole("button", { name: "Save general settings" }).click();
  await expect(page.getByText("Settings saved on this Mac.")).toBeVisible();
  await page.getByRole("tab", { name: "Appearance" }).click();
  await page.getByRole("radio", { name: "Dark mode" }).check();
  await page.getByRole("radio", { name: "Spacious density" }).check();
  await page.getByRole("checkbox", { name: "Reduce interface motion" }).check();
  await page.getByRole("button", { name: "Restore appearance defaults" }).click();
  await page.getByRole("tab", { name: "Model defaults" }).click();
  await page.getByLabel("Text grading model").fill("grader-text:8b");
  await page.getByLabel("Vision and handwriting model").fill("grader-vision:1.3b");
  await page.getByLabel("Embedding model").fill("nomic-embed-text:latest");
  await page.getByRole("button", { name: "Save model defaults" }).click();
  await page.getByRole("tab", { name: "Rubrics" }).click();
  await page.getByRole("button", { name: "Load into editor" }).click();
  await expect(page.getByText("Loaded “Synthetic Essay Rubric” into the editor.")).toBeVisible();
  await page.getByLabel("Rubric title").fill("Synthetic Essay Rubric v2");
  await page.getByLabel("Rubric text").fill("Thesis: 0-5\nEvidence: 0-5");
  await page.getByRole("button", { name: "Save rubric" }).click();
  await expect(page.getByText("Rubric “Synthetic Essay Rubric v2” saved.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("settings-all-controls.png"), fullPage: true });
  await page.getByRole("button", { name: /Back to queue/ }).click();

  await page.getByRole("button", { name: "Local models" }).click();
  await page.getByRole("button", { name: "Refresh status" }).click();
  await page.getByRole("button", { name: "Install handwriting model" }).click();
  await expect(page.getByText("minicpm-v4.6:latest downloaded.")).toBeVisible();
  await page.getByRole("button", { name: "Install exemplar finder model" }).click();
  await expect(page.getByText("nomic-embed-text:latest downloaded.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("models-all-controls.png"), fullPage: true });
  await page.getByRole("button", { name: /Back to queue/ }).click();
  await expectNoSeriousAccessibilityViolations(page);
});

test("engine disconnect is surfaced with an instructor recovery action", async ({ page }) => {
  await page.route("http://127.0.0.1:8756/health", route => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Skip appearance choices" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Local engine connected")).toBeVisible();
  await page.getByRole("button", { name: "Start grading" }).click();
  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();

  await page.route("http://127.0.0.1:8756/health", route => route.abort());

  await expect(page.getByRole("alert")).toContainText("Local engine needs attention", { timeout: 7000 });
  await expect(page.getByRole("button", { name: "Restart local engine" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("visual sweep records every core screen and visible control inventory", async ({ page }) => {
  const screens: Awaited<ReturnType<typeof captureVisualScreen>>[] = [];
  const capture = async (name: string) => {
    await expectNoSeriousAccessibilityViolations(page);
    screens.push(await captureVisualScreen(page, name));
  };

  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Your grading stays yours" })).toBeVisible();
  await capture("01-onboarding-welcome");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Choose a comfortable view" })).toBeVisible();
  await page.getByRole("radio", { name: "Dark mode" }).check();
  await page.getByRole("radio", { name: "Light mode" }).check();
  await page.getByRole("radio", { name: "Spacious density" }).check();
  await page.getByRole("radio", { name: "Comfortable density" }).check();
  await page.getByRole("checkbox", { name: "Reduce interface motion" }).check();
  await capture("02-onboarding-appearance");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Check this Mac" })).toBeVisible();
  await capture("03-onboarding-readiness");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Local engine connected")).toBeVisible();
  await capture("04-onboarding-connect");
  await page.getByRole("button", { name: "Start grading" }).click();

  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();
  await capture("05-queue-all");
  await page.getByRole("button", { name: "All submissions" }).click();
  await page.getByRole("button", { name: "Needs review" }).click();
  await page.getByRole("button", { name: "Standard review" }).click();
  await page.getByRole("button", { name: "Confident drafts" }).click();
  await page.getByRole("button", { name: "Refresh queue" }).click();
  await page.getByRole("button", { name: "Grade submissions" }).click();
  await expect(page.getByText("3 submissions queued.")).toBeVisible();
  await capture("06-queue-controls");

  await page.getByRole("button", { name: /Student 1042/ }).click();
  await expect(page.getByRole("heading", { name: "Review Student 1042" })).toBeVisible();
  await capture("07-review-draft");
  await page.getByRole("radio", { name: "5 points for Thesis" }).check();
  await page.getByRole("textbox", { name: "Feedback for Student 1042" }).fill("Strong claim. Add one more cited example.");
  await capture("08-review-edited");
  await page.getByRole("button", { name: "Save changes and approve" }).click();
  await expect(page.getByText("Your changes are approved. Score updated from 7 to 8.")).toBeVisible();
  await capture("09-review-approved");
  await page.getByRole("button", { name: /Back to queue/ }).click();

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "General" }).click();
  await page.getByLabel("Canvas URL").fill("https://canvas.example.invalid");
  await page.getByPlaceholder("Paste a new token").fill("synthetic-token");
  await page.getByLabel("Canvas attachment directory").fill("/tmp/grader-synthetic");
  await page.getByRole("button", { name: "Save general settings" }).click();
  await expect(page.getByText("Settings saved on this Mac.")).toBeVisible();
  await capture("10-settings-general");

  await page.getByRole("tab", { name: "Appearance" }).click();
  await page.getByRole("radio", { name: "Dark mode" }).check();
  await page.getByRole("radio", { name: "Light mode" }).check();
  await page.getByRole("button", { name: "Restore appearance defaults" }).click();
  await capture("11-settings-appearance");

  await page.getByRole("tab", { name: "Model defaults" }).click();
  await page.getByLabel("Text grading model").fill("grader-text:8b");
  await page.getByLabel("Vision and handwriting model").fill("grader-vision:1.3b");
  await page.getByLabel("Embedding model").fill("nomic-embed-text:latest");
  await page.getByRole("button", { name: "Save model defaults" }).click();
  await capture("12-settings-model-defaults");

  await page.getByRole("tab", { name: "Rubrics" }).click();
  await page.getByRole("button", { name: "Load into editor" }).click();
  await expect(page.getByText("Loaded “Synthetic Essay Rubric” into the editor.")).toBeVisible();
  await page.getByLabel("Rubric title").fill("Synthetic Essay Rubric v2");
  await page.getByLabel("Rubric text").fill("Thesis: 0-5\nEvidence: 0-5");
  await page.getByRole("button", { name: "Save rubric" }).click();
  await expect(page.getByText("Rubric “Synthetic Essay Rubric v2” saved.")).toBeVisible();
  await capture("13-settings-rubrics");
  await page.getByRole("button", { name: /Back to queue/ }).click();

  await page.getByRole("button", { name: "Local models" }).click();
  await expect(page.getByRole("heading", { name: "Local models" })).toBeVisible();
  await page.getByRole("button", { name: "Refresh status" }).click();
  await page.getByRole("button", { name: "Install handwriting model" }).click();
  await expect(page.getByText("minicpm-v4.6:latest downloaded.")).toBeVisible();
  await page.getByRole("button", { name: "Install exemplar finder model" }).click();
  await expect(page.getByText("nomic-embed-text:latest downloaded.")).toBeVisible();
  await capture("14-model-manager");

  await page.setViewportSize({ width: 720, height: 900 });
  await page.getByRole("button", { name: /Back to queue/ }).click();
  await expect(page.getByRole("heading", { name: "Grading queue" })).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const pageOverflows = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(pageOverflows).toBe(false);
  await capture("15-responsive-200-percent");

  await page.evaluate(() => { document.documentElement.style.fontSize = ""; });
  await page.route("http://127.0.0.1:8756/health", route => route.abort());
  await expect(page.getByRole("alert")).toContainText("Local engine needs attention", { timeout: 7000 });
  await expect(page.getByRole("button", { name: "Restart local engine" })).toBeVisible();
  await capture("16-engine-recovery-alert");

  const requiredScreens = [
    "01-onboarding-welcome",
    "02-onboarding-appearance",
    "03-onboarding-readiness",
    "04-onboarding-connect",
    "05-queue-all",
    "06-queue-controls",
    "07-review-draft",
    "08-review-edited",
    "09-review-approved",
    "10-settings-general",
    "11-settings-appearance",
    "12-settings-model-defaults",
    "13-settings-rubrics",
    "14-model-manager",
    "15-responsive-200-percent",
    "16-engine-recovery-alert",
  ];
  expect(screens.map(screen => screen.name)).toEqual(requiredScreens);
  expect(screens.every(screen => screen.visible_control_count > 0)).toBe(true);

  const evidence = {
    status: "PASS",
    generated_at: new Date().toISOString(),
    method: "Playwright visual sweep with screenshots, control inventory, axe serious/critical checks, and 200% text reflow check",
    screen_count: screens.length,
    total_visible_controls: screens.reduce((sum, screen) => sum + screen.visible_control_count, 0),
    screens,
  };
  mkdirSync(dirname(visualEvidenceJson), { recursive: true });
  writeFileSync(visualEvidenceJson, `${JSON.stringify(evidence, null, 2)}\n`);
});
