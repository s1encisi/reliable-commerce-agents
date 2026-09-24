import { test, expect, type Page } from "@playwright/test";
import { skipIfKnownGap } from "./parity-gaps";

/**
 * 一致性对齐（parity）关卡：验证前端期望的编排展示类界面，
 * 后端实际能力是否真的提供了。
 *
 * 这里刻意挑选的是 `web/e2e/` 其余部分从不触碰的功能。
 * 那套用例包含 9 个 spec、109 个测试，而它对后端敏感的界面
 * 总共只引用了一次，所以即便后端缺了这四项功能
 * 也能全绿通过。这里检查的全是「是否存在」——要防的是那种
 * 悄无声息的失败：
 *
 *   - 当 `GET /modes` 返回 404 时，模式切换器会返回 `null`，于是它
 *     直接消失，而不是报错
 *   - `/runs` 用 `.catch(() => {})` 吞掉了 404，于是页面看起来一切正常，
 *     面板却永远为空
 *   - 个人中心的「AI 记忆」卡片会显示「暂无记忆。与商品或评价智能体
 *     对话即可逐步建立您的画像。」——接口一旦缺失，这句空态文案会让
 *     「没有接口」与「确实还没有记忆」完全无法区分
 *   - 若后端的请求记录里没有 `mode` 字段，它就会被丢弃，
 *     于是用户选了工作流，却悄悄拿到了工具模式
 *
 * 一个只断言「没有出现错误」的测试，在上面每一种情况下都会通过。
 */

const CUSTOMER = { email: "alice.johnson@gmail.com", password: "customer123" };

/**
 * API 级断言所对话的编排器地址。默认取 compose 技术栈发布的端口，
 * 并可通过环境变量覆盖，以便带外验证另一个编排器实例——否则，把它跑在
 * 另一个端口、而前端仍指向默认实例时，那些断言就会悄悄验证错误的实例
 * 并报出一个假通过。
 */
const ORCH_URL = process.env.ORCH_URL ?? "http://localhost:8080";

async function login(page: Page) {
  await page.goto("/login");
  await page.fill('input[type="email"]', CUSTOMER.email);
  await page.fill('input[type="password"]', CUSTOMER.password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 15000 });
  await assertFrontendTalksToOrchUrl(page);
}

/**
 * 当被测前端指向的编排器与 `ORCH_URL` *不是同一个* 时，大声失败。
 *
 * 这不是假想：在搭建这个关卡的过程中，这种假通过真实发生过两次。
 * 最初的成因如今已不存在——后端地址以前是 `NEXT_PUBLIC_API_URL`，
 * 在构建时被内联进产物，于是第二个 dev server 从一份热构建目录启动时，
 * 会悄悄连上第一个的后端。现在前端在服务端代理 `/api/*`，地址改为
 * 运行时读取的 `ORCHESTRATOR_URL`，任何构建产物都无法编码错误的后端。
 *
 * 这个守卫依然保留，因为这个失败并没有消失，只是换了形态：以错误的
 * `ORCHESTRATOR_URL` 启动的前端，失败的样子一模一样——用实例 A 登录，
 * 这里所有 API 级断言却打在实例 B 上，最后跑出一个从未碰过它自称那个
 * 实例的绿色结果。令牌仍是最廉价的判别依据：它由提供登录的那个实例
 * 签发，所以从 `ORCH_URL` 拿到 401，就意味着两者不是同一个进程。
 */
async function assertFrontendTalksToOrchUrl(page: Page) {
  const token = await page.evaluate(
    () => localStorage.getItem("ecommerce_access_token") ?? "",
  );
  const probe = await page.request.get(`${ORCH_URL}/api/runs`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(
    probe.status(),
    `${test.info().project.use.baseURL} 上的前端签发的令牌被 ` +
      `ORCH_URL (${ORCH_URL}) 拒绝，说明两者不是同一个编排器实例。 ` +
      `本次运行会验证一个实例、却声称验证的是另一个—— ` +
      `请把 E2E_BASE_URL（而不是 PLAYWRIGHT_BASE_URL）指向那个 ` +
      `ORCHESTRATOR_URL 指向 ${ORCH_URL} 的前端。`,
  ).not.toBe(401);
}

/**
 * 等待一个对话回合结束。`isResponding` 为 true 时，输入框的提交按钮会
 * 显示停止图标，回合结束后再换回来——这才是唯一可靠的信号。
 * 「正在路由到专业智能体…」只意味着流式响应*开始*了，据此行动会和
 * `sendMessage` 里的守卫抢跑。
 */
async function sendMessageAndWaitForTurn(page: Page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  await page.waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
    timeout: 20000,
    state: "attached",
  }).catch(() => {});
  await page.waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
    timeout: 120000,
    state: "detached",
  });
}

test.describe("编排一致性", () => {
  // 串行执行，因为其中几个会驱动真实的「多智能体回合」，后面的用例依赖
  // 前面创建的状态。超时值远高于仓库默认值：工作流模式的一个回合会扇出到
  // 多个专业智能体、每个都发起真实的 LLM 调用，30 秒会在运行中途就掐断，
  // 于是失败看起来像元素缺失，而不是元素太慢。
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  test("模式切换器提供不止一种编排模式", async ({ page }) => {
    skipIfKnownGap("模式切换器提供不止一种编排模式");
    await login(page);
    await page.goto("/chat");

    // 只有当 GET /api/orchestration/modes 返回非空列表后，切换器才会渲染，
    // 所以它「存在」本身就是断言。
    const switcher = page.locator('[aria-label="编排模式"]').first();
    await expect(switcher, "模式切换器必须渲染出来，而不是悄悄返回 null").toBeVisible({
      timeout: 15000,
    });

    await switcher.click();

    // base-ui 把 select 选项渲染成普通的 div，既没有 role 也没有 data-slot，
    // 所以只能通过弹层里的文本来定位它们。
    const menu = page.locator('[data-slot="select-content"]');
    await expect(menu, "模式列表必须能打开").toBeVisible({ timeout: 10000 });
    await expect(menu.getByText(/工具路由|Tool Router/i).first()).toBeVisible();
    await expect(
      menu.getByText(/购前调研|Pre-Purchase|处理权交接|Handoff|群聊|Group Chat/i).first(),
      "除了默认的工具路由之外，还必须提供至少一种模式",
    ).toBeVisible();
  });

  test("选中某个工作流模式会被真正尊重，而不是被悄悄降级", async ({ page }) => {
    skipIfKnownGap("选中某个工作流模式会被真正尊重，而不是被悄悄降级");
    await login(page);
    await page.goto("/chat");

    const switcher = page.locator('[aria-label="编排模式"]').first();
    await switcher.click();
    await page.locator('[data-slot="select-content"]').getByText(/购前调研|Pre-Purchase/i).first().click();

    await sendMessageAndWaitForTurn(page, "我该买 Sony WH-1000XM5 吗？");

    // UI 通过渲染 OrchestrationGraph 来体现实际执行的模式，而它的渲染
    // 以 msg.mode 已被赋值为前提。断言一个 pre-purchase 图独有的节点标签，
    // 正是区分「被尊重」与「被悄悄降级」的关键——丢弃了该字段的后端
    // 在工具模式下依然能正常作答，表面上看起来一切正常。
    const graph = page.locator('svg[id^="orchestration-graph"]').first();
    await expect(graph, "工作流模式下必须渲染出一张图").toBeVisible({ timeout: 20000 });
    await expect(
      graph.getByText(/reviews|stock|price|synthes|评论|库存|定价|汇总|综合/i).first(),
      "这张图必须是 pre-purchase 的图，而不是工具路由的图",
    ).toBeVisible({ timeout: 10000 });
  });

  test("工作流模式下会渲染编排图", async ({ page }) => {
    skipIfKnownGap("工作流模式下会渲染编排图");
    await login(page);
    await page.goto("/chat");

    const switcher = page.locator('[aria-label="编排模式"]').first();
    await switcher.click();
    await page.locator('[data-slot="select-content"]').getByText(/购前调研|Pre-Purchase/i).first().click();

    await sendMessageAndWaitForTurn(page, "我该买 Dyson V15 吗？");

    // Mermaid 渲染成一个内联 SVG，组件 id 为 "orchestration-graph-N"。
    // 容器为空意味着图接口返回了 404，进而执行了 setSource(null)。
    const graph = page.locator('svg[id^="orchestration-graph"]').first();
    await expect(graph, "这张图必须渲染成 SVG，而不是一个空面板").toBeVisible({
      timeout: 20000,
    });
  });

  test("选中两种模式后可以运行模式对比", async ({ page }) => {
    skipIfKnownGap("选中两种模式后可以运行模式对比");
    await login(page);
    await page.goto("/chat");

    await page.getByRole("button", { name: /^对比$/ }).first().click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 15000 });

    // 这个对话框的数据来自 GET /api/orchestration/modes。在缺少该接口的
    // 后端上，对话框仍会打开但列表为空，运行按钮则被「至少选择 2 个模式」
    // 的守卫永久禁用——所以「列出模式」才是真正要紧的断言。
    await expect(dialog.getByText(/工具路由|Tool Router/i).first()).toBeVisible();
    await expect(dialog.getByText(/购前调研|Pre-Purchase|处理权交接|Handoff|群聊|Group Chat/i).first()).toBeVisible();

    // 刻意只停在「模式已被列出」，而不去驱动选择交互。这个关卡存在的目的是
    // 证明*后端*提供了 UI 所需的数据；对话框如何切换选中项属于 UI 自身的
    // 关注点，现有用例集已经覆盖。在这里断言 UI 内部细节，只会让关卡因为
    // 与一致性无关的原因而失败。
    await expect(
      dialog.getByRole("button", { name: /开始对比/ }),
      "只要模式可用，运行对比的控件就必须存在",
    ).toBeVisible();
  });

  test("/runs 上会列出某次运行的检查点", async ({ page }) => {
    skipIfKnownGap("/runs 上会列出某次运行的检查点");
    await login(page);

    // 刻意不先创建一次工作流运行。那样做就需要选择某个工作流模式，会把
    // 这个测试与模式注册表耦合在一起，并让它在「已有检查点接口、但还没有
    // 模式接口」的后端上无法测试——两种彼此独立的能力被当成一种来失败。
    // 任何一条已存在的运行记录都足以证明该接口在正常服务。
    await page.goto("/runs");
    await expect(page.getByText(/智能体运行记录/).first()).toBeVisible({ timeout: 20000 });

    // 在 API 层断言，是因为 /runs 会用 `.catch(() => {})` 吞掉 404：
    // 页面渲染得完美无缺，检查点面板只是空着，所以光看 DOM，「没有接口」
    // 和「没有检查点」完全一样。这种无法区分，正是要防的那种静默失败。
    const token = await page.evaluate(() =>
      localStorage.getItem("ecommerce_access_token") ?? "",
    );
    const runs = await page.request.get(`${ORCH_URL}/api/runs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(runs.status()).toBe(200);

    const runId = (await runs.json()).entries?.[0]?.id;
    expect(runId, "必须至少有一条可检查的运行记录").toBeTruthy();

    const checkpoints = await page.request.get(`${ORCH_URL}/api/runs/${runId}/checkpoints`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(
      checkpoints.status(),
      "检查点接口必须正常服务；正是 404 让 /runs 悄悄变成了空页面",
    ).toBe(200);
  });

  test("个人中心展示一条已存储的记忆", async ({ page }) => {
    skipIfKnownGap("个人中心展示一条已存储的记忆");
    await login(page);

    // 刻意在 API 层断言，而不是通过卡片。
    //
    // 无论接口返回空列表还是 404，卡片都会渲染「暂无记忆。与商品或评价
    // 智能体对话即可逐步建立您的画像。」——UI 两种情况都会把错误吞掉。
    // 所以光看 DOM，一个没有记忆接口的后端，和一个单纯还没有记忆的用户
    // 完全无法区分，而这正是这个关卡要捕获的静默失败。驱动一个回合、
    // 然后指望模型选择调用 store_memory，也会让它变得不稳定。
    await page.goto("/profile");
    await expect(page.getByText(/AI 记忆/).first()).toBeVisible({ timeout: 15000 });

    const token = await page.evaluate(() =>
      localStorage.getItem("ecommerce_access_token") ?? "",
    );
    expect(token, "为了做 API 断言，会话令牌必须可读").not.toBe("");

    const response = await page.request.get(`${ORCH_URL}/api/user/memories`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(
      response.status(),
      "记忆接口必须正常服务；正是 404 让卡片上的提示语变成了谎话",
    ).toBe(200);
  });

  test("有事实依据的回答会渲染核验徽章", async ({ page }) => {
    skipIfKnownGap("有事实依据的回答会渲染核验徽章");
    await login(page);
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "给我看看 400 元以下的无线耳机");

    const badge = page.getByText(/已对照数据库核验/).first();
    await expect(
      badge,
      "商品类回答必须报告核验结果；徽章从不渲染就是那种静默失败",
    ).toBeVisible({ timeout: 20000 });
  });
});
