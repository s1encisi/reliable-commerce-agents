import { test, expect, type Page } from "@playwright/test";

// Run against scripts/demo.sh --reset. Uses only public synthetic demo accounts.
// Browser plugin not available: repository Playwright runner is the validation path.
test.setTimeout(90_000);
const normal = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
const missing = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";
const lost = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4";

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("DemoPass123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 });
}

async function requestReturn(page: Page, orderId: string): Promise<void> {
  await page.goto(`/orders/${orderId}`);
  await page.getByRole("button", { name: "Return Order", exact: true }).click();
  await page.getByLabel("Reason for return").fill("Damaged demo product");
  await page.getByRole("button", { name: "Submit Return", exact: true }).click();
}

test("approval precedes creation, then customer confirms the receipt", async ({ page, browser }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await login(page, "customer@example.test");
  await requestReturn(page, normal);
  await expect(page.getByRole("status")).toContainText("submitted for approval");
  await expect(page.getByText("Return Information", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "/tmp/reliable-commerce-pending.png", fullPage: false });

  const reviewerContext = await browser.newContext({ baseURL: test.info().project.use.baseURL, viewport: { width: 1440, height: 1000 } });
  const reviewer = await reviewerContext.newPage();
  await login(reviewer, "admin@example.test");
  await reviewer.goto("/admin/approvals");
  await reviewer.getByText("Show input", { exact: true }).first().click();
  await expect(reviewer.getByText(normal, { exact: false })).toBeVisible();
  await reviewer.screenshot({ path: "/tmp/reliable-commerce-approval.png", fullPage: false });
  await reviewer.getByRole("button", { name: "Approve", exact: true }).first().click();
  await expect(reviewer.getByText("Return request created. No refund has been issued.", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "Check request status", exact: true }).click();
  await expect(page.getByText("Return Information", { exact: true })).toBeVisible();
  await expect(page.getByText(/View the label in your order details/)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/orders/${normal}`));
  expect(await page.title()).toBeTruthy();
  expect(runtimeErrors).toEqual([]);
  await page.getByText("Return Information", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/reliable-commerce-completed.png", fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  await expect(page.getByText("Return Information", { exact: true })).toBeVisible();
  await page.getByText("Return Information", { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/reliable-commerce-mobile.png", fullPage: true });
  await reviewerContext.close();
});

test("lost initial response retains operation ID for status lookup", async ({ page }) => {
  await login(page, "customer@example.test");
  let intercepted = false;
  await page.route(`**/api/orders/${lost}/return`, async (route) => {
    expect(route.request().headers()["idempotency-key"]).toMatch(/^[a-f0-9-]{36}$/);
    const response = await route.fetch();
    expect(response.status()).toBe(409); // actual backend queued the request
    intercepted = true;
    await route.abort("failed"); // intentionally discard the response, not the server-side effect
  });
  await requestReturn(page, lost);
  await expect.poll(() => intercepted).toBe(true);
  await page.unroute(`**/api/orders/${lost}/return`);
  await page.getByRole("button", { name: "Check request status", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("submitted for approval");
  await expect(page.getByText("Return Information", { exact: true })).toHaveCount(0);
});

test("missing delivery evidence yields a review message, not a success toast", async ({ page }) => {
  await login(page, "customer@example.test");
  await requestReturn(page, missing);
  await expect(page.getByRole("status")).toContainText("human verification");
  await expect(page.getByText("Return Information", { exact: true })).toHaveCount(0);
});
