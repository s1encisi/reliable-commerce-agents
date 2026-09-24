#!/usr/bin/env python3
"""在所有携带版本号的位置统一设置项目版本。

git tag 是版本号的唯一事实来源，其余位置都由本脚本同步过去。``release.yml`` 里的
``version-check`` 作业会在二者不一致时让发布失败 —— 正是它避免了本脚本当初要修的
那种局面重演：``pyproject.toml`` 写的是 ``0.1.0``，唯一的 git tag 却是 ``v1.0.0``，
而 README 里写的又是 v1.1。

用法::

    python scripts/bump_version.py 1.1.0
    python scripts/bump_version.py 1.2.0-rc.1
    python scripts/bump_version.py 1.1.0 --check     # 只校验，不做任何修改

随后检查 diff、提交并打 tag::

    git commit -am "chore: bump version to 1.1.0"
    git tag v1.1.0 && git push origin v1.1.0

``--check`` 是持续集成使用的方式：只要有任何文件与传入的版本号不一致就以非零状态
退出，并且绝不写文件。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# PEP 440 与 semver 的交集：X.Y.Z 加上可选的预发布后缀。这里刻意保持严格 ——
# 一个写错却仍能通过解析的版本号，比在这里直接失败更糟，因为它会先被推上 tag，
# 之后才被人发现。
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


@dataclass(frozen=True)
class VersionFile:
    """一个携带版本号的文件，以及用于定位版本号的正则。"""

    path: Path
    pattern: re.Pattern[str]
    template: str
    description: str

    def current(self, text: str) -> str | None:
        match = self.pattern.search(text)
        return match.group(1) if match else None


VERSION_FILES: tuple[VersionFile, ...] = (
    VersionFile(
        path=REPO_ROOT / "agents/python/pyproject.toml",
        pattern=re.compile(r'^version = "([^"]+)"', re.MULTILINE),
        template='version = "{version}"',
        description="Python 后端",
    ),
    VersionFile(
        path=REPO_ROOT / "web/package.json",
        pattern=re.compile(r'^  "version": "([^"]+)",', re.MULTILINE),
        template='  "version": "{version}",',
        description="Next.js 前端",
    ),
)


def normalise(version: str) -> str:
    """接受 ``v1.1.0`` 或 ``1.1.0``；文件里从不携带 ``v``。"""
    return version[1:] if version.startswith("v") else version


def bump_file(spec: VersionFile, version: str, *, check: bool) -> bool:
    """文件已匹配或更新成功时返回 True。"""
    if not spec.path.exists():
        print(f"  [MISS] {spec.path.relative_to(REPO_ROOT)} 不存在", file=sys.stderr)
        return False

    text = spec.path.read_text(encoding="utf-8")
    current = spec.current(text)

    if current is None:
        print(
            f"  [MISS] {spec.path.relative_to(REPO_ROOT)}: 没有任何版本号字段匹配"
            f"（正则: {spec.pattern.pattern!r}）",
            file=sys.stderr,
        )
        return False

    if current == version:
        print(f"  [OK]   {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} 已是 {version}")
        return True

    if check:
        print(
            f"  [DIFF] {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} 为 {current}，期望 {version}",
            file=sys.stderr,
        )
        return False

    spec.path.write_text(spec.pattern.sub(spec.template.format(version=version), text, count=1), encoding="utf-8")
    print(f"  [SET]  {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} {current} -> {version}")
    return True


def open_changelog_section(version: str, *, check: bool) -> bool:
    """在 Unreleased 标题下插入本版本的空章节。

    只添加骨架。具体写什么属于「哪些改动对用户可见」的判断，那不该由脚本生成。
    """
    path = REPO_ROOT / "CHANGELOG.md"
    if not path.exists():
        print("  [MISS] CHANGELOG.md 不存在", file=sys.stderr)
        return False

    text = path.read_text(encoding="utf-8")
    heading = f"## [{version}]"

    if heading in text:
        print(f"  [OK]   changelog         章节 {heading} 已存在")
        return True

    if check:
        print(f"  [DIFF] changelog         CHANGELOG.md 中没有 {heading} 章节", file=sys.stderr)
        return False

    anchor = "## [Unreleased]"
    if anchor not in text:
        print(f"  [MISS] CHANGELOG.md 中没有可在其下插入内容的 {anchor!r} 标题", file=sys.stderr)
        return False

    # 把 Unreleased 下已有的内容搬进新章节，而不是在它上面压一个空骨架 ——
    # 否则发布说明会是空的，而本该写进去的内容却孤零零地留在下面，发布 1.2.0
    # 时正是如此。
    before, _, rest = text.partition(anchor)
    carried, sep, remainder = rest.partition("\n## [")
    carried = carried.strip("\n")
    body = carried if carried else "### Added\n\n### Fixed\n\n### Changed"
    skeleton = f"{anchor}\n\n{heading} - {date.today().isoformat()}\n\n{body}\n\n"
    path.write_text(before + skeleton + (sep + remainder if sep else ""), encoding="utf-8")
    print(f"  [SET]  changelog         已开启 {heading} —— 打 tag 前请先补全内容")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在所有出现版本号的位置统一设置项目版本",
        epilog="git tag 是事实来源；本脚本把各个文件同步到它。",
    )
    parser.add_argument("version", help="要设置的版本号，例如 1.1.0 或 v1.1.0")
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验每个文件是否已携带该版本号；不写任何文件，不一致时以非零状态退出",
    )
    args = parser.parse_args()

    version = normalise(args.version)
    if not VERSION_RE.match(version):
        print(f"error: {version!r} 不是合法版本号（应为 X.Y.Z 或 X.Y.Z-suffix）", file=sys.stderr)
        return 2

    verb = "正在校验" if args.check else "正在设置"
    print(f"{verb}版本 {version}\n")

    results = [bump_file(spec, version, check=args.check) for spec in VERSION_FILES]
    results.append(open_changelog_section(version, check=args.check))

    if not all(results):
        if args.check:
            print(f"\n校验失败 —— 运行 'python scripts/bump_version.py {version}' 修复", file=sys.stderr)
        else:
            print("\n部分文件无法更新", file=sys.stderr)
        return 1

    if not args.check:
        print("\n完成。请检查 diff、补全 CHANGELOG 章节，然后执行:\n")
        print(f"  git commit -am 'chore: bump version to {version}'")
        print(f"  git tag v{version} && git push origin v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
