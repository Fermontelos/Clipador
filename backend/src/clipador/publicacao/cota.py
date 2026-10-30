"""A cota diária da API do YouTube: quanto já foi usado hoje e se ainda cabe um envio.

Conferido na documentação oficial (em 2026-10-14 e de novo em 2026-10-27):

- os envios de vídeo (``videos.insert``) têm uma cota própria: 100 por dia;
- as outras chamadas dividem 10.000 unidades por dia (ex.: trocar a thumbnail = 50);
- o dia da cota vira à meia-noite do horário do Pacífico (Califórnia), e não no nosso:
  4h (horário de verão de lá) ou 5h em Brasília.

Cada ``Publicacao`` guarda quando o envio terminou (``enviada_em``) e as unidades que
gastou; a cota do dia soma pelo **horário do envio**, e não pelo da aprovação (um envio
que esperou a cota renovar conta no dia em que de fato subiu).

As métricas (etapa 17) também gastam: cada coleta de um vídeo do YouTube é um
``videos.list`` (1 unidade), contado pelo horário da coleta (``Metrica.coletada_em``). O
YouTube Analytics tem cota própria, fora destas 10.000.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select

from clipador.banco.modelos import Metrica, Plataforma, Publicacao

FUSO_DA_COTA = ZoneInfo("America/Los_Angeles")
LIMITE_DE_ENVIOS = 100
LIMITE_DE_UNIDADES = 10_000
CUSTO_DA_THUMBNAIL = 50
CUSTO_DAS_ESTATISTICAS = 1  # videos.list
PREFIXO_SIMULADO = "simulado-"  # o ID que o YouTube simulado dá (publicar.YoutubeSimulado)


@dataclass(frozen=True)
class CotaYoutube:
    envios: int
    limite_envios: int
    unidades: int
    limite_unidades: int
    renova_em: datetime  # UTC

    def cabe(self, unidades: int = 0) -> bool:
        """Cabe mais um envio (e mais estas unidades) hoje?"""
        return self.envios < self.limite_envios and self.cabem_unidades(unidades)

    def cabem_unidades(self, unidades: int) -> bool:
        """Cabem estas unidades hoje (sem envio de vídeo: ex.: uma coleta de métricas)?"""
        return self.unidades + unidades <= self.limite_unidades


def inicio_do_dia_da_cota(agora: datetime) -> datetime:
    """Meia-noite (horário do Pacífico) do dia da cota em que ``agora`` está, em UTC."""
    local = agora.astimezone(FUSO_DA_COTA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(agora.tzinfo)


def cota_youtube(sessao: Session, agora: datetime) -> CotaYoutube:
    inicio = inicio_do_dia_da_cota(agora)
    do_youtube_de_verdade = (
        Publicacao.plataforma == Plataforma.YOUTUBE,
        # O modo simulado (a demo, os testes do fluxo) não gasta cota do Google.
        ~func.coalesce(col(Publicacao.id_na_plataforma), "").startswith(PREFIXO_SIMULADO),
    )
    do_dia = (*do_youtube_de_verdade, col(Publicacao.enviada_em) >= inicio)
    envios = sessao.exec(select(func.count()).select_from(Publicacao).where(*do_dia)).one()
    unidades = sessao.exec(
        select(func.coalesce(func.sum(Publicacao.unidades_cota), 0)).where(*do_dia)
    ).one()
    coletas = sessao.exec(
        select(func.count())
        .select_from(Metrica)
        .join(Publicacao, col(Publicacao.id) == col(Metrica.publicacao_id))
        .where(*do_youtube_de_verdade, col(Metrica.coletada_em) >= inicio)
    ).one()
    # Amanhã à meia-noite no Pacífico (somar 1 dia no horário local acerta o horário de verão).
    amanha = (inicio.astimezone(FUSO_DA_COTA) + timedelta(days=1)).replace(hour=0)
    return CotaYoutube(
        envios=envios,
        limite_envios=LIMITE_DE_ENVIOS,
        unidades=int(unidades) + coletas * CUSTO_DAS_ESTATISTICAS,
        limite_unidades=LIMITE_DE_UNIDADES,
        renova_em=amanha.astimezone(agora.tzinfo),
    )
