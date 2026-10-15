"""Fila de tarefas retomável.

Ciclo de vida de uma tarefa::

    pendente ──pegar_proxima──> em_andamento ──concluir──> feita (cria a próxima da cadeia)
        ^                            │
        └──── falhar (tentativas < 3)┘──falhar (3ª falha)──> erro

- Tudo fica no banco, então desligar o PC no meio não perde trabalho: ao abrir,
  ``recuperar_interrompidas`` devolve para a fila o que estava "em andamento".
- Tarefas podem ser agendadas (``executar_a_partir_de``) e não rodam antes da hora.
- Uma tarefa que falha volta para a fila com uma pequena espera (1 min, depois 2 min),
  para um erro passageiro (arquivo ainda travado, internet caindo) não virar erro
  definitivo, e um erro permanente não ficar repetindo sem parar.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, col, select, update

from clipador.banco.modelos import StatusTarefa, Tarefa, TipoTarefa, agora

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


def pegar_proxima(sessao: Session, momento: datetime | None = None) -> Tarefa | None:
    """Pega a próxima tarefa que já pode rodar e a marca como "em andamento".

    A mais antiga primeiro (pelo horário marcado e, no empate, pela ordem de criação).
    Devolve ``None`` se não houver nada para fazer agora.
    """
    momento = momento or agora()
    candidatas = sessao.exec(
        select(Tarefa.id)
        .where(Tarefa.status == StatusTarefa.PENDENTE)
        .where(Tarefa.executar_a_partir_de <= momento)
        .order_by(col(Tarefa.executar_a_partir_de), col(Tarefa.id))
    ).all()
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


def concluir(sessao: Session, tarefa: Tarefa) -> Tarefa | None:
    """Marca a tarefa como feita e cria a próxima da cadeia, se houver.

    As duas coisas são gravadas **no mesmo commit**: se o PC desligar no meio, ou as duas
    acontecem ou nenhuma. Gravar separado poderia deixar a tarefa "feita" sem a próxima,
    e a gravação pararia no meio da pipeline sem erro nenhum.

    Devolve a tarefa nova criada (ou ``None``).
    """
    tarefa.status = StatusTarefa.FEITA
    tarefa.ultimo_erro = None
    tarefa.atualizada_em = agora()
    sessao.add(tarefa)

    proxima = None
    proximo_tipo = PROXIMA_DA_CADEIA.get(tarefa.tipo)
    if proximo_tipo is not None:
        proxima = Tarefa(tipo=proximo_tipo, gravacao_id=tarefa.gravacao_id)
        sessao.add(proxima)

    sessao.commit()
    log.info("Tarefa %s (%s) concluída.", tarefa.id, tarefa.tipo.value)
    if proxima is not None:
        sessao.refresh(proxima)
        log.info("Tarefa %s (%s) adicionada à fila.", proxima.id, proxima.tipo.value)
    return proxima


def falhar(sessao: Session, tarefa: Tarefa, erro: str) -> Tarefa:
    """Registra uma falha. Volta para a fila ou, na 3ª falha, vira "erro"."""
    tarefa.tentativas += 1
    tarefa.ultimo_erro = erro
    tarefa.atualizada_em = agora()
    if tarefa.tentativas >= MAX_TENTATIVAS:
        tarefa.status = StatusTarefa.ERRO
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
    for status in sessao.exec(select(Tarefa.status)).all():
        resumo_fila.por_status[status] += 1
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
