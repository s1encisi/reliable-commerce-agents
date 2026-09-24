"""浏览器与 A2A 路径都必须完整保留多行数据。"""

from shared.sse import encode_sse, iter_sse


async def test_multiline_card_roundtrips_including_trailing_newline():
    text = '商品如下：\n```product\n{"id":"x","price":399}\n```\n'
    frame = encode_sse(text, "delta")

    async def lines():
        for line in frame.splitlines():
            yield line

    assert [x async for x in iter_sse(lines())] == [("delta", text)]
