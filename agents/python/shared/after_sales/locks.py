"""Session locks release on disconnect: never reclaim live work by elapsed time."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from shared.db import get_pool


@asynccontextmanager
async def workflow_resume_lock(run_id: str) -> AsyncIterator[bool]:
    key = int.from_bytes(hashlib.sha256(f"return-resume:{run_id}".encode()).digest()[:8], signed=True)
    async with get_pool().acquire() as conn:
        acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
        try:
            yield bool(acquired)
        finally:
            if acquired and not conn.is_closed():
                await conn.execute("SELECT pg_advisory_unlock($1)", key)
