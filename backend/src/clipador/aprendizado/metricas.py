"""Os exemplos de treino e as medidas de precisão do detector.

**Exemplo** = um momento que você decidiu: as forças de cada detector nele (de 0 a 1, as
mesmas que deram a nota) e a resposta, aprovou ou não.

**Precisão no topo**: em cada dia de live, os momentos são ordenados pela nota e se olha a
metade de cima (os que a revisão mostra primeiro). Precisão = quantos desses você aprovou.
É a medida que importa na prática: se a nota funciona, os de nota alta são os que você
aprova. O máximo nem sempre é 100%: num dia em que você aprovou 1 de 4, a metade de cima
(2 momentos) acerta no máximo 1. Por isso ela serve para **comparar** pesos nas mesmas
decisões (antes e depois), e não como nota absoluta. A precisão "simples" (aprovados ÷
decididos, por dia) também é calculada, para o gráfico de evolução.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlmodel import Session, col, select

from clipador.banco.modelos import Decisao, Gravacao, Momento, id_de
from clipador.revisao.consultas import dia_da_gravacao

FRACAO_DO_TOPO = 0.5


@dataclass(frozen=True)
class Exemplo:
    momento_id: int
    dia: date
    forcas: dict[str, float]  # só os detectores disponíveis naquela live
    aprovado: bool
    ajuste_inicio_s: float | None
    ajuste_fim_s: float | None
    detectado_em: datetime  # quando a gravação entrou no app (antes da detecção)


def exemplos(sessao: Session) -> list[Exemplo]:
    """Todos os momentos decididos, na ordem das decisões."""
    linhas = sessao.exec(
        select(Momento, Decisao, Gravacao)
        .join(Decisao, col(Decisao.momento_id) == col(Momento.id))
        .join(Gravacao, col(Gravacao.id) == col(Momento.gravacao_id))
        .order_by(col(Decisao.decidida_em), col(Momento.id))
    ).all()
    return [
        Exemplo(
            momento_id=id_de(momento),
            dia=dia_da_gravacao(gravacao),
            forcas=dict(momento.sinais),
            aprovado=decisao.aprovado,
            ajuste_inicio_s=decisao.ajuste_inicio_s,
            ajuste_fim_s=decisao.ajuste_fim_s,
            detectado_em=gravacao.criada_em,
        )
        for momento, decisao, gravacao in linhas
    ]


def detectores(lista: list[Exemplo]) -> list[str]:
    """Os detectores que aparecem nos exemplos, em ordem alfabética."""
    return sorted({nome for exemplo in lista for nome in exemplo.forcas})


def nota(forcas: dict[str, float], pesos: dict[str, float]) -> float:
    """A nota (0 a 100) com estes pesos: a mesma conta de ``detectores.nota``.

    Média ponderada só dos detectores disponíveis naquela live (as chaves de ``forcas``).
    """
    total = sum(pesos.get(nome, 0.0) for nome in forcas)
    if total <= 0:
        return 0.0
    return 100 * sum(pesos.get(nome, 0.0) * forca for nome, forca in forcas.items()) / total


def acertos_no_topo(
    lista: list[Exemplo], pesos: dict[str, float], fracao: float = FRACAO_DO_TOPO
) -> tuple[int, int]:
    """(aprovados, olhados) na metade de cima de cada dia, ordenando pela nota com ``pesos``."""
    por_dia: dict[date, list[Exemplo]] = defaultdict(list)
    for exemplo in lista:
        por_dia[exemplo.dia].append(exemplo)
    aprovados = olhados = 0
    for do_dia in por_dia.values():
        # Empate na nota: o de id menor primeiro (a ordem não pode depender do acaso).
        ordenados = sorted(do_dia, key=lambda e: (-nota(e.forcas, pesos), e.momento_id))
        topo = ordenados[: max(1, math.ceil(len(ordenados) * fracao))]
        aprovados += sum(e.aprovado for e in topo)
        olhados += len(topo)
    return aprovados, olhados


def precisao_no_topo(lista: list[Exemplo], pesos: dict[str, float]) -> float | None:
    aprovados, olhados = acertos_no_topo(lista, pesos)
    return aprovados / olhados if olhados else None


def precisao_por_dia(lista: list[Exemplo]) -> list[dict]:
    """Por dia de live: decididos, aprovados e a precisão (aprovados ÷ decididos)."""
    por_dia: dict[date, list[Exemplo]] = defaultdict(list)
    for exemplo in lista:
        por_dia[exemplo.dia].append(exemplo)
    resultado = []
    for dia in sorted(por_dia):
        decididos = len(por_dia[dia])
        aprovados = sum(e.aprovado for e in por_dia[dia])
        resultado.append(
            {
                "dia": dia.isoformat(),
                "decididos": decididos,
                "aprovados": aprovados,
                "precisao": round(aprovados / decididos, 3),
            }
        )
    return resultado
