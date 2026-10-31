"""Acesso ao FFmpeg e ao ffprobe.

O app não usa uma biblioteca Python para vídeo: ele chama os programas ``ffmpeg`` (para
gerar e converter) e ``ffprobe`` (para inspecionar) diretamente, com a lista de
argumentos montada em Python. Assim dá para testar o comando montado sem rodar nada, e
usar tudo o que o FFmpeg oferece (filtros, NVENC) sem esperar uma biblioteca suportar.
"""

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class _Resultado:
    codigo: int
    saida: str
    erros: str


def _executar(
    comando: list[str], tempo_limite_s: float | None, pasta: Path | None = None
) -> _Resultado:
    """Roda um programa e devolve o código de saída e o que ele escreveu.

    No limite de tempo, encerra o programa **e os processos que ele abriu**. Isso importa
    quando o ``ffmpeg.exe`` do PATH é um "shim" (Chocolatey, Scoop): um programa pequeno
    que abre o FFmpeg verdadeiro como processo filho. O ``subprocess.run`` só encerraria o
    shim; o FFmpeg verdadeiro continuaria rodando, com a saída aberta, e o Python
    esperaria por ela para sempre. Foi o que travou a CI (com o FFmpeg do Chocolatey).

    ``stdin`` fica fechado: um programa que tente ler o teclado não fica esperando.
    ``pasta``: onde o programa roda (os caminhos relativos dos argumentos partem dela).
    """
    # errors="replace": uma mensagem com caracteres fora do UTF-8 (comum no Windows em
    # português) não pode derrubar o app; o pior caso é aparecer um "�" na mensagem.
    processo = subprocess.Popen(
        comando,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=pasta,
    )
    try:
        saida, erros = processo.communicate(timeout=tempo_limite_s)
    except subprocess.TimeoutExpired:
        _encerrar_com_os_filhos(processo)
        raise
    return _Resultado(processo.returncode, saida, erros)


def _encerrar_com_os_filhos(processo: subprocess.Popen) -> None:
    # /T: a árvore toda (o processo e os que ele abriu); /F: à força.
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(processo.pid)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )
    processo.kill()  # garantia, se o taskkill não tiver conseguido
    # Se sobrou algum processo segurando a saída, não vale travar esperando por ele.
    with contextlib.suppress(subprocess.TimeoutExpired):
        processo.communicate(timeout=10)


def rodar(
    argumentos: list[str], tempo_limite_s: float | None = None, pasta: Path | None = None
) -> None:
    """Roda o ``ffmpeg`` com os argumentos dados (sem o nome do programa).

    ``tempo_limite_s``: se o FFmpeg passar disso, é encerrado e vira ``FFmpegFalhouError``.
    Importante de madrugada (etapas 5 e 9): sem limite, um FFmpeg travado pararia a fila.
    ``pasta``: onde o FFmpeg roda. A etapa 9 usa para passar a legenda pelo nome, sem o
    caminho: dentro de um filtro, os ":" e as barras invertidas de um caminho do Windows
    precisariam de escapes.
    """
    comando = [localizar("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *argumentos]
    try:
        resultado = _executar(comando, tempo_limite_s, pasta)
    except subprocess.TimeoutExpired as erro:
        raise FFmpegFalhouError(
            f"O FFmpeg passou do limite de {tempo_limite_s} s e foi encerrado."
        ) from erro
    if resultado.codigo != 0:
        raise FFmpegFalhouError(
            f"O FFmpeg falhou (código {resultado.codigo}): {resultado.erros.strip()}"
        )


@dataclass(frozen=True)
class InfoMidia:
    """O que o ffprobe diz sobre um arquivo de vídeo.

    ``duracao_s`` é ``None`` quando o arquivo não informa a duração: é o caso de um MKV
    cuja gravação foi interrompida (OBS travou, faltou luz). O MKV continua legível, mas
    a duração só é escrita no cabeçalho quando a gravação termina normalmente.
    """

    duracao_s: float | None
    faixas_audio: int
    largura: int | None
    altura: int | None
    titulos_faixas: tuple[str, ...]
    fps: float | None = None


def _fps(fracao: str | None) -> float | None:
    """Converte o fps do ffprobe, que vem como fração ("60/1", "30000/1001")."""
    if not fracao or "/" not in fracao:
        return None
    numerador, denominador = fracao.split("/", 1)
    try:
        valor = float(numerador) / float(denominador)
    except (ValueError, ZeroDivisionError):  # "0/0" quando o arquivo não informa
        return None
    return round(valor, 3) or None


def inspecionar(arquivo: Path, tempo_limite_s: float = 60) -> InfoMidia:
    """Duração, quantidade de faixas de áudio, resolução e fps do vídeo.

    O ffprobe só lê o cabeçalho e leva menos de 1 s; o limite existe para um arquivo
    corrompido que o trave não parar a fila da madrugada.
    """
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
    try:
        resultado = _executar(comando, tempo_limite_s)
    except subprocess.TimeoutExpired as erro:
        raise FFmpegFalhouError(
            f"O ffprobe passou do limite de {tempo_limite_s} s lendo {arquivo} e foi encerrado."
        ) from erro
    if resultado.codigo != 0:
        raise FFmpegFalhouError(f"O ffprobe não conseguiu ler {arquivo}: {resultado.erros.strip()}")
    dados = json.loads(resultado.saida)
    duracao = dados.get("format", {}).get("duration")
    fluxos = dados.get("streams", [])
    audios = [f for f in fluxos if f.get("codec_type") == "audio"]
    video: dict[str, Any] = next((f for f in fluxos if f.get("codec_type") == "video"), {})
    return InfoMidia(
        duracao_s=float(duracao) if duracao is not None else None,
        faixas_audio=len(audios),
        largura=video.get("width"),
        altura=video.get("height"),
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        # O MKV às vezes guarda a tag em maiúsculas ("TITLE").
        titulos_faixas=tuple(
            f.get("tags", {}).get("title") or f.get("tags", {}).get("TITLE", "") for f in audios
        ),
    )
