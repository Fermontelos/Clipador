"""Impede o Windows de suspender o PC no meio do processamento.

Também fica ligado depois do processamento, enquanto o desligamento automático (etapa 11)
espera você ficar ocioso: senão o PC poderia dormir antes de desligar.

Usa a função ``SetThreadExecutionState`` do Windows. Dois detalhes importantes:

- o pedido vale **para a thread que chamou**: por isso o serviço ativa e desativa sempre
  da mesma thread (o laço principal);
- ele não impede a tela de desligar nem o desligamento pedido pelo usuário; só a
  suspensão automática por inatividade.
"""

import ctypes
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000  # o pedido continua valendo até ser trocado
ES_SYSTEM_REQUIRED = 0x00000001  # o sistema precisa ficar acordado


def _set_thread_execution_state(flags: int) -> int:
    return ctypes.windll.kernel32.SetThreadExecutionState(flags)


class ManterAcordado:
    """Liga e desliga o pedido de "não suspender". Chamar duas vezes não faz mal."""

    def __init__(self, funcao: Callable[[int], int] | None = None) -> None:
        self._funcao = funcao or _set_thread_execution_state
        self.ativo = False

    def ativar(self) -> None:
        if self.ativo:
            return
        if self._funcao(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) == 0:
            log.warning("O Windows recusou o pedido para não suspender o PC.")
            return
        self.ativo = True
        log.info("O PC não vai suspender (processando, ou esperando para desligar).")

    def desativar(self) -> None:
        if not self.ativo:
            return
        self._funcao(ES_CONTINUOUS)  # volta ao normal
        self.ativo = False
        log.info("Nada a fazer: o PC pode suspender normalmente.")
