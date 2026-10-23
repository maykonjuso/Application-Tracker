"""track LinkedIn discovery runs separately from public boards

Revision ID: b286a01e5ac1
Revises: 7434e1400df1
Create Date: 2026-10-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b286a01e5ac1"
down_revision: str | None = "7434e1400df1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("saved_search", sa.Column("last_linkedin_run_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("saved_search", "last_linkedin_run_at")
