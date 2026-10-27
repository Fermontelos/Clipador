"""A cota diária da API do YouTube: quanto já foi usado hoje e se ainda cabe um envio.

Conferido na documentação oficial (em 2026-10-14 e de novo em 2026-10-27):

- os envios de vídeo (``videos.insert``) têm uma cota própria: 100 por dia;
- as outras chamadas dividem 10.000 unidades por dia (ex.: trocar a thumbnail = 50);
- o dia da cota vira à meia-noite do horário do Pacífico (Califórnia), e não no nosso:
  4h (horário de verão de lá) ou 5h em Brasília.

Cada ``Publicacao`` guarda quando o envio terminou (``enviada_em``) e as unidades que
gastou; a cota do dia soma pelo **horário do envio**, e não pelo da aprovação (um envio
que esperou a cota renovar conta no dia em que de fato subiu).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select

from clipador.banco.modelos import Plataforma, Publicacao

FUSO_DA_COTA = ZoneInfo("America/Los_Angeles")
LIMITE_DE_ENVIOS = 100
LIMITE_DE_UNIDADES = 10_000
CUSTO_DA_THUMBNAIL = 50


@dataclass(frozen=True)
class CotaYoutube:
    envios: int
    limite_envios: int
    unidades: int
    limite_unidades: int
    renova_em: datetime  # UTC

    def cabe(self, unidades: int = 0) -> bool:
        """Cabe mais um envio (e mais estas unidades) hoje?"""
        return self.envios < self.limite_envios and self.unidades + unidades <= self.limite_unidades


def inicio_do_dia_da_cota(agora: datetime) -> datetime:
    """Meia-noite (horário do Pacífico) do dia da cota em que ``agora`` está, em UTC."""
    local = agora.astimezone(FUSO_DA_COTA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(agora.tzinfo)


def cota_youtube(sessao: Session, agora: datetime) -> CotaYoutube:
    inicio = inicio_do_dia_da_cota(agora)
    do_dia = (
        Publicacao.plataforma == Plataforma.YOUTUBE,
        col(Publicacao.enviada_em) >= inicio,
    )
    envios = sessao.exec(select(func.count()).select_from(Publicacao).where(*do_dia)).one()
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
