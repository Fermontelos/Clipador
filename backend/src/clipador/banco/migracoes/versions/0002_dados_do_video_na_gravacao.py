"""Dados do vídeo (largura, altura e fps) na gravação, lidos pela etapa 5.

Revisão: 0002
Anterior: 0001
Criada em: 2026-10-16 18:11:56.187467
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("gravacao", schema=None) as batch_op:
        batch_op.add_column(sa.Column("largura", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("altura", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("fps", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("gravacao", schema=None) as batch_op:
        batch_op.drop_column("fps")
        batch_op.drop_column("altura")
        batch_op.drop_column("largura")
