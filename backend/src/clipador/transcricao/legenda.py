"""Legenda .srt da transcrição, para conferir o que o Whisper entendeu.

O formato SRT é texto puro, um bloco por frase::

    1
    00:00:25,000 --> 00:00:28,000
    NÃO ACREDITO NO QUE ESSE YASUO FEZ

Abre em qualquer player (VLC, o Filmes e TV do Windows) junto com o vídeo da live.
"""

from pathlib import Path
from typing import Any

# Sem segmentos (transcrição antiga, só com palavras), uma pausa maior que esta entre
# duas palavras começa uma frase nova na legenda.
PAUSA_ENTRE_FRASES_S = 1.0


def tempo_srt(segundos: float) -> str:
    """``3725.5`` -> ``"01:02:05,500"`` (horas:minutos:segundos,milissegundos)."""
    milissegundos = round(max(segundos, 0) * 1000)
    horas, resto = divmod(milissegundos, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    segundos_inteiros, milissegundos = divmod(resto, 1000)
    return f"{horas:02d}:{minutos:02d}:{segundos_inteiros:02d},{milissegundos:03d}"


def frases_das_palavras(palavras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta palavras em frases, quebrando nas pausas (quando não há segmentos salvos)."""
    frases: list[dict[str, Any]] = []
    for palavra in palavras:
        atual = frases[-1] if frases else None
        if atual is None or palavra["inicio_s"] - atual["fim_s"] > PAUSA_ENTRE_FRASES_S:
            frases.append({"inicio_s": palavra["inicio_s"], "fim_s": palavra["fim_s"], "texto": ""})
            atual = frases[-1]
        atual["texto"] = f"{atual['texto']} {palavra['palavra']}".strip()
        atual["fim_s"] = palavra["fim_s"]
    return frases


def gerar_srt(segmentos: list[dict[str, Any]]) -> str:
    blocos = [
        f"{numero}\n{tempo_srt(s['inicio_s'])} --> {tempo_srt(s['fim_s'])}\n{s['texto']}\n"
        for numero, s in enumerate((s for s in segmentos if s["texto"].strip()), start=1)
    ]
    return "\n".join(blocos)


def salvar_srt(destino: Path, segmentos: list[dict[str, Any]]) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: com o BOM, players antigos do Windows mostram os acentos certos.
    destino.write_text(gerar_srt(segmentos), encoding="utf-8-sig")
    return destino
