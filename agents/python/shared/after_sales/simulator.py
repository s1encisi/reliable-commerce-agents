"""Persistent loopback fulfillment supplier. Synthetic pickup receipts only; no payments."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from shared.after_sales.fulfillment import payload_hash


class PickupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_id: UUID
    order_id: UUID
    action: Literal["return_pickup"]


def create_app(database: Path) -> FastAPI:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pickups (
                idempotency_key TEXT PRIMARY KEY,
                return_id TEXT NOT NULL UNIQUE,
                payload_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                receipt TEXT NOT NULL
            )"""
        )
    app = FastAPI(title="Local fulfillment simulator", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "simulation": True}

    @app.post("/pickups")
    def pickup(body: PickupRequest, idempotency_key: str = Header(alias="Idempotency-Key", max_length=100)) -> dict:
        if idempotency_key != f"return-pickup:{body.return_id}":
            raise HTTPException(409, "Idempotency key must bind this return")
        payload = body.model_dump(mode="json")
        digest = payload_hash(payload)
        with closing(sqlite3.connect(database, timeout=5)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_hash, receipt FROM pickups WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise HTTPException(409, "The idempotency key is bound to different parameters")
                return json.loads(existing[1])
            receipt = {
                "idempotency_key": idempotency_key,
                "payload_hash": digest,
                "supplier_reference": f"sim-pickup-{uuid4()}",
                "status": "ACCEPTED",
                "simulation": True,
            }
            conn.execute(
                "INSERT INTO pickups VALUES (?, ?, ?, ?, ?)",
                (idempotency_key, str(body.return_id), digest, json.dumps(payload), json.dumps(receipt)),
            )
        return receipt

    @app.get("/pickups/{idempotency_key}")
    def get_pickup(idempotency_key: str) -> dict:
        with closing(sqlite3.connect(database)) as conn, conn:
            row = conn.execute("SELECT receipt FROM pickups WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if row is None:
            raise HTTPException(404, "No accepted pickup for this key")
        return json.loads(row[0])

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="只在本机运行的履约模拟供应商，不处理真实支付")
    parser.add_argument("--database", type=Path, required=True, help="独立 SQLite 回执文件")
    parser.add_argument("--port", type=int, default=8199)
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
