"""Executor da tarefa ``gerar_titulos``: 3 títulos e as hashtags de cada momento.

É a última tarefa da cadeia de uma gravação (depois dos cortes): quando ela termina, a
gravação fica "concluída" e os arquivos temporários dela são apagados (etapa 5).

- **Retomável**: cada momento é salvo assim que ganha as sugestões; se o PC cair, os que
  já têm títulos são pulados.
- **Pausa** entre um momento e outro se começar uma live (o modelo usa a placa de vídeo).
- **Aprende o seu estilo**: os últimos títulos que você escolheu ou editou na revisão
  (tabela ``Decisao``) vão como exemplo no pedido ao modelo.
- Na demo (``fonte = "demo"``), os títulos são os prontos do roteiro: a demo roda em
  qualquer PC, sem o Ollama.
"""

import logging
import time
from pathlib import Path

from sqlmodel import Session, col, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada
from clipador.banco.modelos import Decisao, Gravacao, Momento, Tarefa, Transcricao
from clipador.config import Config
from clipador.demo import roteiro
from clipador.detectores.base import Entrada
from clipador.detectores.lol import Lol
from clipador.jogo import alinhamento
from clipador.titulos import texto
from clipador.titulos.gerador import Gerador, Pedido, Sugestoes

log = logging.getLogger(__name__)


def exemplos_seus(sessao: Session, quantos: int) -> list[dict[str, str]]:
    """Os últimos títulos que você escolheu ou editou na revisão (do mais novo ao mais velho)."""
    if quantos <= 0:
        return []
    decisoes = sessao.exec(
        select(Decisao)
        .where(Decisao.aprovado, col(Decisao.titulo).is_not(None))
        .order_by(col(Decisao.decidida_em).desc())
        .limit(quantos)
    ).all()
    return [{"titulo": d.titulo, "tipo": d.tipo_titulo or ""} for d in decisoes]


def titulos_da_demo(momento: Momento, cfg: Config) -> Sugestoes:
    """Os títulos prontos do roteiro da demo, do pico que cai dentro do momento."""
    for pico, titulos in zip(roteiro.PICOS_VOZ, roteiro.TITULOS, strict=True):
        if momento.inicio_s <= pico.inicio_s <= momento.fim_s:
            hashtags = texto.juntar_hashtags(
                cfg.titulos.hashtags_fixas, ["#demo"], cfg.titulos.max_hashtags
            )
            return Sugestoes([dict(t) for t in titulos], hashtags, origem="demo")
    return Sugestoes([{"titulo": "Momento da demo", "tipo": "descricao"}], [], origem="demo")


def gerar_titulos(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``gerar_titulos`` (registrado em ``sistema.servico``)."""
    contexto = contexto or Contexto()
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id) if tarefa.gravacao_id else None
    if gravacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma gravação.")
    pendentes = [
        momento
        for momento in sessao.exec(
            select(Momento)
            .where(Momento.gravacao_id == gravacao.id)
            .order_by(col(Momento.nota).desc(), col(Momento.inicio_s))
        ).all()
        if not momento.titulos_sugeridos  # retomável: os que já têm títulos são pulados
    ]
    if not pendentes:
        return
    transcricao = sessao.exec(
        select(Transcricao).where(Transcricao.gravacao_id == gravacao.id)
    ).first()
    palavras = list(transcricao.palavras) if transcricao else []
    eventos = alinhamento.eventos_da_gravacao(sessao, gravacao)
    exemplos = exemplos_seus(sessao, cfg.titulos.exemplos)
    lol = Lol(cfg.deteccao.lol)
    gerador = Gerador(cfg.titulos)
    inicio = time.perf_counter()
    origens: set[str] = set()
    try:
        for momento in pendentes:
            if contexto.deve_pausar():
                raise TarefaPausada(f"Live em andamento: títulos pausados no momento {momento.id}.")
            if cfg.titulos.fonte == "demo":
                sugestoes = titulos_da_demo(momento, cfg)
            else:
                no_trecho = [
                    (e, s) for e, s in eventos if momento.inicio_s <= s <= momento.fim_s
                ]  # fmt: skip
                pedido = Pedido(
                    falas=texto.falas_do_trecho(palavras, momento.inicio_s, momento.fim_s),
                    motivo=momento.motivo,
                    eventos=[
                        sinal.motivo
                        for sinal in lol.detectar(Entrada(gravacao, 0.0, eventos=no_trecho))
                    ],
                    frase_marcante=texto.frase_marcante(
                        palavras, momento.inicio_s, momento.fim_s, momento.pico_s
                    ),
                    exemplos=exemplos,
                )
                sugestoes = gerador.sugerir(pedido)
            momento.titulos_sugeridos = sugestoes.titulos
            momento.hashtags_sugeridas = sugestoes.hashtags
            sessao.add(momento)
            sessao.commit()  # este momento está salvo: se o PC cair, continua do próximo
            origens.add(sugestoes.origem)
    finally:
        gerador.fechar()
    log.info(
        "Títulos de '%s': %d momentos em %.1f min (%s).",
        Path(gravacao.arquivo).name,
        len(pendentes),
        (time.perf_counter() - inicio) / 60,
        ", ".join(sorted(origens)),
    )
