"""Executores das tarefas ``cortar`` e ``recortar``: momentos viram clipes em ``dados/clipes/``.

``cortar`` (etapa 9, na cadeia depois de ``detectar``): corta todos os momentos da
gravação, do de maior nota para o de menor, em cada formato de ``[cortes] formatos``.

- **Retomável**: cada clipe é salvo no banco (tabela ``Clipe``) assim que fica pronto.
  Se o PC cair no meio, os clipes que já existem são pulados.
- **Pausa** entre um clipe e outro se começar uma live (``contexto.deve_pausar``).
- Cada clipe é gravado com outro nome e só renomeado no fim, para um MP4 pela metade
  nunca parecer pronto.
- Nome: ``data_nota_id_formato.mp4`` (ex.: ``2026-10-14_087_12_vertical_a.mp4``): no
  explorador de arquivos, os clipes de uma live ficam juntos, e dá para ver a nota.
- **Thumbnail**: um quadro do pico do momento (``Momento.pico_s``), em
  ``data_nota_id_thumb.jpg``, para quando o horizontal sobe como vídeo normal no YouTube.

``recortar`` (etapa 12): corta de novo **um** momento, depois que você ajusta o início ou o
fim dele na revisão. Usa as mesmas funções e substitui os arquivos.
"""

import logging
import time
from pathlib import Path

from sqlmodel import Session, col, select

from clipador import ffmpeg
from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada
from clipador.banco.modelos import Clipe, FormatoClipe, Gravacao, Momento, Tarefa, Transcricao
from clipador.config import Config
from clipador.cortes import comandos
from clipador.cortes.legenda import salvar_legenda_do_clipe

log = logging.getLogger(__name__)


def _prefixo(gravacao: Gravacao, momento: Momento) -> str:
    data = gravacao.inicio.astimezone().strftime("%Y-%m-%d")  # a data da live no seu relógio
    return f"{data}_{round(momento.nota):03d}_{momento.id}"


def caminho_do_clipe(cfg: Config, gravacao: Gravacao, momento: Momento, formato: str) -> Path:
    return cfg.pasta_clipes / f"{_prefixo(gravacao, momento)}_{formato}.mp4"


def caminho_da_thumbnail(cfg: Config, gravacao: Gravacao, momento: Momento) -> Path:
    return cfg.pasta_clipes / f"{_prefixo(gravacao, momento)}_thumb.jpg"


class Cortador:
    """O que é igual para todos os clipes de uma gravação (lido uma vez só)."""

    def __init__(self, sessao: Session, cfg: Config, gravacao: Gravacao) -> None:
        self.sessao, self.cfg, self.gravacao = sessao, cfg, gravacao
        self.arquivo = Path(gravacao.arquivo)
        if not self.arquivo.is_file():
            raise FileNotFoundError(
                f"A gravação {self.arquivo} não foi encontrada (o arquivo foi movido ou apagado?)."
            )
        self.faixas_na_gravacao = ffmpeg.inspecionar(self.arquivo).faixas_audio
        try:
            self.encoder = comandos.escolher_encoder(cfg.cortes.encoder)
        except RuntimeError as erro:
            raise FalhaDefinitivaError(str(erro)) from erro
        transcricao = sessao.exec(
            select(Transcricao).where(Transcricao.gravacao_id == gravacao.id)
        ).first()
        self.palavras = list(transcricao.palavras) if transcricao else []
        self.pasta_legendas = cfg.pasta_trabalho / str(gravacao.id)

    def cortar_momento(self, momento: Momento, contexto: Contexto, refazer: bool = False) -> int:
        """Corta o momento em cada formato. Devolve quantos clipes foram feitos agora."""
        feitos = 0
        for formato in self.cfg.cortes.formatos:
            if contexto.deve_pausar():
                raise TarefaPausada(f"Live em andamento: cortes pausados no momento {momento.id}.")
            existente = self.sessao.exec(
                select(Clipe).where(Clipe.momento_id == momento.id, Clipe.formato == formato)
            ).first()
            if existente and Path(existente.arquivo).is_file() and not refazer:
                continue  # já cortado (ex.: o PC caiu depois dele)
            destino = caminho_do_clipe(self.cfg, self.gravacao, momento, formato)
            self._cortar(momento, formato, destino)
            clipe = existente or Clipe(momento_id=momento.id, formato=FormatoClipe(formato))
            if existente and Path(existente.arquivo) != destino:
                Path(existente.arquivo).unlink(missing_ok=True)  # a nota mudou o nome
            clipe.arquivo = str(destino)
            clipe.duracao_s = ffmpeg.inspecionar(destino).duracao_s
            self.sessao.add(clipe)
            self.sessao.commit()  # este clipe está salvo: se o PC cair, continua do próximo
            feitos += 1
        if "horizontal" in self.cfg.cortes.formatos:
            self._thumbnail(momento, refazer)
        return feitos

    def _cortar(self, momento: Momento, formato: str, destino: Path) -> None:
        duracao = momento.fim_s - momento.inicio_s
        legenda = None
        if formato.startswith("vertical") and self.cfg.cortes.legendas:
            srt = self.pasta_legendas / f"legenda_{momento.id}.srt"
            if salvar_legenda_do_clipe(srt, self.palavras, momento.inicio_s, momento.fim_s):
                legenda = srt
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_name(destino.stem + ".parcial.mp4")
        try:
            ffmpeg.rodar(
                comandos.comando_do_clipe(
                    self.arquivo, momento.inicio_s, duracao, formato, self.cfg.faixas,
                    self.faixas_na_gravacao, self.cfg.cortes, self.encoder, temporario,
                    legenda=legenda.name if legenda else None,
                ),
                tempo_limite_s=comandos.tempo_limite_s(duracao),
                pasta=legenda.parent if legenda else None,
            )  # fmt: skip
            temporario.replace(destino)
        finally:
            temporario.unlink(missing_ok=True)
            if legenda:
                legenda.unlink(missing_ok=True)

    def _thumbnail(self, momento: Momento, refazer: bool) -> None:
        destino = caminho_da_thumbnail(self.cfg, self.gravacao, momento)
        if destino.is_file() and not refazer:
            return
        pico = (
            momento.pico_s if momento.pico_s is not None else (momento.inicio_s + momento.fim_s) / 2
        )
        ffmpeg.rodar(comandos.comando_da_thumbnail(self.arquivo, pico, destino), tempo_limite_s=60)


def _gravacao_da_tarefa(sessao: Session, tarefa: Tarefa) -> Gravacao:
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id) if tarefa.gravacao_id else None
    if gravacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma gravação.")
    return gravacao


def cortar(sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None) -> None:
    """Executor da tarefa ``cortar`` (registrado em ``sistema.servico``)."""
    contexto = contexto or Contexto()
    gravacao = _gravacao_da_tarefa(sessao, tarefa)
    inicio = time.perf_counter()
    cortador = Cortador(sessao, cfg, gravacao)
    momentos = sessao.exec(
        select(Momento)
        .where(Momento.gravacao_id == gravacao.id)
        .order_by(col(Momento.nota).desc(), col(Momento.inicio_s))
    ).all()
    feitos = sum(cortador.cortar_momento(momento, contexto) for momento in momentos)
    log.info(
        "Cortes de '%s': %d clipes novos de %d momentos em %.1f min (%s, %s).",
        Path(gravacao.arquivo).name,
        feitos,
        len(momentos),
        (time.perf_counter() - inicio) / 60,
        ", ".join(cfg.cortes.formatos),
        cortador.encoder,
    )


def recortar(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``recortar``: corta de novo um momento (início ou fim ajustados)."""
    momento = sessao.get(Momento, tarefa.momento_id) if tarefa.momento_id else None
    if momento is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para um momento.")
    gravacao = sessao.get(Gravacao, momento.gravacao_id)
    feitos = Cortador(sessao, cfg, gravacao).cortar_momento(
        momento, contexto or Contexto(), refazer=True
    )
    log.info("Momento %s cortado de novo: %d clipes.", momento.id, feitos)
