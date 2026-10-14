"""One account row lock serializes reservations and financial writes across workers."""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.server.database.pool import get_db_connection


class LedgerTransaction:
    def __init__(self, cursor, account: dict, now):
        self.cursor = cursor
        self.account = account
        self.now = now
        self.account_id = account["account_id"]

    async def pending_orders(self):
        await self.cursor.execute(
            "SELECT * FROM simulation_orders WHERE account_id=%s AND status='pending'",
            (self.account_id,),
        )
        return await self.cursor.fetchall()

    async def suggestions(self):
        await self.cursor.execute(
            """SELECT * FROM simulation_suggestions WHERE account_id=%s
               ORDER BY created_at DESC, suggestion_id DESC LIMIT 100""",
            (self.account_id,),
        )
        return await self.cursor.fetchall()

    async def pending_suggestion_count(self) -> int:
        await self.cursor.execute(
            "SELECT COUNT(*) AS n FROM simulation_suggestions WHERE account_id=%s AND status='pending'",
            (self.account_id,),
        )
        return (await self.cursor.fetchone())["n"]

    async def orders(self):
        await self.cursor.execute(
            """SELECT * FROM simulation_orders WHERE account_id=%s
               ORDER BY confirmed_at DESC, order_id DESC LIMIT 100""",
            (self.account_id,),
        )
        return await self.cursor.fetchall()

    async def fills(self):
        await self.cursor.execute(
            """SELECT * FROM simulation_fills WHERE account_id=%s
               ORDER BY created_at DESC, fill_id DESC LIMIT 100""",
            (self.account_id,),
        )
        return await self.cursor.fetchall()

    async def suggestion(self, suggestion_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_suggestions WHERE account_id=%s AND suggestion_id=%s",
            (self.account_id, suggestion_id),
        )
        return await self.cursor.fetchone()

    async def by_client_request(self, request_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_suggestions WHERE account_id=%s AND client_request_id=%s",
            (self.account_id, request_id),
        )
        return await self.cursor.fetchone()

    async def order(self, order_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_orders WHERE account_id=%s AND order_id=%s",
            (self.account_id, order_id),
        )
        return await self.cursor.fetchone()

    async def order_for_suggestion(self, suggestion_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_orders WHERE account_id=%s AND suggestion_id=%s",
            (self.account_id, suggestion_id),
        )
        return await self.cursor.fetchone()

    async def bump(self, state: dict | None = None):
        if state is None:
            await self.cursor.execute(
                """UPDATE simulation_accounts SET ledger_version=ledger_version+1
                   WHERE account_id=%s RETURNING *""",
                (self.account_id,),
            )
        else:
            await self.cursor.execute(
                """UPDATE simulation_accounts SET ledger_version=ledger_version+1, ledger_state=%s
                   WHERE account_id=%s RETURNING *""",
                (Jsonb(state), self.account_id),
            )
        self.account = await self.cursor.fetchone()

    async def event(self, kind: str, payload: dict, cash_delta=Decimal("0")):
        await self.cursor.execute(
            """INSERT INTO simulation_events
               (account_id,event_type,payload,cash_delta,created_at) VALUES (%s,%s,%s,%s,%s)""",
            (self.account_id, kind, Jsonb(payload), cash_delta, self.now),
        )

    async def expire(self):
        await self.cursor.execute(
            """UPDATE simulation_suggestions SET status='expired'
               WHERE account_id=%s AND status='pending' AND expires_at<=%s RETURNING suggestion_id""",
            (self.account_id, self.now),
        )
        for row in await self.cursor.fetchall():
            await self.event(
                "suggestion_expired", {"suggestion_id": str(row["suggestion_id"])}
            )
        await self.cursor.execute(
            """UPDATE simulation_orders SET status='expired'
               WHERE account_id=%s AND status='pending' AND expires_at<=%s RETURNING order_id""",
            (self.account_id, self.now),
        )
        expired = await self.cursor.fetchall()
        if expired:
            await self.bump()
        for row in expired:
            await self.event(
                "order_expired",
                {
                    "order_id": str(row["order_id"]),
                    "account_version": self.account["ledger_version"],
                },
            )

    async def insert_suggestion(self, payload: dict):
        await self.cursor.execute(
            """INSERT INTO simulation_suggestions
               (suggestion_id,account_id,client_request_id,payload,status,expires_at,created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s) RETURNING *""",
            (
                uuid4(),
                self.account_id,
                payload["client_request_id"],
                Jsonb(payload),
                payload["expires_at"],
                self.now,
            ),
        )
        return await self.cursor.fetchone()

    async def insert_order(self, suggestion: dict, reserved_cash: Decimal):
        payload = {
            **suggestion["payload"],
            "suggestion_id": str(suggestion["suggestion_id"]),
            "confirmed_by": self.account["user_id"],
            "confirmed_account_version": self.account["ledger_version"],
        }
        await self.cursor.execute(
            """INSERT INTO simulation_orders
               (order_id,account_id,suggestion_id,payload,status,reserved_cash,expires_at,confirmed_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s,%s) RETURNING *""",
            (
                uuid4(),
                self.account_id,
                suggestion["suggestion_id"],
                Jsonb(payload),
                reserved_cash,
                suggestion["expires_at"],
                self.now,
            ),
        )
        return await self.cursor.fetchone()

    async def suggestion_status(self, suggestion_id: UUID, status: str):
        await self.cursor.execute(
            "UPDATE simulation_suggestions SET status=%s WHERE account_id=%s AND suggestion_id=%s",
            (status, self.account_id, suggestion_id),
        )

    async def order_status(self, order_id: UUID, status: str):
        await self.cursor.execute(
            "UPDATE simulation_orders SET status=%s WHERE account_id=%s AND order_id=%s",
            (status, self.account_id, order_id),
        )

    async def fill(self, order_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_fills WHERE account_id=%s AND order_id=%s",
            (self.account_id, order_id),
        )
        return await self.cursor.fetchone()

    async def fill_by_id(self, fill_id: UUID):
        await self.cursor.execute(
            "SELECT * FROM simulation_fills WHERE account_id=%s AND fill_id=%s",
            (self.account_id, fill_id),
        )
        return await self.cursor.fetchone()

    async def insert_fill(self, order_id: UUID, payload: dict):
        await self.cursor.execute(
            """INSERT INTO simulation_fills (fill_id,account_id,order_id,payload,created_at)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (uuid4(), self.account_id, order_id, Jsonb(payload), self.now),
        )
        return await self.cursor.fetchone()

    async def events(self, before: int | None, limit: int):
        await self.cursor.execute(
            """SELECT * FROM simulation_events WHERE account_id=%s
               AND (%s::bigint IS NULL OR event_id<%s) ORDER BY event_id DESC LIMIT %s""",
            (self.account_id, before, before, limit + 1),
        )
        return await self.cursor.fetchall()


@asynccontextmanager
async def transaction(user_id: str):
    async with (
        get_db_connection() as conn,
        conn.transaction(),
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        await cursor.execute(
            "SELECT * FROM simulation_accounts WHERE user_id=%s FOR UPDATE",
            (user_id,),
        )
        account = await cursor.fetchone()
        await cursor.execute("SELECT clock_timestamp() AS now")
        now = (await cursor.fetchone())["now"]
        yield LedgerTransaction(cursor, account, now) if account else None
