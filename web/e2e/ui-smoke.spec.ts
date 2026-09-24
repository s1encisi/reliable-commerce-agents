import { test, expect, type Page } from "@playwright/test";

/**
 * 增强版应用外壳 + 第 2 阶段各界面的 UI 冒烟测试套件。
 *
 * 登录态与后端 API 均为模拟（localStorage 会话 + 请求拦截），因此本套件
 * 可脱离完整技术栈、仅针对前端运行。与 e2e/ 下的其他文件一样，它在本地
 * 针对 `pnpm dev` 运行，不进 CI。
 */

const IMG =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='300'%3E%3Crect width='400' height='300' fill='%2393c5cf'/%3E%3C/svg%3E";

const USAGE = {
  overall: {
    total_requests: 12840,
    total_tokens_in: 4230000,
    total_tokens_out: 1180000,
    avg_duration_ms: 1240,
    pending_requests: 2,
  },
  by_agent: [
    { agent_name: "orchestrator", request_count: 5200, tokens_in: 1800000, tokens_out: 520000, avg_duration_ms: 1450 },
    { agent_name: "product_discovery", request_count: 3100, tokens_in: 980000, tokens_out: 240000, avg_duration_ms: 980 },
  ],
  daily: [
    { date: "2026-05-27", request_count: 1890, tokens_in: 680000, tokens_out: 188000 },
    { date: "2026-05-28", request_count: 2360, tokens_in: 820000, tokens_out: 230000 },
    { date: "2026-05-29", request_count: 2410, tokens_in: 500000, tokens_out: 152000 },
  ],
};

const ORDERS = {
  orders: [
    { id: "a1b2c3d4e5", status: "delivered", total: 329.98, created_at: "2026-05-20" },
    { id: "f6g7h8i9j0", status: "shipped", total: 89.5, created_at: "2026-05-26" },
  ],
};

const PRODUCTS = {
  products: [
    { id: "p1", name: "Sony WH-1000XM5", price: 299.99, brand: "Sony", category: "Electronics", image_url: IMG },
    { id: "p2", name: "Ember Smart Mug 2", price: 129.95, brand: "Ember", category: "Home", image_url: IMG },
  ],
  total: 2,
  categories: ["Electronics", "Home"],
};

async function seedAuth(
  page: Page,
  role: "customer" | "admin" = "customer",
  theme: "light" | "dark" = "light",
) {
  await page.addInitScript(
    ([r, t]) => {
      localStorage.setItem(
        "ecommerce_user",
        JSON.stringify({ name: "Alice Johnson", email: "alice@example.com", role: r }),
      );
      localStorage.setItem("ecommerce_access_token", "mock.jwt");
      localStorage.setItem("ecommerce_refresh_token", "mock.refresh");
      localStorage.setItem("theme", t as string);
    },
    [role, theme] as const,
  );
}

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/api/admin/usage")) return route.fulfill({ json: USAGE });
    if (url.includes("/api/orders")) return route.fulfill({ json: ORDERS });
    if (url.includes("/api/products")) return route.fulfill({ json: PRODUCTS });
    if (url.includes("/api/cart"))
      return route.fulfill({ json: { items: [], item_count: 2, subtotal: 174.9 } });
    if (url.includes("/api/conversations")) return route.fulfill({ json: [] });
    // 对话页在挂载时会读取编排模式注册表。此处返回 {} 会让它落到错误边界
    // （「此页面无法加载」），这正是当时针对一个根本没渲染出来的页面做管理
    // 端侧边栏断言会失败的原因——与所测后端无关。
    if (url.includes("/api/orchestration/modes"))
      return route.fulfill({ json: { modes: [{ name: "tool", label: "工具路由", description: "", is_graph: false }] } });
    // 未匹配到的请求返回空「数组」而不是空对象：本应用绝大多数接口返回的是
    // 集合，而 {} 是最容易在 .map() 里抛错的那种形状。
    return route.fulfill({ json: [] });
  });
}

test.describe("公开页面", () => {
  test("落地页渲染主视觉、智能体与行动号召", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /多智能体平台/ }),
    ).toBeVisible();
    await expect(page.getByText("认识这些智能体")).toBeVisible();
    await expect(
      page.getByText("商品发现", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /立即体验/ })).toBeVisible();
  });

  test("登录页渲染表单", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole("button", { name: /登录/ })).toBeVisible();
  });
});

test.describe("已登录应用外壳", () => {
  test("首页仪表盘渲染问候语、快捷提问与订单", async ({ page }) => {
    await seedAuth(page);
    await mockApi(page);
    await page.goto("/home");
    await expect(page.getByText(/(早上好|下午好|晚上好)，Alice/)).toBeVisible();
    // 快捷提问派生自 DEMO_SCENARIOS（web/src/lib/scenarios.ts），因此断言一个
    // 真实存在的标签，而不是一句在场景列表被改动的那一刻就已过时的硬编码
    // 文案。
    await expect(
      page.getByRole("link", { name: "商品搜索" }).first(),
    ).toBeVisible();
    await expect(page.getByText("最近订单")).toBeVisible();
    await expect(page.getByText("专业智能体")).toBeVisible();
  });

  test("分组侧边栏对管理员显示管理端导航（用量统计、审计）", async ({ page }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    // 用 /home 而不是 /chat。本用例考察的是「侧边栏」，而侧边栏在每个已登录
    // 页面都会渲染——相比之下 /chat 需要足够多的实时数据，模拟 API 会把它送
    // 进错误边界（「此页面无法加载」），随后这里每一条断言都会因与导航毫不
    // 相干的原因失败。
    //
    // 另外值得单独记一笔：API 返回意外形状时对话页会硬崩溃而不是降级，这是
    // 一个真实的健壮性缺口，不是测试假象。已记录在计划 20 中；此处不修，因为
    // 扩大错误边界并不属于导航改动。
    await page.goto("/home");
    await expect(page.getByRole("link", { name: "对话" }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "用量统计" })).toBeVisible();
    // 没有「审计」：/admin/audit 与 /runs 重复，其导航入口已被有意移除。
    // src/lib/nav.test.ts 断言它不存在，因此在这里期待它会让两个套件互相
    // 矛盾——这个 e2e 用例只是没能跟上改动。页面本身仍然存在，只是链接没了。
    await expect(page.getByRole("link", { name: "Audit" })).toHaveCount(0);
    // 智能体市场已被整体移除。
    await expect(page.getByRole("link", { name: "Marketplace" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Requests" })).toHaveCount(0);
  });

  test("买家只能看到「购物」+「账户」导航", async ({ page }) => {
    await seedAuth(page, "customer");
    await mockApi(page);
    await page.goto("/home");
    // 限定在侧边栏内——「对话」/「个人中心」也会出现在首页的「进入对话」
    // 快捷入口以及顶栏头像（aria-label="个人中心"）上。
    const sidebar = page.getByRole("complementary");
    await expect(sidebar.getByRole("link", { name: "对话", exact: true })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "个人中心" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Marketplace" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "My Agents" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "用量统计" })).toHaveCount(0);
  });

  test("命令面板可打开并完成跳转", async ({ page }) => {
    await seedAuth(page);
    await mockApi(page);
    await page.goto("/home");
    // 通过顶栏搜索按钮打开（同时覆盖 ⌘K 集成）。
    await page.getByRole("button", { name: /搜索/ }).click();
    const search = page.getByPlaceholder(/搜索页面/);
    await expect(search).toBeVisible();
    await search.fill("商品");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/products/);
  });

  test("主题切换可切换到深色模式", async ({ page }) => {
    await seedAuth(page, "customer", "light");
    await mockApi(page);
    await page.goto("/home");
    const toggle = page.getByRole("button", { name: /切换到深色模式/ });
    await toggle.click();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("管理端用量页渲染 KPI 与图表", async ({ page }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    await page.goto("/admin/usage");
    await expect(
      page.getByRole("heading", { name: /用量分析/ }),
    ).toBeVisible();
    await expect(page.getByText("每日活跃度")).toBeVisible();
    // recharts 会渲染一个 SVG 画布
    await expect(page.locator("svg.recharts-surface").first()).toBeVisible();
  });
});
