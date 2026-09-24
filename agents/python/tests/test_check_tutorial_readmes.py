"""教程章节契约检查器测试。

直接导入检查脚本，用合成 README 验证各检查函数。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_tutorial_readmes.py"

_spec = importlib.util.spec_from_file_location("check_tutorial_readmes", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_tutorial_readmes"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

ChapterResult = _module.ChapterResult
check_concept = _module.check_concept
check_diagram = _module.check_diagram
check_run_command = _module.check_run_command
check_walkthrough = _module.check_walkthrough
check_capstone_pointer = _module.check_capstone_pointer
check_gotchas = _module.check_gotchas
check_dead_links = _module.check_dead_links
check_chapter = _module.check_chapter
discover_chapters = _module.discover_chapters


_LONG_CONCEPT = "x" * 250


def test_check_concept_fails_on_missing_section() -> None:
    result = ChapterResult(chapter="t")
    check_concept("# Chapter\n\nNo concept here.", result)
    assert result.failures and "未找到「## 本章动机」" in result.failures[0]


def test_check_concept_fails_on_short_stub() -> None:
    result = ChapterResult(chapter="t")
    check_concept("## 核心概念\n\nToo short.\n", result)
    assert result.failures and "过短" in result.failures[0]


def test_check_concept_passes_with_enough_prose() -> None:
    result = ChapterResult(chapter="t")
    check_concept(f"## 核心概念\n\n{_LONG_CONCEPT}\n\n## Next\n", result)
    assert result.failures == []


def test_check_concept_combines_why_and_concept_sections() -> None:
    result = ChapterResult(chapter="t")
    text = f"## 本章动机\n\n{'a' * 120}\n\n## 核心概念\n\n{'b' * 120}\n"
    check_concept(text, result)
    assert result.failures == []


def test_check_diagram_fails_without_mermaid_block() -> None:
    result = ChapterResult(chapter="t")
    check_diagram("no diagrams here", result)
    assert result.failures

    result2 = ChapterResult(chapter="t")
    check_diagram("```mermaid\ngraph LR\n  a --> b\n```", result2)
    assert result2.failures == []


def test_check_run_command_fails_without_a_path() -> None:
    result = ChapterResult(chapter="t")
    check_run_command("```bash\necho hi\n```", result)
    assert result.failures


def test_check_run_command_passes_for_real_existing_chapter_script() -> None:
    result = ChapterResult(chapter="t")
    text = "```bash\nuv run --project tutorials python tutorials/01-first-agent/python/main.py\n```"
    check_run_command(text, result)
    assert result.failures == []


def test_check_run_command_fails_for_nonexistent_script() -> None:
    result = ChapterResult(chapter="t")
    text = "```bash\nuv run --project tutorials python tutorials/nonexistent-chapter/python/main.py\n```"
    check_run_command(text, result)
    assert result.failures and "不存在" in result.failures[0]


def test_check_run_command_recognizes_cd_pattern() -> None:
    # 覆盖第 20b 章先进入独立项目目录的命令形式。
    result = ChapterResult(chapter="t")
    text = "```bash\ncd tutorials/01-first-agent/python\nuv run python main.py\n```"
    check_run_command(text, result)
    assert result.failures == []


def test_check_walkthrough_fails_with_only_bash_blocks() -> None:
    result = ChapterResult(chapter="t")
    check_walkthrough("```bash\nuv run something\n```", result)
    assert result.failures


def test_check_walkthrough_passes_with_a_real_code_block() -> None:
    result = ChapterResult(chapter="t")
    code = "```python\nagent = Agent(...)\nresponse = await agent.run(prompt)\nprint(response.text)\n```"
    check_walkthrough(code, result)
    assert result.failures == []


def test_check_capstone_pointer_fails_without_section() -> None:
    result = ChapterResult(chapter="t")
    check_capstone_pointer("# no such section", result)
    assert result.failures


def test_check_capstone_pointer_fails_for_nonexistent_file() -> None:
    result = ChapterResult(chapter="t")
    text = "## 在完整项目中的落点\n\nSee `agents/python/no_such_file.py:10`.\n"
    check_capstone_pointer(text, result)
    assert result.failures and "不存在" in result.failures[0]


def test_check_capstone_pointer_fails_when_line_exceeds_file_length() -> None:
    result = ChapterResult(chapter="t")
    # 该脚本真实存在，但行数远小于 999999。
    text = "## 在完整项目中的落点\n\nSee `scripts/check_tutorial_readmes.py:999999`.\n"
    check_capstone_pointer(text, result)
    assert result.failures and "超出" in result.failures[0]


def test_check_capstone_pointer_passes_for_a_real_pointer() -> None:
    result = ChapterResult(chapter="t")
    text = "## 在完整项目中的落点\n\nSee `scripts/check_tutorial_readmes.py:1`.\n"
    check_capstone_pointer(text, result)
    assert result.failures == []


def test_check_gotchas_fails_without_bullets() -> None:
    result = ChapterResult(chapter="t")
    check_gotchas("## 常见坑\n\nJust prose, no bullets.\n", result)
    assert result.failures


def test_check_gotchas_passes_with_a_bullet() -> None:
    result = ChapterResult(chapter="t")
    check_gotchas("## 常见坑\n\n- Watch out for X.\n", result)
    assert result.failures == []


def test_check_dead_links_warns_on_missing_relative_target(tmp_path: Path) -> None:
    result = ChapterResult(chapter="t")
    text = "[broken](../does-not-exist/)"
    check_dead_links(text, tmp_path, result)
    assert result.warnings and "不存在" in result.warnings[0]


def test_check_dead_links_ignores_http_and_anchor_links(tmp_path: Path) -> None:
    result = ChapterResult(chapter="t")
    text = "[external](https://example.com) and [anchor](#section)"
    check_dead_links(text, tmp_path, result)
    assert result.warnings == []


def test_check_dead_links_passes_for_real_relative_target(tmp_path: Path) -> None:
    (tmp_path / "sibling").mkdir()
    result = ChapterResult(chapter="t")
    check_dead_links("[ok](./sibling/)", tmp_path, result)
    assert result.warnings == []


# ─────────────────────── discover + full-chapter integration ──────────


def test_discover_chapters_excludes_underscore_and_dot_dirs() -> None:
    chapters = discover_chapters()
    assert not any(c.startswith(("_", ".")) for c in chapters)
    assert "01-first-agent" in chapters
    assert ".pytest_cache" not in chapters


def test_check_chapter_fails_multiple_checks_on_a_launcher_stub() -> None:
    # 合成最小占位文档，只有标题、摘要和运行命令，
    # 缺少概念、图、走读、落点和常见坑。
    # 用于验证不完整章节会被拒绝，
    # 不绑定任何真实章节的当前内容，
    # 避免随文档更新而失效。
    result = ChapterResult(chapter="stub")
    stub_text = (
        "# Chapter NN — Something\n\n"
        "A one-line summary.\n\n"
        "## Run the demo\n\n"
        "```bash\nuv run --project tutorials python tutorials/01-first-agent/python/main.py\n```\n"
    )
    check_concept(stub_text, result)
    check_diagram(stub_text, result)
    check_walkthrough(stub_text, result)
    check_capstone_pointer(stub_text, result)
    check_gotchas(stub_text, result)
    assert not result.passed
    assert len(result.failures) >= 3


def test_every_chapter_passes() -> None:
    # 历史修复补全了缺失教程内容，
    # 完整项目导览也依赖这些章节，
    # 这里保留相应回归检查：
    # 全部发现的章节必须真正通过检查，
    # 不能只满足检查程序不崩溃。
    chapters = discover_chapters()
    assert chapters, "expected to find restored chapters"
    failing = [c for c in chapters if not check_chapter(c).passed]
    assert failing == []


def test_cli_check_mode_returns_zero_now_that_every_chapter_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # 教程 CI 使用相同检查，无需排除章节。
    # main() 返回退出码，只有 __main__ 才调用 sys.exit，
    # 因此直接调用不会抛出 SystemExit。
    monkeypatch.setattr(sys, "argv", ["check_tutorial_readmes.py", "--check"])
    assert _module.main() == 0


def test_cli_exclude_flag_drops_a_chapter_from_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # 通用排除行为不依赖章节当前是否通过：
    # 排除已有章节应缩小检查集合，
    # 排除不存在章节不应崩溃。
    monkeypatch.setattr(
        sys, "argv", ["check_tutorial_readmes.py", "--check", "--exclude", "01-first-agent", "--exclude", "nope"]
    )
    assert _module.main() == 0


def test_00_setup_is_exempted_from_walkthrough_and_run_command_checks() -> None:
    result = check_chapter("00-setup")
    joined = " ".join(result.failures)
    assert "walkthrough" not in joined
    assert "runnable command" not in joined


@pytest.mark.parametrize("chapter", ["00-setup", "21-capstone-tour", "20b-devui"])
def test_every_discovered_chapter_is_checkable_without_crashing(chapter: str) -> None:
    # 此处只检查程序能处理实际章节结构，
    # 不对各章节的通过状态作断言，
    # 也覆盖结构不同的指南章节。
    result = check_chapter(chapter)
    assert isinstance(result.failures, list)
