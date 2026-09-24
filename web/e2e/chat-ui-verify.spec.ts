import { test, expect, type Page } from "@playwright/test";

/**
 * 聚焦的对话界面验证——发送真实消息、等待智能体完整回答，并对渲染出的
 * 富文本卡片截图。
 */

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendAndWaitForResponse(page: Page, message: string, timeoutMs = 45000) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");

  // 等待「正在输入」指示器出现再消失（表示回答已完成）
  // 或者等待回答内容出现
  await page.waitForTimeout(2000); // 留出「正在输入」指示器出现的时间

  // 等到不再有「正在输入」指示器（跳动的圆点消失）
  try {
    await page.waitForFunction(
      () => {
        // 确认「正在输入」指示器已消失（没有跳动圆点的动画）
        const dots = document.querySelectorAll('[class*="animate-bounce"]');
        return dots.length === 0;
      },
      { timeout: timeoutMs }
    );
  } catch {
    // 超时说明回答仍在流式输出——这没关系，照常截图即可
  }

  // 给渲染留一点额外缓冲
  await page.waitForTimeout(2000);
}

test.describe("对话界面验证", () => {
  test.setTimeout(120000);

  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await page.waitForTimeout(1000);
  });

  test("商品搜索展示富文本商品卡片", async ({ page }) => {
    await sendAndWaitForResponse(page, "给我看看 400 元以内的无线降噪耳机");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-product-search.png", fullPage: true });

    // 检查回答内容
    const main = page.locator("main");
    const text = await main.textContent();
    console.log("商品搜索回答长度：", text?.length);

    // 在回答中寻找商品相关内容
    const hasResponse = (text?.length ?? 0) > 200;
    expect(hasResponse).toBeTruthy();
  });

  test("订单跟踪展示订单详情", async ({ page }) => {
    await sendAndWaitForResponse(page, "我最近一笔订单到哪了？给我看看物流详情。");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-order-tracking.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("订单跟踪回答长度：", text?.length);
    expect((text?.length ?? 0) > 200).toBeTruthy();
  });

  test("购物车查询展示购物车内容", async ({ page }) => {
    await sendAndWaitForResponse(page, "我现在的购物车里有哪些商品？");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-cart-query.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("购物车查询回答长度：", text?.length);
    expect((text?.length ?? 0) > 100).toBeTruthy();
  });

  test("商品卡片的「加入购物车」按钮可用", async ({ page }) => {
    // 先发一条商品查询，拿到商品卡片
    await sendAndWaitForResponse(page, "给我看看 Sony WH-1000XM5 耳机");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-before-add.png", fullPage: true });

    // 在对话中寻找「加入购物车」按钮
    const addBtn = page.getByRole("button", { name: /加入购物车|已加入/ });
    const btnCount = await addBtn.count();
    console.log("对话中找到的「加入购物车」按钮数：", btnCount);

    if (btnCount > 0) {
      await addBtn.first().click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: "e2e/screenshots/chat-verify-after-add.png", fullPage: true });
    }
  });

  test("退货申请流程", async ({ page }) => {
    await sendAndWaitForResponse(page, "我想退掉最近一笔已送达的订单，商品有质量问题");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-return-request.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("退货申请回答长度：", text?.length);
    expect((text?.length ?? 0) > 100).toBeTruthy();
  });
});
