"""One deadline and attempt budget; uncertain writes are reconciled, not retried."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg


@dataclass
class RetryBudget:
    deadline: float
    max_attempts: int = 3
    attempts: int = 0

    @classmethod
    def start(cls, seconds: float = 10.0, max_attempts: int = 3) -> "RetryBudget":
        return cls(time.monotonic() + seconds, max_attempts)

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    async def wait(self, delay: float) -> None:
        if delay < 0 or delay >= self.remaining:
            raise TimeoutError("Operation deadline exhausted")
        await asyncio.sleep(delay)


TRANSIENT = (OSError, asyncpg.PostgresConnectionError, asyncpg.SerializationError, asyncpg.DeadlockDetectedError)


async def retry_read[T](call: Callable[[], Awaitable[T]], budget: RetryBudget) -> T:
    """For side-effect-free calls only, sharing the caller's total deadline."""
    while budget.attempts < budget.max_attempts:
        budget.attempts += 1
        try:
            async with asyncio.timeout(budget.remaining):
                return await call()
        except (*TRANSIENT, TimeoutError):
            if budget.attempts >= budget.max_attempts or budget.remaining <= 0:
                raise
            await budget.wait(min(0.05 * 2 ** (budget.attempts - 1), 0.5))
    raise TimeoutError("Attempt budget exhausted")
