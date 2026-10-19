"""Partida, EventID e o seu papel em cada evento do LoL, para a etapa 7.

Revisão: 0004
Anterior: 0003
Criada em: 2026-10-19 18:11:28.462960

Os eventos que já existem (só os da demo, antes da etapa 7) são preenchidos assim: o
início da partida é o horário do evento menos o tempo de jogo, e o EventID vem do JSON
salvo. O papel fica vazio (a demo é refeita com ele por ``clipador demo --do-zero``).
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNICO = "uq_eventojogo_partida_evento"
# Como o SQLModel grava as datas (UTC, sem o fuso no texto).
FORMATO = "%Y-%m-%d %H:%M:%S.%f"


def upgrade() -> None:
    # 1) Colunas novas, ainda opcionais (as linhas que já existem não têm valor).
    with op.batch_alter_table("eventojogo", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("partida_inicio", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True)
        )
        batch_op.add_column(sa.Column("event_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "papel",
                sa.Enum("ABATE", "MORTE", "ASSISTENCIA", name="papelnoevento"),
                nullable=True,
            )
        )

    # 2) Preenche as linhas que já existem.
    conexao = op.get_bind()
    linhas = conexao.execute(
        sa.text("SELECT id, horario_real, tempo_jogo_s, dados FROM eventojogo")
    ).all()
    for id_, horario_real, tempo_jogo_s, dados in linhas:
        horario = datetime.strptime(str(horario_real)[:26].ljust(26, "0"), FORMATO)
        inicio = horario - timedelta(seconds=tempo_jogo_s or 0)
        event_id = (json.loads(dados) if dados else {}).get("EventID", id_)
        conexao.execute(
            sa.text(
                "UPDATE eventojogo SET partida_inicio = :inicio, event_id = :ev WHERE id = :id"
            ),
            {"inicio": inicio.strftime(FORMATO), "ev": int(event_id), "id": id_},
        )

    # 3) Agora sim, obrigatórias, com o índice e a regra de não duplicar.
    with op.batch_alter_table("eventojogo", schema=None) as batch_op:
        batch_op.alter_column(
            "partida_inicio", existing_type=sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False
        )
        batch_op.alter_column("event_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_eventojogo_partida_inicio"), ["partida_inicio"], unique=False
        )
        batch_op.create_unique_constraint(UNICO, ["partida_inicio", "event_id"])


def downgrade() -> None:
    with op.batch_alter_table("eventojogo", schema=None) as batch_op:
        batch_op.drop_constraint(UNICO, type_="unique")
        batch_op.drop_index(batch_op.f("ix_eventojogo_partida_inicio"))
        batch_op.drop_column("papel")
        batch_op.drop_column("event_id")
        batch_op.drop_column("partida_inicio")
