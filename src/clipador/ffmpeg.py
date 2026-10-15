"""Acesso ao FFmpeg e ao ffprobe.

O app não usa uma biblioteca Python para vídeo: ele chama os programas ``ffmpeg`` (para
gerar e converter) e ``ffprobe`` (para inspecionar) diretamente, com a lista de
argumentos montada em Python. Assim dá para testar o comando montado sem rodar nada, e
usar tudo o que o FFmpeg oferece (filtros, NVENC) sem esperar uma biblioteca suportar.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegNaoEncontradoError(FileNotFoundError):
    """O FFmpeg (ou o ffprobe) não está instalado ou não está no PATH."""


class FFmpegFalhouError(RuntimeError):
    """O FFmpeg terminou com erro."""


def localizar(programa: str = "ffmpeg") -> str:
    """Caminho do ``ffmpeg`` ou do ``ffprobe``; erro claro se não estiver instalado."""
    caminho = shutil.which(programa)
    if caminho is None:
        raise FFmpegNaoEncontradoError(
            f"'{programa}' não foi encontrado. Instale o FFmpeg com "
            "'winget install Gyan.FFmpeg' e abra um terminal novo."
        )
    return caminho


def rodar(argumentos: list[str], tempo_limite_s: float | None = None) -> None:
    """Roda o ``ffmpeg`` com os argumentos dados (sem o nome do programa).

    ``tempo_limite_s``: se o FFmpeg passar disso, é encerrado e vira ``FFmpegFalhouError``.
    Importante de madrugada (etapas 5 e 9): sem limite, um FFmpeg travado pararia a fila.
    """
    comando = [localizar("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *argumentos]
    # errors="replace": uma mensagem com caracteres fora do UTF-8 (comum no Windows em
    # português) não pode derrubar o app; o pior caso é aparecer um "�" na mensagem.
    try:
        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=tempo_limite_s,
        )
    except subprocess.TimeoutExpired as erro:
        raise FFmpegFalhouError(
            f"O FFmpeg passou do limite de {tempo_limite_s} s e foi encerrado."
        ) from erro
    if resultado.returncode != 0:
        raise FFmpegFalhouError(
            f"O FFmpeg falhou (código {resultado.returncode}): {resultado.stderr.strip()}"
        )


@dataclass(frozen=True)
class InfoMidia:
    """O que o ffprobe diz sobre um arquivo de vídeo."""

    duracao_s: float
    faixas_audio: int
    largura: int | None
    altura: int | None
    titulos_faixas: tuple[str, ...]


def inspecionar(arquivo: Path) -> InfoMidia:
    """Duração, quantidade de faixas de áudio e resolução do vídeo."""
    comando = [
        localizar("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(arquivo),
    ]
    resultado = subprocess.run(
        comando, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if resultado.returncode != 0:
        raise FFmpegFalhouError(
            f"O ffprobe não conseguiu ler {arquivo}: {resultado.stderr.strip()}"
        )
    dados = json.loads(resultado.stdout)
    duracao = dados.get("format", {}).get("duration")
    if duracao is None:
        raise FFmpegFalhouError(f"O ffprobe não encontrou a duração de {arquivo}.")
    fluxos = dados.get("streams", [])
    audios = [f for f in fluxos if f.get("codec_type") == "audio"]
    video = next((f for f in fluxos if f.get("codec_type") == "video"), {})
    return InfoMidia(
        duracao_s=float(duracao),
        faixas_audio=len(audios),
        largura=video.get("width"),
        altura=video.get("height"),
        # O MKV às vezes guarda a tag em maiúsculas ("TITLE").
        titulos_faixas=tuple(
            f.get("tags", {}).get("title") or f.get("tags", {}).get("TITLE", "") for f in audios
        ),
    )
