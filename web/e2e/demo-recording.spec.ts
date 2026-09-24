/**
 * demo-recording.spec.ts
 *
 * 录制一段 60-90 秒的无声短片，放在 README 顶部与文档站点首页。
 * 它不是测试——几乎不断言任何东西。它用一条连续镜头，把本仓库
 * 与众不同的六件事演示一遍：
 *
 *   1. 带生成式 UI 的流式对话（渲染成商品卡片，而不是原始 JSON）
 *   2. 在输入框中切换编排模式
 *   3. 编排图依据实时 SSE 事件逐个节点地动画展开
 *   4. 一个工作流在 human-in-the-loop 关卡处暂停
 *   5. /runs 上的待审批记录
 *   6. 批准后，运行从真实的 Postgres 检查点恢复
 *
 * 需要完整技术栈已启动，且 LLM 密钥可用：
 *   ./scripts/dev.sh --demo
 *
 * 运行：
 *   cd web && pnpm exec playwright test e2e/demo-recording.spec.ts
 *
 * 输出：test-results/<...>/video.webm。用下面的命令转换后嵌入：
 *   ffmpeg -i video.webm -c:v libx264 -crf 24 -pix_fmt yuv420p demo.mp4
 *
 * 把它写成提交进仓库的脚本、而不是手工录屏，意味着每次 UI 改动后都能
 * 重新录制，而不会慢慢变成一段过时的素材。
 *
 * ── 两件会毁掉这次录制的事，都是踩过坑才总结出来的 ──────────────────────
 *
 * 提示词必须是商品目录真能回答的。`search_products` 走的是
 * ILIKE '%<整段短语>%'，所以 "running shoes" 什么都匹配不到，而
 * "Allbirds" 能匹配上——种子目录里没有任何商品的文本包含 "shoes" 这个词。
 * 一个听起来自然、却回答「我找不到」的提示词是最糟糕的第一印象，
 * 因此下面每一句提示词都对照过种子数据。
 *
 * 绝不要等待「正在路由到专业智能体…」这类文案。一个回合进行中时，
 * 输入框的提交按钮会换成停止图标，回合结束后再换回来；这才是唯一可靠的
 * 信号。等待文案会和流式响应抢跑，而在流中途落下的点击会静默失效。
 */

import { test, expect, type Page } from "@playwright/test";

const CUSTOMER = { email: "alice.johnson@gmail.com", password: "customer123" };

// 仅用于录制 —— 不重试、单 worker、为真实 LLM 调用留出充裕预算。
test.use({
  viewport: { width: 1440, height: 900 },
  video: { mode: "on", size: { width: 1440, height: 900 } },
});

test.setTimeout(600_000);

/** 按人类节奏停顿。这段短片是给人看的，不是用来跑分的——UI 切换太快
 *  在视频里会显得像卡顿。 */
const beat = (page: Page, ms = 1_200) => page.waitForTimeout(ms);

async function login(page: Page) {
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
  await page.goto("/login");
  await page.fill('input[type="email"]', CUSTOMER.email);
  await page.fill('input[type="password"]', CUSTOMER.password);
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 20_000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

/** 以人类速度输入，这样视频里能看到打字过程，而不是文字瞬移进来。 */
async function typeAndSend(page: Page, message: string) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();
  await input.pressSequentially(message, { delay: 28 });
  await beat(page, 500);
  await input.press("Enter");
}

/** 等待一个回合真正结束 —— 见文件头的说明。 */
async function waitForTurn(page: Page) {
  const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';
  await page.waitForSelector(STOP, { timeout: 30_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 180_000, state: "detached" });
  await beat(page, 1_500);
}

test("演示短片 —— 对话、模式、编排图、审批、恢复", async ({ page }) => {
  await login(page);
  await beat(page, 1_500);

  // ── 1. 流式对话 + 生成式 UI ─────────────────────────────────────────────
  // "Allbirds" 是某个种子商品名的字面子串，所以 ILIKE 能找到它，
  // 回答就会渲染成卡片。
  await typeAndSend(page, "你们有哪些 Allbirds 的商品？");
  await waitForTurn(page);
  await beat(page, 2_000);

  // ── 2. 追问一次，展示对话上下文得以保留 ─────────────────────────────────
  await typeAndSend(page, "它们多少钱？");
  await waitForTurn(page);
  await beat(page, 2_000);

  // ── 3. 切换编排模式，然后重新提问 ───────────────────────────────────────
  // 模式切换器的数据来自 GET /api/orchestration/modes。在镜头前打开它
  // 正是重点：同一个问题，用不同的方式路由。
  // 通过 aria-label 定位，而不是按钮文本。触发器上显示的是「当前」模式的
  // 标签，所以任何基于文本的定位器只在第一次切换之前有效，之后就静默失配——
  // 下面第二次切换之所以连续五次录制都失败、而 spec 每次都退出 0，正是这个原因。
  const modeSwitcher = page.getByLabel("编排模式");
  if (await modeSwitcher.isVisible().catch(() => false)) {
    await modeSwitcher.click();
    await beat(page, 1_200);
    const preP = page.getByRole("option", { name: /pre-purchase|购前|购买前/i }).first();
    if (await preP.isVisible().catch(() => false)) {
      await preP.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await beat(page, 1_200);
  }

  await typeAndSend(page, "我在考虑 Allbirds Wool Runners —— 该买吗？");
  await waitForTurn(page);
  await beat(page, 3_000); // 在动画展开的编排图上多停留一会儿

  // ── 4. 触发 HITL 关卡 ───────────────────────────────────────────────────
  //
  // 必须先切到 workflow:return-replace。审批关卡位于那个工作流的图里，
  // 而不在平台层——在仍是 pre-purchase 模式时提退货问题，会把它路由进一个
  // 没有关卡的流程，这就是为什么前两次录制都记录了「/runs 上没有待审批」，
  // 却依然通过，并丢掉了短片最后两个节拍。
  const switcher2 = page.getByLabel("编排模式");
  if (await switcher2.isVisible().catch(() => false)) {
    await switcher2.click();
    await beat(page, 1_200);
    // 标签是 "Return & Replace (sequential + in-workflow HITL)"。词与词之间的
    // `.?` 无法跨过 " & "，所以旧的正则什么都匹配不到，`else` 分支按了
    // Escape，短片就在 pre-purchase 模式下问了退货问题——那是个没有审批关卡的
    // 工作流。这才是五次录制都记录「/runs 上没有待审批」却依然退出 0 的真正原因。
    const rr = page.getByRole("option", { name: /return\s*&\s*replace|退货|换货/i }).first();
    if (await rr.isVisible().catch(() => false)) {
      await rr.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await beat(page, 1_200);
  }

  // 订单是查出来的，不是写死的——这不是吹毛求疵。
  //
  // 审批关卡要触发，必须同时满足四个约束，每一条都是拿一次录制换来的：
  //
  //   选中 workflow:return-replace      关卡在那个图里，不在平台层——在其他
  //                                     任何模式下提退货，都会被路由进一个
  //                                     没有关卡的流程
  //   状态为 'delivered'                该模式会回退到用户「最近」一笔订单，
  //                                     而那笔是 'shipped'
  //   在 30 天退货窗口内                金额最大的两笔已送达订单分别是
  //                                     39 天和 42 天前
  //   总价高于 $500                     RETURN_HITL_THRESHOLD；更便宜的退货
  //                                     会自动批准，永远不会暂停
  //
  // 还有第五条，也是为什么即使前四条都对了、写死 id 依然会失败：同一笔订单
  // 只能发起一次退货。第一次录制把它用掉了，此后每次重跑都会走另一条路径、
  // 静默丢掉这个节拍。Alice 在任何时刻都恰好只有一笔符合条件的订单，所以
  // 写死的 id 只能生效一次。
  //
  // 读 /api/orders 再做过滤，让这个 spec 可以反复重跑；而当种子数据无法支撑
  // 这次录制时，它会带着真实原因失败。
  // 刻意使用绝对 URL。同源的 "/api/orders" 现在其实也能用——前端会把
  // /api/* 代理给编排器——但直连能让这个探测独立于代理，这样代理坏了就报
  // 代理坏了，而不是报成种子数据缺失。
  const apiBase = process.env.E2E_API_URL ?? "http://localhost:8080";

  const orderId = await page.evaluate(async (base) => {
    const token = localStorage.getItem("ecommerce_access_token");
    const res = await fetch(`${base}/api/orders?limit=50`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const body = await res.json();
    const orders = Array.isArray(body) ? body : (body.orders ?? body.entries ?? []);
    // orders 接口返回的是 `date`，不是 `created_at` —— 这个字段形状与
    // 数据库列不同，很容易想当然搞错。
    const cutoff = Date.now() - 25 * 24 * 60 * 60 * 1000; // 处于 30 天窗口内，留有余量
    const match = orders.find(
      (o: Record<string, unknown>) =>
        o.status === "delivered" &&
        Number(o.total) > 500 &&
        new Date(String(o.date)).getTime() > cutoff
    );
    return match ? String((match as Record<string, unknown>).id) : "";
  }, apiBase);

  expect(
    orderId,
    "30 天窗口内没有超过 $500 的已送达订单 —— 种子数据无法产生审批暂停，" +
      "录制前请重新播种（./scripts/dev.sh --clean）"
  ).not.toBe("");

  await typeAndSend(
    page,
    `我想退掉订单 ${orderId} —— 它和我想的不一样。`
  );
  await waitForTurn(page);
  await beat(page, 2_500);

  // ── 5-6. /runs 上的待审批，然后恢复运行 ─────────────────────────────────
  await page.goto("/runs");
  await page.waitForLoadState("networkidle").catch(() => {});
  await beat(page, 2_500);

  const approve = page.getByRole("button", { name: /批准/ }).first();
  if (await approve.isVisible().catch(() => false)) {
    await approve.scrollIntoViewIfNeeded();
    await beat(page, 1_200);
    await approve.click();
    await beat(page, 4_000); // 运行正从它的检查点恢复
  } else {
    // 失败，而不是打日志。
    //
    // 这个分支以前只 console.log 并让测试通过，结果连续六次录制产出的短片
    // 都缺了最后两个节拍——审批与恢复——而 spec 每次都退出 0。一个对不完整的
    // 录制报告成功的脚本，正是本仓库在其他地方反复发现的同一种失败：
    // 表面健康，实则悄悄出错。
    //
    // 诊断所需的一切都写进消息里，因为产物是那段短片，而没人会去重看一次
    // 绿色的运行。
    const modeLabel = await page.getByLabel("编排模式").textContent().catch(() => "(not found)");
    const bodyText = (await page.locator("main").textContent().catch(() => "")) ?? "";
    throw new Error(
      "/runs 上没有待审批，因此短片缺少审批与恢复这两个节拍。\n" +
        `运行结束时输入框的模式：${modeLabel}\n` +
        "这笔退货必须同时满足以下四项：选中 workflow:return-replace、订单" +
        "状态为 'delivered'、处于 30 天退货窗口内，且总价高于 " +
        "RETURN_HITL_THRESHOLD（$500）。\n" +
        `/runs 页面文本（前 400 字符）：${bodyText.slice(0, 400)}`
    );
  }

  await beat(page, 2_000);

  // 唯一一处真正的断言：我们结束在了合理的位置，这样坏掉的录制会大声失败，
  // 而不是静默产出一段没法用的视频。
  expect(page.url()).toMatch(/\/(runs|chat)/);
});
