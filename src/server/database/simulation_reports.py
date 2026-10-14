"""Report writes use the caller's already locked account transaction."""

from uuid import UUID

from psycopg.types.json import Jsonb

from src.server.database.simulation_orders import LedgerTransaction


async def by_request(tx: LedgerTransaction, request_id: UUID):
    await tx.cursor.execute(
        "SELECT * FROM simulation_reports WHERE account_id=%s AND client_request_id=%s",
        (tx.account_id, request_id),
    )
    return await tx.cursor.fetchone()


async def by_id(tx: LedgerTransaction, report_id: UUID):
    await tx.cursor.execute(
        "SELECT * FROM simulation_reports WHERE account_id=%s AND report_id=%s",
        (tx.account_id, report_id),
    )
    return await tx.cursor.fetchone()


async def insert(tx: LedgerTransaction, request, payload: dict, content_hash: str):
    await tx.cursor.execute(
        """INSERT INTO simulation_reports
           (report_id,account_id,client_request_id,requested_version,payload,content_hash,created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            payload["report_id"],
            tx.account_id,
            request.client_request_id,
            request.account_version,
            Jsonb(payload),
            content_hash,
            tx.now,
        ),
    )
    return await tx.cursor.fetchone()


async def page(tx: LedgerTransaction, before: int | None, limit: int):
    await tx.cursor.execute(
        """SELECT * FROM simulation_reports WHERE account_id=%s
           AND (%s::bigint IS NULL OR sequence<%s)
           ORDER BY sequence DESC LIMIT %s""",
        (tx.account_id, before, before, limit + 1),
    )
    return await tx.cursor.fetchall()
