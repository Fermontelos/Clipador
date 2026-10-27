"""Executor da tarefa ``publicar`` (etapa 15): sobe um clipe aprovado para o YouTube.

Antes de enviar, confere, nesta ordem:

1. a publicação ainda está pendente (se já foi, não envia de novo);
2. não há live acontecendo (o envio disputaria a internet com a transmissão): pausa;
3. o clipe não tem um novo corte na fila (etapa 12): espera 5 min e confere de novo,
   senão subiria o corte antigo;
4. cabe na cota do dia (100 envios; a thumbnail gasta 50 unidades): se não, espera a cota
   renovar (meia-noite no Pacífico, 4h–5h em Brasília);
5. o horário planejado ainda está no futuro (com a antecedência mínima): se passou (ex.:
   a fila esperou a cota), escolhe o próximo livre.

Depois do envio, o ID e o link do vídeo ficam na ``Publicacao`` **na hora** (um commit só
para isso): se o PC desligar logo depois, o vídeo não é enviado de novo. A thumbnail (só
nos horizontais) vem em seguida; se o canal não puder usá-la, o vídeo fica com a escolhida
pelo YouTube e a publicação segue normal.

A conversa com o YouTube é do pacote ``integracoes`` (ADR 0003). ``[youtube] modo =
"simulado"`` troca o YouTube por um falso que não usa a internet (a demo usa esse modo).
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from sqlmodel import Session, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada, TentarDepois
from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Gravacao,
    Momento,
    Publicacao,
    StatusPublicacao,
    Tarefa,
    agora,
)
from clipador.config import Config
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador.publicacao import agenda, cota, pedido
from clipador.publicacao.planejar import horarios_ocupados
from clipador.revisao.consultas import recorte_pendente
from clipador_integracoes import youtube

log = logging.getLogger(__name__)

ESPERA_PELO_RECORTE = timedelta(minutes=5)
FOLGA_DEPOIS_DA_COTA = timedelta(minutes=5)  # o relógio do Google pode estar um pouco atrás


class Enviador(Protocol):
    def enviar(
        self, arquivo: Path, corpo: dict, notificar_inscritos: bool
    ) -> youtube.VideoEnviado: ...

    def definir_thumbnail(self, video_id: str, imagem: Path) -> None: ...


class YoutubeSimulado:
    """Finge o envio, sem internet nem cota: para a demo e para testar o fluxo."""

    def __init__(self) -> None:
        self.enviados: list[tuple[Path, dict]] = []

    def enviar(self, arquivo: Path, corpo: dict, notificar_inscritos: bool) -> youtube.VideoEnviado:
        self.enviados.append((arquivo, corpo))
        video_id = f"simulado-{len(self.enviados)}-{arquivo.stem}"
        return youtube.VideoEnviado(id=video_id, link="")

    def definir_thumbnail(self, video_id: str, imagem: Path) -> None:
        pass


def criar_enviador(cfg: Config) -> Enviador:
    """O YouTube de verdade (ou o simulado). Os testes trocam esta função por um falso."""
    if cfg.youtube.modo == "simulado":
        return YoutubeSimulado()
    return youtube.ClienteYoutube(cfg.pasta_segredos)


def _carregar(sessao: Session, tarefa: Tarefa) -> tuple[Publicacao, Clipe, Momento, Decisao]:
    publicacao = sessao.get(Publicacao, tarefa.publicacao_id) if tarefa.publicacao_id else None
    if publicacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma publicação.")
    clipe = sessao.get(Clipe, publicacao.clipe_id)
    momento = sessao.get(Momento, clipe.momento_id)
    decisao = sessao.exec(select(Decisao).where(Decisao.momento_id == momento.id)).first()
    if decisao is None or not decisao.aprovado:
        # Não deveria acontecer (desfazer cancela as pendentes), mas nunca publica sem aprovação.
        raise FalhaDefinitivaError(f"O momento {momento.id} não está aprovado: nada a publicar.")
    return publicacao, clipe, momento, decisao


def publicar(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``publicar`` (registrado em ``sistema.servico``)."""
    contexto = contexto or Contexto()
    publicacao, clipe, momento, decisao = _carregar(sessao, tarefa)
    if publicacao.status is not StatusPublicacao.PENDENTE:
        log.info("Publicação %s já está '%s': nada a enviar.", publicacao.id, publicacao.status)
        return
    if contexto.deve_pausar():
        raise TarefaPausada("Live em andamento: o envio espera ela terminar.")
    agora_ = agora()
    if recorte_pendente(sessao, momento.id):
        raise TentarDepois("o clipe vai ser cortado de novo", agora_ + ESPERA_PELO_RECORTE)
    arquivo = Path(clipe.arquivo)
    if not arquivo.is_file():
        raise FalhaDefinitivaError(f"O arquivo do clipe {clipe.id} não existe mais: {arquivo}")

    gravacao = sessao.get(Gravacao, momento.gravacao_id)
    horizontal = clipe.formato.value == "horizontal"
    thumbnail = caminho_da_thumbnail(cfg, gravacao, momento)
    com_thumbnail = horizontal and cfg.youtube.thumbnail and thumbnail.is_file()
    if cfg.youtube.modo == "real":
        uso = cota.cota_youtube(sessao, agora_)
        if not uso.cabe(cota.CUSTO_DA_THUMBNAIL if com_thumbnail else 0):
            raise TentarDepois(
                f"cota do YouTube de hoje esgotada ({uso.envios} envios, {uso.unidades} unidades)",
                uso.renova_em + FOLGA_DEPOIS_DA_COTA,
            )

    cedo_demais = agora_ + timedelta(minutes=cfg.youtube.antecedencia_min)
    if publicacao.agendada_para is None or publicacao.agendada_para < cedo_demais:
        publicacao.agendada_para = agenda.proximo_horario(
            agora_, horarios_ocupados(sessao, sem=publicacao.id), cfg.youtube
        )
    feito = pedido.montar(
        titulo=decisao.titulo or "",
        hashtags=decisao.hashtags,
        dia_da_live=gravacao.inicio,
        publicar_em=publicacao.agendada_para,
        duracao_s=clipe.duracao_s,
        vertical=not horizontal,
        cfg=cfg.youtube,
    )

    enviador = criar_enviador(cfg)
    try:
        enviado = enviador.enviar(arquivo, feito.corpo, feito.notificar_inscritos)
    except youtube.CotaEsgotadaError as erro:
        renova_em = cota.cota_youtube(sessao, agora_).renova_em
        raise TentarDepois(str(erro), renova_em + FOLGA_DEPOIS_DA_COTA) from erro
    except (youtube.PrecisaEntrarError, youtube.CredencialDoAppAusenteError) as erro:
        raise FalhaDefinitivaError(
            f"{erro} Depois, devolva esta tarefa com 'clipador refazer {tarefa.id}'."
        ) from erro

    # Gravado já: se o PC cair daqui em diante, o vídeo não sobe de novo.
    publicacao.id_na_plataforma = enviado.id
    publicacao.link = enviado.link or None
    if publicacao.link and feito.short:
        publicacao.link = f"https://youtube.com/shorts/{enviado.id}"
    publicacao.status = StatusPublicacao.AGENDADA
    publicacao.enviada_em = agora()
    sessao.add(publicacao)
    sessao.commit()
    log.info(
        "Momento %s (%s) enviado ao YouTube: %s, vai ao ar %s.",
        momento.id, clipe.formato.value, enviado.id,
        publicacao.agendada_para.astimezone().strftime("%d/%m %H:%M"),
    )  # fmt: skip

    if com_thumbnail:
        try:
            enviador.definir_thumbnail(enviado.id, thumbnail)
            publicacao.unidades_cota += cota.CUSTO_DA_THUMBNAIL
        except youtube.SemPermissaoError as erro:
            log.warning("Thumbnail não aceita (o vídeo fica com a do YouTube): %s", erro)
        except youtube.ErroDoYoutubeError as erro:
            log.warning("Não foi possível trocar a thumbnail do vídeo %s: %s", enviado.id, erro)
        sessao.add(publicacao)
        sessao.commit()
