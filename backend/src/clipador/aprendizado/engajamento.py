"""A nota aprende com o público: pesos pelo engajamento previsto (etapa 17, ADR 0012).

**O modelo.** O mesmo da etapa 14 (ADR 0009), com outra pergunta. Cada corte publicado com a
métrica de 7 dias é um exemplo: as entradas são as forças dos detectores no momento e a
resposta é "o índice ficou acima da mediana do grupo?" (``engajamento.indice``). Um momento
publicado no YouTube e no TikTok vale dois exemplos (cada rede tem o seu público).

**A mistura.** Os pesos aprendidos não substituem os do seu gosto: a nota final é
``(1 − mistura) × gosto + mistura × engajamento``, com ``[engajamento] mistura`` (0,5). Assim
o app não passa a sugerir só o que viraliza e você nunca aprovaria. O "gosto" aqui é a parte
do gosto da versão atual (``sugestao.componentes``), e não os pesos atuais, que já podem ser
uma mistura: aceitar duas vezes não mistura o que já estava misturado.

**Comparação justa.** Validação cruzada em 5 partes, como na etapa 14: em cada parte, os
pesos do engajamento são aprendidos com as outras quatro, misturados, e os dois conjuntos
(os atuais e os misturados) são medidos na parte que o modelo não viu. A medida é a precisão
no topo: dos cortes que a nota põe na metade de cima de cada dia, quantos ficaram acima da
mediana do canal.

**Só com 50 cortes.** Engajamento varia muito (horário, tendência, algoritmo). Com poucos
vídeos, o modelo aprenderia ruído: abaixo de ``[engajamento] minimo_cortes``, não há sugestão.
"""

import logging
from dataclasses import dataclass, field

from sqlmodel import Session

from clipador.aprendizado import metricas, pesos
from clipador.aprendizado.metricas import Exemplo
from clipador.aprendizado.sugestao import (
    NadaParaAceitarError,
    SugestaoMudouError,
    componentes,
    versao_atual,
)
from clipador.banco.modelos import PesosNota
from clipador.config import Config
from clipador.detectores import nota
from clipador.engajamento import indice
from clipador.revisao.consultas import dia_da_gravacao

log = logging.getLogger(__name__)

__all__ = ["NadaParaAceitarError", "SugestaoMudouError", "aceitar", "exemplos", "sugerir"]
misturar = pesos.misturar


def exemplos(lista: list[indice.Indice]) -> list[Exemplo]:
    return [
        Exemplo(
            momento_id=item.linha.momento.id,
            dia=dia_da_gravacao(item.linha.gravacao),
            forcas=dict(item.linha.momento.sinais),
            aprovado=item.acima_da_mediana,  # aqui, "o público aprovou"
            ajuste_inicio_s=None,
            ajuste_fim_s=None,
            detectado_em=item.linha.gravacao.criada_em,
        )
        for item in lista
    ]


@dataclass
class SugestaoEngajamento:
    cortes: int  # publicações com a métrica de 7 dias
    minimo: int
    mistura: float
    pesos_atuais: dict[str, float]  # os que valem hoje (já podem ser uma mistura)
    pesos_do_gosto: dict[str, float] = field(default_factory=dict)  # a parte que continua
    pesos_do_engajamento: dict[str, float] | None = None
    pesos_novos: dict[str, float] | None = None  # a mistura, que é o que vale se aceitar
    precisao_antes: float | None = None
    precisao_depois: float | None = None
    sem_pesos_porque: str | None = None
    precisao_atual: float | None = None  # quanto a nota de hoje já acerta o engajamento
    detalhes: list[indice.Indice] = field(default_factory=list)


def sugerir(sessao: Session, cfg: Config) -> SugestaoEngajamento:
    lista_de_indices = indice.indices(sessao, cfg.engajamento.pesos)
    lista = exemplos(lista_de_indices)
    atuais = nota.pesos_atuais(sessao, cfg.deteccao)
    gosto, _ = componentes(sessao, cfg)
    sugestao = SugestaoEngajamento(
        cortes=len(lista),
        minimo=cfg.engajamento.minimo_cortes,
        mistura=cfg.engajamento.mistura,
        pesos_atuais=atuais,
        pesos_do_gosto=gosto,
        precisao_atual=metricas.precisao_no_topo(lista, atuais),
        detalhes=lista_de_indices,
    )
    if len(lista) < sugestao.minimo:
        sugestao.sem_pesos_porque = (
            f"São {len(lista)} cortes com a métrica de 7 dias; a sugestão precisa de pelo "
            f"menos {sugestao.minimo}."
        )
        return sugestao
    acima = sum(e.aprovado for e in lista)
    if min(acima, len(lista) - acima) < pesos.DOBRAS:
        sugestao.sem_pesos_porque = (
            f"Precisa de pelo menos {pesos.DOBRAS} cortes acima e {pesos.DOBRAS} abaixo da "
            f"mediana (são {acima} e {len(lista) - acima})."
        )
        return sugestao
    nomes = metricas.detectores(lista)

    def com_o_gosto(do_engajamento: dict[str, float]) -> dict[str, float]:
        return misturar(gosto, do_engajamento, cfg.engajamento.mistura)

    try:
        # Aprende sobre a parte do gosto (a soma dos pesos e os detectores que não aparecem).
        aprendidos = pesos.treinar(lista, nomes, gosto, minimo=sugestao.minimo)
    except pesos.SemSinalUtilError:
        sugestao.sem_pesos_porque = "Nenhum sinal ajuda a prever o engajamento ainda."
        return sugestao
    misturados = com_o_gosto(aprendidos)
    sugestao.pesos_do_engajamento = aprendidos
    # Mede o que de fato valeria: a mistura, aprendida sem a parte que serve de teste.
    sugestao.precisao_antes, sugestao.precisao_depois = pesos.validacao_cruzada(
        lista, nomes, atuais, base=gosto, final=com_o_gosto
    )
    if misturados == atuais:
        sugestao.sem_pesos_porque = "Os pesos misturados são iguais aos atuais."
    else:
        sugestao.pesos_novos = misturados
    return sugestao


def aceitar(sessao: Session, cfg: Config, cortes_vistos: int) -> PesosNota:
    """Grava a mistura como nova versão da nota (``origem = "engajamento"``)."""
    sugestao = sugerir(sessao, cfg)
    if sugestao.cortes != cortes_vistos:
        raise SugestaoMudouError(
            f"Agora são {sugestao.cortes} cortes com métricas (você viu a sugestão com "
            f"{cortes_vistos}): veja a sugestão de novo antes de aceitar."
        )
    if sugestao.pesos_novos is None:
        raise NadaParaAceitarError(sugestao.sem_pesos_porque or "Não há sugestão agora.")
    atual = versao_atual(sessao)
    versao = PesosNota(
        versao=(atual.versao if atual else 0) + 1,
        valores=sugestao.pesos_novos,
        pesos_do_gosto=sugestao.pesos_do_gosto,
        pesos_do_engajamento=sugestao.pesos_do_engajamento,
        precisao=sugestao.precisao_depois,
        precisao_antes=sugestao.precisao_antes,
        decisoes=sugestao.cortes,
        # A janela do corte não muda aqui: continua a da versão anterior (ou a do config).
        segundos_antes=atual.segundos_antes if atual else None,
        segundos_depois=atual.segundos_depois if atual else None,
        origem="engajamento",
    )
    sessao.add(versao)
    sessao.commit()
    sessao.refresh(versao)
    log.info("Versão %d da nota aceita pelo engajamento: pesos %s (%d cortes).",
             versao.versao, versao.valores, versao.decisoes)  # fmt: skip
    return versao
