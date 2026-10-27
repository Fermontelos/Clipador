"""Executor da tarefa ``publicar`` (etapas 15 e 16): leva um clipe aprovado ao YouTube ou ao TikTok.

Antes de enviar (às duas plataformas), confere, nesta ordem:

1. a publicação ainda está pendente (se já foi, não envia de novo);
2. não há live acontecendo (o envio disputaria a internet com a transmissão): pausa;
3. o clipe não tem um novo corte na fila (etapa 12): espera 5 min e confere de novo,
   senão subiria o corte antigo;
4. (YouTube) cabe na cota do dia (100 envios; a thumbnail gasta 50 unidades): se não, espera
   a cota renovar (meia-noite no Pacífico, 4h–5h em Brasília);
5. (YouTube) o horário planejado ainda está no futuro (com a antecedência mínima): se passou
   (ex.: a fila esperou a cota), escolhe o próximo livre.

No TikTok (ADR 0011), o vertical e um ``.txt`` com a legenda vão para a pasta de exportação
(você posta pelo celular). Com ``[tiktok] modo = "rascunho"``, o vídeo também vai para a
caixa de entrada do app (você finaliza pela notificação, e cola a legenda do ``.txt``: o
rascunho chega sem ela). O TikTok aceita no máximo 5 rascunhos pendentes a cada 24 h: o
sexto espera na fila.

Depois do envio, o ID e o link do vídeo ficam na ``Publicacao`` **na hora** (um commit só
para isso): se o PC desligar logo depois, o vídeo não é enviado de novo. A thumbnail (só
nos horizontais) vem em seguida; se o canal não puder usá-la, o vídeo fica com a escolhida
pelo YouTube e a publicação segue normal.

A conversa com o YouTube e o TikTok é do pacote ``integracoes`` (ADR 0003). ``[youtube] modo =
"simulado"`` troca o YouTube por um falso que não usa a internet (a demo usa esse modo, e o
TikTok em ``pasta``, que também não usa).
"""

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlmodel import Session, col, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada, TentarDepois
from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Gravacao,
    Momento,
    Plataforma,
    Publicacao,
    StatusPublicacao,
    Tarefa,
    agora,
)
from clipador.config import LIMITE_LEGENDA_TIKTOK, Config
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador.publicacao import agenda, cota, pedido
from clipador.publicacao.planejar import horarios_ocupados
from clipador.revisao.consultas import recorte_pendente
from clipador_integracoes import tiktok, youtube

log = logging.getLogger(__name__)

ESPERA_PELO_RECORTE = timedelta(minutes=5)
FOLGA_DEPOIS_DA_COTA = timedelta(minutes=5)  # o relógio do Google pode estar um pouco atrás
RASCUNHOS_POR_DIA = 5  # pendentes a cada 24 h, pela documentação do TikTok
JANELA_DOS_RASCUNHOS = timedelta(hours=24)
ESPERA_POR_MUITOS_PEDIDOS = timedelta(minutes=2)  # o TikTok aceita 6 inícios de envio por minuto


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


class EnviadorDeRascunho(Protocol):
    def enviar_rascunho(self, arquivo: Path) -> tiktok.Rascunho: ...


def criar_enviador_tiktok(cfg: Config) -> EnviadorDeRascunho:
    """O TikTok de verdade. Os testes trocam esta função por um falso."""
    return tiktok.ClienteTiktok(cfg.pasta_segredos, cfg.arquivo_chaves)


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
    if publicacao.plataforma is Plataforma.TIKTOK:
        _publicar_no_tiktok(sessao, tarefa, cfg, publicacao, arquivo, decisao, gravacao)
        return
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


# ------------------------------------------------------------------ TikTok (etapa 16)


def exportar_para_o_tiktok(
    arquivo: Path, titulo: str, hashtags: list[str], dia_da_live: datetime, cfg: Config
) -> Path:
    """Copia o vertical para a pasta do TikTok, com o ``.txt`` da legenda ao lado.

    Devolve o caminho do vídeo copiado. Os dois são gravados com outro nome e renomeados no
    fim: um PC que cai no meio não deixa um vídeo pela metade na pasta (que pode estar
    sincronizada com o celular).
    """
    pasta = cfg.pasta_exportar_tiktok
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / arquivo.name
    legenda = cfg.tiktok.legenda.format(
        titulo=titulo,
        hashtags=" ".join(hashtags),
        data=dia_da_live.astimezone().strftime("%d/%m/%Y"),
    ).strip()[:LIMITE_LEGENDA_TIKTOK]
    texto = destino.with_suffix(".txt")
    texto.with_suffix(".parcial.txt").write_text(legenda + "\n", encoding="utf-8")
    texto.with_suffix(".parcial.txt").replace(texto)
    parcial = destino.with_suffix(".parcial.mp4")
    shutil.copyfile(arquivo, parcial)
    parcial.replace(destino)
    return destino


def _rascunhos_recentes(sessao: Session, agora_: datetime) -> list[datetime]:
    """Quando foram enviados os rascunhos do TikTok das últimas 24 h (os mais antigos antes)."""
    return list(
        sessao.exec(
            select(Publicacao.enviada_em)
            .where(
                Publicacao.plataforma == Plataforma.TIKTOK,
                col(Publicacao.id_na_plataforma).is_not(None),
                col(Publicacao.enviada_em) >= agora_ - JANELA_DOS_RASCUNHOS,
            )
            .order_by(col(Publicacao.enviada_em))
        ).all()
    )


def _publicar_no_tiktok(
    sessao: Session,
    tarefa: Tarefa,
    cfg: Config,
    publicacao: Publicacao,
    arquivo: Path,
    decisao: Decisao,
    gravacao: Gravacao,
) -> None:
    agora_ = agora()
    # A pasta vem primeiro e sempre: mesmo com o rascunho esperando o limite (ou o login), o
    # vídeo e a legenda já ficam à mão para postar pelo celular.
    destino = exportar_para_o_tiktok(
        arquivo, decisao.titulo or "", decisao.hashtags, gravacao.inicio, cfg
    )
    rascunho = cfg.tiktok.modo == "rascunho"
    if rascunho:
        # O TikTok não deixa passar de 5 pendentes em 24 h; nem pedimos (o 6º espera).
        recentes = _rascunhos_recentes(sessao, agora_)
        if len(recentes) >= RASCUNHOS_POR_DIA:
            raise TentarDepois(
                f"já foram {len(recentes)} rascunhos ao TikTok nas últimas 24 h (o limite é "
                f"{RASCUNHOS_POR_DIA}); o vídeo e a legenda já estão em {destino.parent}",
                recentes[0] + JANELA_DOS_RASCUNHOS + FOLGA_DEPOIS_DA_COTA,
            )
    publish_id = None
    if rascunho:
        try:
            publish_id = criar_enviador_tiktok(cfg).enviar_rascunho(arquivo).publish_id
        except tiktok.LimiteDeRascunhosError as erro:
            # Rascunhos enviados por fora do app (ou que o app não viu) também contam.
            raise TentarDepois(str(erro), agora_ + timedelta(hours=6)) from erro
        except tiktok.MuitosPedidosError as erro:
            raise TentarDepois(str(erro), agora_ + ESPERA_POR_MUITOS_PEDIDOS) from erro
        except (
            tiktok.PrecisaEntrarError,
            tiktok.CredencialDoAppAusenteError,
            tiktok.ProibidoDePostarError,
        ) as erro:
            raise FalhaDefinitivaError(
                f"{erro} O vídeo e a legenda já estão em {destino.parent}. Depois de resolver, "
                f"devolva esta tarefa com 'clipador refazer {tarefa.id}'."
            ) from erro

    publicacao.id_na_plataforma = publish_id
    publicacao.status = StatusPublicacao.ENVIADA
    publicacao.enviada_em = agora()
    sessao.add(publicacao)
    sessao.commit()
    if publish_id:
        log.info("Rascunho no TikTok (%s): finalize pela notificação do app.", publish_id)
    else:
        log.info("Vertical para o TikTok salvo em %s.", destino)
