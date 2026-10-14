import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import type { ProcurementTask } from "../src/lib/procurement";

// Uses real local HTTP + database. No route mocking, data reset, model call or checkout.
// Browser plugin skill is unavailable in this session; use the repository Playwright runner.
test.skip(process.env.E2E_PROCUREMENT !== "1", "仅显式启用的本地采购离线验收运行");
test.setTimeout(180_000);
test.use({ viewport: { width: 1280, height: 900 }, video: { mode: "on", size: { width: 1280, height: 900 } }, trace: "on" });

test("真实离线采购：人工确认、自动多服务执行、两方案与恢复", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("/login");
  await page.getByLabel("邮箱", { exact: true }).fill("customer@example.test");
  await page.getByLabel("密码", { exact: true }).fill("DemoPass123!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto("/procurement");
  const capabilities = await page.evaluate(async () => {
    const token = localStorage.getItem("ecommerce_access_token");
    const response = await fetch("/api/runtime/capabilities", { headers: { Authorization: `Bearer ${token}` } });
    return response.json();
  });
  expect(capabilities.procurement).toMatchObject({ enabled: true, mode: "offline", model_authorized: false, write_operations: false });
  await page.getByRole("button", { name: "填入本地合成示例" }).click();
  await page.getByRole("button", { name: "解析为需求草案" }).click();
  await expect(page.getByLabel("每个方案采购数量")).toHaveValue("3");
  await expect(page.getByLabel("每个方案总货款预算（元）")).toHaveValue("1500.00");
  // The rule parser is intentionally bounded; explicitly review the synthetic product set.
  await page.getByLabel("商品检索词").fill("采购专用耳机");
  await page.screenshot({ path: testInfo.outputPath("procurement-live-draft.png"), fullPage: true });
  await page.getByRole("checkbox").check();
  const started = page.waitForResponse((response) => response.url().endsWith("/start") && response.request().method() === "POST");
  await page.getByRole("button", { name: "确认并自动启动" }).click();
  expect((await started).status()).toBe(202);
  await expect(page.getByText("当前任务 · 方案已核验", { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("region", { name: "采购备选方案" }).getByText(/^方案 [12] ·/)).toHaveCount(2);
  await expect(page.getByText(/固定离线计划 · 跨专业服务查询真实数据库/)).toBeVisible();
  await expect(page.getByText(/评论引用 /).first()).toBeVisible();
  const taskId = new URL(page.url()).searchParams.get("task");
  const result = await page.evaluate(async (id) => {
    const response = await fetch(`/api/tasks/${id}`, { headers: { Authorization: `Bearer ${localStorage.getItem("ecommerce_access_token")}` } });
    return response.json();
  }, taskId) as ProcurementTask;
  expect(result.status).toBe("completed");
  expect(result.counters.model_calls).toBe(0);
  expect(result.options).toHaveLength(2);
  expect(new Set(result.options.map((option) => option.product_id)).size).toBe(2);
  for (const option of result.options) {
    expect(option.quantity).toBe(3);
    expect(Number(option.quote.final_total)).toBeLessThanOrEqual(1500);
    expect(option.inventory.available_quantity).toBeGreaterThanOrEqual(3);
    expect(option.review.reviews.length).toBeGreaterThan(0);
  }
  await writeFile(testInfo.outputPath("procurement-live-1500.json"), JSON.stringify(result, null, 2));
  await testInfo.attach("真实采购回执", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
  await page.getByRole("region", { name: "采购备选方案" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-options-1500.png"), fullPage: true });
  await page.waitForTimeout(1500); // Reading time in the real interaction recording, not a readiness check.

  // Honest control-plane revision of an already completed task, not a staged mid-run pause.
  await page.getByRole("button", { name: "修订需求", exact: true }).click();
  await expect(page.getByText("当前任务 · 已暂停", { exact: true })).toBeVisible();
  await page.getByLabel("每个方案总货款预算（元）").fill("1200");
  await page.getByRole("checkbox").check();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-revised-draft.png"), fullPage: true });
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "确认并自动启动", exact: true }).click();
  await expect(page.getByText("当前任务 · 方案已核验", { exact: true })).toBeVisible({ timeout: 90_000 });
  const revised = await page.evaluate(async (id) => {
    const response = await fetch(`/api/tasks/${id}`, { headers: { Authorization: `Bearer ${localStorage.getItem("ecommerce_access_token")}` } });
    return response.json();
  }, taskId) as ProcurementTask;
  expect(revised.requirements?.total_budget).toBe("1200.00");
  expect(revised.goal).toContain("1200.00");
  expect(revised.spec_revision).toBeGreaterThan(result.spec_revision);
  expect(revised.counters.model_calls).toBe(0);
  expect(revised.options).toHaveLength(2);
  expect(revised.counters.repairs_used).toBeGreaterThanOrEqual(1);
  for (const option of revised.options) {
    expect(Number(option.quote.final_total)).toBeLessThanOrEqual(1200);
    expect(option.inventory.available_quantity).toBeGreaterThanOrEqual(3);
  }
  await writeFile(testInfo.outputPath("procurement-live-1200.json"), JSON.stringify(revised, null, 2));
  await page.getByText("失败与局部修复记录", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-repair.png"), fullPage: true });
  await page.waitForTimeout(1500);
  await page.getByRole("region", { name: "采购备选方案" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-options-1200.png"), fullPage: true });
  await page.waitForTimeout(1500);
  await page.reload();
  await expect(page.getByText("当前任务 · 方案已核验", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  expect(await page.locator("main").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("procurement-live-mobile.png"), fullPage: true });
  await page.getByRole("region", { name: "采购备选方案" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("procurement-live-options-mobile.png"), fullPage: true });
  await writeFile(testInfo.outputPath("browser-verification.json"), JSON.stringify({ base_url: process.env.E2E_BASE_URL, task_id: taskId, revision_scenario: "completed_then_explicit_revision", original_spec_revision: result.spec_revision, revised_spec_revision: revised.spec_revision, original_options: result.options.map((o) => ({ product_id: o.product_id, total: o.quote.final_total })), revised_options: revised.options.map((o) => ({ product_id: o.product_id, total: o.quote.final_total })), model_calls: revised.counters.model_calls, repairs_used: revised.counters.repairs_used, reused_steps: revised.counters.reused_steps, console_errors: errors, mobile_width: 393 }, null, 2));
  expect(errors).toEqual([]);
});
