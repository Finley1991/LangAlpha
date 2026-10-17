"""Freeze simulation checking reports without changing financial history.

Downgrade destroys report history; only use on disposable databases or after
exporting reports. It does not rewind account state or order history.
"""

from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE simulation_reports (
            sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            report_id UUID NOT NULL UNIQUE,
            account_id UUID NOT NULL REFERENCES simulation_accounts(account_id),
            client_request_id UUID NOT NULL,
            requested_version BIGINT NOT NULL CHECK (requested_version > 0),
            payload JSONB NOT NULL,
            content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (account_id, client_request_id)
        )
    """)
    op.execute("CREATE INDEX ON simulation_reports (account_id, sequence DESC)")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE simulation_reports")
