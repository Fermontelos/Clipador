"""Quanto da cota diária da API do YouTube já foi usado hoje (para o painel da revisão).

Conferido na documentação oficial em 2026-10-14:

- os envios de vídeo têm uma cota própria: 100 por dia;
- as outras chamadas dividem 10.000 unidades por dia (ex.: trocar a thumbnail = 50);
- o dia da cota vira à meia-noite do horário do Pacífico (Califórnia), e não no nosso.

A publicação (etapa 15) grava em cada ``Publicacao`` as unidades que gastou; aqui só se soma.

Limitação até a etapa 15: o dia de cada publicação é o da criação (``criada_em``). Se ela
for criada num dia e enviada em outro (ex.: esperou a cota renovar), conta no dia errado. A
etapa 15 precisa gravar o horário do envio e somar por ele.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select

from clipador.banco.modelos import Plataforma, Publicacao, StatusPublicacao

FUSO_DA_COTA = ZoneInfo("America/Los_Angeles")
LIMITE_DE_ENVIOS = 100
LIMITE_DE_UNIDADES = 10_000
# Publicações que chegaram a subir o vídeo (as pendentes e as com erro não gastaram envio).
SUBIRAM = (StatusPublicacao.ENVIADA, StatusPublicacao.AGENDADA, StatusPublicacao.PUBLICADA)


@dataclass(frozen=True)
class CotaYoutube:
    envios: int
    limite_envios: int
    unidades: int
    limite_unidades: int
    renova_em: datetime  # UTC


def inicio_do_dia_da_cota(agora: datetime) -> datetime:
    """Meia-noite (horário do Pacífico) do dia da cota em que ``agora`` está, em UTC."""
    local = agora.astimezone(FUSO_DA_COTA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(agora.tzinfo)


def cota_youtube(sessao: Session, agora: datetime) -> CotaYoutube:
    inicio = inicio_do_dia_da_cota(agora)
    do_dia = (
        Publicacao.plataforma == Plataforma.YOUTUBE,
        col(Publicacao.criada_em) >= inicio,
    )
    envios = sessao.exec(
        select(func.count())
        .select_from(Publicacao)
        .where(*do_dia, col(Publicacao.status).in_(SUBIRAM))
    ).one()
    unidades = sessao.exec(
        select(func.coalesce(func.sum(Publicacao.unidades_cota), 0)).where(*do_dia)
    ).one()
    # Amanhã à meia-noite no Pacífico (somar 1 dia no horário local acerta o horário de verão).
    amanha = (inicio.astimezone(FUSO_DA_COTA) + timedelta(days=1)).replace(hour=0)
    return CotaYoutube(
        envios=envios,
        limite_envios=LIMITE_DE_ENVIOS,
        unidades=int(unidades),
        limite_unidades=LIMITE_DE_UNIDADES,
        renova_em=amanha.astimezone(agora.tzinfo),
    )
