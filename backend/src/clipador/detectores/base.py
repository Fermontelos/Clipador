"""A interface comum dos detectores: cada um é um plugin.

Um detector olha **um** aspecto da live (o volume da voz, o que você fala, os eventos do
jogo) e devolve **sinais**: trechos da gravação com uma força de 0 a 1 e o motivo
("voz alta (+18 dB)"). Quem junta os sinais em momentos e dá a nota é ``detectores.nota``.

O roteiro previa ``Detector.detectar(gravacao) -> list[Momento]``; o detector devolve
sinais, e não momentos, porque um momento bom costuma juntar vários aspectos (um grito
**e** um pentakill), e é a nota que pesa cada um (ADR 0005). Para acrescentar um detector
(outro jogo, o chat da Twitch), basta uma classe nova com ``nome``, ``disponivel`` e
``detectar``, registrada em ``detectores.detectar.DETECTORES``, e uma seção no
``[deteccao]`` do config.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from clipador.banco.modelos import EventoJogo, Gravacao


@dataclass(frozen=True)
class Sinal:
    """Um trecho que chamou a atenção de um detector."""

    detector: str
    inicio_s: float
    fim_s: float
    forca: float  # de 0 a 1
    motivo: str  # curto, para a revisão: "grito (+18 dB)", "pentakill seu"

    def __post_init__(self) -> None:
        if not 0 <= self.forca <= 1:
            raise ValueError(f"A força de um sinal vai de 0 a 1 (veio {self.forca}).")
        if self.fim_s < self.inicio_s:
            raise ValueError("O sinal termina antes de começar.")


@dataclass
class Entrada:
    """Tudo o que os detectores podem usar de uma gravação (lido do banco uma vez só)."""

    gravacao: Gravacao
    duracao_s: float
    voz_wav: Path | None = None  # áudio da voz (etapa 5)
    palavras: list[dict[str, Any]] = field(default_factory=list)  # transcrição (etapa 6)
    eventos: list[tuple[EventoJogo, float]] = field(default_factory=list)  # (evento, segundo)


class Detector(Protocol):
    nome: str

    def disponivel(self, entrada: Entrada) -> bool:
        """Este detector tem com o que trabalhar nesta gravação?

        Ex.: o do LoL, numa live de outro jogo, não tem. Um detector indisponível sai da
        conta da nota (senão nenhuma live sem LoL passaria de 75).
        """
        ...

    def detectar(self, entrada: Entrada) -> list[Sinal]: ...
