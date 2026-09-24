import { test, expect, type Page } from "@playwright/test";

// 针对 scripts/demo.sh --reset 运行。只使用公开的合成演示账号。
// 浏览器插件不可用：本仓库的 Playwright 运行器就是验证路径。
test.setTimeout(90_000);
const normal = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
const missing = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";
const lost = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4";

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page.getByLabel("密码", { exact: true }).fill("DemoPass123!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 });
}

async function requestReturn(page: Page, orderId: string): Promise<void> {
  await page.goto(`/orders/${orderId}`);
  await page.getByRole("button", { name: "退货", exact: true }).click();
  await page.getByLabel("退货原因").fill("演示商品有破损");
  await page.getByRole("button", { name: "提交退货申请", exact: true }).click();
}

test("审批先于创建，随后由顾客确认收货", async ({ page, browser }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await login(page, "customer@example.test");
  await requestReturn(page, normal);
  await expect(page.getByRole("status")).toContainText("submitted for approval");
  await expect(page.getByText("退货信息", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "/tmp/reliable-commerce-pending.png", fullPage: false });

  const reviewerContext = await browser.newContext({ baseURL: test.info().project.use.baseURL, viewport: { width: 1440, height: 1000 } });
  const reviewer = await reviewerContext.newPage();
  await login(reviewer, "admin@example.test");
  await reviewer.goto("/admin/approvals");
  await reviewer.getByText("查看输入参数", { exact: true }).first().click();
  await expect(reviewer.getByText(normal, { exact: false })).toBeVisible();
  await reviewer.screenshot({ path: "/tmp/reliable-commerce-approval.png", fullPage: false });
  await reviewer.getByRole("button", { name: "批准", exact: true }).first().click();
  await expect(reviewer.getByText("Return request created. No refund has been issued.", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "查看请求状态", exact: true }).click();
  await expect(page.getByText("退货信息", { exact: true })).toBeVisible();
  await expect(page.getByText(/View the label in your order details/)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/orders/${normal}`));
  expect(await page.title()).toBeTruthy();
  expect(runtimeErrors).toEqual([]);
  await page.getByText("退货信息", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/reliable-commerce-completed.png", fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  await expect(page.getByText("退货信息", { exact: true })).toBeVisible();
  await page.getByText("退货信息", { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/reliable-commerce-mobile.png", fullPage: true });
  await reviewerContext.close();
});

test("首次响应丢失后仍保留操作 ID 以便查询状态", async ({ page }) => {
  await login(page, "customer@example.test");
  let intercepted = false;
  await page.route(`**/api/orders/${lost}/return`, async (route) => {
    expect(route.request().headers()["idempotency-key"]).toMatch(/^[a-f0-9-]{36}$/);
    const response = await route.fetch();
    expect(response.status()).toBe(409); // 后端确实把该请求排入了队列
    intercepted = true;
    await route.abort("failed"); // 有意丢弃响应，但服务端副作用依然保留
  });
  await requestReturn(page, lost);
  await expect.poll(() => intercepted).toBe(true);
  await page.unroute(`**/api/orders/${lost}/return`);
  await page.getByRole("button", { name: "查看请求状态", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("submitted for approval");
  await expect(page.getByText("退货信息", { exact: true })).toHaveCount(0);
});

test("缺少送达凭证时给出复核提示，而不是成功 toast", async ({ page }) => {
  await login(page, "customer@example.test");
  await requestReturn(page, missing);
  await expect(page.getByRole("status")).toContainText("human verification");
  await expect(page.getByText("退货信息", { exact: true })).toHaveCount(0);
});
