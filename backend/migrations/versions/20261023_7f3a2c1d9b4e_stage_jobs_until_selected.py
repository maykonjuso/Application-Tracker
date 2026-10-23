"""stage collected jobs until selected by the user

Revision ID: 7f3a2c1d9b4e
Revises: b286a01e5ac1
Create Date: 2026-10-23 12:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7f3a2c1d9b4e"
down_revision: str | None = "b286a01e5ac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE job SET triage = 'discovered' WHERE triage = 'new'")


def downgrade() -> None:
    op.execute("UPDATE job SET triage = 'new' WHERE triage = 'discovered'")
