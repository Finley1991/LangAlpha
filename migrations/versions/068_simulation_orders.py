"""Add forward simulation ledger; immutable openings remain untouched.

Downgrade destroys order/audit history and current ledger states. Only use on
disposable databases or after exporting this history; never rewind live ledgers.
"""

from alembic import op

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        ALTER TABLE simulation_accounts
        ADD COLUMN ledger_version BIGINT NOT NULL DEFAULT 1 CHECK (ledger_version > 0),
        ADD COLUMN ledger_state JSONB
    """)
    op.execute("""
        CREATE TABLE simulation_suggestions (
            suggestion_id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            client_request_id UUID NOT NULL,
            payload JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending','confirmed','cancelled','expired')),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (account_id, suggestion_id),
            UNIQUE (account_id, client_request_id)
        )
    """)
    op.execute("""
        CREATE TABLE simulation_orders (
            order_id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            suggestion_id UUID NOT NULL UNIQUE,
            payload JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending','filled','cancelled','expired')),
            reserved_cash NUMERIC(24,2) NOT NULL CHECK (reserved_cash >= 0),
            expires_at TIMESTAMPTZ NOT NULL,
            confirmed_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (account_id, suggestion_id)
                REFERENCES simulation_suggestions(account_id, suggestion_id),
            UNIQUE (account_id, order_id)
        )
    """)
    op.execute("""
        CREATE TABLE simulation_fills (
            fill_id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            order_id UUID NOT NULL UNIQUE,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (account_id, order_id)
                REFERENCES simulation_orders(account_id, order_id)
        )
    """)
    op.execute("""
        CREATE TABLE simulation_events (
            event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            event_type TEXT NOT NULL CHECK (event_type IN
                ('suggested','confirmed','suggestion_cancelled','suggestion_expired',
                 'order_cancelled','order_expired','filled')),
            payload JSONB NOT NULL,
            cash_delta NUMERIC(24,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)
    for table in ("suggestions", "orders", "fills", "events"):
        op.execute(
            f"CREATE INDEX ON simulation_{table} (account_id, created_at DESC)"
            if table != "orders"
            else "CREATE INDEX ON simulation_orders (account_id, confirmed_at DESC)"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("events", "fills", "orders", "suggestions"):
        op.execute(f"DROP TABLE simulation_{table}")
    op.execute(
        "ALTER TABLE simulation_accounts DROP COLUMN ledger_state, DROP COLUMN ledger_version"
    )
