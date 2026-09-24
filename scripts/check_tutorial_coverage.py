#!/usr/bin/env python3
"""生成 —— 并在 CI 中校验 —— tutorials/README.md 里的章节覆盖表。

tutorials/README.md 长期声称它的状态表「由磁盘上的真实情况生成，因此这段说明
一旦与表不符，以表为准」。这句话曾经不成立：表是手工维护的，而且确实漂移了。
旧表把第 12–20 章标为已有代码、测试待补，而其中若干章当时没有代码和测试。

本脚本让这句话成立。它遍历各章目录，从**真实存在**的文件推导每章状态，并重写
tutorials/README.md 中两个标记之间的表格。

状态是推导出来的，从不靠声明：

    可运行 · CI 已测试    有代码，且有测试目录
    可运行 · 测试待补      有代码，无测试
    仅指南                该章按设计不提供可运行代码
    规划中                该章尚未落地

用法：
    python scripts/check_tutorial_coverage.py            # 打印表格
    python scripts/check_tutorial_coverage.py --write    # 重写 README.md
    python scripts/check_tutorial_coverage.py --check    # CI：表格过期则退出码 1
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"
README = TUTORIALS_DIR / "README.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED COVERAGE TABLE -->"
END_MARKER = "<!-- END GENERATED COVERAGE TABLE -->"

TESTED = "可运行 · CI 已测试"
UNTESTED = "可运行 · 测试待补"
NO_CODE = "无代码"
GUIDE_ONLY = "仅指南"
PLANNED = "规划中"

# 按设计不提供可运行代码的章节。其余章节一律按磁盘上的实际内容判定，
# 因此某章无法悄悄声称自己拥有并不存在的覆盖。
NO_CODE_BY_DESIGN = {
    "00-setup": GUIDE_ONLY,
    "21-capstone-tour": PLANNED,
}


@dataclass(frozen=True)
class Chapter:
    slug: str
    title: str
    status: str

    @property
    def number(self) -> str:
        return self.slug.split("-", 1)[0]


def _has_python_code(chapter_dir: Path) -> bool:
    py = chapter_dir / "python"
    return py.is_dir() and any(p.suffix == ".py" for p in py.glob("*.py"))


def _has_python_tests(chapter_dir: Path) -> bool:
    tests = chapter_dir / "python" / "tests"
    return tests.is_dir() and any(tests.glob("test_*.py"))


def _status(has_code: bool, has_tests: bool) -> str:
    if not has_code:
        return NO_CODE
    return TESTED if has_tests else UNTESTED


def _title(chapter_dir: Path) -> str:
    """章节标题，取自其 README 的一级标题。"""
    readme = chapter_dir / "README.md"
    for line in readme.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            # "第 22 章 · 群聊辩论（…）" -> "群聊辩论（…）"
            return re.sub(r"^第\s*\d+[a-z]?\s*章\s*[·—–-]\s*", "", heading)
    return chapter_dir.name


def discover() -> list[Chapter]:
    chapters: list[Chapter] = []

    for chapter_dir in sorted(TUTORIALS_DIR.iterdir(), key=lambda p: p.name):
        if not chapter_dir.is_dir():
            continue
        if chapter_dir.name.startswith(("_", ".")):
            continue
        if not (chapter_dir / "README.md").exists():
            continue

        slug = chapter_dir.name

        if slug in NO_CODE_BY_DESIGN:
            chapters.append(Chapter(slug, _title(chapter_dir), NO_CODE_BY_DESIGN[slug]))
            continue

        chapters.append(Chapter(
            slug,
            _title(chapter_dir),
            _status(_has_python_code(chapter_dir), _has_python_tests(chapter_dir)),
        ))

    return chapters


def render(chapters: list[Chapter]) -> str:
    lines = [
        "| # | 章节 | 状态 |",
        "|---|------|------|",
    ]

    for chapter in chapters:
        lines.append(
            f"| {chapter.number} | [{chapter.title}](./{chapter.slug}/) | {chapter.status} |"
        )

    return "\n".join(lines)


def summarize(chapters: list[Chapter]) -> str:
    def count(status: str) -> int:
        return sum(1 for c in chapters if c.status == status)

    return (
        f"共 {len(chapters)} 章 —— "
        f"可运行且 CI 已测试 {count(TESTED)} 章，"
        f"可运行但测试待补 {count(UNTESTED)} 章，"
        f"无代码 {count(NO_CODE)} 章，"
        f"仅指南 {count(GUIDE_ONLY)} 章，"
        f"规划中 {count(PLANNED)} 章"
    )


def _splice(text: str, table: str) -> str:
    pattern = re.compile(
        rf"({re.escape(BEGIN_MARKER)}\n).*?(\n{re.escape(END_MARKER)})",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(
            f"{README} 缺少 {BEGIN_MARKER} / {END_MARKER} 标记 —— "
            "请先在覆盖表两侧加上这两个标记，再运行本脚本。"
        )
    return pattern.sub(lambda m: f"{m.group(1)}{table}{m.group(2)}", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="就地重写 tutorials/README.md")
    group.add_argument("--check", action="store_true", help="已提交的表格过期则退出码 1")
    args = parser.parse_args()

    chapters = discover()
    table = render(chapters)

    if not args.write and not args.check:
        print(table)
        print()
        print(summarize(chapters))
        return 0

    current = README.read_text(encoding="utf-8")
    updated = _splice(current, table)

    if args.write:
        if updated != current:
            README.write_text(updated, encoding="utf-8")
            print(f"已更新 {README.relative_to(REPO_ROOT)}")
        else:
            print(f"{README.relative_to(REPO_ROOT)} 已是最新")
        print(summarize(chapters))
        return 0

    if updated != current:
        print(
            "tutorials/README.md 的覆盖表与磁盘实际内容不一致。\n"
            "请运行：python scripts/check_tutorial_coverage.py --write\n",
            file=sys.stderr,
        )
        print("期望内容：\n", file=sys.stderr)
        print(table, file=sys.stderr)
        return 1

    print(f"覆盖表已是最新 —— {summarize(chapters)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
