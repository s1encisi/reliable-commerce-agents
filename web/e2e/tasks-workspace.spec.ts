import { test, expect } from "@playwright/test";
import type { TaskRecord } from "../src/lib/tasks";

// Browser plugin not available. 这组测试只验证 UI/API 契约，所有任务接口均 mock，不调用模型。
const ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1";
const PRODUCT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
function task(overrides: Partial<TaskRecord> = {}): TaskRecord {
  return { id: ID, revision: 0, status: "pending", goal: "找一款预算内耳机", constraints: ["适合通勤"], requirements: { max_price: 500, min_items: 1 }, planning_attempts: 0, active_step: null, receipt_history: [], results: {}, plan: null, ...overrides };
}
const PLAN: NonNullable<TaskRecord["plan"]> = { goal: "找一款预算内耳机", constraints: ["适合通勤"], steps: [{ id: "find", service: "product-discovery", instruction: "核验预算内商品", depends_on: [], acceptance: { kind: "product_selection", required_ids: [], min_items: 1, from_steps: [] } }] };

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("ecommerce_user", JSON.stringify({ email: "customer@example.test", name: "演示客户", role: "customer" }));
    localStorage.setItem("ecommerce_access_token", "ui-contract-test");
  });
});

test("任务创建、规划、逐步执行、人工确认与刷新恢复", async ({ page }) => {
  let current = task();
  let executions = 0;
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path === "/api/tasks" && method === "POST") current = task(route.request().postDataJSON());
    if (path.endsWith("/plan")) current = { ...current, status: "ready", revision: 2, planning_attempts: 1, plan: PLAN };
    if (path.endsWith("/next")) {
      executions++;
      expect(route.request().postDataJSON().revision).toBe(2);
      current = { ...current, status: "needs_review", revision: 4, results: { find: { receipt_id: "receipt-1", verified_at: 1, valid_until: 9999999999, result: { response: "已找到符合预算的演示耳机。" }, verification: { passed: true, errors: [], evidence: [{ id: PRODUCT, price: 299 }], validation_scope: ["预算", "商品数量"] } } } };
    }
    if (path.endsWith("/confirm")) {
      expect(route.request().postDataJSON()).toEqual({ revision: 4, confirmed: true });
      current = { ...current, status: "completed", revision: 5 };
    }
    await route.fulfill({ json: path.startsWith("/api/tasks") ? current : { items: [], item_count: 0 } });
  });
  await page.goto("/tasks");
  await page.getByLabel("任务目标", { exact: true }).fill("找一款预算内耳机");
  await page.getByLabel("单件预算上限（元，可选）", { exact: true }).fill("500");
  await page.getByLabel("其他文字约束（可选，每行一条，需人工核实）", { exact: true }).fill("适合通勤");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`task=${ID}`));
  await page.getByRole("button", { name: "生成计划", exact: true }).click();
  await expect(page.getByRole("region", { name: "任务计划" })).toBeVisible();
  expect(executions).toBe(0);
  await page.getByRole("button", { name: "执行下一步", exact: true }).click();
  await expect(page.getByText("任务要求待核实", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认要求并完成任务" })).toBeDisabled();
  await page.getByText("查看事实证据", { exact: true }).click();
  await expect(page.getByText(/"price": 299/)).toBeVisible();
  await page.getByLabel("我已逐项核实以上任务要求", { exact: true }).check();
  await page.getByRole("button", { name: "确认要求并完成任务" }).click();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible();
  expect(executions).toBe(1);
  await expect(page).toHaveTitle(/可靠电商/);
  await page.screenshot({ path: "/tmp/reliable-commerce-tasks-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/reliable-commerce-tasks-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("版本冲突仅刷新状态，由用户显式恢复后再执行", async ({ page }) => {
  let current = task({ status: "ready", revision: 2, plan: PLAN, planning_attempts: 1 });
  let executions = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/next")) {
      executions++;
      current = { ...current, status: "running", revision: 3, active_step: "find" };
      await route.fulfill({ status: 409, json: { detail: "版本冲突" } }); return;
    }
    if (path.endsWith("/recover")) {
      expect(route.request().postDataJSON()).toEqual({ revision: 3 });
      current = { ...current, status: "ready", revision: 4, active_step: null };
    }
    await route.fulfill({ json: path.startsWith("/api/tasks") ? current : { items: [], item_count: 0 } });
  });
  await page.goto(`/tasks?task=${ID}`);
  await page.getByRole("button", { name: "执行下一步", exact: true }).click();
  await expect(page.getByText(/已读取最新版本/)).toBeVisible();
  expect(executions).toBe(1);
  await page.getByRole("button", { name: "核实并恢复中断任务", exact: true }).click();
  await expect(page.getByRole("button", { name: "执行下一步", exact: true })).toBeEnabled();
  expect(executions).toBe(1);
});
