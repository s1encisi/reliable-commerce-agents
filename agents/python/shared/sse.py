"""标准 SSE 多行编码，避免整块核验输出丢失换行与卡片 JSON。"""

from collections.abc import AsyncIterator


def encode_sse(data: str, event: str = "") -> str:
    prefix = f"event: {event}\n" if event else ""
    return prefix + "".join(f"data: {line}\n" for line in data.split("\n")) + "\n"


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    event = ""
    parts: list[str] = []
    async for line in lines:
        if not line:
            if parts:
                yield event, "\n".join(parts)
            event, parts = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            parts.append(line[5:].removeprefix(" "))
    if parts:
        yield event, "\n".join(parts)
