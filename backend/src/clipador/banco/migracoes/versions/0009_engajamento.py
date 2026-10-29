"""Engajamento (etapa 17): duração média assistida e a origem de cada versão da nota.

- ``metrica.duracao_media_s``: o ``averageViewDuration`` do YouTube Analytics (segundos);
- ``pesosnota.origem``: ``gosto`` (etapa 14, das suas decisões) ou ``engajamento`` (etapa
  17, misturando o gosto com o engajamento previsto). As versões antigas são do gosto;
- ``pesosnota.pesos_do_gosto`` e ``pesos_do_engajamento``: as duas partes da nota, guardadas
  separadas. ``valores`` é a mistura delas (o que a detecção usa); cada aceite troca só a
  parte dele e mistura de novo, para um não apagar o outro.

Revisão: 0009
Anterior: 0008
Criada em: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("metrica", schema=None) as batch_op:
        batch_op.add_column(sa.Column("duracao_media_s", sa.Float(), nullable=True))
    with op.batch_alter_table("pesosnota", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("origem", sa.String(), nullable=False, server_default="gosto")
        )
        batch_op.add_column(sa.Column("pesos_do_gosto", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("pesos_do_engajamento", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pesosnota", schema=None) as batch_op:
        batch_op.drop_column("pesos_do_engajamento")
        batch_op.drop_column("pesos_do_gosto")
        batch_op.drop_column("origem")
    with op.batch_alter_table("metrica", schema=None) as batch_op:
        batch_op.drop_column("duracao_media_s")
