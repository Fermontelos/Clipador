"""Banco: migrações, modo WAL, chaves estrangeiras e datas."""

from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, select

from clipador.banco.conexao import abrir_banco, migrar, nova_sessao
from clipador.banco.modelos import Gravacao, StatusTarefa, Tarefa, TipoTarefa

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
