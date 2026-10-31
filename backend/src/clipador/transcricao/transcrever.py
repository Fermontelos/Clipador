"""Executor da tarefa ``transcrever``: a voz da live vira texto, em blocos retomáveis.

O WAV da voz (etapa 5) é transcrito em blocos de ``minutos_por_bloco`` (10 min por padrão).
Depois de cada bloco, as palavras e frases vão para o banco num commit, junto com
``transcrito_ate_s``, que diz até onde a transcrição chegou. Assim:

- se o PC desligar, a transcrição continua do último bloco salvo;
- se uma live começar, a tarefa para entre um bloco e outro (``fila.TarefaPausada``) e
  continua depois, para não disputar a placa de vídeo com o OBS.

**Sem cortar palavras na emenda dos blocos.** Uma frase que ainda está sendo dita no fim
do bloco sairia cortada. Por isso, fora o último bloco, só ficam as frases que terminam
pelo menos ``MARGEM_S`` antes do fim do bloco, e o bloco seguinte começa no fim da última
frase aceita: a frase da emenda é transcrita de novo, inteira, no bloco seguinte.

No fim, salva a legenda ``.srt`` em ``<pasta_dados>/transcricoes/`` e registra no log o
tempo gasto e o modelo usado.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
from sqlmodel import Session, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada
from clipador.banco.modelos import Gravacao, Tarefa, Transcricao, id_de
from clipador.config import Config
from clipador.ingestao import audio
from clipador.ingestao.audio import caminho_da_voz
from clipador.transcricao import legenda, whisper
from clipador.transcricao.whisper import Segmento, Transcritor

log = logging.getLogger(__name__)

# Uma frase que termina a menos disto do fim do bloco pode ter sido cortada.
MARGEM_S = 3.0
# O que sobra no fim do áudio menor que isto não é transcrito (não cabe uma palavra).
SOBRA_MINIMA_S = 0.5

# Quem cria o transcritor. Os testes trocam por um falso, que não precisa de placa de vídeo.
CarregarTranscritor = Callable[[Config], Transcritor]


def carregar_whisper(cfg: Config) -> Transcritor:
    return whisper.carregar(cfg.transcricao, cfg.pasta_modelos)


carregar_transcritor: CarregarTranscritor = carregar_whisper


def ler_trecho(wav: Path, inicio_s: float, fim_s: float) -> np.ndarray:
    """Um trecho do WAV como números de -1 a 1 (o formato que o Whisper recebe)."""
    taxa = soundfile.info(str(wav)).samplerate
    audio, _ = soundfile.read(
        str(wav), start=round(inicio_s * taxa), stop=round(fim_s * taxa), dtype="float32"
    )
    return audio


def emendar(
    segmentos: list[Segmento], inicio_s: float, fim_s: float, ultimo_bloco: bool
) -> tuple[list[Segmento], float]:
    """Quais frases do bloco ficam e onde começa o próximo bloco.

    ``segmentos`` vem com tempos contados do começo do bloco. Devolve as frases aceitas
    (ainda com tempos do bloco) e o instante da gravação em que o próximo bloco começa.
    """
    if ultimo_bloco:
        return segmentos, fim_s
    limite = fim_s - inicio_s - MARGEM_S
    aceitos = [segmento for segmento in segmentos if segmento.fim_s <= limite]
    if aceitos:
        proximo = inicio_s + aceitos[-1].fim_s
    elif segmentos:
        # Nenhuma frase terminou a tempo: o próximo bloco começa na primeira delas.
        proximo = inicio_s + segmentos[0].inicio_s
    else:
        proximo = inicio_s + limite  # só silêncio
    # Garantia de que a transcrição sempre anda (uma frase de Whisper tem até 30 s, e um
    # bloco tem pelo menos 1 min; isto só pesa num caso que não deveria acontecer).
    if proximo <= inicio_s:
        proximo = inicio_s + limite
    return aceitos, proximo


def para_o_banco(
    segmentos: list[Segmento], deslocamento_s: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Frases e palavras no formato do banco, com os tempos contados do começo da live."""
    frases: list[dict[str, Any]] = []
    palavras: list[dict[str, Any]] = []
    for segmento in segmentos:
        frases.append(
            {
                "inicio_s": round(deslocamento_s + segmento.inicio_s, 3),
                "fim_s": round(deslocamento_s + segmento.fim_s, 3),
                "texto": segmento.texto,
            }
        )
        palavras.extend(
            {
                "palavra": palavra.texto,
                "inicio_s": round(deslocamento_s + palavra.inicio_s, 3),
                "fim_s": round(deslocamento_s + palavra.fim_s, 3),
                "probabilidade": round(palavra.probabilidade, 3),
            }
            for palavra in segmento.palavras
        )
    return frases, palavras


def caminho_da_legenda(cfg: Config, gravacao: Gravacao) -> Path:
    return cfg.pasta_transcricoes / f"{Path(gravacao.arquivo).stem}.srt"


def exportar_legenda(cfg: Config, gravacao: Gravacao, transcricao: Transcricao) -> Path:
    frases = transcricao.segmentos or legenda.frases_das_palavras(transcricao.palavras)
    return legenda.salvar_srt(caminho_da_legenda(cfg, gravacao), frases)


def transcrever(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``transcrever`` (registrado em ``sistema.servico``)."""
    contexto = contexto or Contexto()
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id) if tarefa.gravacao_id else None
    if gravacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma gravação.")
    transcricao = sessao.exec(
        select(Transcricao).where(Transcricao.gravacao_id == gravacao.id)
    ).first() or Transcricao(gravacao_id=id_de(gravacao))

    if not transcricao.concluida:
        wav = caminho_da_voz(cfg, id_de(gravacao))
        if not wav.is_file():
            # Ex.: a pasta dados/trabalho foi apagada à mão. Extrair de novo leva uns 2 min.
            log.warning("O áudio da voz de '%s' sumiu; extraindo de novo.", gravacao.arquivo)
            audio.extrair_audio(sessao, tarefa, cfg)
        _transcrever_em_blocos(sessao, cfg, gravacao, transcricao, wav, contexto)

    destino = exportar_legenda(cfg, gravacao, transcricao)
    log.info("Legenda de '%s' salva em %s.", Path(gravacao.arquivo).name, destino)


def _transcrever_em_blocos(
    sessao: Session,
    cfg: Config,
    gravacao: Gravacao,
    transcricao: Transcricao,
    wav: Path,
    contexto: Contexto,
) -> None:
    total_s = soundfile.info(str(wav)).duration
    bloco_s = cfg.transcricao.minutos_por_bloco * 60
    nome = Path(gravacao.arquivo).name
    if transcricao.transcrito_ate_s > 0:
        log.info(
            "Continuando a transcrição de '%s' a partir de %.0f min.",
            nome,
            transcricao.transcrito_ate_s / 60,
        )
    inicio_da_tarefa = time.perf_counter()
    if total_s - transcricao.transcrito_ate_s <= SOBRA_MINIMA_S:
        # Todos os blocos já estavam salvos (o PC caiu antes de marcar como concluída):
        # não vale abrir o Whisper só para isso.
        transcritor = None
    else:
        transcritor = carregar_transcritor(cfg)
    try:
        while total_s - transcricao.transcrito_ate_s > SOBRA_MINIMA_S:
            if contexto.deve_pausar():
                raise TarefaPausada(
                    f"Live em andamento: transcrição de '{nome}' pausada em "
                    f"{transcricao.transcrito_ate_s / 60:.0f} min."
                )
            inicio_s = transcricao.transcrito_ate_s
            fim_s = min(inicio_s + bloco_s, total_s)
            ultimo = total_s - fim_s <= SOBRA_MINIMA_S
            assert transcritor is not None  # só é None quando não sobrou bloco nenhum
            segmentos = transcritor.transcrever(ler_trecho(wav, inicio_s, fim_s))
            aceitos, proximo = emendar(segmentos, inicio_s, fim_s, ultimo)
            frases, palavras = para_o_banco(aceitos, inicio_s)
            # Listas novas (e não .extend): o SQLAlchemy só percebe a mudança numa coluna
            # JSON quando ela recebe outro objeto.
            transcricao.segmentos = [*(transcricao.segmentos or []), *frases]
            transcricao.palavras = [*(transcricao.palavras or []), *palavras]
            transcricao.transcrito_ate_s = total_s if ultimo else proximo
            transcricao.modelo = transcritor.nome
            sessao.add(transcricao)
            sessao.commit()  # o bloco está salvo: se o PC cair agora, continua daqui
            log.info(
                "Transcrição de '%s': %.0f de %.0f min (%d frases neste bloco).",
                nome,
                transcricao.transcrito_ate_s / 60,
                total_s / 60,
                len(frases),
            )
    finally:
        if transcritor is not None:
            transcritor.fechar()

    transcricao.concluida = True
    sessao.add(transcricao)
    sessao.commit()
    log.info(
        "Transcrição de '%s' concluída em %.1f min com o modelo %s: %d palavras.",
        nome,
        (time.perf_counter() - inicio_da_tarefa) / 60,
        transcricao.modelo,
        len(transcricao.palavras),
    )
