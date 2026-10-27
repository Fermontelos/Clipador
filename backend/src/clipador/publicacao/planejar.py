"""Da decisão aprovada às publicações na fila (etapa 15).

- **Aprovar** um momento cria uma ``Publicacao`` pendente para cada formato aprovado com
  destino YouTube (o horizontal sobe como vídeo normal; o vertical, como Short) e uma
  tarefa ``publicar`` para cada uma. O horário de publicação já sai planejado (``agenda``)
  para aparecer na revisão; o envio confere de novo na hora de subir.
- O envio espera ``[youtube] minutos_para_desfazer`` (10 min): até lá, desfazer ou mudar a
  decisão **cancela** as publicações pendentes (e as tarefas delas) sem nada ter saído do PC.
- Depois que o envio começa (tarefa em andamento) ou termina, a publicação **trava** a
  decisão: mudar aqui não mudaria o que já está no YouTube.
- O TikTok (etapa 16) ainda não publica: o destino fica salvo na decisão, e as publicações
  dele serão criadas quando a etapa 16 chegar.
"""

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Momento,
    Plataforma,
    Publicacao,
    StatusPublicacao,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
    agora,
)
from clipador.config import Config
from clipador.publicacao import agenda

log = logging.getLogger(__name__)

# Publicações que já saíram do PC (ou estão saindo): não dá mais para cancelar.
JA_ENVIADAS = (StatusPublicacao.ENVIADA, StatusPublicacao.AGENDADA, StatusPublicacao.PUBLICADA)
# Plataformas que o app já sabe publicar.
PUBLICAVEIS = (Plataforma.YOUTUBE,)


def publicacoes_do_momento(sessao: Session, momento_id: int) -> list[Publicacao]:
    return list(
        sessao.exec(
            select(Publicacao)
            .join(Clipe, col(Clipe.id) == col(Publicacao.clipe_id))
            .where(Clipe.momento_id == momento_id)
            .order_by(col(Publicacao.id))
        ).all()
    )


def _tarefa_da(sessao: Session, publicacao: Publicacao) -> Tarefa | None:
    return sessao.exec(
        select(Tarefa).where(
            Tarefa.tipo == TipoTarefa.PUBLICAR, Tarefa.publicacao_id == publicacao.id
        )
    ).first()


def travada(sessao: Session, publicacao: Publicacao) -> bool:
    """Já foi enviada, ou o envio está acontecendo agora."""
    if publicacao.status in JA_ENVIADAS:
        return True
    tarefa = _tarefa_da(sessao, publicacao)
    return tarefa is not None and tarefa.status is StatusTarefa.EM_ANDAMENTO


def status_visivel(sessao: Session, publicacao: Publicacao) -> StatusPublicacao:
    """O status que a revisão mostra: uma pendente cuja tarefa falhou de vez aparece como erro.

    O erro não fica gravado na ``Publicacao``: ``clipador refazer`` devolve a tarefa à fila,
    e ela volta a aparecer como pendente sem mais nada a mudar.
    """
    if publicacao.status is StatusPublicacao.PENDENTE:
        tarefa = _tarefa_da(sessao, publicacao)
        if tarefa is not None and tarefa.status is StatusTarefa.ERRO:
            return StatusPublicacao.ERRO
    return publicacao.status


def momento_travado(sessao: Session, momento_id: int) -> bool:
    return any(travada(sessao, p) for p in publicacoes_do_momento(sessao, momento_id))


def cancelar(sessao: Session, momento_id: int) -> int:
    """Apaga as publicações ainda não enviadas do momento (e as tarefas). Sem commit.

    Devolve quantas foram canceladas. Quem chama confere antes que nada está travado.
    """
    canceladas = 0
    for publicacao in publicacoes_do_momento(sessao, momento_id):
        if travada(sessao, publicacao):
            continue
        tarefa = _tarefa_da(sessao, publicacao)
        if tarefa is not None:
            sessao.delete(tarefa)
            # A tarefa aponta para a publicação (chave estrangeira): ela tem de sair do banco
            # antes. Sem este flush, o SQLAlchemy pode apagar as duas na ordem errada.
            sessao.flush()
        sessao.delete(publicacao)
        sessao.flush()
        canceladas += 1
    return canceladas


def horarios_ocupados(sessao: Session, sem: int | None = None) -> list[datetime]:
    """Os horários já marcados no YouTube (pendentes e agendadas), menos a publicação ``sem``."""
    linhas = sessao.exec(
        select(Publicacao.id, Publicacao.agendada_para).where(
            Publicacao.plataforma == Plataforma.YOUTUBE,
            col(Publicacao.status).in_([StatusPublicacao.PENDENTE, *JA_ENVIADAS]),
            col(Publicacao.agendada_para).is_not(None),
        )
    ).all()
    return [quando for id_, quando in linhas if id_ != sem]


def planejar(sessao: Session, momento: Momento, decisao: Decisao, cfg: Config) -> list[Publicacao]:
    """Cria as publicações da decisão aprovada (sem commit). Ver o topo do arquivo."""
    if not decisao.aprovado:
        return []
    clipes = {
        clipe.formato.value: clipe
        for clipe in sessao.exec(select(Clipe).where(Clipe.momento_id == momento.id)).all()
    }
    agora_ = agora()
    comeca_em = agora_ + timedelta(minutes=cfg.youtube.minutos_para_desfazer)
    criadas = []
    for formato, plataformas in decisao.destinos.items():
        clipe = clipes.get(formato)
        for plataforma in plataformas:
            if clipe is None or plataforma not in PUBLICAVEIS:
                continue
            quando = agenda.proximo_horario(comeca_em, horarios_ocupados(sessao), cfg.youtube)
            publicacao = Publicacao(
                clipe_id=clipe.id, plataforma=Plataforma(plataforma), agendada_para=quando
            )
            sessao.add(publicacao)
            sessao.flush()  # gera o id (e o horário passa a contar como ocupado)
            sessao.add(
                Tarefa(
                    tipo=TipoTarefa.PUBLICAR,
                    gravacao_id=momento.gravacao_id,
                    momento_id=momento.id,
                    publicacao_id=publicacao.id,
                    executar_a_partir_de=comeca_em,
                )
            )
            criadas.append(publicacao)
    if criadas:
        log.info(
            "Momento %s: %d publicação(ões) no YouTube na fila (envio a partir de %s).",
            momento.id, len(criadas), comeca_em.astimezone().strftime("%H:%M"),
        )  # fmt: skip
    return criadas
