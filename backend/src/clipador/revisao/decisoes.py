"""O que a revisão grava: aprovar ou recusar, desfazer e ajustar o corte.

Regras:

- **Nada se publica sem aprovação**: a decisão só fica salva aqui. A publicação (etapa 15)
  vai ler as decisões aprovadas e virar tarefas na fila.
- Depois que um clipe do momento foi para a publicação, a decisão e o corte não mudam mais
  (``RevisaoBloqueadaError``): mudar aqui não mudaria o que já subiu.
- **Ajuste do corte**: muda o início e o fim do momento (com a mesma duração mínima e
  máxima que o detector usa, ``[deteccao] duracao_minima_s`` e ``duracao_maxima_s``) e põe
  uma tarefa ``recortar`` na fila, que corta de novo só esse momento. O trecho que o
  detector escolheu fica guardado (``Momento.inicio_detectado_s``) e a diferença vai para a
  decisão: a etapa 14 aprende a janela padrão com a média desses ajustes.
"""

import logging
import re

from sqlmodel import Session, col, select

from clipador.banco.modelos import (
    Decisao,
    Gravacao,
    Momento,
    Plataforma,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
    agora,
)
from clipador.config import Config
from clipador.cortes.cortar import DURACAO_MINIMA_S
from clipador.publicacao import planejar
from clipador.revisao.consultas import EM_PROCESSAMENTO, clipes_do_momento, publicado
from clipador.titulos.texto import LIMITE_TITULO_YOUTUBE, TIPOS_DE_TITULO

log = logging.getLogger(__name__)

MAX_HASHTAGS = 15  # o YouTube recomenda 3 a 5; mais de 60, ignora todas
HASHTAG_VALIDA = re.compile(r"^#\w+$")


class MomentoNaoEncontradoError(LookupError):
    pass


class DecisaoNaoEncontradaError(LookupError):
    pass


class RevisaoBloqueadaError(RuntimeError):
    """O pedido faz sentido, mas não agora (ex.: o clipe já foi para a publicação)."""


class DecisaoInvalidaError(ValueError):
    """O pedido em si está errado (ex.: aprovar sem título)."""


def _momento(sessao: Session, momento_id: int) -> Momento:
    momento = sessao.get(Momento, momento_id)
    if momento is None:
        raise MomentoNaoEncontradoError(f"Não existe o momento {momento_id}.")
    return momento


def _exigir_nao_publicado(sessao: Session, momento_id: int, acao: str) -> None:
    if publicado(sessao, momento_id):
        raise RevisaoBloqueadaError(
            f"O momento {momento_id} já foi para a publicação: não dá para {acao}."
        )


def _cancelar_publicacoes(sessao: Session, momento_id: int, acao: str) -> int:
    """``planejar.cancelar``; se o envio começou no meio do caminho, desfaz tudo e trava."""
    try:
        return planejar.cancelar(sessao, momento_id)
    except planejar.EnvioComecouError as erro:
        sessao.rollback()  # nada desta decisão fica pela metade na sessão
        raise RevisaoBloqueadaError(f"{erro} Não dá para {acao}.") from erro


def normalizar_hashtags(hashtags: list[str]) -> list[str]:
    """``lol`` vira ``#lol``; repetidas (sem diferenciar maiúsculas) saem."""
    resultado: list[str] = []
    vistas: set[str] = set()
    for bruta in hashtags:
        hashtag = bruta.strip()
        if not hashtag:
            continue
        if not hashtag.startswith("#"):
            hashtag = "#" + hashtag
        if not HASHTAG_VALIDA.match(hashtag):
            raise DecisaoInvalidaError(
                f"Hashtag inválida: '{bruta}' (só letras, números e _, sem espaço)."
            )
        if hashtag.casefold() not in vistas:
            vistas.add(hashtag.casefold())
            resultado.append(hashtag)
    if len(resultado) > MAX_HASHTAGS:
        raise DecisaoInvalidaError(f"No máximo {MAX_HASHTAGS} hashtags (são {len(resultado)}).")
    return resultado


def _validar_destinos(
    sessao: Session, momento: Momento, destinos: dict[str, list[str]]
) -> dict[str, list[str]]:
    cortados = {clipe.formato.value for clipe in clipes_do_momento(sessao, momento.id)}
    validos: dict[str, list[str]] = {}
    for formato, plataformas in destinos.items():
        unicas = list(dict.fromkeys(plataformas))  # sem repetir, na ordem dada
        if not unicas:
            continue  # formato sem destino = formato não escolhido
        if formato not in cortados:
            raise DecisaoInvalidaError(
                f"O momento {momento.id} não tem clipe no formato '{formato}' "
                f"(tem: {', '.join(sorted(cortados)) or 'nenhum'})."
            )
        desconhecidas = [p for p in unicas if p not in Plataforma.__members__.values()]
        if desconhecidas:
            raise DecisaoInvalidaError(
                f"Plataforma desconhecida: {desconhecidas} "
                f"(use {', '.join(p.value for p in Plataforma)})."
            )
        validos[formato] = unicas
    if not validos:
        raise DecisaoInvalidaError("Para aprovar, escolha ao menos um formato com um destino.")
    return validos


def _tipo_do_titulo(momento: Momento, titulo: str) -> str | None:
    """O tipo da sugestão escolhida (``frase_sua``, ``exagero``...), se você não editou o título."""
    for sugestao in momento.titulos_sugeridos:
        if sugestao.get("titulo", "").strip() == titulo:
            return sugestao.get("tipo") or None
    return None


def _ajustes(momento: Momento) -> tuple[float | None, float | None]:
    """Quanto o início e o fim foram movidos em relação ao trecho detectado."""
    if momento.inicio_detectado_s is None or momento.fim_detectado_s is None:
        return None, None
    return (
        round(momento.inicio_s - momento.inicio_detectado_s, 3),
        round(momento.fim_s - momento.fim_detectado_s, 3),
    )


def decidir(
    sessao: Session,
    momento_id: int,
    *,
    aprovado: bool,
    destinos: dict[str, list[str]] | None = None,
    titulo: str | None = None,
    hashtags: list[str] | None = None,
    tipo_titulo: str | None = None,
    cfg: Config | None = None,
) -> Decisao:
    """Aprova ou recusa o momento (troca a decisão anterior, se houver).

    Aprovar exige ao menos um formato já cortado com um destino, e um título de até 100
    caracteres. Recusar não guarda formatos, destinos nem título.

    Com ``cfg``, as publicações seguem a decisão no mesmo commit (etapa 15): as pendentes de
    uma decisão anterior são canceladas, e uma aprovação cria as novas. Sem ``cfg`` (testes
    da revisão em si), só a decisão é gravada.
    """
    momento = _momento(sessao, momento_id)
    _exigir_nao_publicado(sessao, momento_id, "mudar a decisão")
    # Tudo é validado ANTES de mexer na decisão salva: um erro no meio não pode deixar a
    # decisão pela metade na sessão (quem reaproveitasse a sessão a gravaria no próximo commit).
    # Recusar: sem formatos, destinos, hashtags nem título.
    valores = {"destinos": {}, "formatos": [], "hashtags": [], "titulo": None, "tipo_titulo": None}
    if aprovado:
        titulo = " ".join((titulo or "").split())  # sem espaços sobrando nem quebras de linha
        if not titulo:
            raise DecisaoInvalidaError("Para aprovar, escreva ou escolha um título.")
        if len(titulo) > LIMITE_TITULO_YOUTUBE:  # o do TikTok (2.200) é bem maior
            raise DecisaoInvalidaError(
                f"O título tem {len(titulo)} caracteres; o YouTube aceita até "
                f"{LIMITE_TITULO_YOUTUBE}."
            )
        if "<" in titulo or ">" in titulo:  # o YouTube recusa o envio com eles
            raise DecisaoInvalidaError("O YouTube não aceita '<' nem '>' no título.")
        if tipo_titulo is not None and tipo_titulo not in TIPOS_DE_TITULO:
            # Os tipos são os da etapa 10; a etapa 17 compara o engajamento de cada um.
            raise DecisaoInvalidaError(
                f"Tipo de título desconhecido: '{tipo_titulo}' (use {', '.join(TIPOS_DE_TITULO)})."
            )
        valores["destinos"] = _validar_destinos(sessao, momento, destinos or {})
        valores["formatos"] = list(valores["destinos"])
        valores["hashtags"] = normalizar_hashtags(hashtags or [])
        valores["titulo"] = titulo
        valores["tipo_titulo"] = tipo_titulo or _tipo_do_titulo(momento, titulo)
    decisao = sessao.exec(select(Decisao).where(Decisao.momento_id == momento_id)).first()
    decisao = decisao or Decisao(momento_id=momento_id, aprovado=aprovado)
    decisao.aprovado = aprovado
    for campo, valor in valores.items():
        setattr(decisao, campo, valor)
    decisao.ajuste_inicio_s, decisao.ajuste_fim_s = _ajustes(momento)
    decisao.decidida_em = agora()
    sessao.add(decisao)
    if cfg is not None:
        # Decisão e publicações no mesmo commit: nunca fica uma aprovação sem a fila dela.
        _cancelar_publicacoes(sessao, momento_id, "mudar a decisão")
        planejar.planejar(sessao, momento, decisao, cfg)
    sessao.commit()
    sessao.refresh(decisao)
    log.info("Momento %s %s na revisão.", momento_id, "aprovado" if aprovado else "recusado")
    return decisao


def desfazer(sessao: Session, momento_id: int) -> None:
    """Apaga a decisão: o momento volta a ficar pendente. Não mexe num ajuste do corte."""
    _momento(sessao, momento_id)
    _exigir_nao_publicado(sessao, momento_id, "desfazer a decisão")
    decisao = sessao.exec(select(Decisao).where(Decisao.momento_id == momento_id)).first()
    if decisao is None:
        raise DecisaoNaoEncontradaError(f"O momento {momento_id} ainda não tem decisão.")
    canceladas = _cancelar_publicacoes(sessao, momento_id, "desfazer")  # as que não saíram
    sessao.delete(decisao)
    sessao.commit()
    if canceladas:
        log.info("%d publicação(ões) do momento %s cancelada(s).", canceladas, momento_id)
    log.info("Decisão do momento %s desfeita.", momento_id)


def ajustar(
    sessao: Session,
    momento_id: int,
    inicio_s: float,
    fim_s: float,
    *,
    duracao_minima_s: float,
    duracao_maxima_s: float,
) -> Tarefa | None:
    """Muda o trecho do momento e põe na fila um novo corte só dele.

    A duração tem de ficar entre ``duracao_minima_s`` e ``duracao_maxima_s`` (as do
    detector). Numa gravação mais curta que o mínimo, vale a gravação inteira.

    Devolve a tarefa ``recortar`` (uma que já estava esperando na fila é reaproveitada) ou
    ``None`` se o trecho não mudou.
    """
    momento = _momento(sessao, momento_id)
    _exigir_nao_publicado(sessao, momento_id, "ajustar o corte")
    gravacao = sessao.get(Gravacao, momento.gravacao_id)
    if gravacao.status in EM_PROCESSAMENTO:
        raise RevisaoBloqueadaError(
            f"A gravação do momento {momento_id} ainda está sendo processada: espere terminar."
        )
    inicio_s, fim_s = round(inicio_s, 3), round(fim_s, 3)
    if inicio_s < 0:
        raise DecisaoInvalidaError("O início não pode ser antes do começo da gravação (0 s).")
    if gravacao.duracao_s is not None and fim_s > gravacao.duracao_s:
        raise DecisaoInvalidaError(
            f"O fim ({fim_s:.1f} s) passa do fim da gravação ({gravacao.duracao_s:.1f} s)."
        )
    duracao = round(fim_s - inicio_s, 3)
    minima = duracao_minima_s
    if gravacao.duracao_s is not None:
        minima = min(minima, gravacao.duracao_s)
    minima = max(minima, DURACAO_MINIMA_S)  # o corte nunca aceita menos que isto
    if not minima <= duracao <= duracao_maxima_s:
        raise DecisaoInvalidaError(
            f"O clipe ficaria com {duracao:.1f} s: precisa ter de {minima:.0f} a "
            f"{duracao_maxima_s:.0f} s."
        )
    if (inicio_s, fim_s) == (momento.inicio_s, momento.fim_s):
        return None
    if momento.inicio_detectado_s is None:  # primeiro ajuste: guarda o que o detector achou
        momento.inicio_detectado_s, momento.fim_detectado_s = momento.inicio_s, momento.fim_s
    momento.inicio_s, momento.fim_s = inicio_s, fim_s
    sessao.add(momento)
    decisao = sessao.exec(select(Decisao).where(Decisao.momento_id == momento_id)).first()
    if decisao is not None:
        decisao.ajuste_inicio_s, decisao.ajuste_fim_s = _ajustes(momento)
        sessao.add(decisao)
    # Uma tarefa que ainda não começou lê o trecho novo quando rodar: basta ela. Uma que já
    # está cortando usa o trecho antigo, então é preciso outra.
    esperando = sessao.exec(
        select(Tarefa)
        .where(
            Tarefa.tipo == TipoTarefa.RECORTAR,
            Tarefa.momento_id == momento_id,
            Tarefa.status == StatusTarefa.PENDENTE,
        )
        .order_by(col(Tarefa.id))
    ).first()
    tarefa = esperando or Tarefa(
        tipo=TipoTarefa.RECORTAR, gravacao_id=momento.gravacao_id, momento_id=momento_id
    )
    sessao.add(tarefa)
    # Trecho novo e tarefa no mesmo commit: se o PC cair no meio, não fica um ajuste salvo
    # sem o corte novo na fila (o vídeo continuaria mostrando o trecho antigo).
    sessao.commit()
    sessao.refresh(tarefa)
    log.info(
        "Corte do momento %s ajustado para %.1f-%.1f s (tarefa %s, %s).",
        momento_id, inicio_s, fim_s, tarefa.id, TipoTarefa.RECORTAR.value,
    )  # fmt: skip
    return tarefa
