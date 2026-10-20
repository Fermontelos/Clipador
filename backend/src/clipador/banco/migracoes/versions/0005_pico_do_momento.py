"""Pico do momento (o instante da thumbnail), para a etapa 9.

Revisão: 0005
Anterior: 0004
Criada em: 2026-10-20 18:06:44.718871
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("momento", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pico_s", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("momento", schema=None) as batch_op:
        batch_op.drop_column("pico_s")
