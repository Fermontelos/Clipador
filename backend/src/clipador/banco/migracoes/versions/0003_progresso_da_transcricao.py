"""Progresso da transcrição (segmentos, até onde foi e se terminou), para a etapa 6.

Revisão: 0003
Anterior: 0002
Criada em: 2026-10-19 16:31:06.259263
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transcricao", schema=None) as batch_op:
        batch_op.add_column(sa.Column("segmentos", sa.JSON(), nullable=True))
        # server_default: as linhas que já existem precisam de um valor nas colunas novas.
        batch_op.add_column(
            sa.Column("transcrito_ate_s", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("concluida", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Antes desta etapa, só a demo criava transcrições, e elas já vêm prontas: marca como
    # concluídas, para a etapa 6 não tentar transcrever de novo o áudio sintético da demo.
    op.execute(
        "UPDATE transcricao SET concluida = 1, transcrito_ate_s = COALESCE("
        "(SELECT duracao_s FROM gravacao WHERE gravacao.id = transcricao.gravacao_id), 0)"
    )


def downgrade() -> None:
    with op.batch_alter_table("transcricao", schema=None) as batch_op:
        batch_op.drop_column("concluida")
        batch_op.drop_column("transcrito_ate_s")
        batch_op.drop_column("segmentos")
