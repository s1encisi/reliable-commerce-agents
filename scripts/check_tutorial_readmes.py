#!/usr/bin/env python3
"""章节契约检查器 —— 强制校验 tutorials/_template/README.md 规定的章节形态。

按章节契约，每个 tutorials/<NN-slug>/README.md 都必须**真正具备**
（而不是只有一个空标题）：

1. 概念小节（## 本章动机 + ## 核心概念），且正文长度达到下限，
   使空壳标题无法通过检查。
2. 至少一个 ```mermaid 围栏代码块。
3. 一条可运行的命令，且其目标路径在磁盘上真实存在。
4. 代码走读 —— 一段展示真实源码的内联代码块，而不仅仅是一条运行命令。
5. 一个「完整项目落点」小节，其中含有 `路径:行号` 指针，且该文件真实存在、
   行数不少于指针所指行号。
6. 一个「常见坑」小节，且至少含一条列表项。
7. 没有失效的相对链接（指向仓库内不存在的路径）。

有两个章节在结构上不属于「概念教学章」，适用精简后的检查项 —— 见
CHAPTER_OVERRIDES：
  - 00-setup：环境准备章，不是概念章。
  - 21-capstone-tour：完整项目导览章，不是可独立运行的示例
    （其 README 已明确说明这一点）。

用法：
    python scripts/check_tutorial_readmes.py            # 全量报告，检查所有章节
    python scripts/check_tutorial_readmes.py --check     # CI 模式：任一失败即退出码 1
    python scripts/check_tutorial_readmes.py 01-first-agent 22-group-chat-debate
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"

_MERMAID_RE = re.compile(r"```mermaid\b")
_CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_PATH_LINE_RE = re.compile(r"`?([a-zA-Z0-9_./-]+\.(?:py|cs|tsx?|ts))(?::(\d+))?`?")
_RELATIVE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_MIN_CONCEPT_CHARS = 200

# 章节契约要求的小节标题（中文）。
# 这四行是全仓库 36 个章节 README 的硬契约：标题必须逐字相等，
# 否则 CI 的 --check 模式会判定该章失败。修改此处必须同步修改所有章节。
HEADING_WHY = "本章动机"
HEADING_CONCEPT = "核心概念"
HEADING_CAPSTONE = "在完整项目中的落点"
HEADING_GOTCHAS = "常见坑"

# 结构上不适用某些检查的章节。某检查名未出现在该章的豁免集合中时，照常执行。
CHAPTER_OVERRIDES: dict[str, set[str]] = {
    "00-setup": {"walkthrough", "run_command", "capstone_pointer"},
    # 21-capstone-tour 的正文本身就是完整项目映射（一张按计划 4.4 列出
    # 大量 文件:行号 指针的表格），而不是一个单独标注的
    # 「## 在完整项目中的落点」小节 —— 专用检查不适配它的实际形态。
    "21-capstone-tour": {"walkthrough", "run_command", "capstone_pointer"},
}


@dataclass
class ChapterResult:
    chapter: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def discover_chapters() -> list[str]:
    return sorted(
        p.name
        for p in TUTORIALS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "README.md").exists()
    )


def _section(text: str, heading: str) -> str | None:
    """返回 `## <heading>` 这一行下方的正文，直到下一个 `##` 为止。"""
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def check_concept(text: str, result: ChapterResult) -> None:
    why = _section(text, HEADING_WHY) or ""
    concept = _section(text, HEADING_CONCEPT) or ""
    combined = f"{why}\n{concept}".strip()
    if not combined:
        result.failures.append(f"未找到「## {HEADING_WHY}」或「## {HEADING_CONCEPT}」小节")
        return
    prose_len = len(re.sub(r"\s+", " ", combined))
    if prose_len < _MIN_CONCEPT_CHARS:
        result.failures.append(f"概念正文过短（{prose_len} 字符 < {_MIN_CONCEPT_CHARS}）—— 疑似空壳")


def check_diagram(text: str, result: ChapterResult) -> None:
    if not _MERMAID_RE.search(text):
        result.failures.append("缺少 ```mermaid 代码块 —— 每章都至少需要一张图")


def check_run_command(text: str, result: ChapterResult) -> None:
    # 支持两种运行命令形式：
    # 直接执行 uv run --project tutorials python tutorials/<ch>/python/main.py，
    # 或进入章节独立项目目录后运行，
    # 例如先 cd tutorials/<ch>/python，
    # 随后 python main.py，第 20b 章采用此形式。
    code_blocks = [body for lang, body in _CODE_BLOCK_RE.findall(text) if lang in ("bash", "sh", "")]
    candidates: list[str] = []
    for block in code_blocks:
        for line in block.splitlines():
            m = re.search(r"tutorials/([\w./-]+\.py)", line)
            if m:
                candidates.append(m.group(1))
                continue
            m = re.search(r"^\s*cd\s+tutorials/([\w./-]+)\s*$", line)
            if m:
                candidates.append(m.group(1))
    if not candidates:
        result.failures.append("未找到引用 tutorials/... 路径的可运行命令")
        return
    # 捕获组已排除 "tutorials/" 前缀，因此它们是相对于 TUTORIALS_DIR 的路径。
    for rel in candidates:
        target = TUTORIALS_DIR / rel
        if not target.exists():
            result.failures.append(f"运行命令引用了「{rel}」，但该路径不存在")


def check_walkthrough(text: str, result: ChapterResult) -> None:
    for lang, body in _CODE_BLOCK_RE.findall(text):
        if lang in ("bash", "sh", "", "text"):
            continue
        if len(body.strip().splitlines()) >= 3:
            return
    result.failures.append(
        "缺少内联源码走读（一个不少于 3 行的非 bash 代码块）—— 只有运行命令不算走读"
    )


def check_capstone_pointer(text: str, result: ChapterResult) -> None:
    section = _section(text, HEADING_CAPSTONE)
    if section is None:
        result.failures.append(f"未找到「## {HEADING_CAPSTONE}」小节")
        return
    matches = [(p, ln) for p, ln in _PATH_LINE_RE.findall(section) if ln]
    if not matches:
        result.failures.append("完整项目落点小节中没有 `路径/文件.py:行号` 指针")
        return
    for rel_path, line_str in matches:
        target = REPO_ROOT / rel_path
        if not target.exists():
            result.failures.append(f"完整项目落点指针「{rel_path}」不存在")
            continue
        line_no = int(line_str)
        line_count = sum(1 for _ in target.open(encoding="utf-8", errors="replace"))
        if line_count < line_no:
            result.failures.append(
                f"完整项目落点指针「{rel_path}:{line_no}」超出该文件的 {line_count} 行"
            )


def check_gotchas(text: str, result: ChapterResult) -> None:
    section = _section(text, HEADING_GOTCHAS)
    if section is None:
        result.failures.append(f"未找到「## {HEADING_GOTCHAS}」小节")
        return
    bullets = [line for line in section.splitlines() if line.strip().startswith(("-", "*"))]
    if not bullets:
        result.failures.append(f"「## {HEADING_GOTCHAS}」小节没有任何列表项")


def check_dead_links(text: str, chapter_dir: Path, result: ChapterResult) -> None:
    for target in _RELATIVE_LINK_RE.findall(text):
        target = target.split(" ", 1)[0].strip()  # 去掉可选的 "title" 后缀
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path_part = target.split("#", 1)[0]
        if not path_part:
            continue
        resolved = (chapter_dir / path_part).resolve()
        if not resolved.exists():
            result.warnings.append(f"相对链接目标不存在：{target}")


def check_chapter(chapter: str) -> ChapterResult:
    result = ChapterResult(chapter=chapter)
    chapter_dir = TUTORIALS_DIR / chapter
    readme = chapter_dir / "README.md"
    text = readme.read_text(encoding="utf-8")
    exempt = CHAPTER_OVERRIDES.get(chapter, set())

    if "concept" not in exempt:
        check_concept(text, result)
    if "diagram" not in exempt:
        check_diagram(text, result)
    if "run_command" not in exempt:
        check_run_command(text, result)
    if "walkthrough" not in exempt:
        check_walkthrough(text, result)
    if "capstone_pointer" not in exempt:
        check_capstone_pointer(text, result)
    if "gotchas" not in exempt:
        check_gotchas(text, result)
    if "dead_links" not in exempt:
        check_dead_links(text, chapter_dir, result)

    return result



# ---------------------------------------------------------------------------
# docs/concepts/** 的源码指针
#
# 概念页会指向真实源码。过去用的是 `file.py:123` 这种行号引用，而它会无声
# 漂移：文件改了，行号没改，指针于是自信地指向了毫不相干的代码。写这个检查
# 时已经有两个指针越过了文件末尾，还有几个只差个位数 —— 后者更糟，因为读者
# 无法分辨哪些是错的、哪些是对的。
#
# 现在它们改为引用文件、并在正文中点名符号。本检查防止该修复回退。
#
# 关于范围的说明：本检查的早期版本还试图校验被点名的符号仍存在于所引文件
# 中，那样还能顺带发现删除。但正则无法可靠区分「为这个文件引用的符号」和
# 「恰好出现在文件名附近的标识符」—— 那次尝试在约 45 条链接中只匹配到 6 条，
# 其中还有两条是错配。要真正校验符号，需要在页面里写显式的机器可读标注，
# 而不是靠启发式。因此「符号被删除」仍是一个已知缺口。
# ---------------------------------------------------------------------------

CONCEPTS_DIR = REPO_ROOT / "docs" / "concepts"
_LINE_CITATION = re.compile(r"`?[A-Za-z0-9_/.-]+\.(?:py|tsx|ts|sql|cs):[0-9]+")


def check_concept_pointers() -> list[str]:
    """若 docs/concepts/ 中重新出现带行号的源码引用，则判定失败。"""
    failures: list[str] = []
    if not CONCEPTS_DIR.exists():
        return failures
    for page in sorted(CONCEPTS_DIR.glob("*.md")):
        for m in _LINE_CITATION.finditer(page.read_text(encoding="utf-8")):
            failures.append(
                f"{page.name}：出现带行号的引用 {m.group(0)!r} —— 请改为引用文件、"
                f"并在正文中点名符号；行号会无声漂移"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("chapters", nargs="*", help="指定要检查的章节目录（默认：全部）")
    parser.add_argument("--check", action="store_true", help="CI 模式：任一章节失败即退出码 1")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="要跳过的章节目录（可重复）—— 例如某个仍在恢复中的章节",
    )
    args = parser.parse_args()

    chapters = [c for c in (args.chapters or discover_chapters()) if c not in args.exclude]
    results = [check_chapter(c) for c in chapters]

    passed = sum(1 for r in results if r.passed)
    print(f"章节契约检查 —— {passed}/{len(results)} 通过\n")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.chapter}")
        for f in r.failures:
            print(f"    ✗ {f}")
        for w in r.warnings:
            print(f"    ! {w}")

    pointer_failures = check_concept_pointers()
    if pointer_failures:
        print("\n概念页源码指针")
        for f in pointer_failures:
            print(f"    x {f}")
    else:
        print("\n概念页源码指针 —— 无带行号的引用")

    any_failed = any(not r.passed for r in results) or bool(pointer_failures)
    if args.check and any_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
