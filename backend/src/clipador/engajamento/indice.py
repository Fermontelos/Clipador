"""O índice de engajamento: as métricas de 7 dias de um vídeo num número comparável.

Visualizações sozinhas enganam: um canal que cresce faz todo vídeo novo parecer melhor, e
YouTube e TikTok têm públicos de tamanhos diferentes. Por isso cada medida é comparada com a
**mediana do mesmo grupo no seu canal** (o vídeo "típico"). Os grupos são as redes, e no
YouTube os Shorts ficam separados dos vídeos horizontais: um Short costuma ter muito mais
visualizações, e com uma mediana só todo horizontal pareceria ruim.

- **alcance** = visualizações ÷ a mediana das visualizações;
- **interação** = (curtidas + comentários + compartilhamentos) ÷ visualizações, ÷ a mediana
  dessa taxa;
- **retenção** = a fração média do vídeo assistida ÷ a mediana (só no YouTube: o TikTok não a
  dá pela API).

Cada medida vale 1 no vídeo típico, 2 no que foi o dobro... e no máximo ``TETO`` (10), para
um viral não engolir os outros. O índice é a média dessas medidas com os pesos de
``[engajamento.pesos]``; uma medida que o vídeo não tem (a retenção no TikTok) sai da conta, e
os pesos das outras são divididos entre elas.

Um vídeo está **acima da mediana** se o índice dele passa da mediana dos índices do mesmo
grupo: é essa a resposta que a regressão logística aprende a prever (``aprendizado.engajamento``).
"""

import statistics
from dataclasses import dataclass

from sqlmodel import Session, col, select

from clipador.banco.modelos import (
    Clipe,
    ColetaMetrica,
    Decisao,
    Gravacao,
    Metrica,
    Momento,
    Plataforma,
    Publicacao,
)
from clipador.config import PesosDoIndice
from clipador.publicacao.pedido import e_short

TETO = 10.0


@dataclass(frozen=True)
class Linha:
    """Uma publicação com a métrica de 7 dias, e o momento de onde ela veio."""

    publicacao: Publicacao
    metrica: Metrica
    momento: Momento
    decisao: Decisao | None
    gravacao: Gravacao
    formato: str
    duracao_s: float | None = None  # do clipe: um vertical longo não é Short

    @property
    def grupo(self) -> str:
        """Com quem o vídeo é comparado: a rede (e, no YouTube, Short ou vídeo normal)."""
        plataforma = self.publicacao.plataforma
        if plataforma is Plataforma.YOUTUBE:
            vertical = self.formato.startswith("vertical")
            return "youtube_short" if e_short(vertical, self.duracao_s) else "youtube_video"
        return plataforma.value


@dataclass(frozen=True)
class Indice:
    linha: Linha
    valor: float
    alcance: float | None
    interacao: float | None
    retencao: float | None
    acima_da_mediana: bool


def linhas_de_7_dias(sessao: Session) -> list[Linha]:
    """As publicações que já têm a coleta de 7 dias (a que entra no índice)."""
    resultado = sessao.exec(
        select(Publicacao, Metrica, Momento, Gravacao, Clipe)
        .join(Metrica, col(Metrica.publicacao_id) == col(Publicacao.id))
        .join(Clipe, col(Clipe.id) == col(Publicacao.clipe_id))
        .join(Momento, col(Momento.id) == col(Clipe.momento_id))
        .join(Gravacao, col(Gravacao.id) == col(Momento.gravacao_id))
        .where(Metrica.coleta == ColetaMetrica.D7)
        .order_by(col(Publicacao.id))
    ).all()
    decisoes = {
        d.momento_id: d
        for d in sessao.exec(
            select(Decisao).where(
                col(Decisao.momento_id).in_([m.id for _, _, m, _, _ in resultado])
            )
        ).all()
    }
    return [
        Linha(
            pub,
            metrica,
            momento,
            decisoes.get(momento.id),
            gravacao,
            clipe.formato.value,
            clipe.duracao_s,
        )  # fmt: skip
        for pub, metrica, momento, gravacao, clipe in resultado
    ]


def taxa_de_interacao(metrica: Metrica) -> float | None:
    if not metrica.visualizacoes:
        return None
    partes = [metrica.curtidas, metrica.comentarios, metrica.compartilhamentos]
    if all(p is None for p in partes):
        return None
    return sum(p or 0 for p in partes) / metrica.visualizacoes


def _mediana(valores: list[float]) -> float | None:
    positivos = [v for v in valores if v is not None and v > 0]
    return statistics.median(positivos) if positivos else None


def _relativo(valor: float | None, mediana: float | None) -> float | None:
    if valor is None or not mediana:
        return None
    return min(valor / mediana, TETO)


def calcular(linhas: list[Linha], pesos: PesosDoIndice) -> list[Indice]:
    """O índice de cada publicação (ver o topo). As que não têm nenhuma medida ficam de fora."""
    resultado: list[Indice] = []
    for grupo in sorted({linha.grupo for linha in linhas}):
        da_rede = [linha for linha in linhas if linha.grupo == grupo]
        med_vis = _mediana([float(x.metrica.visualizacoes or 0) for x in da_rede])
        med_int = _mediana([taxa_de_interacao(x.metrica) for x in da_rede])
        med_ret = _mediana([x.metrica.retencao for x in da_rede])
        parciais = []
        for linha in da_rede:
            medidas = {
                "alcance": _relativo(linha.metrica.visualizacoes, med_vis),
                "interacao": _relativo(taxa_de_interacao(linha.metrica), med_int),
                "retencao": _relativo(linha.metrica.retencao, med_ret),
            }
            com_valor = {nome: v for nome, v in medidas.items() if v is not None}
            peso_total = sum(getattr(pesos, nome) for nome in com_valor)
            if not com_valor or peso_total <= 0:
                continue
            valor = sum(getattr(pesos, nome) * v for nome, v in com_valor.items()) / peso_total
            parciais.append((linha, round(valor, 4), medidas))
        if not parciais:
            continue
        mediana_do_indice = statistics.median(valor for _, valor, _ in parciais)
        resultado += [
            Indice(
                linha=linha,
                valor=valor,
                alcance=medidas["alcance"],
                interacao=medidas["interacao"],
                retencao=medidas["retencao"],
                acima_da_mediana=valor > mediana_do_indice,
            )
            for linha, valor, medidas in parciais
        ]
    # Na ordem das publicações (a cronológica), e não na dos grupos: a validação cruzada
    # sorteia as partes pela posição na lista, e um detalhe interno não pode mudá-las.
    return sorted(resultado, key=lambda item: item.linha.publicacao.id)


def indices(sessao: Session, pesos: PesosDoIndice) -> list[Indice]:
    return calcular(linhas_de_7_dias(sessao), pesos)
