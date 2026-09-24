"""真实 HTTP 评测发现的标识符误脱敏与查询别名回归。"""

from types import SimpleNamespace

from shared.middleware import PiiRedactionMiddleware
from shared.search import expand_catalog_query


async def test_uuid_survives_redaction_but_real_card_is_masked():
    uid = "11111111-1111-4111-8111-111111111111"
    content = SimpleNamespace(text=f"查商品 {uid}，银行卡 4111-1111-1111-1111")
    ctx = SimpleNamespace(messages=[SimpleNamespace(contents=[content])])

    async def next_call():
        pass

    await PiiRedactionMiddleware().process(ctx, next_call)
    assert uid in content.text
    assert "[REDACTED-CARD]" in content.text
    assert "银行卡 4111" not in content.text


def test_query_expansion_retains_original_words():
    assert expand_catalog_query("无线耳机") == "无线耳机 headphones"
    assert expand_catalog_query("headphones") == "headphones"
