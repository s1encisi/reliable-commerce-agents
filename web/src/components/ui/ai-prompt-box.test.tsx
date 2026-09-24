import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AGENT_MODES, PromptInputBox } from "./ai-prompt-box";

/**
 * 在 issue #4 之前，这个输入框组件完全没有测试——正因如此，它才长出了一整行
 * 永远可见的六个胶囊按钮，而没有人需要为此负责。下面这些用例覆盖 #4 改动的
 * 两件事——折叠式专业智能体选择器与建议提问行——以及二者共同汇入的发送路径：
 * 因为「选择器看起来正确、却发错了模式」才是真正致命的失败。
 */

const SUGGESTIONS = [
  { label: "查询库存", prompt: "这件商品有货吗？" },
  { label: "查看评论", prompt: "大家怎么评价这件商品？" },
  { label: "找相似款", prompt: "给我看看相似的商品" },
  { label: "第四个", prompt: "永不渲染" },
];

const openPicker = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole("button", { name: "专业智能体" }));
  return screen.getByRole("listbox", { name: "专业智能体" });
};

/**
 * 选择器与建议提问行都通过 `AnimatePresence` 的退出过渡离场，因此在其渲染
 * 条件翻转后，它们仍会以逐渐淡出的透明度多挂载一两帧。同步断言会与这个退出
 * 动画抢跑，从而在一个行为完全正确的组件上失败。
 */
const expectGone = (role: string, name?: string) =>
  waitFor(() =>
    expect(screen.queryByRole(role, name ? { name } : undefined)).not.toBeInTheDocument()
  );

describe("PromptInputBox —— 专业智能体选择器", () => {
  it("只渲染一个折叠控件，而不是每个模式一个胶囊", () => {
    // 本文件存在的意义就是防这个回归：六个常驻胶囊占掉了输入框上方三分之一
    // 的空间，只为暴露一个大多数轮次都不会用到的控件。
    render(<PromptInputBox onSend={() => {}} />);

    expect(screen.getByRole("button", { name: "专业智能体" })).toHaveTextContent("自动");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    for (const mode of AGENT_MODES.slice(1)) {
      expect(screen.queryByRole("button", { name: mode.label })).not.toBeInTheDocument();
    }
  });

  it("展开后列出全部模式，并标记当前选中项", async () => {
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    const options = within(listbox).getAllByRole("option");

    expect(options.map((o) => o.textContent)).toEqual(AGENT_MODES.map((m) => m.label));
    expect(options[0]).toHaveAttribute("aria-selected", "true");
  });

  it("选择后折叠回所选模式", async () => {
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "订单" }));

    expect(screen.getByRole("button", { name: "专业智能体" })).toHaveTextContent("订单");
    await expectGone("listbox");
  });

  it("切换占位符以匹配指定的专业智能体", async () => {
    // 既然标签都收进了菜单里，占位符就是屏幕上唯一提示「当前指定模式期望
    // 什么输入」的地方。
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "定价" }));

    expect(screen.getByPlaceholderText(/优惠、优惠券或价格趋势/)).toBeInTheDocument();
  });

  it("按 Escape 与点击外部都能关闭", async () => {
    // 只能靠自身触发按钮关闭的弹层，会把误触打开的人困住——而误触正是它最
    // 常见的打开方式。
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    await openPicker(user);
    await user.keyboard("{Escape}");
    await expectGone("listbox");

    await openPicker(user);
    await user.click(document.body);
    await expectGone("listbox");
  });

  it("发送的是指定模式的 id，而不是它的标签", async () => {
    // AGENT_MODES 的 id 就是后端智能体名；发送「订单」而不是
    // "order-management" 会在服务端失败，而且离本组件很远。
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "订单" }));
    await user.type(screen.getByRole("textbox"), "我的订单到哪了{Enter}");

    expect(onSend).toHaveBeenCalledWith("我的订单到哪了", "order-management");
  });

  it("自动模式发送 null，交由编排器路由", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} />);

    await user.type(screen.getByRole("textbox"), "你好{Enter}");

    expect(onSend).toHaveBeenCalledWith("你好", null);
  });
});

describe("PromptInputBox —— 建议提问", () => {
  it("最多渲染三个，且保持顺序", () => {
    // 这一行按设计只占一行；第四个胶囊会换行，从而让输入框高度在轮次之间跳动。
    render(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} />);

    expect(screen.getByRole("button", { name: "查询库存" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "找相似款" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "第四个" })).not.toBeInTheDocument();
  });

  it("把提示词填入输入框，而不是直接发送", async () => {
    // 从胶囊直接发送就剥夺了修改的机会，一次误点就要付出一次真实的 LLM 调用。
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} suggestions={SUGGESTIONS} />);

    await user.click(screen.getByRole("button", { name: "查看评论" }));

    expect(screen.getByRole("textbox")).toHaveValue("大家怎么评价这件商品？");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("有输入时隐藏该行，流式返回期间也隐藏", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} />);

    await user.type(screen.getByRole("textbox"), "a");
    await expectGone("button", "查询库存");

    rerender(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} isLoading />);
    await expectGone("button", "查询库存");
  });

  it("没有可建议内容时完全不渲染该行", () => {
    render(<PromptInputBox onSend={() => {}} suggestions={[]} />);
    expect(screen.queryByRole("button", { name: "查询库存" })).not.toBeInTheDocument();
  });
});
