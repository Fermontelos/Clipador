"""O que a revisão mostra: os dias com momentos, os momentos de um dia e as estatísticas.

O "dia" de um momento é a data em que a live **começou**, no relógio do PC: uma live que
passa da meia-noite continua sendo do dia em que começou.

Só entram gravações que já saíram da pipeline (concluídas, ou que pararam num erro): uma
gravação ainda em processamento pode ter os momentos trocados pela etapa 8 a qualquer hora.
Um momento de uma gravação que parou antes dos cortes aparece sem clipes.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, col, select

from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Gravacao,
    Momento,
    Publicacao,
    StatusGravacao,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
)

# Gravações cujos momentos ainda podem mudar.
EM_PROCESSAMENTO = (StatusGravacao.NOVA, StatusGravacao.PROCESSANDO)
LARGURA_DA_FAIXA_DE_NOTA = 10


def dia_da_gravacao(gravacao: Gravacao) -> date:
    return gravacao.inicio.astimezone().date()


def _gravacoes_revisaveis(sessao: Session) -> list[Gravacao]:
    return list(
        sessao.exec(select(Gravacao).where(col(Gravacao.status).not_in(EM_PROCESSAMENTO))).all()
    )


@dataclass
class ResumoDia:
    dia: date
    momentos: int = 0
    decididos: int = 0

    @property
    def pendentes(self) -> int:
        return self.momentos - self.decididos


def dias_com_momentos(sessao: Session) -> list[ResumoDia]:
    """Os dias que têm momentos para revisar, do mais recente para o mais antigo."""
    dias: dict[date, ResumoDia] = {}
    for gravacao in _gravacoes_revisaveis(sessao):
        linhas = sessao.exec(
            select(Momento.id, Decisao.id)
            .join(Decisao, col(Decisao.momento_id) == col(Momento.id), isouter=True)
            .where(Momento.gravacao_id == gravacao.id)
        ).all()
        if not linhas:
            continue
        dia = dia_da_gravacao(gravacao)
        resumo = dias.setdefault(dia, ResumoDia(dia))
        resumo.momentos += len(linhas)
        resumo.decididos += sum(decisao_id is not None for _, decisao_id in linhas)
    return sorted(dias.values(), key=lambda r: r.dia, reverse=True)


def momentos_do_dia(sessao: Session, dia: date) -> list[Momento]:
    """Os momentos das lives que começaram no ``dia``, da maior nota para a menor."""
    ids = [g.id for g in _gravacoes_revisaveis(sessao) if dia_da_gravacao(g) == dia]
    if not ids:
        return []
    return list(
        sessao.exec(
            select(Momento)
            .where(col(Momento.gravacao_id).in_(ids))
            .order_by(col(Momento.nota).desc(), col(Momento.gravacao_id), col(Momento.inicio_s))
        ).all()
    )


def clipes_do_momento(sessao: Session, momento_id: int) -> list[Clipe]:
    return list(
        sessao.exec(
            select(Clipe).where(Clipe.momento_id == momento_id).order_by(col(Clipe.formato))
        ).all()
    )


def recorte_pendente(sessao: Session, momento_id: int) -> bool:
    """Há um novo corte do momento na fila (depois de um ajuste)?

    A publicação (etapa 15) tem de esperar ele ficar pronto: senão subiria o corte antigo.
    """
    tarefa = sessao.exec(
        select(Tarefa.id)
        .where(
            Tarefa.tipo == TipoTarefa.RECORTAR,
            Tarefa.momento_id == momento_id,
            col(Tarefa.status).in_([StatusTarefa.PENDENTE, StatusTarefa.EM_ANDAMENTO]),
        )
        .limit(1)
    ).first()
    return tarefa is not None


def publicado(sessao: Session, momento_id: int) -> bool:
    """Algum clipe do momento já foi para a publicação (em qualquer situação, até com erro)?

    Uma publicação com erro também trava a revisão: o reenvio é cuidado na etapa 15.
    """
    publicacao = sessao.exec(
        select(Publicacao.id)
        .join(Clipe, col(Clipe.id) == col(Publicacao.clipe_id))
        .where(Clipe.momento_id == momento_id)
        .limit(1)
    ).first()
    return publicacao is not None


@dataclass
class Estatisticas:
    """Dados dos gráficos da revisão (etapa 13) e da precisão do detector (etapa 14)."""

    momentos: int = 0
    aprovados: int = 0
    recusados: int = 0
    # Dos momentos que você decidiu, quantos aprovou (a precisão do detector).
    precisao: float | None = None
    # Por dia de live: momentos, aprovados e recusados.
    por_dia: list[dict] = field(default_factory=list)
    # Por faixa de nota (0-9, 10-19... 90-100, sempre as 10): momentos, aprovados e
    # recusados. Se a nota funciona, as faixas altas têm mais aprovações.
    por_nota: list[dict] = field(default_factory=list)
    # Em quantos aprovados cada formato e cada plataforma foram escolhidos.
    formatos: dict[str, int] = field(default_factory=dict)
    plataformas: dict[str, int] = field(default_factory=dict)
    # Média dos ajustes do corte (segundos; negativo = você puxou para mais cedo).
    ajuste_medio_inicio_s: float | None = None
    ajuste_medio_fim_s: float | None = None


def estatisticas(sessao: Session) -> Estatisticas:
    resultado = Estatisticas()
    gravacoes = {g.id: g for g in _gravacoes_revisaveis(sessao)}
    if not gravacoes:
        return resultado
    linhas = sessao.exec(
        select(Momento, Decisao)
        .join(Decisao, col(Decisao.momento_id) == col(Momento.id), isouter=True)
        .where(col(Momento.gravacao_id).in_(list(gravacoes)))
    ).all()
    por_dia: dict[date, Counter] = {}
    por_nota: dict[int, Counter] = {}
    formatos: Counter = Counter()
    plataformas: Counter = Counter()
    ajustes_inicio: list[float] = []
    ajustes_fim: list[float] = []
    for momento, decisao in linhas:
        dia = dia_da_gravacao(gravacoes[momento.gravacao_id])
        contagem_dia = por_dia.setdefault(dia, Counter())
        faixa = min(int(momento.nota // LARGURA_DA_FAIXA_DE_NOTA), 9) * LARGURA_DA_FAIXA_DE_NOTA
        contagem_nota = por_nota.setdefault(faixa, Counter())
        resultado.momentos += 1
        contagem_dia["momentos"] += 1
        contagem_nota["momentos"] += 1
        if decisao is None:
            continue
        chave = "aprovados" if decisao.aprovado else "recusados"
        contagem_dia[chave] += 1
        contagem_nota[chave] += 1
        if decisao.aprovado:
            resultado.aprovados += 1
            formatos.update(decisao.formatos)
            plataformas.update({p for destinos in decisao.destinos.values() for p in destinos})
        else:
            resultado.recusados += 1
        if decisao.ajuste_inicio_s is not None:
            ajustes_inicio.append(decisao.ajuste_inicio_s)
        if decisao.ajuste_fim_s is not None:
            ajustes_fim.append(decisao.ajuste_fim_s)
    decididos = resultado.aprovados + resultado.recusados
    if decididos:
        resultado.precisao = resultado.aprovados / decididos
    resultado.por_dia = [
        {"dia": dia.isoformat(), **{k: c[k] for k in ("momentos", "aprovados", "recusados")}}
        for dia, c in sorted(por_dia.items())
    ]
    resultado.por_nota = [
        {
            "faixa": f"{faixa}-{faixa + LARGURA_DA_FAIXA_DE_NOTA - 1}" if faixa < 90 else "90-100",
            **{k: c[k] for k in ("momentos", "aprovados", "recusados")},
        }
        # Todas as 10 faixas, mesmo vazias: o gráfico (etapa 13) não fica com buracos.
        for faixa, c in (
            (f, por_nota.get(f, Counter())) for f in range(0, 100, LARGURA_DA_FAIXA_DE_NOTA)
        )
    ]
    resultado.formatos = dict(formatos)
    resultado.plataformas = dict(plataformas)
    if ajustes_inicio:
        resultado.ajuste_medio_inicio_s = sum(ajustes_inicio) / len(ajustes_inicio)
    if ajustes_fim:
        resultado.ajuste_medio_fim_s = sum(ajustes_fim) / len(ajustes_fim)
    return resultado
