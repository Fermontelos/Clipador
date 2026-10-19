"""O serviço em segundo plano: o laço que roda o app sozinho.

A cada volta do laço (``passo``):

1. o monitor procura gravações que terminaram e as coloca na fila;
2. faz o backup do dia do banco, se ainda não houver um;
3. se houver uma gravação **em andamento** (a live está acontecendo), não processa nada,
   para não disputar CPU e GPU com a live;
4. senão, executa **uma** tarefa da fila, a mais antiga que já pode rodar. Tarefas
   agendadas cujo horário já passou (inclusive enquanto o PC estava desligado) rodam
   normalmente.

Entre uma volta e outra, o laço espera até ``INTERVALO`` ou até o watchdog avisar que
algo mudou na pasta de gravações.

Limite importante: uma tarefa que **já começou** não é interrompida se uma live começar
no meio dela; só a próxima espera. As tarefas longas (transcrição, etapa 6; cortes, etapa
9) devem trabalhar em blocos e checar ``Servico.ha_gravacao_em_andamento`` entre eles.

Executores: cada tipo de tarefa tem uma função que sabe executá-la, registrada em
``EXECUTORES`` pela etapa dela (a etapa 5 registrou o ``extrair_audio``). O serviço só
pega tarefas que tem como executar; as outras ficam pendentes, esperando a etapa delas.
Um executor que levanta ``FalhaDefinitivaError`` manda a tarefa direto para "erro".
Cada executor recebe um ``fila.Contexto``: as tarefas longas perguntam a ele, entre um bloco e
outro, se devem pausar (começou uma live) e, se sim, levantam ``fila.TarefaPausada``.

Quando uma gravação termina a pipeline, os arquivos temporários dela são apagados.
"""

import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session
from watchdog.observers import Observer

from clipador.banco import fila
from clipador.banco.backup import backup_do_dia
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, StatusGravacao, Tarefa, TipoTarefa, agora
from clipador.config import Config
from clipador.ingestao import audio
from clipador.ingestao.monitor import AvisoDeMudanca, MonitorDeGravacoes, registrar_gravacao
from clipador.sistema.energia import ManterAcordado
from clipador.sistema.trava import TravaDoServico
from clipador.transcricao import transcrever

log = logging.getLogger(__name__)

INTERVALO = timedelta(seconds=10)

Executor = Callable[[Session, Tarefa, Config, fila.Contexto], None]
# Cada etapa acrescenta o seu.
EXECUTORES: dict[TipoTarefa, Executor] = {
    TipoTarefa.EXTRAIR_AUDIO: audio.extrair_audio,
    TipoTarefa.TRANSCREVER: transcrever.transcrever,
}


@dataclass
class ResultadoPasso:
    """O que aconteceu numa volta do laço (útil para os testes e para o log)."""

    gravacoes_novas: list[str] = field(default_factory=list)
    pausado_por_gravacao: bool = False
    tarefa_executada: str | None = None
    tarefa_falhou: bool = False
    tarefa_pausada: bool = False


class Servico:
    def __init__(
        self,
        cfg: Config,
        executores: dict[TipoTarefa, Executor] | None = None,
        monitor: MonitorDeGravacoes | None = None,
        energia: ManterAcordado | None = None,
        relogio: Callable[[], datetime] = agora,
    ) -> None:
        self.cfg = cfg
        self.executores = EXECUTORES if executores is None else executores
        self.monitor = monitor or MonitorDeGravacoes(
            cfg.pasta_gravacoes,
            estavel_apos=timedelta(minutes=cfg.minutos_gravacao_parada),
            so_nomes_do_obs=cfg.so_nomes_do_obs,
        )
        self.energia = energia or ManterAcordado()
        self.relogio = relogio
        self.trava = TravaDoServico(cfg.pasta_dados)
        self.engine: Engine | None = None
        self._pausado = False

    # ---------------------------------------------------------------- ciclo de vida

    def iniciar(self) -> None:
        """Pega a trava, abre o banco e devolve para a fila o que ficou pela metade."""
        self.trava.adquirir()  # levanta ServicoJaRodandoError se já houver outro serviço
        try:
            self.engine = abrir_banco(self.cfg.caminho_banco, self.cfg.pasta_backups_efetiva)
            with nova_sessao(self.engine) as sessao:
                fila.recuperar_interrompidas(sessao)
        except Exception:
            self.trava.liberar()
            raise
        tipos = ", ".join(sorted(t.value for t in self.executores)) or "nenhuma ainda"
        log.info(
            "Serviço iniciado. Pasta de gravações: %s. Tarefas que ele sabe executar: %s.",
            self.cfg.pasta_gravacoes,
            tipos,
        )

    def encerrar(self) -> None:
        self.energia.desativar()
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        self.trava.liberar()
        log.info("Serviço encerrado.")

    # ---------------------------------------------------------------- uma volta do laço

    def passo(self) -> ResultadoPasso:
        if self.engine is None:
            raise RuntimeError("Chame iniciar() antes de passo().")
        resultado = ResultadoPasso()
        momento = self.relogio()

        verificacao = self.monitor.verificar(momento)
        with nova_sessao(self.engine) as sessao:
            for arquivo in verificacao.prontas:
                if registrar_gravacao(sessao, arquivo) is not None:
                    resultado.gravacoes_novas.append(arquivo.name)

        self._backup_do_dia(momento)

        if verificacao.em_andamento:
            # A live está acontecendo: nada de processar agora.
            if not self._pausado:
                nomes = ", ".join(arquivo.name for arquivo in verificacao.em_andamento)
                log.info("Gravação em andamento (%s): fila pausada até ela terminar.", nomes)
                self._pausado = True
            resultado.pausado_por_gravacao = True
            self.energia.desativar()
            return resultado
        if self._pausado:
            log.info("A gravação terminou: fila retomada.")
            self._pausado = False

        with nova_sessao(self.engine) as sessao:
            # Lê o relógio de novo: uma gravação registrada agora mesmo criou uma tarefa com
            # horário alguns milissegundos depois de "momento", e ela já deve rodar nesta volta.
            tarefa = fila.pegar_proxima(sessao, self.relogio(), tipos=self.executores.keys())
            if tarefa is None:
                self.energia.desativar()
                return resultado
            self.energia.ativar()
            resultado.tarefa_executada = tarefa.tipo.value
            contexto = fila.Contexto(deve_pausar=self.ha_gravacao_em_andamento)
            try:
                self.executores[tarefa.tipo](sessao, tarefa, self.cfg, contexto)
            except fila.TarefaPausada:
                sessao.rollback()  # o que a tarefa salvou antes de pausar já está gravado
                fila.devolver(sessao, tarefa)
                resultado.tarefa_pausada = True
            except Exception as erro:
                log.error("Tarefa %s (%s) falhou:\n%s", tarefa.id, tarefa.tipo.value,
                          traceback.format_exc())  # fmt: skip
                sessao.rollback()  # desfaz o que a tarefa deixou pela metade
                fila.falhar(
                    sessao,
                    tarefa,
                    f"{type(erro).__name__}: {erro}",
                    definitiva=isinstance(erro, fila.FalhaDefinitivaError),
                )
                resultado.tarefa_falhou = True
            else:
                fila.concluir(sessao, tarefa)
                self._limpar_se_terminou(sessao, tarefa)
        return resultado

    def _limpar_se_terminou(self, sessao: Session, tarefa: Tarefa) -> None:
        if tarefa.gravacao_id is None:
            return
        gravacao = sessao.get(Gravacao, tarefa.gravacao_id)
        if gravacao is None or gravacao.status is not StatusGravacao.CONCLUIDA:
            return
        try:
            audio.limpar_trabalho(self.cfg, tarefa.gravacao_id)
        except Exception:
            # Sobrar um arquivo temporário não é motivo para parar o serviço.
            log.exception("Não foi possível apagar os temporários da gravação %s.",
                          tarefa.gravacao_id)  # fmt: skip

    def ha_gravacao_em_andamento(self) -> bool:
        """Há uma live acontecendo agora? Para tarefas longas checarem entre um bloco e outro."""
        return bool(self.monitor.verificar(self.relogio()).em_andamento)

    def _backup_do_dia(self, momento: datetime) -> None:
        try:
            backup_do_dia(self.cfg.caminho_banco, self.cfg.pasta_backups_efetiva, momento=momento)
        except Exception:
            # Um backup que falha (ex.: pasta do Google Drive fora do ar) não pode parar o
            # serviço: fica registrado no log e é tentado de novo na próxima volta.
            log.exception("Não foi possível fazer o backup do dia.")

    # ---------------------------------------------------------------- laço

    def rodar(self, parar: threading.Event, intervalo: timedelta = INTERVALO) -> None:
        """Roda até ``parar`` ser acionado (Ctrl+C, por exemplo)."""
        self.iniciar()
        acordar = threading.Event()
        observador = None
        if self.cfg.pasta_gravacoes.is_dir():
            observador = Observer()
            observador.schedule(AvisoDeMudanca(acordar), str(self.cfg.pasta_gravacoes))
            observador.start()
        try:
            while not parar.is_set():
                try:
                    resultado = self.passo()
                except Exception:
                    # Erro fora de uma tarefa (ex.: banco travado): registra e tenta de novo.
                    log.exception("Erro numa volta do serviço; tentando de novo.")
                    resultado = ResultadoPasso()
                if resultado.tarefa_executada is not None:
                    continue  # acabou uma tarefa: já tenta a próxima, sem esperar
                self._esperar(acordar, parar, intervalo)
        finally:
            if observador is not None:
                observador.stop()
                observador.join(timeout=5)
            self.encerrar()

    @staticmethod
    def _esperar(acordar: threading.Event, parar: threading.Event, intervalo: timedelta) -> None:
        """Espera o intervalo, mas acorda antes se a pasta mudar ou se pedirem para parar.

        Espera em pedaços de meio segundo: no Windows, uma espera longa não é interrompida
        pelo Ctrl+C, e o serviço demoraria até 10 s para fechar.
        """
        limite = intervalo.total_seconds()
        esperado = 0.0
        while esperado < limite and not parar.is_set():
            if acordar.wait(0.5):
                break
            esperado += 0.5
        acordar.clear()
