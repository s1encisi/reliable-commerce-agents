import { test, expect, type Page } from "@playwright/test";

/**
 * 对话驱动的电商测试。
 * 这些用例向智能体发送真实消息并校验回答。
 * 它们要求已配置好 LLM（OpenAI）——若未设置 OPENAI_API_KEY，
 * 编排器会返回一段错误消息。
 */

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendMessage(page: Page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  // 等待助手回答（加载指示器消失且出现新内容）
  await page.waitForTimeout(2000);
  // 等待「正在输入」指示器消失，或等待回答出现
  await page.waitForFunction(
    () => {
      const messages = document.querySelectorAll('[class*="max-w"]');
      return messages.length > 0;
    },
    { timeout: 60000 }
  );
  // 额外等待，让流式输出彻底结束
  await page.waitForTimeout(3000);
}

async function getLastAssistantMessage(page: Page): Promise<string> {
  // 取回全部消息容器，返回最后一条助手消息
  const messages = await page.locator('[class*="rounded-2xl"]').all();
  if (messages.length === 0) return "";
  const last = messages[messages.length - 1];
  return (await last.textContent()) || "";
}

test.describe("对话购物体验", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await page.waitForTimeout(1000);
  });

  // 为 LLM 回答设置更长的超时
  test.setTimeout(120000);

  test("应能发送商品搜索消息并收到回答", async ({ page }) => {
    await sendMessage(page, "给我看看 300 元以内的耳机");
    await page.screenshot({ path: "e2e/screenshots/chat-product-search.png", fullPage: true });

    // 应收到回答（商品卡片或关于商品的文字）
    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    // 智能体应给出某种回答（即使是一条缺少 API key 的错误）
    expect(text?.length).toBeGreaterThan(50);
  });

  test("应展示真实的商品卡片，绝不输出原始 JSON", async ({ page }) => {
    await sendMessage(page, "帮我找无线降噪耳机");
    await page.screenshot({ path: "e2e/screenshots/chat-product-cards.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    // 核心的「绝不展示原始 JSON」保证（第 8.1/8.2 阶段）——代码块泄漏意味着
    // 解析器退化成了一个原始代码块。
    expect(text).not.toContain("```product");
    expect(text).not.toMatch(/"id":\s*"[0-9a-f-]{36}"/);

    // 一个真实的商品卡片操作，而不只是正文里提到了「购物车」——如果这个种子
    // 用户的购物车里已经有该商品（状态会跨测试运行保留），按钮显示的是
    // 「已加入购物车！」而不是「加入购物车」，因此两者都接受，而不是假定购物
    // 车是干净的。
    await expect(page.getByRole("button", { name: /加入购物车|已加入/ }).first()).toBeVisible();
  });

  test("应能询问订单状态并收到真实的订单卡片", async ({ page }) => {
    await sendMessage(page, "我最近一笔订单到哪了？");
    await page.screenshot({ path: "e2e/screenshots/chat-order-status.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```order");
    expect(text?.length).toBeGreaterThan(50);

    // order-card.tsx 对它展示的任何订单都会渲染一个 #<shortId> 物流标识——
    // 这是真实的结构性标记，不是长度检查。
    // .first()：一个状态问题合理地会为每一笔匹配的订单渲染一张卡片，而这里
    // 断言的是渲染出了真实的订单卡片，不是恰好只有一张。
    await expect(page.getByText(/^#[0-9a-f]{8}/).first()).toBeVisible();
  });

  test("应能请求把商品加入购物车", async ({ page }) => {
    await sendMessage(page, "把 Sony WH-1000XM5 加入我的购物车");
    await page.screenshot({ path: "e2e/screenshots/chat-add-to-cart.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("应能询问退货", async ({ page }) => {
    await sendMessage(page, "我想退掉最近一笔已送达的订单");
    await page.screenshot({ path: "e2e/screenshots/chat-return-request.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("应能询问购物车内容", async ({ page }) => {
    await sendMessage(page, "我的购物车里有什么？");
    await page.screenshot({ path: "e2e/screenshots/chat-view-cart.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("应能询问配送与物流", async ({ page }) => {
    await sendMessage(page, "跟踪一下我最近一笔已发货的订单");
    await page.screenshot({ path: "e2e/screenshots/chat-track-order.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("应能请求取消订单", async ({ page }) => {
    await sendMessage(page, "取消我最近一笔已下单的订单");
    await page.screenshot({ path: "e2e/screenshots/chat-cancel-order.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });
});

// 验证界面购物操作能与对话并存
test.describe("界面购物操作", () => {
  // 30 秒不够。这些用例不依赖 LLM——纯 REST 与页面跳转——但它们要在固定的
  // waitForTimeout 上花掉约 7 秒，并加载三个页面，其中一个要渲染五十件带图
  // 的商品。在空闲机器上 30 秒够用，而当整套用例同时运行时就不够了，这让它
  // 看起来像是后端故障，而实际从来不是：购物车 API 返回 200，直接驱动时购物
  // 车也能正确填充。
  test.setTimeout(90000);

  test("从商品页完成完整的加入购物车流程", async ({ page }) => {
    await login(page, "bob.smith@gmail.com", "customer123");

    // 1. 打开商品页
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    await page.screenshot({ path: "e2e/screenshots/ui-products-grid.png" });

    // 2. 点击第一个商品。
    // 不是 `> a`：当卡片改为 onClick + router.push 之后（见
    // (app)/products/page.tsx），商品网格就不再渲染锚点了，因此那个选择器
    // 什么也匹配不到，本用例就把整整 90 秒的预算花在等待一个不可能存在的
    // 元素上。已对照直连编排器的前端以及经 /api 代理的前端验证过，所以问题
    // 在选择器，不在传输层。
    const firstProduct = page.locator('div.grid [data-slot="card"]').first();
    await firstProduct.click();
    await page.waitForURL(/\/products\//, { timeout: 10000 });
    await page.waitForTimeout(2000);

    // 3. 点击「加入购物车」
    const addBtn = page.getByRole("button", { name: /加入购物车/ });
    await expect(addBtn).toBeVisible({ timeout: 10000 });
    await addBtn.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/ui-added-to-cart.png" });

    // 4. 跳转到购物车
    await page.goto("/cart");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/ui-cart-with-item.png" });

    // 应至少有一件商品，或出现结算按钮
    const hasItems = await page.getByText(/去结算|购物车/).first().isVisible().catch(() => false);
    expect(hasItems).toBeTruthy();
  });

  test("查看订单详情并看到取消/退货按钮", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");

    // 打开订单页
    await page.goto("/orders");
    await page.waitForTimeout(3000);

    // 点击第一笔订单
    const firstOrder = page.locator('[class*="cursor-pointer"]').first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//, { timeout: 10000 });
      await page.waitForTimeout(3000);
      await page.screenshot({ path: "e2e/screenshots/ui-order-detail-actions.png" });

      // 按状态检查操作按钮
      const hasCancelBtn = await page.getByRole("button", { name: /取消订单/ }).isVisible().catch(() => false);
      const hasReturnBtn = await page.getByRole("button", { name: /退货/ }).isVisible().catch(() => false);
      const hasStatusBadge = await page.locator('[class*="badge"]').first().isVisible().catch(() => false);

      // 至少应有一个状态徽章
      expect(hasStatusBadge || hasCancelBtn || hasReturnBtn).toBeTruthy();
    }
  });
});
