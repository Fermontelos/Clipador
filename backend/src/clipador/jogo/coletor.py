"""Coletor do LoL: durante a live, salva os eventos da partida a cada 2 segundos.

Roda numa thread própria dentro do serviço (etapa 4), que abre no login: assim ele já está
ouvindo quando a partida começa. Fica numa thread separada do laço da fila porque uma
tarefa da fila pode levar minutos, e o coletor não pode ficar esse tempo sem consultar.

A cada consulta (``passo``):

- sem partida, o jogo não responde: o coletor só espera, sem escrever erro no log (ele
  registra uma linha quando a partida começa e outra quando termina);
- com partida, guarda o par (horário do PC, tempo de jogo) para o alinhamento e salva os
  eventos novos que interessam (``jogo.eventos.TIPOS_GUARDADOS``), cada um com o seu papel
  nele (abate, morte, assistência) e o horário real em que aconteceu.

**Sem duplicar.** Cada evento tem um ``EventID``, que recomeça do zero a cada partida. Um
evento é identificado pelo par (início da partida, ``EventID``), protegido também por uma
regra ``UNIQUE`` no banco. Se o app reabrir no meio de uma partida, o início recalculado
difere por milissegundos do anterior: o coletor procura uma partida salva com início a
menos de 10 s e continua nela, em vez de criar outra.
"""

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from clipador.banco.conexao import nova_sessao
from clipador.banco.modelos import EventoJogo
from clipador.jogo import alinhamento, eventos
from clipador_integracoes.lol import ClienteLol, Leitura

log = logging.getLogger(__name__)

INTERVALO_S = 2.0
# Leituras com o relógio do jogo ainda parado no zero (o jogo terminando de carregar)
# dariam um início de partida errado.
TEMPO_MINIMO_S = 0.5
# O tempo de jogo voltou mais do que isto: é outra partida (sem a API ter sumido entre elas).
VOLTA_NO_TEMPO_S = 5.0


class ColetorLol:
    def __init__(self, engine: Engine, cliente: ClienteLol | None = None) -> None:
        self.engine = engine
        self.cliente = cliente or ClienteLol()
        self._zerar()

    def _zerar(self) -> None:
        self.em_partida = False
        self.partida_inicio: datetime | None = None
        self.nome_jogador: str | None = None
        self._amostras: list[tuple[float, float]] = []
        self._ultimo_tempo = 0.0
        self._salvos: set[int] = set()

    # ---------------------------------------------------------------- uma consulta

    def passo(self) -> list[EventoJogo]:
        """Consulta o jogo uma vez. Devolve os eventos que acabaram de ser salvos."""
        leitura = self.cliente.ler(com_nome=self.nome_jogador is None)
        if leitura is None:
            if self.em_partida:
                log.info("Partida encerrada (o jogo parou de responder).")
                self._zerar()
            return []
        if leitura.tempo_jogo_s < TEMPO_MINIMO_S:
            return []
        if self.em_partida and leitura.tempo_jogo_s < self._ultimo_tempo - VOLTA_NO_TEMPO_S:
            log.info("O tempo de jogo voltou: começou outra partida.")
            self._zerar()
        if not self.em_partida:
            self.em_partida = True
            log.info("Partida detectada (tempo de jogo %.0f s).", leitura.tempo_jogo_s)
        if leitura.nome_jogador:
            self.nome_jogador = leitura.nome_jogador
        self._ultimo_tempo = leitura.tempo_jogo_s
        self._amostras.append((leitura.horario_pc, leitura.tempo_jogo_s))
        del self._amostras[: -alinhamento.AMOSTRAS]  # só as recentes importam
        if self.partida_inicio is None:
            self._identificar_partida()
        return self._salvar_novos(leitura)

    def _identificar_partida(self) -> None:
        estimado = alinhamento.estimar_inicio(self._amostras)
        margem = alinhamento.TOLERANCIA_MESMA_PARTIDA
        with nova_sessao(self.engine) as sessao:
            existente = sessao.exec(
                select(EventoJogo.partida_inicio)
                .where(col(EventoJogo.partida_inicio).between(estimado - margem, estimado + margem))
                .limit(1)
            ).first()
            self.partida_inicio = existente or estimado
            self._salvos = set(
                sessao.exec(
                    select(EventoJogo.event_id).where(
                        EventoJogo.partida_inicio == self.partida_inicio
                    )
                ).all()
            )
        if existente:
            log.info(
                "Continuando a partida que começou às %s (%d eventos já salvos).",
                existente.astimezone().strftime("%H:%M:%S"),
                len(self._salvos),
            )

    def _salvar_novos(self, leitura: Leitura) -> list[EventoJogo]:
        novos = [
            evento
            for evento in leitura.eventos
            if evento.get("EventName") in eventos.TIPOS_GUARDADOS
            and isinstance(evento.get("EventID"), int)
            and evento["EventID"] not in self._salvos
        ]
        if not novos:
            return []
        inicio_atual = alinhamento.estimar_inicio(self._amostras)
        assert self.partida_inicio is not None  # definido acima, na 1ª leitura da partida
        salvos = []
        with nova_sessao(self.engine) as sessao:
            for evento in novos:
                tempo_jogo_s = float(evento.get("EventTime", 0))
                registro = EventoJogo(
                    tipo=evento["EventName"],
                    horario_real=alinhamento.horario_do_evento(inicio_atual, tempo_jogo_s),
                    tempo_jogo_s=tempo_jogo_s,
                    partida_inicio=self.partida_inicio,
                    event_id=evento["EventID"],
                    papel=eventos.papel(evento, self.nome_jogador),
                    dados=evento,
                )
                sessao.add(registro)
                try:
                    sessao.commit()
                except IntegrityError:
                    # Já estava salvo (ex.: outro coletor rodando ao mesmo tempo).
                    sessao.rollback()
                else:
                    salvos.append(registro)
                    log.info(
                        "Evento do LoL: %s aos %s de jogo%s.",
                        registro.tipo,
                        _mmss(tempo_jogo_s),
                        f" (seu: {registro.papel.value})" if registro.papel else "",
                    )
                self._salvos.add(evento["EventID"])
        return salvos

    # ---------------------------------------------------------------- laço

    def rodar(self, parar: threading.Event, intervalo_s: float = INTERVALO_S) -> None:
        """Consulta a cada ``intervalo_s`` até ``parar`` ser acionado."""
        ultimo_erro = None
        try:
            while not parar.is_set():
                inicio = time.monotonic()
                try:
                    self.passo()
                    ultimo_erro = None
                except Exception as erro:
                    # Um erro que se repete a cada 2 s entra no log uma vez só.
                    if repr(erro) != ultimo_erro:
                        log.exception("Erro no coletor do LoL; tentando de novo.")
                        ultimo_erro = repr(erro)
                parar.wait(max(0.0, intervalo_s - (time.monotonic() - inicio)))
        finally:
            self.cliente.fechar()


def iniciar_em_segundo_plano(
    engine: Engine, parar: threading.Event, criar: Callable[[Engine], ColetorLol] = ColetorLol
) -> threading.Thread:
    """Abre o coletor numa thread que termina junto com ``parar``."""
    coletor = criar(engine)
    thread = threading.Thread(target=coletor.rodar, args=(parar,), name="coletor-lol", daemon=True)
    thread.start()
    log.info("Coletor do LoL ligado (consulta o jogo a cada %.0f s).", INTERVALO_S)
    return thread


def _mmss(segundos: float) -> str:
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos}:{resto:02d}"
