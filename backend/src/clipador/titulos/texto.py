"""Regras de texto dos títulos e hashtags, e o título simples (plano B, sem IA).

Limites conferidos nas fontes oficiais (ver ``docs/stack.html``):

- título do YouTube: até **100 caracteres**;
- hashtags: o YouTube recomenda de 3 a 5 por vídeo, sempre ligadas ao conteúdo (hashtag
  enganosa pode levar à remoção do vídeo); com mais de 60, ignora todas;
- legenda do TikTok: até 2.200 caracteres (título + hashtags cabem com folga; a etapa 16
  corta nesse limite ao montar a legenda: ``config.LIMITE_LEGENDA_TIKTOK``).
"""

import re
import unicodedata
from typing import Any

LIMITE_TITULO_YOUTUBE = 100
TIPOS_DE_TITULO = ("frase_sua", "pergunta", "exagero", "descricao")


def cortar_titulo(titulo: str, limite: int = LIMITE_TITULO_YOUTUBE) -> str:
    """O título dentro do limite, cortado numa palavra inteira (com "…" no fim)."""
    titulo = " ".join(titulo.split())  # sem quebras de linha nem espaços duplos
    if len(titulo) <= limite:
        return titulo
    corte = titulo[: limite - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{corte}…"


def normalizar_hashtag(texto: str) -> str | None:
    """``"Pentakill!"`` -> ``"#pentakill"``; ``None`` se não sobrar nada.

    Minúsculas, sem espaços e sem pontuação (uma hashtag acaba no primeiro espaço ou
    símbolo). Os acentos ficam: o YouTube e o TikTok aceitam (#barão).
    """
    limpo = unicodedata.normalize("NFC", texto.strip().lstrip("#").casefold())
    limpo = re.sub(r"[^\w]", "", limpo)
    return f"#{limpo}" if limpo else None


def juntar_hashtags(fixas: list[str], sugeridas: list[str], maximo: int) -> list[str]:
    """As fixas do config primeiro, depois as sugeridas, sem repetir, até ``maximo``."""
    resultado: list[str] = []
    for texto in [*fixas, *sugeridas]:
        hashtag = normalizar_hashtag(texto)
        if hashtag and hashtag not in resultado:
            resultado.append(hashtag)
    return resultado[:maximo]


def falas_do_trecho(palavras: list[dict[str, Any]], inicio_s: float, fim_s: float) -> str:
    """O que você falou no trecho, como texto corrido."""
    return " ".join(
        p["palavra"].strip()
        for p in palavras
        if p["fim_s"] > inicio_s and p["inicio_s"] < fim_s and p["palavra"].strip()
    )


def frase_marcante(
    palavras: list[dict[str, Any]], inicio_s: float, fim_s: float, pico_s: float | None
) -> str | None:
    """A frase dita mais perto do pico do momento (o plano B sem IA).

    As palavras do trecho viram frases, quebradas nas pausas de mais de 0,7 s; fica a que
    está mais perto do pico (a jogada, o grito). Sem fala no trecho, ``None``.
    """
    frases: list[list[dict[str, Any]]] = []
    for palavra in palavras:
        if palavra["fim_s"] <= inicio_s or palavra["inicio_s"] >= fim_s:
            continue
        if not frases or palavra["inicio_s"] - frases[-1][-1]["fim_s"] > 0.7:
            frases.append([])
        frases[-1].append(palavra)
    if not frases:
        return None
    alvo = pico_s if pico_s is not None else (inicio_s + fim_s) / 2

    def distancia(frase: list[dict[str, Any]]) -> float:
        comeco, fim = frase[0]["inicio_s"], frase[-1]["fim_s"]
        return 0.0 if comeco <= alvo <= fim else min(abs(comeco - alvo), abs(fim - alvo))

    escolhida = min(frases, key=distancia)
    texto = " ".join(p["palavra"].strip() for p in escolhida).strip(" ,.;")
    return texto[:1].upper() + texto[1:] if texto else None
