import { test } from "@playwright/test";

/**
 * 一致性对齐（parity）关卡的工具函数。
 *
 * 「一致性对齐」在这里指的是：前端期望存在的编排展示类界面，
 * 与后端实际能力之间是否真的对齐。这里只有一个前端、一个后端，
 * 因此「后端做完了吗」的诚实定义不是测试数量、也不是矩阵里的一行，
 * 而是*这套用例真的在断言这些界面存在*。
 *
 * 之所以单独建这个文件、而不是到处散落 `test.skip()`：一个被跳过、
 * 却没记录原因的测试，和一个从来没人写过的测试毫无区别。下面每一条
 * 都写明了将来会删掉它的那个 issue。这份清单就是可执行的一致性
 * 检查表——**当这个对象为空时，一致性对齐即告完成**。
 *
 * 这里要防的失败模式很具体、而且已经真实出现过：现有的 109 个 e2e
 * 测试面对一个整整缺了四项功能的后端也能全绿通过，因为它从不
 * 触碰这些功能。因此这里的断言必须检查「是否存在」——一个只确认
 * 「没有报错」的测试，面对空白页面也会变绿。
 */

/**
 * 已知缺口，按测试标题索引。值是原因，
 * 并且必须引用一个跟踪 issue——只写「尚未实现」，缺口
 * 就会变成永久性的。
 *
 * 在关闭该缺口的那个 PR 里顺手删掉这里的对应行即可。其他什么都不用
 * 改；那个测试自然就开始运行了。
 */
export const PARITY_GAPS: Record<string, string> = {
  // 空。这个关卡检查的每一个界面，后端都能提供。
  //
  // 这正是第 14 阶段围绕它来写的退出标准：不是测试数量，
  // 也不是 docs/parity-matrix.md 里的一行，而是这个对象为空、
  // 同时上面每个测试依然在断言「存在」。那些尚未接入的项
  // （handoff 与 group-chat 模式、magentic、评测框架）记录在
  // umbrella issue 上——它们没有出现在这个关卡里，是因为这里
  // 没有任何测试覆盖它们，这属于覆盖率上的诚实空白，
  // 而不是声称它们已经存在。
};

/**
 * 当某个测试标题记录了缺口时，跳过该测试，
 * 并附带原因——运行摘要里会显示
 * `已知缺口 → #33 PR 5 — …`，而不是一个沉默的短横线。
 *
 * 之所以在测试体第一行调用，而不是包装 `test()`：
 * Playwright 静态要求测试的第一个参数使用对象解构写法，
 * 因此无法接受从 helper 转发过来的 fixtures 对象。
 */
export function skipIfKnownGap(title: string) {
  const gap = PARITY_GAPS[title];
  test.skip(Boolean(gap), `已知缺口 → ${gap}`);
}

/**
 * 当某条缺口记录的测试标题已不存在时失败——一个被重命名
 * 或删除的测试，否则会留下一条过期条目，它什么都没抑制，
 * 却依然被算作一个未关闭的缺口。
 */
export function assertGapsAreLive(declaredTitles: string[]) {
  const declared = new Set(declaredTitles);
  const stale = Object.keys(PARITY_GAPS).filter((t) => !declared.has(t));
  if (stale.length > 0) {
    throw new Error(
      `PARITY_GAPS 引用了已不存在的测试：${stale.join(", ")}。` +
        `请删除这些过期条目。`,
    );
  }
}
