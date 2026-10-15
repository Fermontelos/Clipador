"""Backup do banco.

O banco é o "caderno" do app: aprovações, métricas, hashtags, títulos e pesos
aprendidos. É pequeno, mas não dá para recriar. Por isso o app guarda uma cópia por
dia e mantém as 14 mais recentes.

O nome de cada cópia leva a data e a hora **do PC** (horário de Brasília, no seu caso),
para bater com o relógio que você vê. Ex.: ``clipador-2026-10-15_180121.db``.

A cópia usa a API de backup do próprio SQLite (``sqlite3.Connection.backup``), que é
segura mesmo com o banco em uso: copiar o arquivo direto, com o Windows Explorer, pode
pegar o banco no meio de uma gravação e gerar uma cópia corrompida.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from clipador.banco.modelos import agora

log = logging.getLogger(__name__)

MANTER = 14
PREFIXO = "clipador-"
FORMATO_DATA = "%Y-%m-%d_%H%M%S"


def fazer_backup(
    caminho_banco: Path,
    pasta_backups: Path,
    manter: int = MANTER,
    momento: datetime | None = None,
) -> Path:
    """Copia o banco para ``pasta_backups`` e apaga as cópias além das ``manter`` mais novas.

    Devolve o caminho da cópia criada.
    """
    if manter < 1:
        raise ValueError("É preciso manter pelo menos 1 backup.")
    if not caminho_banco.is_file():
        raise FileNotFoundError(f"Banco não encontrado para backup: {caminho_banco}")
    momento = momento or agora()
    pasta_backups.mkdir(parents=True, exist_ok=True)
    destino = pasta_backups / nome_do_backup(momento)

    origem = sqlite3.connect(caminho_banco)
    copia = sqlite3.connect(destino)
    try:
        origem.backup(copia)
    finally:
        copia.close()
        origem.close()
    log.info("Backup do banco criado em %s", destino)

    apagar_antigos(pasta_backups, manter)
    return destino


def nome_do_backup(momento: datetime) -> str:
    """Nome do arquivo de backup, com a data e a hora no fuso do PC."""
    return f"{PREFIXO}{momento.astimezone():{FORMATO_DATA}}.db"


def listar_backups(pasta_backups: Path) -> list[Path]:
    """Backups existentes, do mais antigo para o mais novo."""
    if not pasta_backups.is_dir():
        return []
    # O nome tem a data no formato ano-mês-dia_horaminutosegundo, então a ordem
    # alfabética é a ordem cronológica.
    return sorted(pasta_backups.glob(f"{PREFIXO}*.db"))


def apagar_antigos(pasta_backups: Path, manter: int = MANTER) -> list[Path]:
    """Apaga os backups mais velhos, deixando só os ``manter`` mais novos."""
    if manter < 1:
        raise ValueError("É preciso manter pelo menos 1 backup.")
    backups = listar_backups(pasta_backups)
    excedentes = backups[:-manter]
    for antigo in excedentes:
        antigo.unlink()
        log.info("Backup antigo apagado: %s", antigo.name)
    return excedentes


def backup_do_dia(
    caminho_banco: Path,
    pasta_backups: Path,
    manter: int = MANTER,
    momento: datetime | None = None,
) -> Path | None:
    """Faz o backup só se ainda não houver um de hoje (data no fuso do PC).

    É o que o serviço da etapa 4 chama uma vez por dia. Devolve a cópia nova ou
    ``None`` se a de hoje já existia.
    """
    momento = momento or agora()
    hoje = f"{PREFIXO}{momento.astimezone():%Y-%m-%d}_"
    if any(backup.name.startswith(hoje) for backup in listar_backups(pasta_backups)):
        return None
    return fazer_backup(caminho_banco, pasta_backups, manter, momento)
