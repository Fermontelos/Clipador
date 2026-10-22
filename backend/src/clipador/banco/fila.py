"""Fila de tarefas retomável.

Ciclo de vida de uma tarefa::

    pendente ──pegar_proxima──> em_andamento ──concluir──> feita (cria a próxima da cadeia)
        ^                            │
        └──── falhar (tentativas < 3)┘──falhar (3ª falha ou definitiva)──> erro

- Tudo fica no banco, então desligar o PC no meio não perde trabalho: ao abrir,
  ``recuperar_interrompidas`` devolve para a fila o que estava "em andamento".
- Tarefas podem ser agendadas (``executar_a_partir_de``) e não rodam antes da hora.
- Uma tarefa que falha volta para a fila com uma pequena espera (1 min, depois 2 min),
  para um erro passageiro (arquivo ainda travado, internet caindo) não virar erro
  definitivo, e um erro permanente não ficar repetindo sem parar.
- Uma tarefa longa pode parar no meio de propósito (``TarefaPausada``, ex.: começou uma
  live): ``devolver`` a põe de volta na fila sem contar como tentativa.
- Um erro que nenhuma nova tentativa resolve (ex.: a gravação não tem a faixa da voz)
  é levantado como ``FalhaDefinitivaError`` e vai direto para "erro".
- A situação da gravação acompanha a cadeia, no mesmo commit da tarefa: vira "concluída"
  quando a última tarefa da cadeia termina e "erro" quando uma tarefa da cadeia desiste.
"""

import logging
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, col, func, select, update

from clipador.banco.modelos import Gravacao, StatusGravacao, StatusTarefa, Tarefa, TipoTarefa, agora

log = logging.getLogger(__name__)

MAX_TENTATIVAS = 3
ESPERA_ENTRE_TENTATIVAS = timedelta(minutes=1)

# Cadeia principal: ao concluir a tarefa da esquerda, a da direita é criada.
PROXIMA_DA_CADEIA: dict[TipoTarefa, TipoTarefa] = {
    TipoTarefa.EXTRAIR_AUDIO: TipoTarefa.TRANSCREVER,
    TipoTarefa.TRANSCREVER: TipoTarefa.DETECTAR,
    TipoTarefa.DETECTAR: TipoTarefa.CORTAR,
    TipoTarefa.CORTAR: TipoTarefa.GERAR_TITULOS,
}
TAREFAS_DA_CADEIA = frozenset(PROXIMA_DA_CADEIA) | frozenset(PROXIMA_DA_CADEIA.values())


@dataclass(frozen=True)
class Contexto:
    """O que quem executa a fila (o serviço) oferece a cada executor enquanto ele roda."""

    # Começou uma live? As tarefas longas conferem entre um bloco e outro e, se sim,
    # levantam ``TarefaPausada``.
    deve_pausar: Callable[[], bool] = lambda: False


class TarefaPausada(Exception):  # noqa: N818 (não é um erro: é uma pausa combinada)
    """A tarefa parou no meio de propósito e salvou o que já tinha feito.

    Ex.: a transcrição confere entre um bloco e outro se começou uma live; se começou, ela
    para para não disputar a placa de vídeo com o OBS, e continua depois de onde parou.
    """


class FalhaDefinitivaError(RuntimeError):
    """Erro que não adianta tentar de novo: a tarefa vai direto para "erro".

    Os executores levantam este erro quando o problema está na entrada e não num acaso
    (ex.: gravação sem a faixa da voz). Tentar 3 vezes só atrasaria o aviso.
    """


def adicionar(
    sessao: Session,
    tipo: TipoTarefa,
    *,
    gravacao_id: int | None = None,
    momento_id: int | None = None,
    publicacao_id: int | None = None,
    executar_a_partir_de: datetime | None = None,
) -> Tarefa:
    """Coloca uma tarefa nova na fila e devolve ela já salva."""
    tarefa = Tarefa(
        tipo=tipo,
        gravacao_id=gravacao_id,
        momento_id=momento_id,
        publicacao_id=publicacao_id,
        executar_a_partir_de=executar_a_partir_de or agora(),
    )
    sessao.add(tarefa)
    sessao.commit()
    sessao.refresh(tarefa)
    log.info("Tarefa %s (%s) adicionada à fila.", tarefa.id, tipo.value)
    return tarefa


def pegar_proxima(
    sessao: Session,
    momento: datetime | None = None,
    tipos: Collection[TipoTarefa] | None = None,
) -> Tarefa | None:
    """Pega a próxima tarefa que já pode rodar e a marca como "em andamento".

    A mais antiga primeiro (pelo horário marcado e, no empate, pela ordem de criação).
    ``tipos`` limita aos tipos que quem chama sabe executar: o serviço só pega tarefas
    que tem como fazer, e as outras esperam (em vez de falhar à toa).
    Devolve ``None`` se não houver nada para fazer agora.
    """
    momento = momento or agora()
    consulta = (
        select(Tarefa.id)
        .where(Tarefa.status == StatusTarefa.PENDENTE)
        .where(Tarefa.executar_a_partir_de <= momento)
        .order_by(col(Tarefa.executar_a_partir_de), col(Tarefa.id))
    )
    if tipos is not None:
        if not tipos:
            return None
        consulta = consulta.where(col(Tarefa.tipo).in_(list(tipos)))
    candidatas = sessao.exec(consulta).all()
    for tarefa_id in candidatas:
        # Só muda se ainda estiver pendente: se outro processo pegou antes,
        # nenhuma linha é alterada e tentamos a próxima.
        resultado = sessao.exec(
            update(Tarefa)
            .where(col(Tarefa.id) == tarefa_id)
            .where(col(Tarefa.status) == StatusTarefa.PENDENTE)
            .values(status=StatusTarefa.EM_ANDAMENTO, atualizada_em=agora())
        )
        sessao.commit()
        if resultado.rowcount == 1:
            tarefa = sessao.get(Tarefa, tarefa_id, populate_existing=True)
            log.info("Tarefa %s (%s) iniciada.", tarefa.id, tarefa.tipo.value)
            return tarefa
    return None


class EstadoInvalidoError(ValueError):
    """A tarefa não está "em andamento" (ex.: concluir duas vezes por engano)."""


def _exigir_em_andamento(tarefa: Tarefa, acao: str) -> None:
    if tarefa.status is not StatusTarefa.EM_ANDAMENTO:
        raise EstadoInvalidoError(
            f"Não dá para {acao} a tarefa {tarefa.id}: ela está '{tarefa.status.value}', "
            "e só tarefas em andamento podem ser concluídas ou falhar."
        )


def concluir(sessao: Session, tarefa: Tarefa) -> Tarefa | None:
    """Marca a tarefa como feita e cria a próxima da cadeia, se houver.

    As duas coisas são gravadas **no mesmo commit**: se o PC desligar no meio, ou as duas
    acontecem ou nenhuma. Gravar separado poderia deixar a tarefa "feita" sem a próxima,
    e a gravação pararia no meio da pipeline sem erro nenhum.

    Devolve a tarefa nova criada (ou ``None``). Levanta ``EstadoInvalidoError`` se a
    tarefa não estiver em andamento: concluir duas vezes criaria a próxima em dobro.
    """
    _exigir_em_andamento(tarefa, "concluir")
    tarefa.status = StatusTarefa.FEITA
    tarefa.ultimo_erro = None
    tarefa.atualizada_em = agora()
    sessao.add(tarefa)

    proxima = None
    proximo_tipo = PROXIMA_DA_CADEIA.get(tarefa.tipo)
    if proximo_tipo is not None:
        proxima = Tarefa(tipo=proximo_tipo, gravacao_id=tarefa.gravacao_id)
        sessao.add(proxima)
    elif tarefa.tipo in TAREFAS_DA_CADEIA:
        _mudar_gravacao(sessao, tarefa, StatusGravacao.CONCLUIDA)

    sessao.commit()
    log.info("Tarefa %s (%s) concluída.", tarefa.id, tarefa.tipo.value)
    if proxima is not None:
        sessao.refresh(proxima)
        log.info("Tarefa %s (%s) adicionada à fila.", proxima.id, proxima.tipo.value)
    return proxima


def falhar(sessao: Session, tarefa: Tarefa, erro: str, definitiva: bool = False) -> Tarefa:
    """Registra uma falha. Volta para a fila ou, na 3ª falha, vira "erro".

    ``definitiva=True`` (ver ``FalhaDefinitivaError``) manda direto para "erro".

    Levanta ``EstadoInvalidoError`` se a tarefa não estiver em andamento.
    """
    _exigir_em_andamento(tarefa, "marcar como falha")
    tarefa.tentativas += 1
    tarefa.ultimo_erro = erro
    tarefa.atualizada_em = agora()
    if definitiva or tarefa.tentativas >= MAX_TENTATIVAS:
        tarefa.status = StatusTarefa.ERRO
        if tarefa.tipo in TAREFAS_DA_CADEIA:
            _mudar_gravacao(sessao, tarefa, StatusGravacao.ERRO)
        if definitiva:
            log.error(
                "Tarefa %s (%s) falhou e não adianta tentar de novo: %s",
                tarefa.id,
                tarefa.tipo.value,
                erro,
            )
        else:
            log.error(
                "Tarefa %s (%s) desistiu após %d tentativas: %s",
                tarefa.id,
                tarefa.tipo.value,
                tarefa.tentativas,
                erro,
            )
    else:
        tarefa.status = StatusTarefa.PENDENTE
        tarefa.executar_a_partir_de = agora() + ESPERA_ENTRE_TENTATIVAS * tarefa.tentativas
        log.warning(
            "Tarefa %s (%s) falhou (tentativa %d de %d), volta para a fila: %s",
            tarefa.id,
            tarefa.tipo.value,
            tarefa.tentativas,
            MAX_TENTATIVAS,
            erro,
        )
    sessao.add(tarefa)
    sessao.commit()
    return tarefa


def devolver(sessao: Session, tarefa: Tarefa) -> Tarefa:
    """Põe de volta na fila uma tarefa que pausou (``TarefaPausada``), sem contar tentativa."""
    _exigir_em_andamento(tarefa, "devolver")
    tarefa.status = StatusTarefa.PENDENTE
    tarefa.executar_a_partir_de = agora()
    tarefa.atualizada_em = agora()
    sessao.add(tarefa)
    sessao.commit()
    log.info("Tarefa %s (%s) pausada; continua depois.", tarefa.id, tarefa.tipo.value)
    return tarefa


def refazer(sessao: Session, tarefa_id: int) -> Tarefa:
    """Devolve para a fila uma tarefa que deu erro, depois que a causa foi corrigida.

    Ex.: o ``[faixas]`` do ``config.toml`` estava errado. A tarefa volta zerada (3 novas
    tentativas), e o último erro fica guardado até ela rodar de novo.
    """
    tarefa = sessao.get(Tarefa, tarefa_id)
    if tarefa is None:
        raise LookupError(f"Não existe a tarefa {tarefa_id}.")
    if tarefa.status is not StatusTarefa.ERRO:
        raise EstadoInvalidoError(
            f"A tarefa {tarefa_id} está '{tarefa.status.value}': só tarefas com erro "
            "podem ser refeitas."
        )
    tarefa.status = StatusTarefa.PENDENTE
    tarefa.tentativas = 0
    tarefa.executar_a_partir_de = agora()
    tarefa.atualizada_em = agora()
    sessao.add(tarefa)
    if tarefa.tipo in TAREFAS_DA_CADEIA:
        _mudar_gravacao(sessao, tarefa, StatusGravacao.PROCESSANDO)
    sessao.commit()
    log.info("Tarefa %s (%s) devolvida para a fila.", tarefa.id, tarefa.tipo.value)
    return tarefa


def _mudar_gravacao(sessao: Session, tarefa: Tarefa, status: StatusGravacao) -> None:
    """Muda a situação da gravação da tarefa (sem commit: vai junto com o da tarefa)."""
    if tarefa.gravacao_id is None:
        return
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id)
    if gravacao is not None:
        gravacao.status = status
        sessao.add(gravacao)


def ha_trabalho(
    sessao: Session, agora: datetime, tipos: Collection[TipoTarefa], horizonte: timedelta
) -> bool:
    """Ainda há o que processar? (Para o desligamento automático, etapa 11.)

    Conta as tarefas em andamento e as pendentes dos ``tipos`` dados (os que o serviço sabe
    executar) que vão rodar antes de ``agora + horizonte``. As agendadas para mais longe
    (ex.: métricas daqui a 7 dias) e as de tipos sem executor não contam.
    """
    em_andamento = sessao.exec(
        select(Tarefa.id).where(Tarefa.status == StatusTarefa.EM_ANDAMENTO).limit(1)
    ).first()
    if em_andamento is not None:
        return True
    pendente = sessao.exec(
        select(Tarefa.id)
        .where(
            Tarefa.status == StatusTarefa.PENDENTE,
            col(Tarefa.tipo).in_(list(tipos)),
            Tarefa.executar_a_partir_de <= agora + horizonte,
        )
        .limit(1)
    ).first()
    return pendente is not None


def recuperar_interrompidas(sessao: Session) -> int:
    """Devolve para a fila as tarefas que estavam "em andamento" quando o app parou.

    Deve ser chamada **só pelo processo que executa a fila** (o serviço da etapa 4), uma
    vez, ao abrir e antes de começar a processar. Se outro processo (ex.: a interface web)
    chamasse isto com o serviço rodando, devolveria para a fila uma tarefa que está sendo
    processada naquele momento.

    A interrupção não conta como tentativa: a culpa foi do desligamento, não da tarefa.
    """
    resultado = sessao.exec(
        update(Tarefa)
        .where(col(Tarefa.status) == StatusTarefa.EM_ANDAMENTO)
        .values(status=StatusTarefa.PENDENTE, atualizada_em=agora())
    )
    sessao.commit()
    if resultado.rowcount:
        log.warning("%d tarefa(s) interrompida(s) voltaram para a fila.", resultado.rowcount)
    return resultado.rowcount


@dataclass
class ResumoFila:
    """Fotografia da fila, para o ``clipador status``."""

    por_status: Counter[StatusTarefa] = field(default_factory=Counter)
    em_andamento: list[Tarefa] = field(default_factory=list)
    proximas: list[Tarefa] = field(default_factory=list)
    com_erro: list[Tarefa] = field(default_factory=list)


def resumo(sessao: Session, limite: int = 10) -> ResumoFila:
    """Contagem por status, as em andamento, as próximas pendentes e as que deram erro."""
    resumo_fila = ResumoFila()
    contagens = sessao.exec(select(Tarefa.status, func.count()).group_by(col(Tarefa.status))).all()
    for status, quantidade in contagens:
        resumo_fila.por_status[status] = quantidade
    resumo_fila.em_andamento = list(
        sessao.exec(
            select(Tarefa)
            .where(Tarefa.status == StatusTarefa.EM_ANDAMENTO)
            .order_by(col(Tarefa.atualizada_em))
            .limit(limite)
        ).all()
    )
    resumo_fila.proximas = list(
        sessao.exec(
            select(Tarefa)
            .where(Tarefa.status == StatusTarefa.PENDENTE)
            .order_by(col(Tarefa.executar_a_partir_de), col(Tarefa.id))
            .limit(limite)
        ).all()
    )
    resumo_fila.com_erro = list(
        sessao.exec(
            select(Tarefa)
            .where(Tarefa.status == StatusTarefa.ERRO)
            .order_by(col(Tarefa.atualizada_em).desc())
            .limit(limite)
        ).all()
    )
    return resumo_fila
