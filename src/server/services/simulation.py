"""Normalize the opening payload before comparing retry identity."""

import hashlib
import json
from decimal import Decimal

from src.server.database import simulation as db
from src.server.models.simulation import OpeningRequest, OpeningSnapshot


def canonical_opening(request: OpeningRequest) -> tuple[dict, str]:
    payload = request.model_dump(mode="json")
    payload["initial_cash"] = format(
        request.initial_cash.quantize(Decimal("0.01")), "f"
    )
    for row, original in zip(payload["holdings"], request.holdings, strict=True):
        row["total_cost"] = format(original.total_cost.normalize(), "f")
        if not row["acquisition_dates"]:
            row.pop("acquisition_dates")
        else:
            row["acquisition_dates"] = sorted(set(row["acquisition_dates"]))
        if row["batch_details_known"]:
            row.pop("batch_details_known")
    payload["holdings"].sort(key=lambda row: json.dumps(row, sort_keys=True))
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return payload, digest


async def initialize(
    user_id: str, request: OpeningRequest
) -> tuple[OpeningSnapshot, bool]:
    payload, digest = canonical_opening(request)
    row, created = await db.initialize(user_id, payload, digest)
    return _snapshot(row), created


async def get_opening(user_id: str) -> OpeningSnapshot | None:
    row = await db.get_opening(user_id)
    return _snapshot(row) if row else None


def _snapshot(row: dict) -> OpeningSnapshot:
    return OpeningSnapshot.model_validate(
        {
            **row["opening_payload"],
            "account_id": row["account_id"],
            "created_at": row["created_at"],
        }
    )
