"""Merge job-staging and robot-removal migration branches.

Revision ID: d9e3a7b4c61f
Revises: 7f3a2c1d9b4e, b2f1c0d7a913
Create Date: 2026-10-24
"""

from collections.abc import Sequence

revision: str = "d9e3a7b4c61f"
down_revision: tuple[str, str] = ("7f3a2c1d9b4e", "b2f1c0d7a913")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
