import { test, expect } from "@playwright/test";

// 仅在显式启用的本地 replay 演示环境运行，不允许意外调用真实规划模型。
test.skip(process.env.E2E_DEMO_TASKS !== "1", "需要本地离线固定任务演示环境");
test.setTimeout(90_000);

test("离线固定计划通过真实数据库查询完成任务", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("/login");
  await page.getByLabel("邮箱", { exact: true }).fill("customer@example.test");
  await page.getByLabel("密码", { exact: true }).fill("DemoPass123!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto("/tasks");
  await page.getByRole("button", { name: "离线固定示例（不调用模型）", exact: true }).click();
  await expect(page.getByLabel("任务目标", { exact: true })).toHaveValue("筛选两款商品并核对库存");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await page.getByRole("button", { name: "生成计划", exact: true }).click();
  await expect(page.getByText(/固定演示计划 · 真实数据库查询/)).toBeVisible();
  await page.getByRole("button", { name: "执行下一步", exact: true }).click();
  await expect(page.getByText("已核验", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "执行下一步", exact: true }).click();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible();
  await expect(page.getByText("已核验", { exact: true })).toHaveCount(2);
  await page.reload();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible();
  await page.getByText("查看事实证据", { exact: true }).first().click();
  await expect(page.locator("pre").first()).toContainText("id");
  await page.screenshot({ path: "/tmp/reliable-commerce-tasks-live-evidence.png", fullPage: true });
  await page.locator("main").evaluate((element) => element.scrollTop = 0);
  await page.screenshot({ path: "/tmp/reliable-commerce-tasks-live.png", fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  await page.locator("main").evaluate((element) => element.scrollTop = 0);
  expect(await page.locator("main").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/reliable-commerce-tasks-live-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
