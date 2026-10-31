"""Hashtags e tipos de título que funcionam, e a escolha entre aproveitar e testar (etapa 17).

**Efeito.** Para cada hashtag (ou tipo de título: frase sua, pergunta, exagero, descrição),
compara-se o índice médio dos vídeos **com** ela e **sem** ela. Efeito positivo = os vídeos
com ela engajaram mais. Só depois de ``minimo_usos`` (5) vídeos com ela o efeito é
"confiável": com 1 ou 2 vídeos, o acaso decide.

Isso é correlação, não causa: uma hashtag de LoL pode ir bem porque os momentos de LoL vão
bem. Por isso ela só muda a **ordem** das sugestões, e nunca inventa uma hashtag.

**Aproveitar ou testar (multi-armed bandit).** Se o app só usasse as melhores conhecidas, uma
hashtag nova nunca teria chance de mostrar que é boa. A técnica *epsilon-greedy* resolve
isso: na maior parte das vezes (1 − ε), ele aproveita o que funciona; com chance ε
(``[engajamento] exploracao``, 10%), põe na frente uma ainda pouco testada. Tudo sempre entre
as hashtags que a IA sugeriu **para aquele clipe**: hashtag sem relação com o vídeo pode fazer
o YouTube removê-lo.
"""

import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from clipador.engajamento.indice import Indice
from clipador.titulos.texto import normalizar_hashtag


@dataclass(frozen=True)
class Desempenho:
    nome: str
    usos: int
    indice_medio: float
    efeito: float | None  # índice médio com ela - sem ela (None se todos os vídeos a usam)
    confiavel: bool


def _desempenho(
    indices: list[Indice], rotulos: Callable[[Indice], Iterable[str]], minimo_usos: int
) -> list[Desempenho]:
    por_rotulo: dict[str, list[float]] = defaultdict(list)
    for item in indices:
        for rotulo in set(rotulos(item)):
            por_rotulo[rotulo].append(item.valor)
    total = sum(item.valor for item in indices)
    resultado = []
    for nome, valores in por_rotulo.items():
        sem = len(indices) - len(valores)
        medio = statistics.fmean(valores)
        efeito = medio - (total - sum(valores)) / sem if sem else None
        resultado.append(
            Desempenho(
                nome,
                len(valores),
                round(medio, 3),
                round(efeito, 3) if efeito is not None else None,
                len(valores) >= minimo_usos and efeito is not None,
            )  # fmt: skip
        )
    # Os confiáveis primeiro, do maior efeito para o menor; depois os em teste, por usos.
    return sorted(
        resultado,
        key=lambda d: (not d.confiavel, -(d.efeito or 0) if d.confiavel else -d.usos, d.nome),
    )


def hashtags(indices: list[Indice], minimo_usos: int) -> list[Desempenho]:
    def da_decisao(item: Indice) -> list[str]:
        decisao = item.linha.decisao
        return (
            [h for h in (normalizar_hashtag(t) for t in decisao.hashtags) if h] if decisao else []
        )

    return _desempenho(indices, da_decisao, minimo_usos)


def tipos_de_titulo(indices: list[Indice], minimo_usos: int) -> list[Desempenho]:
    def do_titulo(item: Indice) -> list[str]:
        decisao = item.linha.decisao
        return [decisao.tipo_titulo] if decisao and decisao.tipo_titulo else []

    return _desempenho(indices, do_titulo, minimo_usos)


def _ordenar(
    nomes: list[str], desempenhos: list[Desempenho], exploracao: float, sorteio: random.Random
) -> list[str]:
    """``nomes`` reordenados: aproveitar o que funciona ou, com chance ε, testar um novo."""
    por_nome = {d.nome: d for d in desempenhos}
    if not any(d.confiavel for d in desempenhos):
        return list(nomes)  # sem nada confiável ainda: a ordem da IA

    def nota(nome: str) -> float:
        d = por_nome.get(nome)
        if d is None or not d.confiavel or d.efeito is None:
            return 0.0  # o não testado fica no meio
        return d.efeito

    # sorted é estável: entre os de mesma nota, vale a ordem da IA.
    ordenados = sorted(nomes, key=lambda n: -nota(n))
    em_teste = [n for n in nomes if not (por_nome.get(n) and por_nome[n].confiavel)]
    if em_teste and sorteio.random() < exploracao:
        escolhido = sorteio.choice(em_teste)
        ordenados.remove(escolhido)
        ordenados.insert(0, escolhido)
    return ordenados


def ordenar_hashtags(
    candidatas: list[str],
    desempenhos: list[Desempenho],
    exploracao: float,
    sorteio: random.Random | None = None,
) -> list[str]:
    """As hashtags que a IA sugeriu para o clipe, na ordem em que devem entrar."""
    normalizadas = [h for h in dict.fromkeys(normalizar_hashtag(c) for c in candidatas) if h]
    return _ordenar(normalizadas, desempenhos, exploracao, sorteio or random.Random())


def ordenar_titulos(
    sugestoes: list[dict[str, str]],
    desempenhos: list[Desempenho],
    exploracao: float,
    sorteio: random.Random | None = None,
) -> list[dict[str, str]]:
    """As 3 sugestões de título com o tipo que mais engaja primeiro (a revisão abre nela)."""
    tipos = [s.get("tipo", "") for s in sugestoes]
    ordem = _ordenar(
        list(dict.fromkeys(tipos)), desempenhos, exploracao, sorteio or random.Random()
    )
    return sorted(sugestoes, key=lambda s: ordem.index(s.get("tipo", "")))
