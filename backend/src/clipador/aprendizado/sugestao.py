"""A sugestão do que mudar na nota (pesos e janela) e o aceite dela.

Nada muda sozinho: a sugestão é recalculada a cada vez que você olha (leva milissegundos) e
só vale depois de aceita, como uma nova versão na tabela ``PesosNota``. A próxima detecção
(etapa 8) já usa a versão mais nova; os momentos já detectados ficam como estavam.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, col, select

from clipador.aprendizado import janela, metricas, pesos
from clipador.banco.modelos import PesosNota
from clipador.config import Config
from clipador.detectores import nota

log = logging.getLogger(__name__)


class SugestaoMudouError(RuntimeError):
    """Entre ver a sugestão e aceitar, chegaram decisões novas: veja de novo."""


class NadaParaAceitarError(RuntimeError):
    pass


@dataclass
class Sugestao:
    decisoes: int
    aprovadas: int
    minimo_para_pesos: int
    versao_atual: int | None
    pesos_atuais: dict[str, float]
    pesos_novos: dict[str, float] | None = None
    precisao_antes: float | None = None
    precisao_depois: float | None = None
    sem_pesos_porque: str | None = None
    janela_atual: tuple[float, float] = (0.0, 0.0)
    janela_nova: janela.SugestaoDeJanela | None = None
    sem_janela_porque: str | None = None
    precisao_por_dia: list[dict] = field(default_factory=list)
    versoes: list[dict] = field(default_factory=list)

    @property
    def tem_sugestao(self) -> bool:
        return self.pesos_novos is not None or self.janela_nova is not None


def versao_atual(sessao: Session) -> PesosNota | None:
    return sessao.exec(select(PesosNota).order_by(col(PesosNota.versao).desc())).first()


def sugerir(sessao: Session, cfg: Config) -> Sugestao:
    lista = metricas.exemplos(sessao)
    atual = versao_atual(sessao)
    atuais = nota.pesos_atuais(sessao, cfg.deteccao)
    deteccao = nota.deteccao_atual(sessao, cfg.deteccao)
    sugestao = Sugestao(
        decisoes=len(lista),
        aprovadas=sum(e.aprovado for e in lista),
        minimo_para_pesos=pesos.MIN_DECISOES,
        versao_atual=atual.versao if atual else None,
        pesos_atuais=atuais,
        janela_atual=(deteccao.segundos_antes, deteccao.segundos_depois),
        precisao_por_dia=metricas.precisao_por_dia(lista),
        versoes=_historico(sessao),
    )
    nomes = metricas.detectores(lista)
    try:
        novos = pesos.treinar(lista, nomes, atuais)
        sugestao.precisao_antes, sugestao.precisao_depois = pesos.validacao_cruzada(
            lista, nomes, atuais
        )
        if novos != atuais:
            sugestao.pesos_novos = novos
        else:
            sugestao.sem_pesos_porque = "Os pesos aprendidos são iguais aos atuais."
    except (pesos.PoucasDecisoesError, pesos.SemSinalUtilError) as erro:
        sugestao.sem_pesos_porque = str(erro)

    desde = _janela_vale_desde(sessao)
    sugestao.janela_nova = janela.sugerir(lista, *sugestao.janela_atual, desde=desde)
    if sugestao.janela_nova is None:
        aprovados = sum(e.aprovado and (desde is None or e.detectado_em >= desde) for e in lista)
        if aprovados < janela.MIN_APROVADOS:
            sugestao.sem_janela_porque = (
                f"São {aprovados} aprovações desde a última mudança da janela; precisa de "
                f"{janela.MIN_APROVADOS}."
            )
        else:
            sugestao.sem_janela_porque = "Os seus ajustes não pedem mudança na janela."
    return sugestao


def _janela_vale_desde(sessao: Session) -> datetime | None:
    """Quando a janela atual passou a valer (``None`` = a do config, desde sempre).

    Uma versão que só mudou os pesos repete a janela da anterior: a contagem dos ajustes
    não recomeça por causa dela.
    """
    versoes = sessao.exec(select(PesosNota).order_by(col(PesosNota.versao).desc())).all()
    if not versoes or versoes[0].segundos_antes is None:
        return None
    janela_atual = (versoes[0].segundos_antes, versoes[0].segundos_depois)
    desde = versoes[0].criada_em
    for versao in versoes[1:]:
        if (versao.segundos_antes, versao.segundos_depois) != janela_atual:
            break
        desde = versao.criada_em
    return desde


def _historico(sessao: Session) -> list[dict]:
    return [
        {
            "versao": v.versao,
            "criada_em": v.criada_em,
            "pesos": v.valores,
            "precisao_antes": v.precisao_antes,
            "precisao": v.precisao,
            "decisoes": v.decisoes,
            "segundos_antes": v.segundos_antes,
            "segundos_depois": v.segundos_depois,
        }
        for v in sessao.exec(select(PesosNota).order_by(col(PesosNota.versao))).all()
    ]


def aceitar(sessao: Session, cfg: Config, decisoes_vistas: int) -> PesosNota:
    """Grava a sugestão como nova versão. ``decisoes_vistas``: quantas havia quando você a viu.

    A sugestão é calculada de novo aqui (e não recebida de fora): se chegaram decisões
    novas desde que você a viu, ela pode ter mudado, e o aceite é recusado.
    """
    sugestao = sugerir(sessao, cfg)
    if sugestao.decisoes != decisoes_vistas:
        raise SugestaoMudouError(
            f"Agora são {sugestao.decisoes} decisões (você viu a sugestão com "
            f"{decisoes_vistas}): veja a sugestão de novo antes de aceitar."
        )
    if not sugestao.tem_sugestao:
        raise NadaParaAceitarError("Não há sugestão para aceitar agora.")
    atual = versao_atual(sessao)
    nova_janela = sugestao.janela_nova
    versao = PesosNota(
        versao=(atual.versao if atual else 0) + 1,
        # Uma versão guarda tudo o que vale a partir dela: o que não mudou vem da anterior.
        valores=sugestao.pesos_novos or (dict(atual.valores) if atual else {}),
        precisao=sugestao.precisao_depois if sugestao.pesos_novos else None,
        precisao_antes=sugestao.precisao_antes if sugestao.pesos_novos else None,
        decisoes=sugestao.decisoes,
        segundos_antes=nova_janela.segundos_antes if nova_janela else _janela(atual, 0),
        segundos_depois=nova_janela.segundos_depois if nova_janela else _janela(atual, 1),
    )
    sessao.add(versao)
    sessao.commit()
    sessao.refresh(versao)
    log.info(
        "Versão %d da nota aceita: pesos %s, janela %s/%s s (%d decisões).",
        versao.versao, versao.valores, versao.segundos_antes, versao.segundos_depois,
        versao.decisoes,
    )  # fmt: skip
    return versao


def _janela(atual: PesosNota | None, qual: int) -> float | None:
    if atual is None:
        return None
    return (atual.segundos_antes, atual.segundos_depois)[qual]
