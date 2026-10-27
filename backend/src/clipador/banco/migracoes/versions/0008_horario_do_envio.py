"""Horário do envio de cada publicação (a cota do dia conta por ele), para a etapa 15.

Revisão: 0008
Anterior: 0007
Criada em: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("publicacao", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("enviada_em", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_publicacao_enviada_em"), ["enviada_em"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("publicacao", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_publicacao_enviada_em"))
        batch_op.drop_column("enviada_em")
