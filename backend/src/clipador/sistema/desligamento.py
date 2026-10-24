"""Desliga o PC sozinho depois do processamento da noite, sem atrapalhar se você estiver usando.

O serviço chama ``Desligamento.verificar`` a cada volta do laço (a cada ~10 s). O PC só
desliga quando **tudo** isto vale:

1. ``desligar_ao_terminar`` está ligado no config;
2. o serviço **processou uma gravação** nesta sessão, isto é, alguma tarefa da cadeia da
   noite (``extrair_audio`` a ``gerar_titulos``). Sem isso, deixar o PC parado 10 min numa
   tarde qualquer, com o app aberto, o desligaria. Um recorte pedido na revisão de manhã
   (etapa 12) não conta, pelo mesmo motivo;
3. a fila terminou: nenhuma tarefa em andamento, nenhuma gravação em andamento (a live
   conta como fila) e nenhuma tarefa pendente que vá rodar logo. Tarefas agendadas para
   daqui a mais de ``HORIZONTE`` (ex.: coleta de métricas daqui a 7 dias) não contam;
   senão o PC nunca desligaria. Também não contam as de um tipo que o serviço ainda não
   sabe executar (ex.: ``publicar``, antes da etapa 15): ficariam pendentes para sempre;
4. teclado e mouse parados há ``minutos_ocioso`` (10 min). Programas abertos não impedem;
5. a fila acabou há menos de ``espera_maxima_horas`` (4 h). Sem esse limite, se você
   estivesse usando o PC quando o processamento terminou, ele desligaria horas depois, na
   primeira vez que você saísse de perto (ex.: no almoço). Passado o limite, o app desiste
   até a próxima noite de processamento.

Fora isso, confere de novo a cada ``INTERVALO_DE_CONFERENCIA`` (1 min).

Quando tudo vale: ``shutdown /s /t 60``, uma contagem de 60 s que o Windows mostra na tela.
Com um tempo maior que 0, o Windows já fecha os programas abertos à força (o ``/f`` fica
implícito, segundo a documentação do ``shutdown``): programas abertos não impedem.
Se você mexer no mouse ou no teclado durante a contagem, o app roda ``shutdown /a``
(cancela) e volta a esperar a ociosidade. Você também pode digitar ``shutdown /a``.

Com ``modo_teste`` (ligado por padrão), em vez de desligar, só escreve no log "desligaria
agora", uma vez por noite: assim dá para conferir numa noite inteira se a regra acerta o
horário antes de deixá-la desligar de verdade.
"""

import logging
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta

from clipador.config import ConfigDesligamento

log = logging.getLogger(__name__)

INTERVALO_DE_CONFERENCIA = timedelta(minutes=1)
# Pendentes que vão rodar antes disto contam como fila (ex.: uma nova tentativa daqui a
# 2 min); depois disto, são "agendadas para o futuro" e não seguram o PC ligado.
HORIZONTE = timedelta(minutes=30)
CONTAGEM_S = 60
# Se a contagem passou disto e o serviço continua rodando, o desligamento não aconteceu
# (algo o bloqueou): o app registra e volta a esperar, em vez de ficar parado "contando".
FOLGA_DEPOIS_DA_CONTAGEM_S = 120
MENSAGEM = "Clipador: o processamento terminou. Mexa no mouse para cancelar o desligamento."


def rodar_shutdown(argumentos: list[str]) -> None:
    """Roda o ``shutdown`` do Windows (os testes trocam por um falso)."""
    subprocess.run(
        ["shutdown", *argumentos],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
    )


class Desligamento:
    def __init__(
        self,
        cfg: ConfigDesligamento,
        ocioso_s: Callable[[], float],
        shutdown: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.ocioso_s = ocioso_s
        self._shutdown = shutdown
        self.processou_algo = False
        self.contagem_desde: datetime | None = None
        self.proxima_conferencia: datetime | None = None
        self.fila_acabou_em: datetime | None = None

    def shutdown(self, argumentos: list[str]) -> None:
        # Procura ``rodar_shutdown`` na hora (e não como valor padrão do parâmetro): assim os
        # testes conseguem trocá-la por uma que falha, e nenhum desliga o PC de verdade.
        (self._shutdown or rodar_shutdown)(argumentos)

    @property
    def esperando(self) -> bool:
        """Processou e vai desligar quando você ficar ocioso.

        Enquanto isso, o serviço impede o Windows de suspender: se o tempo de suspensão do
        Windows for menor que ``minutos_ocioso``, o PC dormiria antes de desligar e ficaria
        suspenso a noite toda. (A tela pode apagar normalmente.)
        """
        return self.cfg.desligar_ao_terminar and (
            self.processou_algo or self.contagem_desde is not None
        )

    def houve_trabalho(self) -> None:
        """O serviço processou uma gravação (a cadeia da noite): ao fim da fila, vale desligar."""
        if not self.processou_algo:
            log.debug("Houve processamento: o desligamento automático fica de olho na fila.")
        self.processou_algo = True

    def verificar(self, agora: datetime, fila_pendente: bool) -> None:
        """Uma conferência. ``fila_pendente``: ainda há o que processar (ver o topo)."""
        if self.contagem_desde is not None:
            self._durante_a_contagem(agora, fila_pendente)
            return
        if not (self.cfg.desligar_ao_terminar and self.processou_algo) or fila_pendente:
            self.proxima_conferencia = None
            self.fila_acabou_em = None
            return
        if self.fila_acabou_em is None:
            self.fila_acabou_em = agora
        if agora - self.fila_acabou_em > timedelta(hours=self.cfg.espera_maxima_horas):
            log.info(
                "A fila acabou há mais de %.0f h e você seguiu usando o PC: desistindo de "
                "desligar até a próxima noite de processamento.",
                self.cfg.espera_maxima_horas,
            )
            self.processou_algo = False
            self.fila_acabou_em = None
            return
        if self.proxima_conferencia is not None and agora < self.proxima_conferencia:
            return
        self.proxima_conferencia = agora + INTERVALO_DE_CONFERENCIA
        ocioso_min = self.ocioso_s() / 60
        if ocioso_min < self.cfg.minutos_ocioso:
            log.debug(
                "Fila terminou, mas você usou o PC há %.0f min: desligamento espera.", ocioso_min
            )
            return
        motivo = f"a fila terminou e o PC está ocioso há {ocioso_min:.0f} min"
        if self.cfg.modo_teste:
            log.info("Modo de teste: desligaria agora (%s).", motivo)
            self.processou_algo = False  # uma vez por noite: só volta depois de mais trabalho
            self.fila_acabou_em = None
            return
        log.warning("Desligando o PC em %d s: %s.", CONTAGEM_S, motivo)
        try:
            self.shutdown(["/s", "/t", str(CONTAGEM_S), "/c", MENSAGEM])
        except Exception:
            log.exception("Não foi possível iniciar o desligamento.")
            return
        self.contagem_desde = agora

    def _durante_a_contagem(self, agora: datetime, fila_pendente: bool) -> None:
        passados = (agora - self.contagem_desde).total_seconds()
        if passados > CONTAGEM_S + FOLGA_DEPOIS_DA_CONTAGEM_S:
            log.error("O desligamento foi pedido, mas o PC não desligou. Voltando a esperar.")
            self.contagem_desde = None
            self.proxima_conferencia = agora + INTERVALO_DE_CONFERENCIA
            return
        mexeu = self.ocioso_s() < passados  # houve input depois de a contagem começar
        if mexeu or fila_pendente:
            motivo = "você mexeu no PC" if mexeu else "chegou trabalho novo na fila"
            try:
                self.shutdown(["/a"])
                log.warning("Desligamento cancelado: %s. Voltando a esperar.", motivo)
            except Exception:
                # Ex.: a contagem já tinha sido cancelada com "shutdown /a" à mão.
                log.info("Desligamento já não estava marcado (%s).", motivo)
            self.contagem_desde = None
            self.proxima_conferencia = agora + INTERVALO_DE_CONFERENCIA
