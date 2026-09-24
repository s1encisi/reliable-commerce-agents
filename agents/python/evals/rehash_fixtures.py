"""一次性迁移：按当前哈希重新为已提交的回放夹具建立键。

每个夹具都存有它自己的原始 ``request``，因此 ``shared/replay_client.py`` 中
哈希方案的变更可以*离线*应用到整个语料库——无需 API 凭据，无需重新录制，
而且已录制的响应永远不会被触碰，这正是让相邻轮次之间的 ``call_id`` 链条
保持完整的原因。

它是为 ``_normalize_for_hash`` 那次变更（issue #25）而写的，但与具体方案
无关：它总是用 ``_request_hash`` 当前的行为重新计算，因此下次键再变化时
仍可复用。

当两个夹具塌缩到同一个哈希上时，说明它们是*同一个逻辑请求*在不同种子会话
中被录制了两次——这正是引入归一化想要消除的那种重复。只能保留其中一个，
而*保留哪一个*很关键：每一个都记录了一条不同的模型轨迹，且后续轮次是
针对某一条特定轨迹录制的。若保留错了兄弟节点，被丢弃那个的下游每一个夹具
都会搁浅，而这种问题会在很久之后表现为一次无从解释的夹具未命中，而不是
在这里报错。

因此，保留者是拥有最多消费方的那个兄弟节点——即其他那些重放其响应的夹具，
无论它们是把这段对话当作字面续写，还是在编排器链中把专业智能体的文字
嵌入到某个工具结果里。mtime 只用作并列时的决胜依据。

用法::

    uv run python -m evals.rehash_fixtures --dry-run
    uv run python -m evals.rehash_fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from shared.replay_client import _request_hash

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "replay"


def plan_rehash(fixtures_dir: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    """按重新计算出的哈希对每个夹具分组。

    返回 ``(groups, unreadable)``，其中 ``groups`` 把新哈希映射到当前声明
    该哈希的文件，最新的排在前面。
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    unreadable: list[Path] = []

    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            new_hash = _request_hash(data["request"])
        except (json.JSONDecodeError, KeyError, OSError):
            unreadable.append(path)
            continue
        groups[new_hash].append(path)

    consumers = _consumer_counts(fixtures_dir)
    for paths in groups.values():
        paths.sort(key=lambda p: (consumers.get(p.name, 0), p.stat().st_mtime), reverse=True)

    return dict(groups), unreadable


def _consumer_counts(fixtures_dir: Path) -> dict[str, int]:
    """每个夹具所记录的响应被多少个其他夹具所依赖。

    后续轮次会逐字嵌入前一轮的响应——或是作为它所续写的助手消息，或是
    （对编排器而言）作为经由 ``call_specialist_agent`` 返回并落入某个工具
    结果的专业智能体文字。无论哪种方式，都是对原始请求做子串匹配，这使得
    依赖关系可以在离线状态下计算，既不需要数据库也不需要模型。
    """
    responses: dict[str, list[str]] = {}
    requests: dict[str, str] = {}

    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        requests[path.name] = json.dumps(data.get("request", {}), sort_keys=True)
        texts: list[str] = []
        for message in data.get("response", {}).get("messages", []):
            for content in message.get("contents", []):
                text = content.get("text")
                # 短字符串（"Sure!"）会到处都匹配；只有足够长的响应才能
                # 唯一标识一条轨迹。
                if isinstance(text, str) and len(text) >= 40:
                    texts.append(text)
        responses[path.name] = texts

    counts: dict[str, int] = {}
    for name, texts in responses.items():
        counts[name] = sum(
            1 for other, blob in requests.items() if other != name and any(json.dumps(t)[1:-1] in blob for t in texts)
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without touching the filesystem.",
    )
    args = parser.parse_args(argv)

    fixtures_dir: Path = args.fixtures_dir
    if not fixtures_dir.is_dir():
        print(f"Not a directory: {fixtures_dir}", file=sys.stderr)
        return 1

    groups, unreadable = plan_rehash(fixtures_dir)
    total = sum(len(p) for p in groups.values()) + len(unreadable)

    renames: list[tuple[Path, Path]] = []
    drops: list[Path] = []
    for new_hash, paths in sorted(groups.items()):
        keeper, *duplicates = paths
        drops.extend(duplicates)
        if keeper.stem != new_hash:
            renames.append((keeper, fixtures_dir / f"{new_hash}.json"))

    print(f"Fixtures scanned:   {total}")
    print(f"Distinct requests:  {len(groups)}")
    print(f"To rename:          {len(renames)}")
    print(f"Duplicates to drop: {len(drops)}")
    if unreadable:
        print(f"Unreadable (left alone): {len(unreadable)}", file=sys.stderr)
        for path in unreadable:
            print(f"  ! {path.name}", file=sys.stderr)

    for old, new in renames:
        print(f"  {old.name} -> {new.name}")
    for path in drops:
        print(f"  drop {path.name} (duplicate of an existing request)")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    for path in drops:
        path.unlink()

    # 先经由临时文件名重命名：某个夹具的新哈希可能与*另一个*夹具当前的
    # 文件名冲突，因此就地重命名会覆盖掉本循环尚未访问到的文件。
    staged: list[tuple[Path, Path]] = []
    for old, new in renames:
        tmp = old.with_suffix(".json.rehash-tmp")
        old.rename(tmp)
        staged.append((tmp, new))
    for tmp, new in staged:
        tmp.rename(new)

    print(f"\nRewrote {len(renames)} filename(s), removed {len(drops)} duplicate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
