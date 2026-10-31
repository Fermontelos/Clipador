"""Detector de fala: expressões que marcam um momento e fala acelerada (pela transcrição).

**Expressões** (``[deteccao.fala] expressoes``): "meu deus", "não acredito", "caramba"...
A comparação ignora maiúsculas, acentos e pontuação ("NÃO ACREDITO!" = "nao acredito"),
porque o Whisper às vezes escreve a mesma fala de jeitos diferentes. Expressões de várias
palavras precisam aparecer em sequência. Cada uma vale 0,8; se houver outra (ou a mesma,
repetida) a menos de ``PERTO_S`` (10 s), as duas valem 1 ("meu Deus... não acredito!").

**Fala acelerada**: palavras por segundo numa janela de ``JANELA_S`` (5 s), comparadas com
a sua velocidade típica na live inteira (a mediana das janelas em que você fala). Quem
narra uma jogada ou se empolga acelera. Com o padrão ``fala_acelerada = 1.6``: 1,6 vezes a
sua velocidade típica vale 0,5, e 2,2 vezes ou mais (o dobro do excesso) vale 1.

Risada não entra aqui: o Whisper não escreve risada ("kkkk"). Ela aparece no detector de
volume, quando é alta.
"""

import re
import unicodedata

import numpy as np

from clipador.config import ConfigFala
from clipador.detectores.base import Entrada, Sinal

FORCA_EXPRESSAO = 0.8
PERTO_S = 10.0
JANELA_S = 5.0
PASSO_S = 1.0
MIN_PALAVRAS_NA_JANELA = 8  # menos que isto em 5 s não é "fala acelerada"


def normalizar(texto: str) -> str:
    """``"NÃO acredito!"`` -> ``"nao acredito"``."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", sem_acento.casefold()).strip()


def achar_expressoes(palavras: list[dict], expressoes: list[str]) -> list[tuple[float, float, str]]:
    """Onde cada expressão aparece: (início, fim, expressão)."""
    # Uma "palavra" do Whisper pode virar mais de uma depois de normalizar ("não-sei").
    tokens: list[tuple[str, float, float]] = []
    for palavra in palavras:
        for parte in normalizar(palavra["palavra"]).split():
            tokens.append((parte, palavra["inicio_s"], palavra["fim_s"]))
    achados = []
    for expressao in expressoes:
        alvo = normalizar(expressao).split()
        if not alvo:
            continue
        for i in range(len(tokens) - len(alvo) + 1):
            if [t[0] for t in tokens[i : i + len(alvo)]] == alvo:
                achados.append((tokens[i][1], tokens[i + len(alvo) - 1][2], expressao))
    return sorted(achados)


def trechos_acelerados(palavras: list[dict], fator: float) -> list[tuple[float, float, float]]:
    """Janelas de fala acelerada: (início, fim, quantas vezes a sua velocidade típica)."""
    if not palavras:
        return []
    inicios = np.array(sorted(p["inicio_s"] for p in palavras))
    fim_da_fala = inicios[-1]
    pontos = np.arange(0, max(fim_da_fala - JANELA_S, 0) + PASSO_S, PASSO_S)
    contagens = np.array(
        [np.count_nonzero((inicios >= t) & (inicios < t + JANELA_S)) for t in pontos]
    )
    falando = contagens[contagens >= 2]
    if len(falando) == 0:
        return []
    tipica = float(np.median(falando))
    trechos: list[tuple[float, float, float]] = []
    for t, contagem in zip(pontos, contagens, strict=True):
        razao = contagem / tipica
        if contagem >= MIN_PALAVRAS_NA_JANELA and razao >= fator:
            if trechos and t <= trechos[-1][1]:  # janelas encostadas viram um trecho só
                anterior = trechos[-1]
                trechos[-1] = (anterior[0], t + JANELA_S, max(anterior[2], razao))
            else:
                trechos.append((float(t), float(t + JANELA_S), razao))
    return trechos


class Fala:
    nome = "fala"

    def __init__(self, cfg: ConfigFala) -> None:
        self.cfg = cfg

    def disponivel(self, entrada: Entrada) -> bool:
        return bool(entrada.palavras)

    def detectar(self, entrada: Entrada) -> list[Sinal]:
        achados = achar_expressoes(entrada.palavras, self.cfg.expressoes)
        sinais = []
        for i, (inicio, fim, expressao) in enumerate(achados):
            acompanhada = any(
                j != i and abs(outro_inicio - inicio) <= PERTO_S
                for j, (outro_inicio, _, _) in enumerate(achados)
            )
            forca = 1.0 if acompanhada else FORCA_EXPRESSAO
            sinais.append(Sinal(self.nome, inicio, fim, forca, f"“{expressao}”"))
        fator = self.cfg.fala_acelerada
        for inicio, fim, razao in trechos_acelerados(entrada.palavras, fator):
            # No limiar (fator) vale 0,5; com o dobro de excesso, 1.
            forca = min(1.0, 0.5 + 0.5 * (razao - fator) / (fator - 1))
            sinais.append(Sinal(self.nome, inicio, fim, forca, f"fala acelerada ({razao:.1f}x)"))
        return sinais
