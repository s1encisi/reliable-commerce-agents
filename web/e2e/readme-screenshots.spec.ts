/**
 * readme-screenshots.spec.ts
 *
 * 抓取 README 的 Screens 流程导览截图，输出到 docs/images/。
 * 需要完整技术栈已启动（./scripts/dev.sh），且 LLM 密钥可用。
 *
 * 运行：cd web && pnpm exec playwright test e2e/readme-screenshots.spec.ts
 *
 * 输出直接落在 docs/images/，这样 README 里的图片引用无需额外复制步骤即可生效。
 * 对话类截图是非确定性的（取决于 LLM）；凡是渲染出空白/加载态的截图请重跑。
 */

import { test, type Page } from "@playwright/test";
import * as path from "path";

const OUT_DIR = path.resolve(__dirname, "../../docs/images");

const USERS = {
  customer: { email: "alice.johnson@gmail.com", password: "customer123" },
  admin: { email: "admin.demo@gmail.com", password: "admin123" },
  seller: { email: "seller.demo@gmail.com", password: "seller123" },
};

test.use({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});

test.setTimeout(180_000);

// ---------------------------------------------------------------------------
// 辅助函数 —— 与 portfolio-screenshots.spec.ts 的约定保持一致
// ---------------------------------------------------------------------------

async function clearSession(page: Page) {
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
}

async function login(page: Page, email: string, password: string) {
  await clearSession(page);
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 15_000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

async function scrollToTop(page: Page) {
  // 对话消息位于一个 overflow-y-auto 的容器里，会自动滚动到最新
  // 一条消息。重置所有滚动容器，好让用户的提问在顶部可见。
  await page.evaluate(() => {
    document.querySelectorAll(".overflow-y-auto").forEach((el) => {
      el.scrollTop = 0;
    });
  });
  await page.waitForTimeout(400);
}

async function shoot(page: Page, filename: string, fullPage = true) {
  await page.waitForTimeout(800);
  await page.screenshot({
    path: path.join(OUT_DIR, filename),
    fullPage,
  });
  console.log(`已保存 ${filename}`);
}

async function sendChat(page: Page, message: string, waitMs = 25_000) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();
  await input.fill(message);
  await input.press("Enter");
  // 等待 LLM 回复与 SSE 步骤落地（卡片在流式响应结束后才出现）
  await page.waitForTimeout(waitMs);
}

// ---------------------------------------------------------------------------
// A 组 —— 访客 / 未登录
// ---------------------------------------------------------------------------

test("guest-01 公共商城首页", async ({ page }) => {
  await clearSession(page);
  await page.goto("/shop");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1_000);
  await shoot(page, "flow-guest-storefront.png", false);
});

test("guest-02 公共商品列表", async ({ page }) => {
  await clearSession(page);
  await page.goto("/shop/products");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1_000);
  await shoot(page, "flow-guest-browse.png");
});

test("guest-03 公共商品详情", async ({ page }) => {
  await clearSession(page);
  // 从公开 REST 接口取第一个商品 id（无需鉴权）
  await page.goto("/shop/products");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1_000);

  // 尝试点击第一个商品卡片的图片
  const productImg = page.locator("img[src*='picsum'], img[alt]").first();
  await productImg
    .click({ timeout: 8_000 })
    .catch(async () => {
      // 兜底：通过 API 解析
      const resp = await page.request.get("http://localhost:8080/api/products?limit=1");
      const data = await resp.json().catch(() => null);
      const id = data?.products?.[0]?.id ?? data?.[0]?.id;
      if (id) await page.goto(`/shop/products/${id}`);
    });

  await page.waitForURL(/\/shop\/products\/[^/]+$/, { timeout: 10_000 }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await shoot(page, "flow-guest-product.png");
});

test("guest-04 公共 AI 助手", async ({ page }) => {
  await clearSession(page);
  await page.goto("/shop/assistant");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(500);
  await sendChat(page, "给我看看无线耳机", 28_000);
  await scrollToTop(page);
  await shoot(page, "flow-guest-assistant.png");
});

// ---------------------------------------------------------------------------
// B 组 —— 已登录的 AI 购物流程（对话界面）
// ---------------------------------------------------------------------------

test("flow-01 商品搜索", async ({ page }) => {
  await login(page, USERS.customer.email, USERS.customer.password);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await sendChat(page, "给我看看无线耳机", 28_000);
  await scrollToTop(page);
  await shoot(page, "flow-product-search.png");
});

test("flow-02 加入购物车", async ({ page }) => {
  await login(page, USERS.customer.email, USERS.customer.password);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await sendChat(page, "帮我找降噪耳机，把最合适的那款加入购物车", 28_000);
  await scrollToTop(page);
  await shoot(page, "flow-add-to-cart.png");
});

test("flow-03 查看购物车", async ({ page }) => {
  await login(page, USERS.customer.email, USERS.customer.password);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await sendChat(page, "看看我的购物车里有什么", 25_000);
  await scrollToTop(page);
  await shoot(page, "flow-view-cart.png");
});

test("flow-04 订单跟踪", async ({ page }) => {
  await login(page, USERS.customer.email, USERS.customer.password);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await sendChat(page, "我最近一笔订单的状态是什么？", 28_000);
  await scrollToTop(page);
  await shoot(page, "flow-order-tracking.png");
});

test("flow-05 退款 / 退货", async ({ page }) => {
  await login(page, USERS.customer.email, USERS.customer.password);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await sendChat(
    page,
    "为我最近一笔已送达的订单发起退货，并把退货面单给我。",
    28_000,
  );
  await scrollToTop(page);
  await shoot(page, "flow-refund.png");
});
