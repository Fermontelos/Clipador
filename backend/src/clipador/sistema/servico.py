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

Limite importante: o serviço não interrompe uma tarefa que **já começou** se uma live
começar no meio dela; só a próxima espera. Por isso as tarefas longas (transcrição, etapa
6; cortes, etapa 9) trabalham em blocos e, entre um e outro, perguntam
``contexto.deve_pausar()``; se for sim, salvam o que fizeram e levantam
``fila.TarefaPausada`` (a transcrição já faz isso).

Executores: cada tipo de tarefa tem uma função que sabe executá-la, registrada em
``EXECUTORES`` pela etapa dela (a etapa 5 registrou o ``extrair_audio``). O serviço só
pega tarefas que tem como executar; as outras ficam pendentes, esperando a etapa delas.
Um executor que levanta ``FalhaDefinitivaError`` manda a tarefa direto para "erro".
Cada executor recebe um ``fila.Contexto``: as tarefas longas perguntam a ele, entre um bloco e
outro, se devem pausar (começou uma live) e, se sim, levantam ``fila.TarefaPausada``.

Quando uma gravação termina a pipeline, os arquivos temporários dela são apagados.

Junto com o laço, roda o coletor do LoL (etapa 7, ``jogo.coletor``), numa thread própria.

Quando a fila da noite termina, o laço também confere o desligamento automático (etapa 11,
``sistema.desligamento``), se ele foi passado (só o ``clipador iniciar`` passa).
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
from clipador.cortes import cortar
from clipador.detectores import detectar
from clipador.ingestao import audio
from clipador.ingestao.monitor import AvisoDeMudanca, MonitorDeGravacoes, registrar_gravacao
from clipador.jogo.coletor import iniciar_em_segundo_plano as iniciar_coletor_do_lol
from clipador.publicacao import publicar
from clipador.sistema.desligamento import HORIZONTE as HORIZONTE_DA_FILA
from clipador.sistema.desligamento import Desligamento
from clipador.sistema.energia import ManterAcordado
from clipador.sistema.trava import TravaDoServico
from clipador.titulos import gerar
from clipador.transcricao import transcrever

log = logging.getLogger(__name__)

INTERVALO = timedelta(seconds=10)

Executor = Callable[[Session, Tarefa, Config, fila.Contexto], None]
# Cada etapa acrescenta o seu.
EXECUTORES: dict[TipoTarefa, Executor] = {
    TipoTarefa.EXTRAIR_AUDIO: audio.extrair_audio,
    TipoTarefa.TRANSCREVER: transcrever.transcrever,
    TipoTarefa.DETECTAR: detectar.detectar,
    TipoTarefa.CORTAR: cortar.cortar,
    TipoTarefa.RECORTAR: cortar.recortar,
    TipoTarefa.GERAR_TITULOS: gerar.gerar_titulos,
    TipoTarefa.PUBLICAR: publicar.publicar,
}


@dataclass
class ResultadoPasso:
    """O que aconteceu numa volta do laço (útil para os testes e para o log)."""

    gravacoes_novas: list[str] = field(default_factory=list)
    pausado_por_gravacao: bool = False
    tarefa_executada: str | None = None
    tarefa_falhou: bool = False
    tarefa_pausada: bool = False
    motivo_da_pausa: str | None = None  # TentarDepois: por que e até quando espera


class Servico:
    def __init__(
        self,
        cfg: Config,
        executores: dict[TipoTarefa, Executor] | None = None,
        monitor: MonitorDeGravacoes | None = None,
        energia: ManterAcordado | None = None,
        relogio: Callable[[], datetime] = agora,
        desligamento: Desligamento | None = None,
    ) -> None:
        """``desligamento``: o desligamento automático (etapa 11). Fica ``None`` (desligado)
        por padrão; só o ``clipador iniciar`` (o serviço da noite) o liga. Um ``clipador
        processar`` rodado à mão nunca desliga o PC."""
        self.cfg = cfg
        self.executores = EXECUTORES if executores is None else executores
        self.monitor = monitor or MonitorDeGravacoes(
            cfg.pasta_gravacoes,
            estavel_apos=timedelta(minutes=cfg.minutos_gravacao_parada),
            so_nomes_do_obs=cfg.so_nomes_do_obs,
            ignorar_antes_de=cfg.ignorar_antes_de,
        )
        self.energia = energia or ManterAcordado()
        self.relogio = relogio
        self.desligamento = desligamento
        self._ultimo_erro_desligamento: str | None = None
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
            self._conferir_desligamento(momento, fila_pendente=True)  # a live conta como fila
            return resultado
        if self._pausado:
            log.info("A gravação terminou: fila retomada.")
            self._pausado = False

        with nova_sessao(self.engine) as sessao:
            # Lê o relógio de novo: uma gravação registrada agora mesmo criou uma tarefa com
            # horário alguns milissegundos depois de "momento", e ela já deve rodar nesta volta.
            tarefa = fila.pegar_proxima(sessao, self.relogio(), tipos=self.executores.keys())
            if tarefa is None:
                if self.desligamento is not None:
                    pendente = fila.ha_trabalho(
                        sessao, self.relogio(), self.executores.keys(), HORIZONTE_DA_FILA
                    )
                    self._conferir_desligamento(self.relogio(), fila_pendente=pendente)
                # Depois da conferência (ela pode ter acabado com a espera desta noite):
                if self.desligamento is not None and self.desligamento.esperando:
                    self.energia.ativar()  # não suspender antes de desligar (etapa 11)
                else:
                    self.energia.desativar()
                return resultado
            self.energia.ativar()
            if self.desligamento is not None and tarefa.tipo in fila.TAREFAS_DA_CADEIA:
                # Só o processamento de uma gravação (a noite) arma o desligamento. Um recorte
                # pedido na revisão de manhã (ou, depois, publicar e coletar métricas) não:
                # senão o PC desligaria na hora do almoço, ao ficar 10 min parado.
                self.desligamento.houve_trabalho()
            resultado.tarefa_executada = tarefa.tipo.value
            contexto = fila.Contexto(deve_pausar=self.ha_gravacao_em_andamento)
            try:
                self.executores[tarefa.tipo](sessao, tarefa, self.cfg, contexto)
            except fila.TarefaPausada as pausa:
                sessao.rollback()  # o que a tarefa salvou antes de pausar já está gravado
                quando = getattr(pausa, "quando", None)  # TentarDepois: volta só nessa hora
                if quando is not None:
                    log.info("Tarefa %s (%s) espera até %s: %s", tarefa.id, tarefa.tipo.value,
                             quando.astimezone().strftime("%d/%m %H:%M"), pausa)  # fmt: skip
                    resultado.motivo_da_pausa = (
                        f"espera até {quando.astimezone().strftime('%d/%m %H:%M')}: {pausa}"
                    )
                fila.devolver(sessao, tarefa, quando)
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

    def _conferir_desligamento(self, momento: datetime, fila_pendente: bool) -> None:
        if self.desligamento is None:
            return
        try:
            self.desligamento.verificar(momento, fila_pendente)
            self._ultimo_erro_desligamento = None
        except Exception as erro:
            # Um problema no desligamento (ex.: GetLastInputInfo falhou) não para a fila, e
            # um erro que se repete a cada conferência entra no log uma vez só.
            if repr(erro) != self._ultimo_erro_desligamento:
                log.exception("Erro ao conferir o desligamento automático.")
                self._ultimo_erro_desligamento = repr(erro)

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
        # O coletor do LoL roda numa thread própria: uma tarefa da fila pode levar minutos,
        # e ele precisa consultar o jogo a cada 2 s (etapa 7).
        coletor = None
        if self.cfg.lol.coletar:
            coletor = iniciar_coletor_do_lol(self.engine, parar)
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
            if coletor is not None:
                parar.set()  # se o laço saiu por erro, o coletor também precisa parar
                coletor.join(timeout=5)
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
