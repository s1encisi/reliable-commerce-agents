import { test, expect, type Page } from "@playwright/test";

/**
 * 问题 #9 —— 追问必须借助上一轮的内容来回答。
 *
 * 正是这块覆盖的缺失让 #9 得以发布：`web/e2e/` 里完全没有多轮对话的用例，
 * 而所有看似覆盖了会话链路的单元测试都是手工设置 ContextVar 或请求头，因此
 * 它们全都通过，专业智能体却完全收不到历史记录。
 *
 * 这里的断言刻意针对「专业智能体」的行为而不是编排器的行为。编排器一直持有
 * 会话历史，它的提示词要求自己把上下文内联进发给专业智能体的消息——这是一条
 * 非确定性的指令，有时有效。这正是该缺陷长期被读作「LLM 不确定性」的原因。
 * 一条什么都不指名的追问（「它们中哪一个……」）只有在真实上下文确实到达了
 * 回答它的那个智能体时才能被解析出来。
 *
 * 如实说明范围：本用例驱动整条链路，因此编排器自身的内联仍可能在某次走运的
 * 运行中掩盖专业智能体层的故障——它只是一道冒烟级防线，不是确定性证明。真
 * 正的证明是 agents/python/tests/test_chat_specialist_context.py（断言出站
 * A2A 请求头上的会话 id），以及对某个专业智能体的直接 /message:send 调用：
 * 会话 id 为空时它逐字复现所报告的症状，不为空时则给出正确回答。
 *
 * 需要已配置好的 LLM；配置有误的技术栈会在这里大声失败，而不是靠一段错误
 * 字符串蒙混过关。
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
  // 等待输入区的停止图标，而不是「正在路由到专业智能体…」——后者只表示流式
  // 输出已经开始，若在它上面触发追问，会被 sendMessage 的 isResponding 守卫
  // 静默吞掉、什么也不做。
  const stopButton = page.locator("button.bg-red-500");
  await stopButton.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  await stopButton.waitFor({ state: "hidden", timeout: 90000 });
}

async function lastAssistantText(page: Page): Promise<string> {
  // 只取助手气泡：输入区用的是同一种 rounded-2xl 形状，因此裸写
  // [class*="rounded-2xl"] 会把输入区外壳（「自动 商品 订单 定价 评论 库存
  // … Enter 发送」）当成回复返回。bg-muted 是助手变体；用户轮次是
  // bg-primary。
  const bubbles = page.locator("div.rounded-2xl.bg-muted");
  const count = await bubbles.count();
  expect(count, "没有渲染出任何助手消息").toBeGreaterThan(0);
  return (await bubbles.nth(count - 1).textContent()) ?? "";
}

test.describe("追问会保留对话上下文（#9）", () => {
  test.setTimeout(180000);

  test("不指名任何商品的追问，仍能落到刚刚展示的那一件上", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");

    await sendMessageAndWaitForTurn(page, "给我看看 Sony WH-1000XM5 耳机");
    const first = await lastAssistantText(page);
    expect(first.toLowerCase()).toContain("sony");

    // 不指名商品、不提 id、不提分类。只有此前的上下文才能解析它。
    await sendMessageAndWaitForTurn(page, "它的续航能用多久？");
    const second = await lastAssistantText(page);

    // 该缺陷的特征是一段与上文无关的通用回复——一次全新的宽泛搜索
    // （「我没找到 350 元以内的耳机……」）或者要求用户重复屏幕上已有的
    // 信息。
    expect(second).not.toMatch(/couldn't find|could not find|unable to find|找不到|没有找到|无法找到/i);
    expect(second).not.toMatch(/which product|which item|please specify|can you clarify|哪件商品|哪一款|请说明|请指明/i);
    expect(second.length).toBeGreaterThan(20);
  });

  test("第二轮立即持久化，因此第三轮仍能看到它", async ({ page }) => {
    // 覆盖 #9 的另一半：助手轮次过去是由一个在 [DONE] 之后才派生的任务写入
    // 的，而 [DONE] 正是重新启用输入区的信号——于是一次快速的追问可能在
    // 那条 INSERT 提交之前就去读历史，从而丢掉它所追问的那一轮。
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");

    await sendMessageAndWaitForTurn(page, "我在本次对话里的名字叫「袋熊」（Wombat）。请记住。");
    await sendMessageAndWaitForTurn(page, "你们有哪些降噪耳机？");
    await sendMessageAndWaitForTurn(page, "我在本次对话开头告诉你我叫什么名字？");

    const answer = await lastAssistantText(page);
    expect(answer).toMatch(/wombat|袋熊/i);
  });
});
