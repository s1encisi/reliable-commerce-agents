import { test, expect, type Page } from "@playwright/test";

/**
 * 针对第 8.4 阶段 Step 4 接入的 3 个专业智能体（评论与情感分析、库存与履约、
 * 定价与促销）的生成式 UI 回归覆盖——也是 8.3 尚未完成的那部分。它使用真实
 * 的 `expect()` 并限定在实际渲染出的 DOM 上（表格表头、图表坐标轴、状态徽章
 * 文本），而不是只看回答长度。
 *
 * 需要已配置好的 LLM（Azure OpenAI / OpenAI）——若未配置，编排器会返回一段
 * 错误消息，这些断言会大声失败，而不是靠一段错误字符串悄悄通过。
 */

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendMessageAndWaitForTurn(page: Page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  // 输入区的提交按钮在本轮进行中时显示红色停止图标，isResponding 变为 false
  // 后再换回发送图标——这是本应用「本轮确实结束」的可靠信号。
  // 「正在路由到专业智能体…」只表示流式输出*已经开始*；改为等它会使得后续
  // 操作在本轮真正结束之前就触发（第 8.4 阶段 Step 5 实测发现——参见记忆
  // 笔记 ecommerce-agents-playwright-streaming-wait.md）。
  const stopButton = page.locator("button.bg-red-500");
  await stopButton.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  await stopButton.waitFor({ state: "hidden", timeout: 90000 });
}

test.describe("生成式 UI —— 评论情感、库存与履约、定价与促销", () => {
  test.setTimeout(120000);

  test("评论情感渲染评分分布图与趋势图，绝不输出原始 JSON", async ({ page }) => {
    await login(page, "carol.davis@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "找一下 Sony WH-1000XM5 耳机");
    await sendMessageAndWaitForTurn(
      page,
      "它的评论情感怎么样？给我看看评分分布，以及最近 6 个月的变化趋势。"
    );

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```sentiment");
    expect(text).not.toContain("overall_sentiment");

    // 三处都加了 .first()，原因与下面仓库表的用例所述相同：两轮对话合理地会
    // 渲染出不止一张卡片，而断言唯一匹配会因为智能体*多*做了它该做的事而让
    // 用例失败。这里要主张的是图表渲染出来了，而不是恰好渲染了一张。已确认
    // 无论经 /api 代理还是直连编排器都会失败，因此这是用例本身的问题，不是
    // 传输层的问题。
    // 评分分布柱状图的 x 轴标签（DistributionChart）
    await expect(page.getByText("5★", { exact: true }).first()).toBeVisible();
    // 趋势图的区块标题（TrendChart）
    await expect(page.getByText("评分随时间变化").first()).toBeVisible();
    // 优点 / 缺点列表
    await expect(page.getByText("优点", { exact: true }).first()).toBeVisible();
  });

  test("库存与履约渲染带列表头的真实仓库表", async ({ page }) => {
    await login(page, "dave.wilson@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "找一下 Sony WH-1000XM5 耳机");
    await sendMessageAndWaitForTurn(page, "这件有货吗？给我看看各仓库的库存明细。");

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```inventory");
    expect(text).not.toContain("total_quantity");

    // DataTable 真实的 <th> 列表头，而不只是正文里提到这些词。
    // 刻意加 .first()：询问库存往往也会带出补货计划，而后者会渲染出第二张
    // 合法的表格，其第一列同样是「仓库」。断言唯一匹配会因为智能体*多*做了它
    // 该做的事而让用例失败，那是错误的信号——这里要主张的是渲染出了真实的
    // DataTable，而不是恰好一张。「区域」原本被假定为库存表独有，用来让断言
    // 更具体；事实并非如此：实测中它解析到了两个列表头，说明补货表也带这一
    // 列。同一套理由，同一种修法。已对照 /api 代理与直连编排器的前端验证过，
    // 因此这是用例本身的问题，不是传输层的问题。
    await expect(page.getByRole("columnheader", { name: "仓库" }).first()).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "区域" }).first()).toBeVisible();
    // 仓库明细的区块标题，卡片只要有仓库行就会渲染它——这正是本用例真正要
    // 求的东西。
    //
    // 另外两条附近的断言尝试过但被否决了，都是因为它们考的是模型的随性发挥
    // 而不是应用本身。「有货 / 缺货」状态徽章需要 `in_stock` 字段，而这个问题
    // 可以由两个已注册工具中的任意一个来回答：`check_stock` 会返回 `in_stock`，
    // 而 `get_warehouse_availability`——同样是正确的选择，也正是它提供了补货
    // 表——在*两个*技术栈上都不返回。卡片的标题是
    // `product_name || "库存与履约"`，因此断言任一字符串都会在模型省略或包含
    // 这个可选字段时失败。两者分别在各自不同的后端上被观察到失败。
    // 精确匹配：用户自己的那一轮（「……各仓库的库存明细」）以及侧边栏里的会话
    // 标题在其他情况下都含有这个短语。
    await expect(page.getByText("按仓库", { exact: true }).first()).toBeVisible();
  });

  test("定价与促销渲染带列表头的真实优惠表", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "平台上目前有哪些优惠与促销活动？");

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```pricing");
    expect(text).not.toContain("discount_value");

    // 加 .first() 的理由与上面仓库表相同：「有哪些优惠在生效」可以用一张优惠
    // 表回答，也可以用好几张。
    await expect(page.getByRole("columnheader", { name: "优惠码" }).first()).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "折扣" }).first()).toBeVisible();
  });

  test("库存与履约的配送方式选择器真实可点击", async ({ page }) => {
    await login(page, "emma.brown@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "找一下 Sony WH-1000XM5 耳机");
    await sendMessageAndWaitForTurn(
      page,
      "把这件商品寄到东部地区要多少钱？我有哪些承运商可选？"
    );

    const selectButtons = page.getByRole("button", { name: "选择" });
    // 刻意比默认的 10 秒更长。sendMessageAndWaitForTurn 返回时本轮已经结束，
    // 但配送卡片是从最后一条消息里的代码块负载渲染出来的——在整套用例同时
    // 运行时，这次渲染会落在输入区已经重新启用之后。本用例单独跑能过、只在
    // 全量运行时失败，这正是「等待太紧」而不是「卡片永远不来」的特征。
    await expect(selectButtons.first()).toBeVisible({ timeout: 30000 });
    const countBefore = await page.locator("textarea").count(); // 健全性检查：输入区存在
    expect(countBefore).toBeGreaterThan(0);

    await selectButtons.first().click();
    // 必须出现一条真实由用户撰写的确认气泡——点击不能静默无效（参见上面的
    // 时序说明；本用例本身就是针对这类缺陷的回归守卫）。
    await expect(page.getByText(/我选择/)).toBeVisible({ timeout: 10000 });
  });
});
