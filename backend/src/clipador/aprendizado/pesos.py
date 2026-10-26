"""Pesos da nota sugeridos pelas suas decisões: regressão logística (ADR 0009).

**O modelo.** Cada momento decidido é um exemplo: as entradas são as forças de cada
detector (0 a 1) e a saída é "aprovou?". A regressão logística aprende um coeficiente por
detector: o quanto aquele sinal aumenta a chance de você aprovar. Como todas as entradas
estão na mesma escala (0 a 1), os coeficientes são comparáveis entre si.

**De coeficiente a peso.** A nota é uma média ponderada, então um peso negativo não faz
sentido: um coeficiente negativo (o sinal até atrapalha) vira 0. Os pesos aprendidos dividem
entre si a mesma soma que esses detectores já tinham; um detector que não aparece nas suas
decisões (ex.: o do LoL, se você nunca revisou uma live de LoL) fica com o peso de antes.

**Comparação justa.** Validação cruzada em 5 partes: os pesos são aprendidos com 4 partes
das decisões e medidos na quinta, que o modelo não viu; repete-se para cada parte. Os pesos
atuais são medidos nas mesmas partes. A medida é a precisão no topo (``metricas``).

**Classes desbalanceadas.** Se você aprova 80% dos momentos, um modelo que só "aprova tudo"
acerta 80% sem aprender nada. ``class_weight="balanced"`` dá o mesmo peso total às
aprovações e às recusas.

**Limitação.** Só os momentos que você revisou têm decisão: o modelo aprende entre os que
o detector já achou bons (os 15 de maior nota de cada dia, em geral), não sobre a live
inteira. Um momento que o detector nunca sugere nunca vira exemplo.
"""

from collections import Counter

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from clipador.aprendizado.metricas import Exemplo, acertos_no_topo

MIN_DECISOES = 100
DOBRAS = 5
SEMENTE = 0  # a divisão em partes não pode mudar a cada vez que você abre a tela


class PoucasDecisoesError(ValueError):
    """Ainda não há decisões suficientes (ou variadas o bastante) para treinar."""


class SemSinalUtilError(ValueError):
    """Nenhum sinal ajuda a prever as aprovações (todos os coeficientes deram ≤ 0)."""


def _matriz(lista: list[Exemplo], nomes: list[str]) -> tuple[np.ndarray, np.ndarray]:
    # Detector indisponível naquela live (ex.: sem LoL) entra como força 0.
    entradas = np.array([[e.forcas.get(nome, 0.0) for nome in nomes] for e in lista])
    respostas = np.array([e.aprovado for e in lista], dtype=int)
    return entradas, respostas


def _coeficientes(lista: list[Exemplo], nomes: list[str]) -> np.ndarray:
    entradas, respostas = _matriz(lista, nomes)
    modelo = LogisticRegression(class_weight="balanced", max_iter=1000)
    modelo.fit(entradas, respostas)
    return modelo.coef_[0]


def _para_pesos(
    coeficientes: np.ndarray, nomes: list[str], atuais: dict[str, float]
) -> dict[str, float]:
    positivos = np.clip(coeficientes, 0, None)
    if positivos.sum() <= 0:
        raise SemSinalUtilError(
            "Nenhum sinal aumenta a chance de você aprovar: os pesos atuais continuam."
        )
    # Os aprendidos dividem a soma que já tinham; os outros detectores ficam como estão.
    soma = sum(atuais.get(nome, 0.0) for nome in nomes) or 1.0
    pesos = dict(atuais)
    for nome, valor in zip(nomes, positivos, strict=True):
        pesos[nome] = round(float(valor / positivos.sum() * soma), 3)
    return pesos


def _conferir(lista: list[Exemplo], minimo: int) -> None:
    if len(lista) < minimo:
        raise PoucasDecisoesError(
            f"São {len(lista)} decisões; o treino precisa de pelo menos {minimo}."
        )
    contagem = Counter(e.aprovado for e in lista)
    if len(contagem) < 2 or min(contagem.values()) < DOBRAS:
        raise PoucasDecisoesError(
            f"Precisa de pelo menos {DOBRAS} aprovações e {DOBRAS} recusas para comparar "
            f"(são {contagem[True]} e {contagem[False]})."
        )


def treinar(
    lista: list[Exemplo],
    nomes: list[str],
    atuais: dict[str, float],
    minimo: int = MIN_DECISOES,
) -> dict[str, float]:
    """Os pesos sugeridos (todos os detectores, somando o que somavam). Ver o topo."""
    _conferir(lista, minimo)
    return _para_pesos(_coeficientes(lista, nomes), nomes, atuais)


def validacao_cruzada(
    lista: list[Exemplo], nomes: list[str], atuais: dict[str, float]
) -> tuple[float, float]:
    """(precisão no topo com os pesos atuais, com os aprendidos), em decisões não vistas."""
    _conferir(lista, DOBRAS * 2)
    _, respostas = _matriz(lista, nomes)
    dobras = StratifiedKFold(n_splits=DOBRAS, shuffle=True, random_state=SEMENTE)
    antes = depois = olhados_antes = olhados_depois = 0
    for treino, teste in dobras.split(np.zeros(len(lista)), respostas):
        de_treino = [lista[i] for i in treino]
        de_teste = [lista[i] for i in teste]
        try:
            aprendidos = _para_pesos(_coeficientes(de_treino, nomes), nomes, atuais)
        except SemSinalUtilError:
            aprendidos = atuais  # nesta parte nada ajudou: fica como está
        a, o = acertos_no_topo(de_teste, atuais)
        antes, olhados_antes = antes + a, olhados_antes + o
        a, o = acertos_no_topo(de_teste, aprendidos)
        depois, olhados_depois = depois + a, olhados_depois + o
    return antes / olhados_antes, depois / olhados_depois
