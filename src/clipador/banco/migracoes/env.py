"""Ambiente do Alembic: diz como conectar no banco e quais tabelas comparar.

Dois jeitos de rodar:
- pelo app (``clipador.banco.conexao.migrar``), que passa a conexão já aberta;
- pela linha de comando, para criar migrações novas durante o desenvolvimento:
  ``uv run alembic revision --autogenerate -m "descricao"`` (usa o alembic.ini).
"""

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import clipador.banco.modelos  # noqa: F401  (registra as tabelas no SQLModel.metadata)

config = context.config
metadados = SQLModel.metadata


def _configurar_e_rodar(conexao) -> None:
    context.configure(
        connection=conexao,
        target_metadata=metadados,
        # O SQLite não consegue alterar colunas com ALTER TABLE. O modo "batch" faz o
        # Alembic recriar a tabela por baixo dos panos quando for preciso.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


conexao_pronta = config.attributes.get("connection")
if conexao_pronta is not None:
    _configurar_e_rodar(conexao_pronta)
else:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as conexao:
        _configurar_e_rodar(conexao)
        conexao.commit()
