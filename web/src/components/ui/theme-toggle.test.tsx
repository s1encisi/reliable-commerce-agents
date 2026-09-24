import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeToggle } from "./theme-toggle";

describe("ThemeToggle", () => {
  afterEach(() => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  it("切换 dark 类并持久化用户选择", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: /切换到深色模式/ });
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await user.click(button);

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("theme")).toBe("dark");
    // 标签翻转为反向操作
    expect(
      screen.getByRole("button", { name: /切换到浅色模式/ }),
    ).toBeInTheDocument();
  });
});
