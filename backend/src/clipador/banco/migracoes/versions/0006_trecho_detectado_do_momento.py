"""Trecho detectado do momento (guardado no primeiro ajuste feito na revisão), para a etapa 12.

Revisão: 0006
Anterior: 0005
Criada em: 2026-10-24 15:44:11.507410
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("momento", schema=None) as batch_op:
        batch_op.add_column(sa.Column("inicio_detectado_s", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fim_detectado_s", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("momento", schema=None) as batch_op:
        batch_op.drop_column("fim_detectado_s")
        batch_op.drop_column("inicio_detectado_s")
