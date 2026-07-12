import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const queue = {
  stats: { total: 3, review_first: 1, standard: 1, high_confidence: 1, summary: "1 needs review, 1 standard, 1 high confidence" },
  items: [
    { id: "grade-1", submission_id: "Student 1042", assignment_id: "default", ai_scores: { Thesis: 4, Evidence: 3 }, ai_feedback: "Clear claim. Add a second source.", confidence: 0.64, flags: ["citation_check"], lane: "review_first", approved: false, teacher_scores: null, teacher_feedback: null },
    { id: "grade-2", submission_id: "Student 1177", assignment_id: "default", ai_scores: { Thesis: 5, Evidence: 5 }, ai_feedback: "Ready for review.", confidence: 0.91, flags: [], lane: "high_confidence", approved: true, teacher_scores: null, teacher_feedback: null },
  ],
};

async function expectNoSeriousAccessibilityViolations(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = result.violations.filter(violation => ["serious", "critical"].includes(violation.impact || ""));
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
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
    if (url.pathname === "/api/settings" && request.method() === "GET") return json({ auto_approve_threshold: 0.95, review_first_threshold: 0.7, model_text: "grader-text:8b", model_vision: "grader-vision:1.3b", model_embed: "nomic-embed-text:latest" });
    if (url.pathname === "/api/settings" && request.method() === "PUT") return json({ success: true });
    if (url.pathname === "/api/rubrics") return json([]);
    if (url.pathname === "/api/models") return json({ ollama_running: true, available: [{ name: "grader-text:8b", size: 8_100_000_000 }] });
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
