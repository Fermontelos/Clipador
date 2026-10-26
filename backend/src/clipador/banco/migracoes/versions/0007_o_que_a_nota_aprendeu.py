"""O que a nota aprendeu: precisão de antes, decisões e janela do corte, para a etapa 14.

Revisão: 0007
Anterior: 0006
Criada em: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("pesosnota", schema=None) as batch_op:
        batch_op.add_column(sa.Column("precisao_antes", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("decisoes", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("segundos_antes", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("segundos_depois", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pesosnota", schema=None) as batch_op:
        batch_op.drop_column("segundos_depois")
        batch_op.drop_column("segundos_antes")
        batch_op.drop_column("decisoes")
        batch_op.drop_column("precisao_antes")
