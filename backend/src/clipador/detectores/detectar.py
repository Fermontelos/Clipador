"""Executor da tarefa ``detectar``: acha os momentos de uma gravação e salva com a nota.

Junta tudo o que as etapas anteriores produziram (o áudio da voz, a transcrição e os
eventos do LoL), roda os detectores ligados e salva os melhores momentos na tabela
``Momento``, com a nota, o motivo e a força de cada detector (``sinais``, que a etapa 14
usa para aprender os pesos).

Rodar de novo (``clipador refazer``) substitui os momentos da gravação, desde que nenhum
deles já tenha clipe ou decisão sua: aí não dá, para não perder a revisão.
"""

import logging
import time
from pathlib import Path

import soundfile
from sqlmodel import Session, col, delete, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError
from clipador.banco.modelos import Clipe, Decisao, Gravacao, Momento, Tarefa, Transcricao, id_de
from clipador.config import Config
from clipador.detectores import nota
from clipador.detectores.base import Detector, Entrada, Sinal
from clipador.detectores.fala import Fala
from clipador.detectores.lol import Lol
from clipador.detectores.volume_voz import VolumeVoz
from clipador.ingestao import audio
from clipador.jogo import alinhamento

log = logging.getLogger(__name__)

# Os plugins: nome (o mesmo da seção em [deteccao] do config) -> classe.
DETECTORES = {"volume_voz": VolumeVoz, "fala": Fala, "lol": Lol}


def criar_detectores(cfg: Config) -> list[Detector]:
    detectores = []
    for nome, classe in DETECTORES.items():
        config_do_detector = getattr(cfg.deteccao, nome)
        if config_do_detector.ligado:
            detectores.append(classe(config_do_detector))
    return detectores


def montar_entrada(sessao: Session, cfg: Config, gravacao: Gravacao, tarefa: Tarefa) -> Entrada:
    wav = audio.caminho_da_voz(cfg, id_de(gravacao))
    if not wav.is_file():
        # Ex.: a pasta dados/trabalho foi apagada à mão. Extrair de novo leva uns 2 min.
        log.warning("O áudio da voz de '%s' sumiu; extraindo de novo.", gravacao.arquivo)
        audio.extrair_audio(sessao, tarefa, cfg)
    transcricao = sessao.exec(
        select(Transcricao).where(Transcricao.gravacao_id == gravacao.id)
    ).first()
    return Entrada(
        gravacao=gravacao,
        duracao_s=gravacao.duracao_s or soundfile.info(str(wav)).duration,
        voz_wav=wav,
        palavras=list(transcricao.palavras) if transcricao else [],
        eventos=alinhamento.eventos_da_gravacao(sessao, gravacao),
    )


def _apagar_momentos_antigos(sessao: Session, gravacao_id: int) -> None:
    ids = list(sessao.exec(select(Momento.id).where(Momento.gravacao_id == gravacao_id)).all())
    if not ids:
        return
    revisados = (
        sessao.exec(select(Decisao.id).where(col(Decisao.momento_id).in_(ids)).limit(1)).first()
        or sessao.exec(select(Clipe.id).where(col(Clipe.momento_id).in_(ids)).limit(1)).first()
    )
    if revisados:
        raise FalhaDefinitivaError(
            f"A gravação {gravacao_id} já tem momentos cortados ou revisados; detectar de novo "
            "apagaria a sua revisão."
        )
    sessao.exec(delete(Momento).where(col(Momento.id).in_(ids)))


def detectar(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``detectar`` (registrado em ``sistema.servico``).

    Não faz commit: o ``fila.concluir`` grava os momentos junto com a conclusão. Leva
    segundos (a parte mais pesada, ler o WAV de uma live de 3 h, leva uns 10 s), então não
    precisa pausar no meio por causa de uma live.
    """
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id) if tarefa.gravacao_id else None
    if gravacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma gravação.")
    inicio = time.perf_counter()
    entrada = montar_entrada(sessao, cfg, gravacao, tarefa)

    sinais: list[Sinal] = []
    disponiveis: list[str] = []
    for detector in criar_detectores(cfg):
        if detector.disponivel(entrada):
            disponiveis.append(detector.nome)
            sinais.extend(detector.detectar(entrada))
        else:
            log.info("Detector %s sem dados nesta gravação (fica fora da nota).", detector.nome)
    # O que você aceitou na etapa 14 (pesos e janela do corte) vale no lugar do config.
    pesos = nota.pesos_atuais(sessao, cfg.deteccao)
    deteccao = nota.deteccao_atual(sessao, cfg.deteccao)
    momentos = nota.calcular(sinais, disponiveis, pesos, deteccao, entrada.duracao_s)

    _apagar_momentos_antigos(sessao, id_de(gravacao))
    for momento in momentos:
        sessao.add(
            Momento(
                gravacao_id=id_de(gravacao),
                inicio_s=round(momento.inicio_s, 2),
                fim_s=round(momento.fim_s, 2),
                pico_s=round(momento.pico_s, 2),
                sinais={nome: round(forca, 3) for nome, forca in momento.forcas.items()},
                nota=round(momento.nota, 1),
                motivo=momento.motivo,
            )
        )
    melhor = f"; a melhor: {momentos[0].nota:.0f} ({momentos[0].motivo})" if momentos else ""
    log.info(
        "Detecção de '%s' em %.1f s: %d sinais, %d momentos (detectores: %s)%s.",
        Path(gravacao.arquivo).name,
        time.perf_counter() - inicio,
        len(sinais),
        len(momentos),
        ", ".join(disponiveis) or "nenhum",
        melhor,
    )
