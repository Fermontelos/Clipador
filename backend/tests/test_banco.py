"""Banco: migrações, modo WAL, chaves estrangeiras e datas."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, select

from clipador.banco import conexao as modulo_conexao
from clipador.banco.conexao import (
    ChaveEstrangeiraQuebradaError,
    abrir_banco,
    ha_migracao_pendente,
    migrar,
    nova_sessao,
    sem_chaves_estrangeiras,
)
from clipador.banco.modelos import (
    Clipe,
    FormatoClipe,
    Gravacao,
    Momento,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
)

TABELAS = {
    "gravacao",
    "tarefa",
    "transcricao",
    "eventojogo",
    "momento",
    "clipe",
    "decisao",
    "publicacao",
    "pesosnota",
    "metrica",
}


def test_migracao_inicial_cria_todas_as_tabelas(engine):
    tabelas = set(inspect(engine).get_table_names())
    assert tabelas >= TABELAS
    assert "alembic_version" in tabelas


def test_migracoes_batem_com_os_modelos(engine):
    # Se alguém mudar modelos.py e esquecer de criar a migração, este teste falha.
    with engine.connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), SQLModel.metadata)
    assert diferencas == []


def test_abrir_de_novo_nao_quebra_nem_apaga_dados(caminho_banco):
    engine = abrir_banco(caminho_banco)
    with nova_sessao(engine) as sessao:
        sessao.add(Gravacao(arquivo="a.mkv", inicio=datetime.now(UTC)))
        sessao.commit()
    engine.dispose()

    engine = abrir_banco(caminho_banco)  # migra de novo: não deve mudar nada
    migrar(engine)
    with nova_sessao(engine) as sessao:
        assert len(sessao.exec(select(Gravacao)).all()) == 1
    engine.dispose()


def test_banco_usa_modo_wal_e_chaves_estrangeiras(engine):
    with engine.connect() as conexao:
        assert conexao.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conexao.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_chave_estrangeira_invalida_e_recusada(sessao):
    sessao.add(Tarefa(tipo=TipoTarefa.TRANSCREVER, gravacao_id=999))
    with pytest.raises(IntegrityError):
        sessao.commit()


def test_arquivo_de_gravacao_nao_pode_repetir(sessao):
    sessao.add(Gravacao(arquivo="a.mkv", inicio=datetime.now(UTC)))
    sessao.commit()
    sessao.add(Gravacao(arquivo="a.mkv", inicio=datetime.now(UTC)))
    with pytest.raises(IntegrityError):
        sessao.commit()


def test_data_sem_fuso_e_recusada(sessao):
    # Evita misturar horário local com UTC sem perceber.
    sessao.add(Gravacao(arquivo="a.mkv", inicio=datetime(2026, 10, 14, 20, 0)))
    with pytest.raises(Exception, match="timezone"):
        sessao.commit()


def test_datas_voltam_em_utc(sessao, gravacao):
    lida = sessao.get(Gravacao, gravacao.id, populate_existing=True)
    assert lida.inicio.tzinfo is not None
    assert lida.inicio.utcoffset().total_seconds() == 0


def test_valores_fixos_voltam_como_enum(sessao, gravacao):
    sessao.add(Tarefa(tipo=TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id))
    sessao.commit()
    tarefa = sessao.exec(select(Tarefa)).one()
    assert tarefa.tipo is TipoTarefa.EXTRAIR_AUDIO
    assert tarefa.status is StatusTarefa.PENDENTE


def test_migracao_futura_pode_alterar_tabela_referenciada(engine, sessao, gravacao):
    # "momento" é referenciada por "clipe". Alterar uma tabela assim no SQLite exige
    # recriá-la (modo batch), o que falharia com as chaves estrangeiras ligadas.
    momento = Momento(gravacao_id=gravacao.id, inicio_s=1, fim_s=20)
    sessao.add(momento)
    sessao.commit()
    sessao.refresh(momento)
    sessao.add(Clipe(momento_id=momento.id, formato=FormatoClipe.HORIZONTAL, arquivo="c.mp4"))
    sessao.commit()

    with sem_chaves_estrangeiras(engine) as conexao:
        operacoes = Operations(MigrationContext.configure(conexao))
        with operacoes.batch_alter_table("momento", recreate="always") as tabela:
            tabela.add_column(sa.Column("coluna_nova", sa.Integer(), nullable=True))

    with engine.connect() as conexao:
        assert conexao.exec_driver_sql("SELECT COUNT(*) FROM clipe").scalar() == 1
        assert conexao.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_migracao_que_deixa_registro_orfao_e_desfeita(engine):
    with (
        pytest.raises(ChaveEstrangeiraQuebradaError),
        sem_chaves_estrangeiras(engine) as conexao,
    ):
        conexao.exec_driver_sql(
            "INSERT INTO clipe (momento_id, formato, arquivo) VALUES (999, 'HORIZONTAL', 'x')"
        )
    with engine.connect() as conexao:
        assert conexao.exec_driver_sql("SELECT COUNT(*) FROM clipe").scalar() == 0
        assert conexao.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_banco_novo_nao_tem_migracao_pendente(engine):
    assert ha_migracao_pendente(engine) is False


def test_banco_sem_versao_tem_migracao_pendente(engine):
    with engine.begin() as conexao:
        conexao.exec_driver_sql("DELETE FROM alembic_version")
    assert ha_migracao_pendente(engine) is True


def test_faz_backup_antes_de_migrar_banco_existente(caminho_banco, tmp_path, monkeypatch):
    abrir_banco(caminho_banco).dispose()  # o banco já existe
    pasta = tmp_path / "backups"
    monkeypatch.setattr(modulo_conexao, "ha_migracao_pendente", lambda engine: True)

    abrir_banco(caminho_banco, pasta).dispose()

    assert len(list(pasta.glob("clipador-*.db"))) == 1


def test_banco_novo_nao_gera_backup(caminho_banco, tmp_path):
    pasta = tmp_path / "backups"
    abrir_banco(caminho_banco, pasta).dispose()
    assert not pasta.exists()


def test_banco_atualizado_nao_gera_backup(caminho_banco, tmp_path):
    pasta = tmp_path / "backups"
    abrir_banco(caminho_banco, pasta).dispose()
    abrir_banco(caminho_banco, pasta).dispose()  # já está na versão mais nova
    assert not pasta.exists()


def test_aberturas_ao_mesmo_tempo_de_um_banco_novo_nao_quebram(tmp_path):
    # Ex.: o serviço abrindo pela 1ª vez enquanto você roda "clipador status".
    import threading

    caminho = tmp_path / "novo" / "clipador.db"
    erros = []
    largada = threading.Barrier(4)

    def abrir():
        try:
            largada.wait()  # as quatro começam juntas
            abrir_banco(caminho).dispose()
        except Exception as erro:
            erros.append(erro)

    threads = [threading.Thread(target=abrir) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert erros == []
    engine = abrir_banco(caminho)
    try:
        assert set(inspect(engine).get_table_names()) >= TABELAS
    finally:
        engine.dispose()


def test_banco_de_uma_versao_mais_nova_do_app_da_erro_claro(caminho_banco):
    from clipador.banco.conexao import BancoMaisNovoQueOCodigoError

    engine = abrir_banco(caminho_banco)
    with engine.begin() as conexao:
        conexao.exec_driver_sql("UPDATE alembic_version SET version_num = '9999'")
    engine.dispose()
    with pytest.raises(BancoMaisNovoQueOCodigoError, match="git pull"):
        abrir_banco(caminho_banco)


def test_migracao_0002_acrescenta_os_dados_do_video_sem_perder_gravacoes(caminho_banco):
    # Um banco criado antes da etapa 5 (versão 0001), com uma gravação.
    engine = modulo_conexao.criar_engine(caminho_banco)
    config = modulo_conexao._config_alembic()
    with modulo_conexao.sem_chaves_estrangeiras(engine) as conexao:
        config.attributes["connection"] = conexao
        command.upgrade(config, "0001")
        conexao.exec_driver_sql(
            "INSERT INTO gravacao (arquivo, inicio, duracao_s, status, criada_em) VALUES "
            "('C:/g/live.mkv', '2026-10-14 23:00:00', 60, 'NOVA', '2026-10-14 23:00:00')"
        )
    engine.dispose()

    engine = abrir_banco(caminho_banco)
    colunas = {coluna["name"] for coluna in inspect(engine).get_columns("gravacao")}
    assert {"largura", "altura", "fps"} <= colunas
    with nova_sessao(engine) as sessao:
        gravacao = sessao.exec(select(Gravacao)).one()
    engine.dispose()
    assert (gravacao.arquivo, gravacao.duracao_s, gravacao.largura) == ("C:/g/live.mkv", 60, None)
