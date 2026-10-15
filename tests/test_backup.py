"""Backup do banco: cópia íntegra, limite de 14 e backup do dia."""

import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from clipador.banco.backup import (
    backup_do_dia,
    fazer_backup,
    listar_backups,
    nome_do_backup,
)
from clipador.banco.conexao import criar_engine, nova_sessao
from clipador.banco.modelos import Gravacao, agora


def test_backup_abre_e_tem_os_mesmos_dados(engine, caminho_banco, gravacao, tmp_path):
    copia = fazer_backup(caminho_banco, tmp_path / "backups")

    # A cópia passa na verificação de integridade do próprio SQLite...
    conexao = sqlite3.connect(copia)
    try:
        assert conexao.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conexao.close()

    # ...e tem a gravação que estava no banco.
    engine_copia = criar_engine(copia)
    try:
        with nova_sessao(engine_copia) as sessao:
            assert [g.arquivo for g in sessao.exec(select(Gravacao))] == [gravacao.arquivo]
    finally:
        engine_copia.dispose()


def test_backup_funciona_com_o_banco_aberto(engine, caminho_banco, sessao, gravacao, tmp_path):
    # A sessão segue aberta enquanto o backup acontece (como o serviço rodando).
    sessao.add(Gravacao(arquivo="outra.mkv", inicio=agora()))
    sessao.commit()
    copia = fazer_backup(caminho_banco, tmp_path / "backups")
    conexao = sqlite3.connect(copia)
    try:
        assert conexao.execute("SELECT COUNT(*) FROM gravacao").fetchone()[0] == 2
    finally:
        conexao.close()


def test_so_as_14_mais_recentes_ficam_guardadas(engine, caminho_banco, tmp_path):
    pasta = tmp_path / "backups"
    inicio = agora()
    for dia in range(20):
        fazer_backup(caminho_banco, pasta, momento=inicio + timedelta(days=dia))

    backups = listar_backups(pasta)
    assert len(backups) == 14
    # Ficaram as mais novas: do dia 6 ao 19.
    assert backups[0].name == nome_do_backup(inicio + timedelta(days=6))


def test_backup_do_dia_so_faz_uma_copia_por_dia(engine, caminho_banco, tmp_path):
    pasta = tmp_path / "backups"
    hoje = agora().replace(hour=10)
    assert backup_do_dia(caminho_banco, pasta, momento=hoje) is not None
    assert backup_do_dia(caminho_banco, pasta, momento=hoje + timedelta(hours=5)) is None
    assert backup_do_dia(caminho_banco, pasta, momento=hoje + timedelta(days=1)) is not None
    assert len(listar_backups(pasta)) == 2


def test_backup_sem_banco_explica_o_problema(tmp_path):
    with pytest.raises(FileNotFoundError, match="Banco não encontrado"):
        fazer_backup(tmp_path / "nao_existe.db", tmp_path / "backups")


def test_manter_zero_backups_e_recusado(engine, caminho_banco, tmp_path):
    with pytest.raises(ValueError):
        fazer_backup(caminho_banco, tmp_path / "backups", manter=0)
    assert listar_backups(tmp_path / "backups") == []


def test_backup_do_dia_usa_a_data_do_relogio_do_pc(engine, caminho_banco, tmp_path):
    # 22h e 23h30 no horário do PC são o mesmo dia, mesmo que em UTC já seja o dia seguinte.
    pasta = tmp_path / "backups"
    noite = datetime.now().astimezone().replace(hour=22, minute=0, second=0, microsecond=0)
    assert backup_do_dia(caminho_banco, pasta, momento=noite) is not None
    assert backup_do_dia(caminho_banco, pasta, momento=noite.replace(hour=23, minute=30)) is None
    assert listar_backups(pasta)[0].name.startswith(f"clipador-{noite:%Y-%m-%d}_22")
