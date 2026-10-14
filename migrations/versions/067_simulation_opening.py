"""Immutable simulation opening snapshot and independent opening cash event.

Additive migration, without backfilling existing portfolio rows. Downgrade drops
the new tables and destroys simulation openings; only use on disposable test data
or after explicitly exporting the simulation ledger.
"""

from alembic import op

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE simulation_accounts (
            account_id UUID PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL UNIQUE REFERENCES users(user_id),
            opening_payload JSONB NOT NULL,
            opening_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (opening_payload->>'cost_includes_fees' IN ('true', 'false'))
        )
    """)
    op.execute("""
        CREATE TABLE simulation_cash_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            event_type TEXT NOT NULL CHECK (event_type = 'opening'),
            amount NUMERIC(24, 2) NOT NULL CHECK (amount >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (account_id, event_type)
        )
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE simulation_cash_events")
    op.execute("DROP TABLE simulation_accounts")
