# 第 28 章 · 反思与批评

[项目首页](../../README.md) · [教程总览](../README.md) · [术语表](../_shared/jargon-glossary.md)

## 本章动机

此前每一章都把模型的第一次回答当作最终答案。第 24 章加了一步核验，但它只是在事后*检查*答案 —— 从不给模型第二次机会。有些输出确实值得再来一次：一段必须提到价格、点明某项特性、并控制在字数上限内的商品描述，正是那种模型第一次只做对一部分、而在明确告知它漏了什么之后第二次能完全做对的东西。本章把这「第二次机会」构建成一个真正的循环 —— 起草、让批评者按具名标准打分、依据批评者给出的具体反馈修订、再打分 —— 并施加一个硬性上限，使「再试一次」永远不会变成「永远再试」。这也是本系列中第一次由本仓库的代码（而不是框架）驱动一个没有内置边界的多轮循环。MAF 会替你给自己的工具调用循环加上边界（第 02 章）；而这个循环除了本章手写的那个常量之外，没有任何东西给它加边界。

## 前置条件

- 已完成[第 02 章 · 添加工具](../02-add-tools/)（单次智能体调用所产生的请求/响应形态）
- 已完成[第 24 章 · 检索与事实核验](../24-rag-and-grounding/)（一个不回流进下一次生成的生成后核验步骤 —— 本章讲的正是把这个环闭上之后会发生什么）
- 仓库根目录的 `.env` 中有可用的 LLM 提供方（`OPENAI_API_KEY`，或 `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_KEY` + `AZURE_OPENAI_DEPLOYMENT`）

## 核心概念

反思（也叫批评者循环，或自我精炼）是把三个角色接成一个环：

1. **起草（Draft）** —— 一个智能体产出输出的第一次尝试。
2. **批评（Critique）** —— 第二遍：可以是同一个智能体换一套指令，也可以像本章这样是一个独立的批评者智能体 —— 它按明确、具名的标准给草稿打分，并返回具体反馈，而不只是一个赞/踩。
3. **修订（Revise）** —— 如果批评未通过，起草智能体再获得一轮机会，这一次把批评者的反馈直接折进提示词，然后循环重复。

这个循环以两种方式之一终止：批评通过，或达到硬性的 `MAX_ITERATIONS` 上限。那个上限不是优化 —— 它是让这个模式可以安全上线的东西。本系列其他每一个循环都有框架强制的边界：第 02 章的工具调用循环在模型停止请求工具的那一刻就停下，而完整项目里的两种 MAF 工作流模式（`workflows/pre_purchase.py`、`workflows/return_replace.py`）是有向无环图 —— 消息从一个执行器流向下一个，工作流随即结束，从不重访任何节点。这两条边界都不适用于批评者循环，因为批评者循环的全部要点就是重访同一步。一个从不说「通过」的批评者 —— 一条微妙地无法满足的评分标准、一个反复犯同一个错的模型、一个真正不可能达成的约束 —— 会让循环一直跑下去，只要你允许，每一轮烧掉一次起草调用加一次批评调用。本仓库自身的架构在其他任何地方都没有应对这种风险的答案，因为本仓库其他任何工作流都根本没有环；本章 `main.py` 里的 `MAX_ITERATIONS` 是这种风险第一次出现的地方，而一个硬编码的整数就是全部的缓解手段。

当输出具有可检验、具体的质量标准，且把这些标准做对比快速回答更重要时，反思才值回它的成本：一段带价格与字数规则的商品描述、一条不得承诺退款的客服回复、一份必须引用来源的摘要。而对于没有任何可检验标准的快速对话式回复，它就是白费力气 —— 批评者没什么可评的，你却要为第二次（甚至第三次）LLM 调用付费，去给一次调用就已经做对的东西盖个橡皮图章。

```mermaid
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb','primaryTextColor': '#ffffff','primaryBorderColor': '#1e40af',
  'lineColor': '#64748b','secondaryColor': '#f59e0b','tertiaryColor': '#10b981',
  'background': 'transparent'}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff
  classDef error    fill:#ef4444,stroke:#b91c1c,color:#ffffff

  product([商品规格])
  draft[起草智能体]
  critic[批评者智能体]
  pass_check{{"已通过 OR\n达到迭代上限？"}}
  final([最终草稿])

  product --> draft
  draft -- "描述" --> critic
  critic -- "通过/不通过 + 反馈" --> pass_check
  pass_check -- "否 —— 修订" --> draft
  pass_check -- "是" --> final

  class draft core
  class critic core
  class pass_check error
  class final success
```

回到 `draft` 的那条环线就是本章的全部。`pass_check` 是唯一能跳出它的东西 —— 而它会在*两*种条件下跳出，不是一种：批评者判定草稿通过，或者无论结论如何、`MAX_ITERATIONS` 已被达到。

## Python

在仓库根目录运行，使用共享的 `tutorials/` uv 项目（一次 `uv sync` 覆盖全部章节）：

```bash
uv sync --project tutorials
uv run --project tutorials python tutorials/28-reflection-and-critique/python/main.py
```

源码：[`python/main.py`](./python/main.py)。两个智能体，各一条指令字符串 —— 起草智能体负责写，批评者智能体按三条具名标准、以固定且可解析的格式打分：

```python
CRITIC_INSTRUCTIONS = (
    "You are a strict copy editor grading a product description against three named criteria: "
    "PRICE (does it mention the exact price given), FEATURE (does it mention at least one of "
    "the listed features), LENGTH (is it at or under the given word limit). "
    "Respond in EXACTLY this format, one line per criterion, nothing before or after it:\n"
    "PRICE: PASS or FAIL\n"
    "FEATURE: PASS or FAIL\n"
    "LENGTH: PASS or FAIL\n"
    "FEEDBACK: one sentence covering every FAIL, or 'none' if all three pass\n"
    "Grade exactly what the text says — do not soften a FAIL into a PASS to be polite."
)
```

`parse_critique()` 把这段固定格式的文本转成循环可以用来分支的 `CritiqueResult` —— 并且把批评者*没有*明确标为 `PASS` 的任何标准都当作 `FAIL`，而不是免费放行：

```python
def parse_critique(text: str) -> CritiqueResult:
    verdicts = {m.group(1).upper(): m.group(2).upper() == "PASS" for m in _CRITERION_RE.finditer(text)}
    feedback_match = _FEEDBACK_RE.search(text)
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    return CritiqueResult(
        price_ok=verdicts.get("PRICE", False),
        feature_ok=verdicts.get("FEATURE", False),
        length_ok=verdicts.get("LENGTH", False),
        feedback=feedback,
    )
```

循环本身 —— 起草一次，然后最多批评/修订 `max_iterations` 次，一旦某次批评通过就立即停止：

```python
async def run_reflection_loop(
    draft_agent: Agent, critic_agent: Agent, product: Product, *, max_iterations: int = MAX_ITERATIONS
) -> list[Iteration]:
    iterations: list[Iteration] = []
    draft = await ask(draft_agent, draft_prompt(product))
    for number in range(1, max_iterations + 1):
        critique_text = await ask(critic_agent, critic_prompt(product, draft))
        critique = parse_critique(critique_text)
        iterations.append(Iteration(number=number, draft=draft, critique=critique))
        if critique.passed or number == max_iterations:
            break
        draft = await ask(draft_agent, revise_prompt(product, draft, critique))
    return iterations
```

`main()` 会打印每一轮的草稿、批评者逐条标准的判定，以及喂给下一次修订的反馈 —— 循环在输出中可见，而不只是最终结果：

```
Product: Aurora Desk Lamp ($39.99)
Criteria: mentions price, mentions a feature, <= 40 words

--- Iteration 1/3 ---
Draft: Illuminate your workspace with the Aurora Desk Lamp—featuring adjustable color
temperature, touch dimmer, and a convenient USB-C charging port. Perfect for any desk
setup, it combines stylish design with modern functionality, all for just $39.99.
Critic: PRICE=PASS FEATURE=PASS LENGTH=PASS
Result: PASS

Passed after 1 iteration(s). Final description:
Illuminate your workspace with the Aurora Desk Lamp—featuring adjustable color
temperature, touch dimmer, and a convenient USB-C charging port. Perfect for any desk
setup, it combines stylish design with modern functionality, all for just $39.99.
```

把字数上限设得更严，或者换一个没有任何已列出特性的商品，就能稳定地在第一轮产出 `FAIL`、并在第二轮看到一次可见的修订 —— 上面的示例之所以只打印了一轮，是因为 `gpt-4.1` 大多数时候第一次就能把这条评分标准做对。

## 常见坑

- **`MAX_ITERATIONS` 上限是承重的，不是装饰。** 批评者循环没有第 02 章工具调用循环那种框架强制的停止条件。去掉这个上限，或者把它设得太高，那么一个从不说 `PASS` 的批评者 —— 一条真正无法满足的评分标准、一个不稳定的模型、彼此矛盾的若干标准 —— 就会永远空转，每一轮一次起草调用加一次批评调用，无界的 token 账单配上无界的时间。测试套件里的 `test_run_reflection_loop_respects_max_iterations_cap` 正是为了证明「即使批评者从不通过，循环也会停下」而存在。
- **批评者回复中缺失的标准算 FAIL，不算 PASS。** `parse_critique()` 把每一条标准都默认为 `False`，除非批评者的文本明确把它标为 `PASS`。反过来默认 —— 把「批评者没提 LENGTH」当成「LENGTH 应该没问题」—— 会让一个把自己的输出格式搞乱的批评者静默地给坏草稿盖上橡皮图章。`test_parse_critique_treats_missing_criterion_as_fail` 覆盖这一点；`test_parse_critique_handles_completely_unparseable_text` 覆盖批评者完全无视格式的情况 —— 循环仍然安全失败并继续修订（或撞到上限），而不是崩溃。真实的批评者指令，永远离一次糟糕的模型轮次导致的不可解析回复只差一步。
- **两个智能体、两套独立指令，但共享同一份解析契约。** `build_draft_agent()` 与 `build_critic_agent()` 是各自独立的 `Agent` 对象 —— 除了 `run_reflection_loop()` 里那个循环先调一个、再调另一个、然后把前者的输出喂进后者的下一次提示词之外，没有任何东西把它们联系起来。MAF 里没有「批评者智能体」这种原语；这就是两个普通智能体加一个 Python `for` 循环。你同样可以用一个智能体、在每次调用时传入两条不同的指令字符串来实现，而不必用第二个智能体对象 —— 无论哪种方式，循环与上限的机制都完全一样。
- **修订质量完全取决于具体反馈，而不是光秃秃的判定。** `revise_prompt()` 把 `critique.feedback` —— 批评者那一句话的解释 —— 直接折进下一次起草提示词。一个永远只返回 `FAIL` 而不给解释的批评者，会让起草智能体无事可做，循环于是每次都重新生成一份相似的草稿，直到撞上上限。`CRITIC_INSTRUCTIONS` 里那行 feedback 不是可有可无的装饰。
- **本章刻意不做成新的编排器模式。** 生产应用的模式注册表（`orchestrator/modes/`）里有五个在线模式，每一个都有自己的 SSE/界面/测试面 —— 为教程的每一章再加一个「反思」模式，与本章所教的内容不成比例。这是独立的教程代码；`agents/python/orchestrator/` 与 `agents/python/workflows/` 下没有任何东西为本章改动过。

## 测试

```bash
uv run --project tutorials pytest tutorials/28-reflection-and-critique/python/tests -v
```

`tutorials/28-reflection-and-critique/python/tests/test_reflection_and_critique.py` 从结构上覆盖：

1. **`parse_critique` 单元测试** —— 全部通过的文本、通过与不通过混合并带反馈、大小写不敏感、缺失标准默认为不通过，以及完全无法解析的文本 —— 不涉及 LLM。
2. **提示词构建函数单元测试** —— `draft_prompt`、`critic_prompt` 与 `revise_prompt` 各自都嵌入了循环所依赖的具体取值（价格、特性、字数上限、此前的反馈）。
3. **用假智能体做的循环机制测试** —— `test_run_reflection_loop_respects_max_iterations_cap` 证明即使什么都通不过，循环也会在上限处停下；`test_run_reflection_loop_stops_early_on_first_pass` 证明一旦某次批评通过，它就不会多跑几轮。两者都使用带异步 `run()` 的普通假对象，没有真实或回放的 LLM。
4. **智能体接线** —— `build_draft_agent` / `build_critic_agent` 产出命名正确的智能体，且其指令包含循环解析逻辑所依赖的内容。
5. **一次回放测试**（`test_replay_reflection_loop_produces_a_trace`），播放 `tests/fixtures/replay/` 中已提交的夹具 —— 不需要网络或凭据，可安全用于 CI。
6. **真实 LLM 集成测试**，在缺少可用凭据时跳过 —— 一个断言循环总能在 `MAX_ITERATIONS` 之内终止，另一个断言最终通过的草稿里确实包含价格。

测试是围绕无界循环出错的两种方式来设计的：它停不下来，或者它因为错误的原因停下。其中一个值得点出的差一错误是：一次三轮的运行应当恰好产生六次调用（1 次起草 + 3 次批评 + 2 次修订）。最后一次批评之后若还多出第七次修订，在输出里是看不见的，只会出现在账单上。

## 在完整项目中的落点

这个模式**没有**接入生产应用 —— `agents/python/` 里没有任何工作流带环，而本章自己的 `MAX_ITERATIONS` 上限正是「这是一个刻意的缺口而非疏忽」的原因：本仓库里还没有任何东西迫切需要一个有界修订循环，以至于值得把那种风险带进一个在线编排器模式。本仓库最接近的单遍类比是 `agents/python/review_sentiment/tools.py:564` 的 `draft_seller_response`：

```python
@tool(name="draft_seller_response", description="Generate a professional response template for a negative review. Returns a template the seller can customize.")
@requires_role("seller", "admin")
async def draft_seller_response(
    review_id: Annotated[str, Field(description="UUID of the review to respond to")],
) -> dict:
```

它根据评论的星级从几个硬编码模板字符串里挑一个返回，交给人工商家去编辑 —— 单遍，不打分，不修订，不循环。它**不**做本章讲授的批评者循环迭代：没有第二遍去按标准给生成的模板打分并要求更好的版本。围绕 `draft_seller_response` 建一个反思循环 —— 按「是否回应了具体投诉」「语气是否恰当」「是否避免了承诺商家无法授权的事」来批评起草好的回复 —— 正是本章模式所能开启的那类扩展，也正是本仓库今天所没有的东西。

## 下一步

- 相关：[第 24 章 · 检索与事实核验](../24-rag-and-grounding/)，讲一个只检查答案、从不回流进下一次生成的核验步骤
- 相关：[第 26 章 · 智能体评估](../26-evals/)，讲在请求路径*之外*、跨整个测试集施加评分标准，而不是内联在单次回复的修订循环里
- 完整源码：[`python/`](./python/)
- 共享资料：[Mermaid 风格指南](../_shared/mermaid-style-guide.md) · [术语表](../_shared/jargon-glossary.md)
