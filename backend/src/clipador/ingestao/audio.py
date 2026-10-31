"""Etapa 5: tira da gravação o áudio da voz, no formato que as próximas etapas usam.

O OBS grava 4 faixas de áudio (1 = mistura, 2 = voz, 3 = jogo, 4 = música; a ordem vem
de ``[faixas]`` no ``config.toml``). Só a voz é extraída, num WAV **mono, 16 kHz,
16 bits**: é o formato que o Whisper espera (etapa 6) e é leve para o detector de volume
(etapa 8). Uma live de 3 h vira um WAV de cerca de 350 MB. O jogo e a música não são
extraídos: os cortes da etapa 9 leem direto do MKV.

Para o FFmpeg, as faixas de áudio são contadas a partir de zero e só entre os áudios:
a faixa 2 do OBS é ``0:a:1`` (arquivo 0, áudio de índice 1).

O WAV fica em ``<pasta_dados>/trabalho/<id da gravação>/voz.wav`` e é apagado quando a
gravação termina a pipeline (ver ``limpar_trabalho``). Se a gravação der erro, ele fica,
para dar para investigar e tentar de novo sem extrair outra vez.

Uma gravação interrompida (OBS travou, faltou luz) é exatamente o caso para o qual o OBS
grava em MKV: o arquivo continua legível, mas fica sem a duração no cabeçalho. Nesse caso
a duração da gravação passa a ser a do próprio WAV extraído, medida no áudio.

O horário de início da gravação já foi lido do nome do arquivo quando ela entrou na fila
(``ingestao.nomes``, etapa 4).
"""

import logging
import shutil
from pathlib import Path

import numpy as np
import soundfile
from sqlmodel import Session

from clipador import ffmpeg
from clipador.banco.fila import Contexto, FalhaDefinitivaError
from clipador.banco.modelos import Gravacao, StatusGravacao, Tarefa, id_de
from clipador.config import Config, Faixas

log = logging.getLogger(__name__)

TAXA_WHISPER = 16_000  # amostras por segundo
BYTES_POR_SEGUNDO = TAXA_WHISPER * 2  # mono, 16 bits
# Abaixo deste pico (numa escala de 0 a 1) a faixa é considerada muda. Um microfone
# ligado capta pelo menos ruído de fundo, bem acima disso.
PICO_MINIMO = 0.001
# Diferença aceitável entre a duração do WAV e a do vídeo.
TOLERANCIA_DURACAO_S = 2.0
# Limite do FFmpeg quando o arquivo não informa a duração. Extrair a voz de uma live de
# 3 h leva uns 2 min; 1 h só estoura se ele travar.
LIMITE_SEM_DURACAO_S = 3600


def caminho_da_voz(cfg: Config, gravacao_id: int) -> Path:
    return cfg.pasta_trabalho / str(gravacao_id) / "voz.wav"


def comando_extrair_voz(arquivo: Path, faixa_voz: int, destino: Path) -> list[str]:
    """Argumentos do FFmpeg para extrair a faixa da voz (``faixa_voz`` conta a partir de 1)."""
    return [
        "-y",
        "-i",
        str(arquivo),
        "-map",
        f"0:a:{faixa_voz - 1}",
        "-vn",  # sem vídeo
        "-ac",
        "1",  # mono
        "-ar",
        str(TAXA_WHISPER),
        "-c:a",
        "pcm_s16le",  # WAV comum, 16 bits
        str(destino),
    ]


def tempo_limite_s(duracao_s: float | None) -> float:
    """Quanto o FFmpeg pode levar: extrair o áudio é cerca de 100 vezes mais rápido que o
    tempo real, então 2 min + 1/5 da duração só estoura se ele travar (3 h -> 38 min)."""
    if duracao_s is None:
        return LIMITE_SEM_DURACAO_S
    return 120 + duracao_s / 5


def conferir_faixas(info: ffmpeg.InfoMidia, faixas: Faixas, nome: str) -> None:
    """Erro definitivo se faltar a faixa da voz; aviso se faltar a do jogo ou a da música."""
    if info.faixas_audio < faixas.voz:
        raise FalhaDefinitivaError(
            f"A gravação '{nome}' tem {info.faixas_audio} faixa(s) de áudio e não tem a "
            f"faixa {faixas.voz} (voz): confira no OBS se as faixas 1 a 4 estão marcadas "
            "(Configurações > Saída > Gravação) e se o microfone está na faixa da voz "
            "(Propriedades avançadas de áudio)."
        )
    faltando = [
        f"{faixa} ({fonte})"
        for fonte, faixa in (("jogo", faixas.jogo), ("música", faixas.musica))
        if info.faixas_audio < faixa
    ]
    if faltando:
        log.warning(
            "A gravação '%s' não tem a(s) faixa(s) %s. A voz pode ser extraída, mas os "
            "cortes (etapa 9) precisam da faixa do jogo.",
            nome,
            ", ".join(faltando),
        )


def conferir_espaco(pasta: Path, duracao_s: float | None) -> None:
    """Erro claro se o disco não tiver espaço para o WAV (com 10% de folga).

    Sem a duração (gravação interrompida), não dá para calcular: o FFmpeg avisa se faltar.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    if duracao_s is None:
        return
    necessario = int(duracao_s * BYTES_POR_SEGUNDO * 1.1)
    livre = shutil.disk_usage(pasta).free
    if livre < necessario:
        raise OSError(
            f"Falta espaço em disco para o áudio da voz: precisa de "
            f"{necessario / 1e6:.0f} MB e há {livre / 1e6:.0f} MB livres em {pasta}."
        )


def extrair_voz(arquivo: Path, faixa_voz: int, destino: Path, duracao_s: float | None) -> Path:
    """Extrai a voz para ``destino``. Grava num arquivo temporário e só então renomeia,
    para um WAV pela metade (PC desligou no meio) nunca parecer pronto."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.stem + ".parcial.wav")
    try:
        ffmpeg.rodar(
            comando_extrair_voz(arquivo, faixa_voz, temporario),
            tempo_limite_s=tempo_limite_s(duracao_s),
        )
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)
    return destino


def pico_do_audio(wav: Path, bloco_s: float = 60) -> float:
    """Maior volume do WAV (0 a 1), lido aos poucos para não carregar 350 MB de uma vez."""
    pico = 0.0
    for bloco in soundfile.blocks(str(wav), blocksize=int(TAXA_WHISPER * bloco_s)):
        if bloco.size:
            pico = max(pico, float(np.abs(bloco).max()))
    return pico


def extrair_audio(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``extrair_audio`` (registrado em ``sistema.servico``).

    Não faz commit: o ``fila.concluir`` grava a gravação atualizada junto com a conclusão.
    Não usa o ``contexto``: leva uns 2 min por live, então não precisa pausar no meio.
    """
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id) if tarefa.gravacao_id else None
    if gravacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma gravação.")
    arquivo = Path(gravacao.arquivo)
    if not arquivo.is_file():
        # Não é definitiva: pode ser um HD externo desconectado por um momento.
        raise FileNotFoundError(
            f"A gravação {arquivo} não foi encontrada (o arquivo foi movido ou apagado?)."
        )

    info = ffmpeg.inspecionar(arquivo)
    conferir_faixas(info, cfg.faixas, arquivo.name)
    destino = caminho_da_voz(cfg, id_de(gravacao))
    conferir_espaco(destino.parent, info.duracao_s)
    extrair_voz(arquivo, cfg.faixas.voz, destino, info.duracao_s)

    dados_wav = soundfile.info(str(destino))
    if info.duracao_s is None:
        log.warning(
            "A gravação '%s' não informa a duração (o OBS fechou sem terminar a gravação?). "
            "Usando a duração do áudio da voz: %.1f s.",
            arquivo.name,
            dados_wav.duration,
        )
    elif abs(dados_wav.duration - info.duracao_s) > TOLERANCIA_DURACAO_S:
        log.warning(
            "O áudio da voz de '%s' tem %.1f s e o vídeo tem %.1f s. Os tempos dos momentos "
            "podem ficar deslocados.",
            arquivo.name,
            dados_wav.duration,
            info.duracao_s,
        )
    if pico_do_audio(destino) < PICO_MINIMO:
        raise FalhaDefinitivaError(
            f"A faixa {cfg.faixas.voz} (voz) de '{arquivo.name}' está em silêncio: confira "
            "no OBS se o microfone está marcado nessa faixa (Propriedades avançadas de "
            "áudio) e se o número da faixa em [faixas] no config.toml está certo."
        )

    gravacao.duracao_s = info.duracao_s if info.duracao_s is not None else dados_wav.duration
    gravacao.largura, gravacao.altura, gravacao.fps = info.largura, info.altura, info.fps
    gravacao.status = StatusGravacao.PROCESSANDO
    sessao.add(gravacao)
    log.info(
        "Voz de '%s' extraída: %.0f min, %s (%.1f MB).",
        arquivo.name,
        gravacao.duracao_s / 60,
        destino,
        destino.stat().st_size / 1e6,
    )


def limpar_trabalho(cfg: Config, gravacao_id: int) -> None:
    """Apaga os arquivos temporários de uma gravação que terminou a pipeline."""
    pasta = cfg.pasta_trabalho / str(gravacao_id)
    # Trava de segurança: só apaga uma subpasta direta de pasta_trabalho.
    if pasta.resolve().parent != cfg.pasta_trabalho.resolve():
        raise RuntimeError(f"Recusado apagar {pasta}: não está em {cfg.pasta_trabalho}.")
    if pasta.is_dir():
        shutil.rmtree(pasta)
        log.info("Arquivos temporários da gravação %s apagados.", gravacao_id)
