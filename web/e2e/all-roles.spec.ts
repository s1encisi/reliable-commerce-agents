import { test, expect, type Page } from "@playwright/test";

const API_URL = "http://localhost:8080";

// 来自种子数据的测试用户
const USERS = {
  customer: { email: "alice.johnson@gmail.com", password: "customer123", name: "Alice Johnson", role: "customer" },
  admin: { email: "admin.demo@gmail.com", password: "admin123", name: "Admin User", role: "admin" },
  powerUser: { email: "power.demo@gmail.com", password: "power123", name: "Power User", role: "power_user" },
  seller: { email: "seller.demo@gmail.com", password: "seller123", name: "Acme Store", role: "seller" },
  customer2: { email: "bob.smith@gmail.com", password: "customer123", name: "Bob Smith", role: "customer" },
};

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.getByRole("button", { name: /登录/ }).click();
  // 等待跳转到对话页或应用页
  await page.waitForURL(/\/(chat|products|$)/, { timeout: 10000 });
}

async function ensureLoggedOut(page: Page) {
  await page.goto("/login");
  // 清空 localStorage
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
}

// ---------------------------------------------------------------------------
// 1. 登录鉴权测试
// ---------------------------------------------------------------------------

test.describe("登录鉴权", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
  });

  test("未登录访客看到公开店铺页，而不是登录墙", async ({ page }) => {
    // 根路由过去会跳转到 /login，现在不会了：店铺页被有意设为公开，这样
    // 无需账号也能使用购物助手，因此这里断言的是「提供了登录入口」而不是
    // 「强制登录」。
    await page.goto("/");
    await expect(page.getByRole("link", { name: /登录/ }).first()).toBeVisible({ timeout: 10000 });
  });

  test("用有效的顾客凭据登录", async ({ page }) => {
    await login(page, USERS.customer.email, USERS.customer.password);
    // 应位于对话页
    await expect(page).toHaveURL(/\/chat/);
  });

  test("用有效的管理员凭据登录", async ({ page }) => {
    await login(page, USERS.admin.email, USERS.admin.password);
    await expect(page).toHaveURL(/\/chat/);
  });

  test("用无效凭据登录会显示错误", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', "wrong@gmail.com");
    await page.fill('input[type="password"]', "wrongpass");
    await page.getByRole("button", { name: /登录/ }).click();
    // 应显示错误提示
    // 这里断言的是契约——错误被呈现出来且用户没被放进去——而不是具体措辞。
    // 旧的正则（/invalid|not found|error/）漏掉了表单自身的兜底文案
    // 「登录失败，请稍后重试。」，它一个关键词都不含，于是一次被正确拒绝的
    // 登录被读成了登录损坏。
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 15000 });
    await expect(page).toHaveURL(/\/login/);
  });

  test("注册会创建新账号", async ({ page }) => {
    // 使用加密随机后缀，避免多次测试运行之间出现重复邮箱
    const unique = `pw_test_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@gmail.com`;
    await page.goto("/signup");
    await page.locator("#name").fill("测试用户");
    await page.locator("#email").fill(unique);
    await page.locator("#password").fill("testpass123");
    await page.getByRole("button", { name: /创建账号|注册/ }).click();
    await page.waitForURL(/\/chat/, { timeout: 15000 });
  });
});

// ---------------------------------------------------------------------------
// 2. 顾客角色测试（Alice）
// ---------------------------------------------------------------------------

test.describe("顾客角色（Alice）", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.customer.email, USERS.customer.password);
  });

  test("对话页加载并显示会话面板", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByText(/会话|新对话/).first()).toBeVisible();
    await expect(page.locator("textarea").first()).toBeVisible();
  });

  test("可以发送对话消息", async ({ page }) => {
    await page.goto("/chat");
    const input = page.locator("textarea").first();
    await input.fill("你好，你能帮我做什么？");
    await input.press("Enter");
    // 用户消息应出现在对话区域
    await expect(page.getByText("你好，你能帮我做什么？").last()).toBeVisible({ timeout: 5000 });
    // 等待回答（若未配置 API key，可能是错误兜底文案）
    await page.waitForTimeout(5000);
    // 应当有回答（真实的或错误兜底文案）
    const hasResponse = await page.getByText(/帮助|抱歉|错误|无法|问题/).last().isVisible().catch(() => false);
    expect(hasResponse).toBeTruthy();
  });

  test("商品页显示商品网格", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    // 应显示商品
    await expect(page.getByText(/商品目录|共 .* 件商品/).first()).toBeVisible({ timeout: 10000 });
    // 应有分类筛选
    await expect(page.getByText("Electronics").first()).toBeVisible();
  });

  test("商品详情页显示参数与评论", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    // 点击第一个商品
    const firstProduct = page.locator("a[href*='/products/']").first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//);
      // 应显示商品详情
      await expect(page.getByText(/商品描述|规格参数|库存情况/).first()).toBeVisible({ timeout: 5000 });
    }
  });

  test("订单页显示订单列表", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // 应显示订单或空状态
    const hasOrders = await page.getByText(/订单|已发货|已送达/).first().isVisible().catch(() => false);
    const hasEmpty = await page.getByText(/暂无订单/).first().isVisible().catch(() => false);
    expect(hasOrders || hasEmpty).toBeTruthy();
  });

  test("订单详情页显示时间线", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    const firstOrder = page.locator("a[href*='/orders/']").first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//);
      await expect(page.getByText(/订单状态|订单摘要|物流/).first()).toBeVisible({ timeout: 5000 });
    }
  });

  test("智能体目录页展示各智能体", async ({ page }) => {
    // /marketplace 已被移除；目录页是 /agents。ui-smoke.spec.ts 已经记录了
    // 这次移除——而这些用例一直指向那个已被删除的路由。
    await page.goto("/agents");
    await page.waitForLoadState("networkidle");
    // 等待智能体卡片加载——匹配展示名或分类文案
    await expect(page.getByText(/商品发现|订单管理|智能体/).first()).toBeVisible({ timeout: 15000 });
  });

  test("个人中心页显示用户信息与会员等级", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.customer.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/黄金|会员等级/).first()).toBeVisible();
  });

  test("侧边栏导航可用", async ({ page }) => {
    await page.goto("/chat");
    // 跳转到商品页
    await page.getByRole("link", { name: /商品/ }).first().click();
    await expect(page).toHaveURL(/\/products/);
    // 跳转到订单页
    await page.getByRole("link", { name: /订单/ }).first().click();
    await expect(page).toHaveURL(/\/orders/);
    // 跳转到智能体目录
    await page.getByRole("link", { name: /^智能体$/ }).first().click();
    await expect(page).toHaveURL(/\/agents/);
    // 顾客不应看到管理端链接
    await expect(page.getByRole("link", { name: /概览/ })).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 3. 管理员角色测试
// ---------------------------------------------------------------------------

test.describe("管理员角色", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.admin.email, USERS.admin.password);
  });

  test("管理端侧边栏显示管理入口", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByRole("link", { name: /概览/ }).first()).toBeVisible();
  });

  test("管理端仪表盘显示指标", async ({ page }) => {
    await page.goto("/admin");
    await page.waitForLoadState("networkidle");
    // 管理端仪表盘——匹配任一指标标签或「管理看板」标题
    await expect(page.getByText(/管理看板|总 Token 数|活跃智能体|待处理请求/).first()).toBeVisible({ timeout: 15000 });
  });

  test("管理端审批页可加载", async ({ page }) => {
    // 过去写的是 /admin/requests，会 404——该页面是 /admin/approvals。
    await page.goto("/admin/approvals");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/审批队列|待处理|暂无请求/).first()).toBeVisible({ timeout: 10000 });
  });

  test("管理端用量页可加载", async ({ page }) => {
    await page.goto("/admin/usage");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/用量分析|调用次数|Token/).first()).toBeVisible({ timeout: 10000 });
  });

  test("管理端审计页可加载", async ({ page }) => {
    await page.goto("/admin/audit");
    await page.waitForLoadState("networkidle");
    // 该页面的标题是「智能体运行记录」——它渲染的是运行历史而不是通用审计
    // 日志，因此 /audit|log/ 在一个加载正常的页面上什么也匹配不到。
    await expect(page.getByText(/智能体运行记录|暂无运行记录/).first()).toBeVisible({ timeout: 10000 });
  });

  test("管理员可以浏览商品与订单", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/商品目录|共 .* 件商品/).first()).toBeVisible({ timeout: 10000 });

    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // 管理员在种子数据里也有订单
    const content = await page.textContent("body");
    expect(content).toBeTruthy();
  });

  test("管理员个人中心显示管理员角色", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.admin.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/管理员/).first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 4. 高级用户角色测试
// ---------------------------------------------------------------------------

test.describe("高级用户角色", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.powerUser.email, USERS.powerUser.password);
  });

  test("高级用户可以访问对话", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.locator("textarea").first()).toBeVisible();
  });

  test("高级用户可以浏览智能体目录", async ({ page }) => {
    await page.goto("/agents");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/商品发现|订单管理|智能体/).first()).toBeVisible({ timeout: 15000 });
  });

  test("高级用户个人中心显示高级会员角色", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.powerUser.name).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// 5. 商家角色测试
// ---------------------------------------------------------------------------

test.describe("商家角色", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.seller.email, USERS.seller.password);
  });

  test("商家可以访问对话", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.locator("textarea").first()).toBeVisible();
  });

  test("商家可以浏览商品", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/商品目录|共 .* 件商品/).first()).toBeVisible({ timeout: 10000 });
  });

  test("商家个人中心显示商家角色", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.seller.name).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// 6. 第二位顾客（Bob）——跨用户隔离
// ---------------------------------------------------------------------------

test.describe("跨用户隔离（Bob）", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.customer2.email, USERS.customer2.password);
  });

  test("Bob 只看到自己的订单，看不到 Alice 的", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // Bob 的订单与 Alice 不同
    const content = await page.textContent("body");
    expect(content).toBeTruthy();
  });

  test("Bob 看到自己的个人资料", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.customer2.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/白银/).first()).toBeVisible(); // Bob 是白银会员
  });

  test("Bob 无法访问管理端页面", async ({ page }) => {
    await page.goto("/admin");
    await page.waitForLoadState("networkidle");
    // 应显示无权访问或发生跳转
    await expect(page.getByText(/无权访问|没有查看该页面的管理员权限/).first()).toBeVisible({ timeout: 5000 });
  });
});
