import { test, expect, type Page } from "@playwright/test";

/**
 * 覆盖全部用户角色的对话 E2E 测试。
 * 验证完整流程：登录 → 发送消息 → 收到 LLM 回答 → 校验界面。
 */

const USERS = [
  { email: "alice.johnson@gmail.com", password: "customer123", name: "Alice Johnson", role: "customer" },
  { email: "admin.demo@gmail.com", password: "admin123", name: "Admin User", role: "admin" },
  { email: "bob.smith@gmail.com", password: "customer123", name: "Bob Smith", role: "customer" },
  { email: "power.demo@gmail.com", password: "power123", name: "Power User", role: "power_user" },
  { email: "seller.demo@gmail.com", password: "seller123", name: "Acme Store", role: "seller" },
];

// 为 LLM 回答加长超时
test.setTimeout(90_000);

async function loginAndGoToChat(page: Page, email: string, password: string) {
  // 清除已有会话
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
  await page.goto("/login");

  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 10000 });
}

async function sendMessageAndWaitForResponse(page: Page, message: string): Promise<string> {
  const input = page.locator("textarea").first();
  await input.fill(message);
  await input.press("Enter");

  await expect(page.getByText(message).last()).toBeVisible({ timeout: 5000 });

  // 等待这一轮真正结束。输入区的提交按钮在 isResponding 为 true 时显示停止
  // 图标，本轮结束时再换回来——这是唯一可靠的信号，也是
  // orchestration-parity.spec.ts 依赖的同一个信号。改为等待文本会和流式输出
  // 抢时序。
  await page
    .waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
      timeout: 20000,
      state: "attached",
    })
    .catch(() => {});
  await page.waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
    timeout: 120000,
    state: "detached",
  });

  // 读取渲染出的助手消息整体，而不是碰巧含有关键词的、最小的那个元素。此前
  // 的定位器按 /help|assist|.../ 匹配，于是返回的是胜出的那个极小节点——
  // 经常只有一个六字符的单词——结果「response.length > 20」这条断言会对一个
  // 完全正常的回答判失败。
  const rendered = page.locator('[class*="prose"]').last();
  await expect(rendered).toBeVisible({ timeout: 10000 });
  return (await rendered.textContent()) ?? "";
}

// ---------------------------------------------------------------------------
// 各用户的对话测试
// ---------------------------------------------------------------------------

for (const user of USERS) {
  test.describe(`对话 — ${user.role}（${user.name}）`, () => {
    test.beforeEach(async ({ page }) => {
      await loginAndGoToChat(page, user.email, user.password);
    });

    test("发送问候语并收到 LLM 回答", async ({ page }) => {
      const response = await sendMessageAndWaitForResponse(page, "你好，你能帮我做什么？");

      // LLM 应当就其能力给出回答
      expect(response.length).toBeGreaterThan(20);

      // 助手消息上应显示「orchestrator」徽章
      await expect(page.getByText("orchestrator").first()).toBeVisible();
    });

    test("首条消息后会话出现在侧边栏", async ({ page }) => {
      await sendMessageAndWaitForResponse(page, "介绍一下你们的商品");

      // 会话应出现在侧边栏中
      const sidebar = page.locator("text=/介绍一下你们的商品/i").first();
      await expect(sidebar).toBeVisible({ timeout: 5000 });
    });

    test("可以在同一会话中发送多条消息", async ({ page }) => {
      // 第一条消息
      await sendMessageAndWaitForResponse(page, "你们有哪些商品分类？");

      // 同一会话中的第二条消息
      const input = page.locator("textarea").first();
      await input.fill("再多讲讲电子类商品");
      await input.press("Enter");

      // 等待第二条回答
      await page.waitForTimeout(2000);
      const secondResponse = page.locator("text=/电子|商品|设备|耳机|音箱/i").last();
      await expect(secondResponse).toBeVisible({ timeout: 60000 });
    });

    test("新建对话按钮会创建全新会话", async ({ page }) => {
      // 发送第一条消息
      await sendMessageAndWaitForResponse(page, "第一个会话的消息");

      // 点击「新对话」按钮
      const newChatBtn = page.getByRole("button", { name: /新对话/ }).first();
      if (await newChatBtn.isVisible()) {
        await newChatBtn.click();
        await page.waitForTimeout(500);

        // 在新会话中发送消息
        await sendMessageAndWaitForResponse(page, "第二个会话的消息");

        // 两个会话都应出现在侧边栏中
        await expect(page.getByText(/第一个会话的消息/).first()).toBeVisible();
        await expect(page.getByText(/第二个会话的消息/).first()).toBeVisible();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 跨用户的对话测试
// ---------------------------------------------------------------------------

test.describe("对话 — 跨用户", () => {
  test("Alice 的会话对 Bob 不可见", async ({ page }) => {
    // 以 Alice 身份登录并创建一个会话
    await loginAndGoToChat(page, "alice.johnson@gmail.com", "customer123");
    await sendMessageAndWaitForResponse(page, "Alice 独有的对话测试消息 xyz123");
    await page.waitForTimeout(1000);

    // 退出登录并以 Bob 身份登录
    await loginAndGoToChat(page, "bob.smith@gmail.com", "customer123");
    await page.waitForTimeout(1000);

    // Bob 不应看到 Alice 的会话
    const aliceConv = page.getByText(/Alice 独有的对话测试消息 xyz123/i);
    await expect(aliceConv).not.toBeVisible();
  });
});
