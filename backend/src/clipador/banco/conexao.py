"""Abertura do banco SQLite.

Cada conexão nova recebe três ajustes (PRAGMAs do SQLite):

- ``journal_mode=WAL``: leitores e um escritor trabalham ao mesmo tempo sem se travar.
  É o que deixa o serviço da madrugada e a interface web usarem o banco juntos.
- ``foreign_keys=ON``: o SQLite só respeita as chaves estrangeiras se pedirmos.
- ``busy_timeout=5000``: se o banco estiver ocupado, espera até 5 s antes de dar erro.

As tabelas são criadas e atualizadas pelas migrações do Alembic (``migrar``), nunca
por ``create_all``. Assim o banco de quem já usa o app evolui junto com o código.
Durante a migração as chaves estrangeiras ficam desligadas (ver
``sem_chaves_estrangeiras``), e um banco que já existia ganha um backup antes de ser
migrado (ver ``abrir_banco``).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config as ConfigAlembic
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, event
from sqlmodel import Session, create_engine

from clipador.banco.backup import fazer_backup

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


class ChaveEstrangeiraQuebradaError(RuntimeError):
    """Uma migração deixou registros apontando para outros que não existem."""


@contextmanager
def sem_chaves_estrangeiras(engine: Engine) -> Iterator[Connection]:
    """Conexão com as chaves estrangeiras desligadas, para migrações.

    Para alterar uma tabela, o SQLite (modo "batch" do Alembic) cria uma cópia nova, apaga
    a antiga e renomeia. Com as chaves estrangeiras ligadas, apagar uma tabela que outras
    referenciam (ex.: ``momento``, referenciada por ``clipe``) falha. Este é o procedimento
    recomendado pelo SQLite: desligar, alterar, conferir com ``foreign_key_check`` e religar.
    """
    with engine.connect() as conexao:
        # Tem de rodar fora de transação para valer; o driver do SQLite não abre
        # transação antes de um PRAGMA, então esta é a primeira instrução.
        conexao.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            yield conexao
            quebradas = conexao.exec_driver_sql("PRAGMA foreign_key_check").all()
            if quebradas:
                conexao.rollback()
                raise ChaveEstrangeiraQuebradaError(
                    f"A migração deixou {len(quebradas)} registro(s) apontando para "
                    f"outros que não existem. Primeiro: {quebradas[0]}"
                )
            conexao.commit()
        finally:
            # A conexão volta para o pool: tem de voltar com as chaves ligadas.
            conexao.exec_driver_sql("PRAGMA foreign_keys=ON")


def migrar(engine: Engine) -> None:
    """Leva o banco para a versão mais nova das tabelas (``alembic upgrade head``)."""
    config = _config_alembic()
    with sem_chaves_estrangeiras(engine) as conexao:
        config.attributes["connection"] = conexao
        command.upgrade(config, "head")
    log.debug("Banco migrado para a versão mais recente.")


def _config_alembic() -> ConfigAlembic:
    config = ConfigAlembic()
    config.set_main_option("script_location", str(PASTA_MIGRACOES))
    return config


def ha_migracao_pendente(engine: Engine) -> bool:
    """O banco está numa versão mais velha que a do código?"""
    versao_mais_nova = ScriptDirectory.from_config(_config_alembic()).get_current_head()
    with engine.connect() as conexao:
        versao_do_banco = MigrationContext.configure(conexao).get_current_revision()
    return versao_do_banco != versao_mais_nova


def abrir_banco(caminho_banco: Path, pasta_backups: Path | None = None) -> Engine:
    """Abre o banco e garante que as tabelas estão atualizadas.

    Se o banco já existia e vai receber uma migração, faz antes um backup em
    ``pasta_backups`` (quando informada). O driver do SQLite grava algumas alterações de
    tabela na hora, fora da transação, então uma migração que falhe no meio nem sempre
    é desfeita por inteiro; o backup garante que nada se perde.
    """
    ja_existia = caminho_banco.is_file()
    engine = criar_engine(caminho_banco)
    if ja_existia and pasta_backups is not None and ha_migracao_pendente(engine):
        copia = fazer_backup(caminho_banco, pasta_backups)
        log.warning("Banco vai ser atualizado; backup feito antes em %s", copia)
    migrar(engine)
    return engine


def nova_sessao(engine: Engine) -> Session:
    """Sessão para ler e gravar no banco. Use com ``with``."""
    # expire_on_commit=False: os objetos continuam legíveis depois do commit.
    return Session(engine, expire_on_commit=False)
