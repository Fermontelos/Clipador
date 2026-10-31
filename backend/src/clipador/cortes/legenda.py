"""Legenda de um clipe: só as palavras do trecho, com os tempos começando do zero.

A transcrição (etapa 6) tem o tempo de cada palavra na gravação inteira. Para o clipe, o
app pega as palavras do trecho, subtrai o início do clipe e as agrupa em **linhas curtas**
(até ``MAX_PALAVRAS`` palavras ou ``MAX_LETRAS`` letras), porque no vertical a tela é
estreita e a legenda é lida de relance. Uma pausa na fala começa uma linha nova.
"""

from pathlib import Path
from typing import Any

from clipador.transcricao.legenda import salvar_srt

MAX_PALAVRAS = 4
MAX_LETRAS = 28
PAUSA_S = 0.6


def linhas_do_clipe(
    palavras: list[dict[str, Any]], inicio_s: float, fim_s: float
) -> list[dict[str, Any]]:
    """As linhas da legenda do clipe [inicio_s, fim_s], com tempos relativos ao clipe."""
    duracao = fim_s - inicio_s
    linhas: list[dict[str, Any]] = []
    for palavra in palavras:
        if palavra["fim_s"] <= inicio_s or palavra["inicio_s"] >= fim_s:
            continue
        comeco = max(palavra["inicio_s"] - inicio_s, 0.0)
        termino = min(palavra["fim_s"] - inicio_s, duracao)
        texto = palavra["palavra"].strip()
        atual = linhas[-1] if linhas else None
        nova = (
            atual is None
            or comeco - atual["fim_s"] > PAUSA_S
            or len(atual["palavras"]) >= MAX_PALAVRAS
            or len(atual["texto"]) + 1 + len(texto) > MAX_LETRAS
        )
        if nova:
            linhas.append(
                {"inicio_s": comeco, "fim_s": termino, "texto": texto, "palavras": [texto]}
            )
        elif atual is not None:
            atual["texto"] = f"{atual['texto']} {texto}"
            atual["fim_s"] = termino
            atual["palavras"].append(texto)
    return [
        {"inicio_s": round(linha["inicio_s"], 3), "fim_s": round(linha["fim_s"], 3),
         "texto": linha["texto"]}
        for linha in linhas
    ]  # fmt: skip


def salvar_legenda_do_clipe(
    destino: Path, palavras: list[dict[str, Any]], inicio_s: float, fim_s: float
) -> Path | None:
    """Salva o ``.srt`` do clipe; ``None`` se não houver fala no trecho."""
    linhas = linhas_do_clipe(palavras, inicio_s, fim_s)
    if not linhas:
        return None
    return salvar_srt(destino, linhas)
