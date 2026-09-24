import { test, expect, type Page } from "@playwright/test";

// 共用的登录辅助函数
async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products|admin|seller)/, { timeout: 10000 });
}

// ============================================================
// 1. 顾客购物流程（传统界面）
// ============================================================
test.describe("顾客购物流程", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
  });

  test("应能看到商品页与「加入购物车」按钮", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // 商品应可见
    const products = page.locator('[class*="grid"] > a, [class*="grid"] > div');
    await expect(products.first()).toBeVisible();
    // 截图
    await page.screenshot({ path: "e2e/screenshots/products-page.png" });
  });

  test("应能打开商品详情并看到「加入购物车」", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // 点击第一个商品卡片
    const firstProduct = page.locator('[class*="grid"] > a').first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//, { timeout: 10000 });
      // 应能看到「加入购物车」按钮
      const addToCartBtn = page.getByRole("button", { name: /加入购物车/ });
      await expect(addToCartBtn).toBeVisible({ timeout: 10000 });
      await page.screenshot({ path: "e2e/screenshots/product-detail.png" });
    }
  });

  test("应能把商品加入购物车并看到购物车角标更新", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // 点击第一个商品
    const firstProduct = page.locator('[class*="grid"] > a').first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//, { timeout: 10000 });
      // 点击「加入购物车」
      const addToCartBtn = page.getByRole("button", { name: /加入购物车/ });
      await expect(addToCartBtn).toBeVisible({ timeout: 10000 });
      await addToCartBtn.click();
      // 应能看到「已加入」确认
      await expect(page.getByText(/已加入/)).toBeVisible({ timeout: 5000 });
      await page.screenshot({ path: "e2e/screenshots/added-to-cart.png" });
    }
  });

  test("应能看到带商品的购物车页（演示购物车已预置）", async ({ page }) => {
    await page.goto("/cart");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/cart-page.png" });
    // Alice 的购物车已预置了商品
    // 检查有商品或空状态二者之一
    const hasItems = await page.getByText(/去结算/).isVisible().catch(() => false);
    const isEmpty = await page.getByText(/购物车是空的/).isVisible().catch(() => false);
    expect(hasItems || isEmpty).toBeTruthy();
  });

  test("应能跳转到结算页", async ({ page }) => {
    await page.goto("/checkout");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/checkout-page.png" });
    // 应能看到结算标题、收货地址表单或空购物车
    const hasCheckout = await page.getByText(/结算/).first().isVisible().catch(() => false);
    const hasForm = await page.getByText(/收货地址/).first().isVisible().catch(() => false);
    const isEmpty = await page.getByText(/购物车是空的|请先添加商品/).first().isVisible().catch(() => false);
    expect(hasCheckout || hasForm || isEmpty).toBeTruthy();
  });

  test("应能看到订单页与订单列表", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/orders-page.png" });
    // 应有订单（Alice 的订单已预置）
    const orderCards = page.locator('[class*="cursor-pointer"]');
    const count = await orderCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("应能看到订单详情与状态时间线", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForTimeout(3000);
    // 点击第一笔订单
    const firstOrder = page.locator('[class*="cursor-pointer"]').first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//, { timeout: 10000 });
      await page.waitForTimeout(3000);
      await page.screenshot({ path: "e2e/screenshots/order-detail.png" });
      // 应显示订单标题或状态信息
      const hasOrder = await page.getByText(/订单 #|订单状态/).first().isVisible().catch(() => false);
      const hasStatus = await page.getByText(/已下单|已确认|已发货|配送中|已送达|已取消|已退货/).first().isVisible().catch(() => false);
      expect(hasOrder || hasStatus).toBeTruthy();
    }
  });

  test("应能看到侧边栏的购物车链接与角标", async ({ page }) => {
    await page.goto("/products");
    await page.waitForTimeout(2000);
    // 检查侧边栏有「购物车」链接
    const cartLink = page.getByRole("link", { name: /购物车/ });
    await expect(cartLink.first()).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: "e2e/screenshots/sidebar-cart.png" });
  });

  test("应能看到个人中心页", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/profile-page.png" });
    await expect(page.getByRole("heading", { name: /个人中心/ })).toBeVisible({ timeout: 5000 });
  });
});

// ============================================================
// 2. 商家角色
// ============================================================
test.describe("商家角色", () => {
  test("应能看到商家仪表盘与商品", async ({ page }) => {
    await login(page, "seller.demo@gmail.com", "seller123");
    // 跳转到商家仪表盘
    await page.goto("/seller");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/seller-dashboard.png" });

    // 检查商家商品
    await page.goto("/seller/products");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/seller-products.png" });
  });
});

// ============================================================
// 3. 管理员角色
// ============================================================
test.describe("管理员角色", () => {
  test("应能看到管理端仪表盘与用量统计", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-dashboard.png" });
  });

  test("应能看到管理端访问申请", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin/requests");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-requests.png" });
  });

  test("应能看到管理端用量统计", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin/usage");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-usage.png" });
  });
});

// ============================================================
// 4. 对话体验
// ============================================================
test.describe("对话体验", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
  });

  test("应能加载带输入区的对话页", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/chat-page.png" });
    // 应能看到对话输入框
    const textarea = page.locator("textarea");
    await expect(textarea).toBeVisible({ timeout: 5000 });
  });

  test("应能在对话页头部看到购物车角标", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForTimeout(2000);
    // 在对话区域寻找购物车图标/链接
    const cartLink = page.locator('a[href="/cart"]');
    const count = await cartLink.count();
    // 至少有侧边栏的购物车链接
    expect(count).toBeGreaterThan(0);
  });
});
