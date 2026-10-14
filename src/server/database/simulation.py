"""One committed opening event per user; retries never add another balance."""

from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.server.database.pool import get_db_connection
from src.server.database.user_lock import lock_user_profile


class OpeningConflict(ValueError):
    pass


async def get_opening(user_id: str) -> dict | None:
    async with get_db_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM simulation_accounts WHERE user_id = %s", (user_id,)
        )
        return await cur.fetchone()


async def initialize(user_id: str, payload: dict, digest: str) -> tuple[dict, bool]:
    async with (
        get_db_connection() as conn,
        conn.transaction(),
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await lock_user_profile(cur, user_id)
        await cur.execute(
            "SELECT * FROM simulation_accounts WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        existing = await cur.fetchone()
        if existing:
            if existing["opening_hash"] != digest:
                raise OpeningConflict(
                    "已有期初账本；不同内容须通过后续更正流程处理，不能覆盖或累计仓位。"
                )
            return existing, False
        account_id = uuid4()
        await cur.execute(
            """INSERT INTO simulation_accounts (account_id, user_id, opening_payload, opening_hash)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (account_id, user_id, Jsonb(payload), digest),
        )
        account = await cur.fetchone()
        await cur.execute(
            """INSERT INTO simulation_cash_events (account_id, event_type, amount)
               VALUES (%s, 'opening', %s)""",
            (account_id, payload["initial_cash"]),
        )
        return account, True
