"""O Whisper (faster-whisper) na placa de vídeo, com plano B se faltar memória.

Escolhas, e por quê:

- **Modelo ``medium`` em ``int8``**: o melhor que cabe nos 4 GB da GTX 1050 Ti com folga
  (medido: cerca de 1,6 GB durante a transcrição, além de ~1 GB que a tela já usa).
  Velocidade medida nesta placa: só voz, 13 vezes o tempo real (91 s em 7 s); com música e
  jogo misturados, 3 vezes (26 min em 8,5 min). Uma live de 3 h leva de 15 a 60 min. Se
  faltar memória (ao carregar ou no meio), troca para o ``modelo_reserva`` (``small``) e
  continua. Sem placa de vídeo, usa o processador.
- **Vocabulário como ``hotwords``**: o faster-whisper põe os ``hotwords`` no começo de
  **toda** janela de 30 s. O ``initial_prompt``, que o roteiro previa, só vale para a
  primeira janela quando ``condition_on_previous_text`` está desligado (conferido no
  código do faster-whisper 1.2.1). Os dois cortam o texto em 223 tokens.
- **``condition_on_previous_text=False``**: com ele ligado, o Whisper usa o texto anterior
  como contexto e, em áudios longos, às vezes entra em loop repetindo uma frase.
- **Filtro de silêncio (``vad_filter``)**: o Whisper só recebe os trechos com fala. Além de
  mais rápido, evita que ele "invente" frases no silêncio (alucinação).
- **O áudio entra já lido** (um ``numpy.ndarray`` de 16 kHz), e não pelo caminho do arquivo:
  assim a transcrição pode ser feita em blocos, e o faster-whisper não precisa do PyAV para
  decodificar (a versão 1.2.1 é incompatível com o PyAV 19).

As bibliotecas da NVIDIA (cuBLAS e cuDNN da linha CUDA 12) vêm dos pacotes
``nvidia-cublas-cu12`` e ``nvidia-cudnn-cu12`` (grupo ``gpu`` do ``pyproject.toml``). O
Windows só as encontra se a pasta delas for registrada antes de carregar o modelo
(``preparar_bibliotecas_da_nvidia``).
"""

from __future__ import annotations

import functools
import gc
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from clipador.config import ConfigTranscricao

log = logging.getLogger(__name__)

IDIOMA = "pt"


class ModeloNaoBaixadoError(RuntimeError):
    """O modelo ainda não está no PC e não deu para baixar (ex.: sem internet)."""


@dataclass(frozen=True)
class Palavra:
    texto: str
    inicio_s: float
    fim_s: float
    probabilidade: float


@dataclass(frozen=True)
class Segmento:
    """Uma frase. Os tempos são contados do começo do áudio que foi transcrito."""

    inicio_s: float
    fim_s: float
    texto: str
    palavras: tuple[Palavra, ...]


class Transcritor(Protocol):
    """O que a etapa 6 precisa de um transcritor. Os testes usam um falso, sem GPU."""

    @property
    def nome(self) -> str:
        """Modelo e onde roda, para o banco e o log (ex.: "medium (cuda, int8)")."""
        ...

    def transcrever(self, audio: np.ndarray) -> list[Segmento]: ...

    def fechar(self) -> None:
        """Libera a memória (da placa de vídeo, se for o caso)."""
        ...


@functools.cache  # uma vez por processo basta
def preparar_bibliotecas_da_nvidia() -> list[Path]:
    """Mostra ao Windows onde estão as DLLs do CUDA instaladas pelo uv (``nvidia/*/bin``)."""
    pastas = sorted((Path(sys.prefix) / "Lib" / "site-packages" / "nvidia").glob("*/bin"))
    for pasta in pastas:
        os.add_dll_directory(str(pasta))
        # Algumas DLLs carregam outras pelo PATH, e não pelas pastas registradas acima.
        if str(pasta) not in os.environ["PATH"]:
            os.environ["PATH"] = f"{pasta}{os.pathsep}{os.environ['PATH']}"
    return pastas


def tem_placa_de_video() -> bool:
    import ctranslate2

    return ctranslate2.get_cuda_device_count() > 0


def faltou_memoria(erro: BaseException) -> bool:
    return "out of memory" in str(erro).lower()


def texto_do_vocabulario(vocabulario: list[str]) -> str | None:
    termos = [termo.strip() for termo in vocabulario if termo.strip()]
    return ", ".join(termos) or None


class TranscritorWhisper:
    """O faster-whisper de verdade. Use ``carregar`` para criar."""

    def __init__(
        self, cfg: ConfigTranscricao, pasta_modelos: Path, dispositivo: str, modelo: str
    ) -> None:
        self.cfg = cfg
        self.pasta_modelos = pasta_modelos
        self.dispositivo = dispositivo
        self.hotwords = texto_do_vocabulario(cfg.vocabulario)
        self._modelo: Any = None  # o WhisperModel, carregado em _abrir
        self._nome_modelo = ""
        self._abrir(modelo)

    @property
    def nome(self) -> str:
        return f"{self._nome_modelo} ({self.dispositivo}, int8)"

    def _abrir(self, modelo: str) -> None:
        # Evita dois avisos inofensivos do Hugging Face no log a cada abertura.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        from faster_whisper import WhisperModel
        from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

        inicio = time.perf_counter()
        # Na primeira vez, baixa o modelo (o medium tem ~1,5 GB) para pasta_modelos.
        try:
            self._modelo = WhisperModel(
                modelo,
                device=self.dispositivo,
                compute_type="int8",
                download_root=str(self.pasta_modelos),
            )
        except (LocalEntryNotFoundError, HfHubHTTPError) as erro:
            raise ModeloNaoBaixadoError(
                f"O modelo '{modelo}' do Whisper ainda não está no PC e não deu para baixá-lo "
                f"para {self.pasta_modelos} (sem internet?). A tarefa tenta de novo sozinha."
            ) from erro
        self._nome_modelo = modelo
        log.info("Whisper carregado: %s em %.0f s.", self.nome, time.perf_counter() - inicio)
        self._avisar_vocabulario_longo()

    def _avisar_vocabulario_longo(self) -> None:
        if self.hotwords is None:
            return
        tokens = len(self._modelo.hf_tokenizer.encode(" " + self.hotwords).ids)
        # O faster-whisper corta os hotwords em max_length // 2 - 1 = 223 tokens.
        if tokens > 223:
            log.warning(
                "O vocabulário tem %d tokens; o Whisper só usa os primeiros 223. Deixe "
                "em [transcricao] vocabulario só os termos que mais aparecem.",
                tokens,
            )

    def transcrever(self, audio: np.ndarray) -> list[Segmento]:
        try:
            return self._transcrever(audio)
        except RuntimeError as erro:
            reserva = self.cfg.modelo_reserva
            if not faltou_memoria(erro) or not reserva or reserva == self._nome_modelo:
                raise
            log.warning(
                "Faltou memória na placa de vídeo com o modelo %s; trocando para o %s.",
                self._nome_modelo,
                reserva,
            )
            self.fechar()
            self._abrir(reserva)
            return self._transcrever(audio)

    def _transcrever(self, audio: np.ndarray) -> list[Segmento]:
        segmentos, _info = self._modelo.transcribe(
            audio,
            language=IDIOMA,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=False,
            hotwords=self.hotwords,
        )
        # "segmentos" é um gerador: a transcrição acontece enquanto ele é percorrido.
        return [
            Segmento(
                inicio_s=segmento.start,
                fim_s=segmento.end,
                texto=segmento.text.strip(),
                palavras=tuple(
                    Palavra(p.word.strip(), p.start, p.end, p.probability)
                    for p in segmento.words or ()
                    if p.word.strip()
                ),
            )
            for segmento in segmentos
        ]

    def fechar(self) -> None:
        """Tira o modelo da placa de vídeo (regra: uma tarefa de GPU por vez)."""
        if self._modelo is not None:
            self._modelo.model.unload_model()
            self._modelo = None
            gc.collect()


def carregar(cfg: ConfigTranscricao, pasta_modelos: Path) -> TranscritorWhisper:
    """Abre o Whisper no melhor lugar disponível (placa de vídeo ou processador)."""
    preparar_bibliotecas_da_nvidia()
    dispositivo = cfg.dispositivo
    if dispositivo == "auto":
        dispositivo = "cuda" if tem_placa_de_video() else "cpu"
        if dispositivo == "cpu":
            log.warning("Nenhuma placa de vídeo NVIDIA encontrada: transcrevendo no processador.")
    elif dispositivo == "cuda" and not tem_placa_de_video():
        raise RuntimeError(
            '[transcricao] dispositivo = "cuda" no config.toml, mas nenhuma placa de vídeo '
            'NVIDIA foi encontrada (confira o driver). Use "auto" para cair no processador.'
        )
    try:
        return TranscritorWhisper(cfg, pasta_modelos, dispositivo, cfg.modelo)
    except RuntimeError as erro:
        reserva = cfg.modelo_reserva
        if not faltou_memoria(erro) or not reserva or reserva == cfg.modelo:
            raise
        log.warning(
            "Faltou memória na placa de vídeo para o modelo %s; usando o %s.", cfg.modelo, reserva
        )
        gc.collect()
        return TranscritorWhisper(cfg, pasta_modelos, dispositivo, reserva)
