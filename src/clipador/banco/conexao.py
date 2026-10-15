"""Abertura do banco SQLite.

Cada conexão nova recebe três ajustes (PRAGMAs do SQLite):

- ``journal_mode=WAL``: leitores e um escritor trabalham ao mesmo tempo sem se travar.
  É o que deixa o serviço da madrugada e a interface web usarem o banco juntos.
- ``foreign_keys=ON``: o SQLite só respeita as chaves estrangeiras se pedirmos.
- ``busy_timeout=5000``: se o banco estiver ocupado, espera até 5 s antes de dar erro.

As tabelas são criadas e atualizadas pelas migrações do Alembic (``migrar``), nunca
por ``create_all``. Assim o banco de quem já usa o app evolui junto com o código.
"""

import logging
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

log = logging.getLogger(__name__)

PASTA_MIGRACOES = files("clipador.banco") / "migracoes"


def criar_engine(caminho_banco: Path) -> Engine:
    """Cria a conexão com o arquivo do banco (a pasta é criada se faltar)."""
    caminho_banco.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{caminho_banco.as_posix()}")

    @event.listens_for(engine, "connect")
    def _ajustar_conexao(conexao_dbapi, _registro):
        cursor = conexao_dbapi.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def migrar(engine: Engine) -> None:
    """Leva o banco para a versão mais nova das tabelas (``alembic upgrade head``)."""
    config = ConfigAlembic()
    config.set_main_option("script_location", str(PASTA_MIGRACOES))
    with engine.begin() as conexao:
        config.attributes["connection"] = conexao
        command.upgrade(config, "head")
    log.debug("Banco migrado para a versão mais recente.")


def abrir_banco(caminho_banco: Path) -> Engine:
    """Abre o banco e garante que as tabelas estão atualizadas."""
    engine = criar_engine(caminho_banco)
    migrar(engine)
    return engine


def nova_sessao(engine: Engine) -> Session:
    """Sessão para ler e gravar no banco. Use com ``with``."""
    # expire_on_commit=False: os objetos continuam legíveis depois do commit.
    return Session(engine, expire_on_commit=False)
