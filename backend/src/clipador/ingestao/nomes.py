"""Horário de início de uma gravação, a partir do nome do arquivo.

O OBS nomeia as gravações com a data e a hora em que começaram, no horário do PC:
``2026-10-14 20-00-00.mkv``. Esse horário é a base do alinhamento com os eventos do LoL
(etapa 7), então o nome é a fonte preferida. Se o nome não seguir o padrão (o usuário
renomeou o arquivo, ou mudou o formato no OBS), o app usa a data de criação do arquivo e
avisa no log.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Padrão do OBS: "ano-mês-dia hora-minuto-segundo", em qualquer lugar do nome.
PADRAO_OBS = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")


def inicio_pelo_nome(arquivo: Path) -> datetime | None:
    """Horário de início lido do nome (em UTC), ou ``None`` se o nome fugir do padrão."""
    achado = PADRAO_OBS.search(arquivo.stem)
    if achado is None:
        return None
    try:
        ano, mes, dia, hora, minuto, segundo = (int(parte) for parte in achado.groups())
        local = datetime(ano, mes, dia, hora, minuto, segundo)
    except ValueError:  # ex.: "2026-13-40 99-99-99" combina com o padrão, mas não é data
        return None
    # O nome está no horário do PC: astimezone() sem argumento aplica o fuso do sistema.
    return local.astimezone().astimezone(UTC)


def horario_de_inicio(arquivo: Path) -> datetime:
    """Horário de início da gravação (UTC): pelo nome ou, se não der, pela criação do arquivo."""
    pelo_nome = inicio_pelo_nome(arquivo)
    if pelo_nome is not None:
        return pelo_nome
    # No Windows, st_ctime é a data de criação do arquivo.
    criado = datetime.fromtimestamp(arquivo.stat().st_ctime, UTC)
    log.warning(
        "O nome '%s' não segue o padrão do OBS (ano-mês-dia hora-minuto-segundo). "
        "Usando a data de criação do arquivo como início: %s",
        arquivo.name,
        criado.astimezone().isoformat(timespec="seconds"),
    )
    return criado
