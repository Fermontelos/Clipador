"""A janela do corte que os seus ajustes pedem (etapa 14).

Na revisão (etapas 12 e 13), cada ajuste do corte guarda quanto você moveu o início e o
fim (``Decisao.ajuste_inicio_s``: negativo = mais cedo). A média nos momentos que você
**aprovou** diz para onde a janela padrão da etapa 8 deve ir: se você adianta o início em
3 s em média, ``segundos_antes`` passa de 10 para 13.

- Um aprovado sem ajuste conta como 0 (você aceitou o corte como estava). Recusados não
  contam: a janela de um momento ruim não diz nada.
- Só entram momentos detectados **depois** da versão aceita da janela: os de antes foram
  ajustados em relação à janela antiga. Sem isso, depois de a janela mudar você pararia de
  ajustar, a média voltaria a 0, e a janela ficaria indo e voltando.
- Precisa de ``MIN_APROVADOS`` (20) aprovações, para um ajuste isolado não mudar tudo.
- Aproximação: perto do começo da gravação, ou num clipe que bateu na duração máxima, o
  ajuste não corresponde exatamente ao contexto antes ou depois do sinal.
"""

from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from clipador.aprendizado.metricas import Exemplo

MIN_APROVADOS = 20
LIMITE_S = 60.0  # o mesmo limite de [deteccao] segundos_antes / segundos_depois
PASSO_S = 0.5  # arredonda para meio segundo (mais que isso é ruído)


@dataclass(frozen=True)
class SugestaoDeJanela:
    segundos_antes: float
    segundos_depois: float
    media_inicio_s: float  # quanto você moveu o início, em média (negativo = mais cedo)
    media_fim_s: float
    aprovados: int


def _arredondar(segundos: float) -> float:
    return min(max(round(segundos / PASSO_S) * PASSO_S, 0.0), LIMITE_S)


def sugerir(
    lista: list[Exemplo], antes_atual: float, depois_atual: float, desde: datetime | None
) -> SugestaoDeJanela | None:
    """A janela sugerida, ou ``None`` se faltam aprovações ou se ela não muda."""
    aprovados = [e for e in lista if e.aprovado and (desde is None or e.detectado_em >= desde)]
    if len(aprovados) < MIN_APROVADOS:
        return None
    media_inicio = mean(e.ajuste_inicio_s or 0.0 for e in aprovados)
    media_fim = mean(e.ajuste_fim_s or 0.0 for e in aprovados)
    sugestao = SugestaoDeJanela(
        segundos_antes=_arredondar(antes_atual - media_inicio),
        segundos_depois=_arredondar(depois_atual + media_fim),
        media_inicio_s=round(media_inicio, 2),
        media_fim_s=round(media_fim, 2),
        aprovados=len(aprovados),
    )
    if (sugestao.segundos_antes, sugestao.segundos_depois) == (antes_atual, depois_atual):
        return None  # os seus ajustes se compensam: nada a mudar
    return sugestao
