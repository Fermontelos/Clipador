"""Da decisão aprovada às publicações na fila (etapa 15).

- **Aprovar** um momento cria uma ``Publicacao`` pendente para cada formato aprovado com
  destino YouTube (o horizontal sobe como vídeo normal; o vertical de até 3 min, como Short)
  ou TikTok (só os verticais), e uma tarefa ``publicar`` para cada uma. No YouTube, o horário
  de publicação já sai planejado (``agenda``) para aparecer na revisão; o envio confere de
  novo na hora de subir. O TikTok não tem horário: o vídeo vai para a pasta (e, no modo
  rascunho, para o app), e você posta quando quiser.
- O envio espera ``[youtube] minutos_para_desfazer`` (10 min): até lá, desfazer ou mudar a
  decisão **cancela** as publicações pendentes (e as tarefas delas) sem nada ter saído do PC.
- Depois que o envio começa (tarefa em andamento) ou termina, a publicação **trava** a
  decisão: mudar aqui não mudaria o que já está no YouTube.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete
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
from clipador.config import FORMATOS_DO_TIKTOK, Config
from clipador.publicacao import agenda

log = logging.getLogger(__name__)

# Publicações que já saíram do PC (ou estão saindo): não dá mais para cancelar.
JA_ENVIADAS = (StatusPublicacao.ENVIADA, StatusPublicacao.AGENDADA, StatusPublicacao.PUBLICADA)
# Plataformas que o app já sabe publicar.
PUBLICAVEIS = (Plataforma.YOUTUBE, Plataforma.TIKTOK)


class EnvioComecouError(RuntimeError):
    """O serviço começou o envio enquanto você cancelava: a publicação não pode mais sair."""


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


ENVIANDO = "enviando"  # só na revisão: pendente com a tarefa em andamento
NA_PASTA = "na_pasta"  # TikTok enviado só para a pasta de exportação
RASCUNHO = "rascunho"  # TikTok na caixa de entrada do app (você finaliza no celular)


def status_visivel(sessao: Session, publicacao: Publicacao) -> str:
    """O status que a revisão mostra. Uma pendente aparece como:

    - ``enviando``, se a tarefa está em andamento (o vídeo está subindo: já não cancela);
    - ``erro``, se a tarefa falhou de vez.

    Os dois não ficam gravados na ``Publicacao``: vêm da tarefa. Assim, ``clipador refazer``
    devolve a tarefa à fila e a publicação volta a aparecer como pendente, sem mais nada a mudar.

    Uma do TikTok já enviada aparece como ``na_pasta`` ou ``rascunho`` (com o ``publish_id``).
    """
    if publicacao.status is StatusPublicacao.PENDENTE:
        tarefa = _tarefa_da(sessao, publicacao)
        if tarefa is not None and tarefa.status is StatusTarefa.EM_ANDAMENTO:
            return ENVIANDO
        if tarefa is not None and tarefa.status is StatusTarefa.ERRO:
            return StatusPublicacao.ERRO.value
    if publicacao.plataforma is Plataforma.TIKTOK and publicacao.status is StatusPublicacao.ENVIADA:
        return RASCUNHO if publicacao.id_na_plataforma else NA_PASTA
    return publicacao.status.value


def momento_travado(sessao: Session, momento_id: int) -> bool:
    return any(travada(sessao, p) for p in publicacoes_do_momento(sessao, momento_id))


def cancelar(sessao: Session, momento_id: int) -> int:
    """Apaga as publicações ainda não enviadas do momento (e as tarefas). Sem commit.

    Devolve quantas foram canceladas. Quem chama confere antes que nada está travado; mesmo
    assim, o serviço pode pegar a tarefa entre essa conferência e o cancelamento. Por isso
    uma publicação travada aqui é erro (``EnvioComecouError``, e quem chama desfaz tudo), e
    nunca é pulada: pular deixaria o vídeo subir com a decisão já apagada. E a tarefa só é
    apagada **se não estiver em andamento**, numa instrução só (como a fila faz ao pegá-la),
    para o serviço não pegá-la entre a leitura e a remoção.
    """
    canceladas = 0
    for publicacao in publicacoes_do_momento(sessao, momento_id):
        if travada(sessao, publicacao):
            raise EnvioComecouError(
                f"O envio da publicação {publicacao.id} já começou: não dá mais para cancelar."
            )
        tarefa = _tarefa_da(sessao, publicacao)
        if tarefa is not None:
            # A tarefa sai antes da publicação (ela aponta para a publicação: chave estrangeira).
            apagadas = sessao.exec(
                delete(Tarefa)
                .where(col(Tarefa.id) == tarefa.id)
                .where(col(Tarefa.status) != StatusTarefa.EM_ANDAMENTO)
            ).rowcount
            if apagadas != 1:
                raise EnvioComecouError(
                    f"O envio da publicação {publicacao.id} acabou de começar: não dá mais "
                    "para cancelar."
                )
            sessao.expunge(tarefa)  # já saiu do banco: a sessão não tenta apagá-la de novo
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
            if plataforma == Plataforma.TIKTOK and formato not in FORMATOS_DO_TIKTOK:
                continue  # a revisão já recusa; aqui é só garantia
            quando = (
                agenda.proximo_horario(comeca_em, horarios_ocupados(sessao), cfg.youtube)
                if plataforma == Plataforma.YOUTUBE
                else None  # o TikTok não tem data de publicação: você posta pelo celular
            )
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
            "Momento %s: %d publicação(ões) na fila (envio a partir de %s).",
            momento.id, len(criadas), comeca_em.astimezone().strftime("%H:%M"),
        )  # fmt: skip
    return criadas
