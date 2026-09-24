"""旧同步评测客户端也必须服从同一累计账本。"""

import io
import json
from decimal import Decimal

from shared.campaign_budget import CampaignBudget, Price
from shared.jev.client import JevClient


def test_sync_client_cannot_bypass_campaign_budget(tmp_path, monkeypatch):
    import urllib.request

    import shared.paid_transport

    budget = CampaignBudget(tmp_path / "budget.json")
    monkeypatch.setattr(shared.paid_transport, "configured_budget", lambda: budget)
    monkeypatch.setattr(
        shared.paid_transport, "configured_price", lambda provider: Price("USD", Decimal(".042"), Decimal(0), "test")
    )
    response = {"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 100, "output_tokens": 2}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    JevClient(api_key="test-only", max_retries=0).ask("synthetic", {"q": {"type": "noul", "instructions": "yes?"}})
    assert budget.snapshot()["jev"]["calls"] == 1
    assert budget.snapshot()["jev"]["committed_or_reserved"] == 0.000005
