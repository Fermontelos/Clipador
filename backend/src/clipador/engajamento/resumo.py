"""O que a tela de Engajamento mostra (etapa 17): rankings, comparações e a sugestão de pesos.

- **Cortes**: do maior índice para o menor (só os que já têm a coleta de 7 dias);
- **Hashtags** e **tipos de título**: o efeito no índice (ver ``ranking``);
- **Por tipo de momento**: o sinal mais forte do momento (grito, fala, LoL) e o índice médio;
- **YouTube × TikTok**: as medianas de cada rede. O índice não serve para comparar as duas
  (cada uma é comparada com ela mesma), então aqui vão os números crus.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from clipador.aprendizado import engajamento as aprendizado
from clipador.banco.modelos import Metrica, Plataforma
from clipador.config import Config
from clipador.engajamento import indice, ranking

NOMES_DOS_SINAIS = {"volume_voz": "grito (voz alta)", "fala": "fala", "lol": "evento do LoL"}


@dataclass(frozen=True)
class Corte:
    publicacao_id: int
    momento_id: int
    titulo: str | None
    plataforma: str
    formato: str
    link: str | None
    indice: float
    visualizacoes: int | None
    curtidas: int | None
    comentarios: int | None
    compartilhamentos: int | None
    retencao: float | None
    tipo_de_momento: str | None


@dataclass(frozen=True)
class Grupo:
    nome: str
    cortes: int
    indice_medio: float


@dataclass(frozen=True)
class Rede:
    plataforma: str
    cortes: int
    mediana_visualizacoes: float | None
    mediana_interacao: float | None
    mediana_retencao: float | None


@dataclass
class Resumo:
    com_metricas: int  # publicações com pelo menos uma coleta
    com_7_dias: int
    cortes: list[Corte]
    hashtags: list[ranking.Desempenho]
    tipos_de_titulo: list[ranking.Desempenho]
    por_tipo_de_momento: list[Grupo]
    por_rede: list[Rede]
    sugestao: aprendizado.SugestaoEngajamento


def tipo_de_momento(sinais: dict[str, float]) -> str | None:
    """O sinal mais forte do momento, em palavras (o que mais "fez" o momento)."""
    if not sinais:
        return None
    mais_forte = max(sorted(sinais), key=lambda nome: sinais[nome])
    return NOMES_DOS_SINAIS.get(mais_forte, mais_forte)


def _mediana(valores: list[float | None]) -> float | None:
    presentes = [v for v in valores if v is not None]
    return round(statistics.median(presentes), 4) if presentes else None


def resumir(sessao: Session, cfg: Config) -> Resumo:
    sugestao = aprendizado.sugerir(sessao, cfg)
    indices = sugestao.detalhes  # já calculados na sugestão
    cortes = sorted(
        (
            Corte(
                publicacao_id=i.linha.publicacao.id,
                momento_id=i.linha.momento.id,
                titulo=i.linha.decisao.titulo if i.linha.decisao else None,
                plataforma=i.linha.publicacao.plataforma.value,
                formato=i.linha.formato,
                link=i.linha.publicacao.link,
                indice=i.valor,
                visualizacoes=i.linha.metrica.visualizacoes,
                curtidas=i.linha.metrica.curtidas,
                comentarios=i.linha.metrica.comentarios,
                compartilhamentos=i.linha.metrica.compartilhamentos,
                retencao=i.linha.metrica.retencao,
                tipo_de_momento=tipo_de_momento(i.linha.momento.sinais),
            )
            for i in indices
        ),
        key=lambda c: (-c.indice, c.publicacao_id),
    )
    por_tipo: dict[str, list[float]] = defaultdict(list)
    for corte in cortes:
        if corte.tipo_de_momento:
            por_tipo[corte.tipo_de_momento].append(corte.indice)
    linhas = [i.linha for i in indices]
    redes = []
    for plataforma in Plataforma:
        da_rede = [x for x in linhas if x.publicacao.plataforma is plataforma]
        if da_rede:
            redes.append(
                Rede(
                    plataforma=plataforma.value,
                    cortes=len(da_rede),
                    mediana_visualizacoes=_mediana([x.metrica.visualizacoes for x in da_rede]),
                    mediana_interacao=_mediana(
                        [indice.taxa_de_interacao(x.metrica) for x in da_rede]
                    ),
                    mediana_retencao=_mediana([x.metrica.retencao for x in da_rede]),
                )
            )
    com_metricas = sessao.exec(select(func.count(func.distinct(col(Metrica.publicacao_id))))).one()
    return Resumo(
        com_metricas=com_metricas,
        com_7_dias=len(indices),
        cortes=cortes,
        hashtags=ranking.hashtags(indices, cfg.engajamento.minimo_usos),
        tipos_de_titulo=ranking.tipos_de_titulo(indices, cfg.engajamento.minimo_usos),
        por_tipo_de_momento=sorted(
            (Grupo(nome, len(v), round(statistics.fmean(v), 3)) for nome, v in por_tipo.items()),
            key=lambda g: -g.indice_medio,
        ),
        por_rede=redes,
        sugestao=sugestao,
    )
