"""Coleta das métricas: 24 h, 72 h e 7 dias depois de cada vídeo ir ao ar (etapa 17).

**Uma tarefa por publicação.** Quando o envio termina (etapa 15 ou 16), ``agendar`` põe na
fila uma tarefa ``coletar_metricas`` para 24 h depois de o vídeo ir ao ar. A cada execução,
ela grava a coleta que venceu e volta para a fila marcada para a próxima
(``fila.TentarDepois``, sem gastar tentativa). Depois da de 7 dias, termina.

**Ir ao ar.** No YouTube, é o ``publishAt`` (``Publicacao.agendada_para``). No TikTok, você
posta pelo celular, então o app não sabe o ID nem a hora: ele procura entre os seus vídeos
públicos (``video.list``) um postado depois do envio com o título aprovado na legenda. Até
achar, tenta de novo a cada 12 h, sem erro, por ``dias_para_achar_no_tiktok`` (14) dias;
achou, guarda o ID, o link e a hora em que o vídeo foi ao ar, e as coletas contam dali.

**PC desligado na hora.** Se a coleta de 24 h só rodar com 80 h, ela não vira uma "24 h"
falsa: vale a mais recente que já venceu (a de 72 h), e a de 24 h fica sem dado.

Fontes (conferidas na documentação oficial em 2026-10-29): no YouTube, o ``videos.list``
(visualizações, curtidas, comentários; 1 unidade da cota) e, nas coletas de 72 h e 7 dias, o
YouTube Analytics (retenção, duração média, compartilhamentos), que costuma demorar alguns
dias para ter os dados. No TikTok, o ``video.query`` (visualizações, curtidas, comentários,
compartilhamentos; sem retenção).
"""

import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from clipador.banco.fila import Contexto, FalhaDefinitivaError, TentarDepois
from clipador.banco.modelos import (
    ColetaMetrica,
    Decisao,
    Metrica,
    Momento,
    Plataforma,
    Publicacao,
    StatusPublicacao,
    Tarefa,
    TipoTarefa,
    agora,
)
from clipador.config import Config
from clipador.publicacao import cota
from clipador_integracoes import tiktok, youtube

log = logging.getLogger(__name__)

DEPOIS_DE_IR_AO_AR = {
    ColetaMetrica.H24: timedelta(hours=24),
    ColetaMetrica.H72: timedelta(hours=72),
    ColetaMetrica.D7: timedelta(days=7),
}
COM_RETENCAO = (ColetaMetrica.H72, ColetaMetrica.D7)  # o Analytics demora a ter os dados
PROCURAR_DE_NOVO = timedelta(hours=12)
PAGINAS_DO_TIKTOK = 10  # até 200 vídeos, do mais novo para o mais antigo
FOLGA_ANTES_DO_ENVIO = timedelta(minutes=10)  # relógios diferentes entre o PC e o TikTok


# ------------------------------------------------------------------ agenda


def agendar(sessao: Session, publicacao: Publicacao, cfg: Config) -> Tarefa | None:
    """Põe na fila as coletas da publicação (sem commit: vai junto com o do envio)."""
    if not cfg.engajamento.coletar:
        return None
    ja_tem = sessao.exec(
        select(Tarefa).where(
            Tarefa.tipo == TipoTarefa.COLETAR_METRICAS, Tarefa.publicacao_id == publicacao.id
        )
    ).first()
    if ja_tem is not None:
        return ja_tem
    # No TikTok, a hora de ir ao ar só se sabe ao achar o vídeo: a 1ª busca é 24 h após o envio.
    base = publicacao.agendada_para or publicacao.enviada_em or agora()
    momento_id = _momento_id(sessao, publicacao)
    momento = sessao.get(Momento, momento_id)
    tarefa = Tarefa(
        tipo=TipoTarefa.COLETAR_METRICAS,
        gravacao_id=momento.gravacao_id,
        momento_id=momento_id,
        publicacao_id=publicacao.id,
        executar_a_partir_de=base + DEPOIS_DE_IR_AO_AR[ColetaMetrica.H24],
    )
    sessao.add(tarefa)
    return tarefa


def _momento_id(sessao: Session, publicacao: Publicacao) -> int:
    from clipador.banco.modelos import Clipe

    return sessao.get(Clipe, publicacao.clipe_id).momento_id


# ------------------------------------------------------------------ clientes


def criar_youtube(cfg: Config) -> Any:
    """O YouTube de verdade (ou o simulado). Os testes trocam esta função por um falso."""
    if cfg.youtube.modo == "simulado":
        from clipador.publicacao.publicar import YoutubeSimulado

        return YoutubeSimulado()
    return youtube.ClienteYoutube(cfg.pasta_segredos)


def criar_tiktok(cfg: Config) -> Any:
    """O TikTok de verdade. Os testes trocam esta função por um falso."""
    return tiktok.ClienteTiktok(cfg.pasta_segredos, cfg.arquivo_chaves)


# ------------------------------------------------------------------ TikTok: achar o vídeo


def simplificar(texto: str) -> str:
    """Sem acentos, maiúsculas, hashtags nem pontuação: para comparar títulos e legendas."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    sem_hashtag = re.sub(r"#\w+", " ", sem_acento.casefold())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", sem_hashtag).split())


def achar_no_tiktok(
    videos: list[dict], titulo: str, enviada_em: datetime, ja_ligados: set[str]
) -> dict | None:
    """O vídeo postado a partir desta publicação: o mais antigo depois do envio cuja legenda
    (ou título) contém o título aprovado, e que ainda não foi ligado a outra publicação."""
    procurado = simplificar(titulo)
    if not procurado:
        return None
    candidatos = []
    for video in videos:
        postado = datetime.fromtimestamp(int(video.get("create_time", 0)), UTC)
        if postado < enviada_em - FOLGA_ANTES_DO_ENVIO or str(video.get("id")) in ja_ligados:
            continue
        legenda = simplificar(f"{video.get('title', '')} {video.get('video_description', '')}")
        if procurado in legenda:
            candidatos.append((postado, video))
    return min(candidatos, key=lambda c: c[0])[1] if candidatos else None


def _procurar_no_tiktok(sessao: Session, cliente: Any, publicacao: Publicacao) -> dict | None:
    decisao = sessao.exec(
        select(Decisao).where(Decisao.momento_id == _momento_id(sessao, publicacao))
    ).first()
    titulo = decisao.titulo if decisao else ""
    ja_ligados = {
        p
        for p in sessao.exec(
            select(Publicacao.id_na_plataforma).where(
                Publicacao.plataforma == Plataforma.TIKTOK,
                Publicacao.status == StatusPublicacao.PUBLICADA,
                col(Publicacao.id) != publicacao.id,
            )
        ).all()
        if p
    }
    vistos: list[dict] = []
    cursor = None
    for _ in range(PAGINAS_DO_TIKTOK):
        videos, cursor, tem_mais = cliente.listar_videos(antes_de_ms=cursor)
        vistos += videos
        mais_antigo = min((int(v.get("create_time", 0)) for v in videos), default=0)
        antes_do_envio = datetime.fromtimestamp(mais_antigo, UTC) < publicacao.enviada_em
        if not tem_mais or antes_do_envio:
            break  # a lista vem do mais novo para o mais antigo: o resto é de antes do envio
    return achar_no_tiktok(vistos, titulo or "", publicacao.enviada_em, ja_ligados)


# ------------------------------------------------------------------ os números


def _do_youtube(
    cliente: Any, publicacao: Publicacao, coleta: ColetaMetrica, agora_: datetime
) -> dict | None:
    video_id = publicacao.id_na_plataforma
    numeros = cliente.estatisticas([video_id]).get(video_id)
    if numeros is None:
        return None  # apagado no YouTube Studio (ou de outro canal)
    numeros = dict(numeros)
    if coleta in COM_RETENCAO:
        try:
            analytics = cliente.retencao(video_id, publicacao.agendada_para.date(), agora_.date())
        except youtube.ErroDoYoutubeError as erro:  # sem a permissão, Analytics desligado...
            log.warning("Retenção do vídeo %s indisponível: %s", video_id, erro)
            analytics = None
        if analytics:
            numeros.update(analytics)
    return numeros


def _do_tiktok(cliente: Any, publicacao: Publicacao) -> dict | None:
    video = cliente.consultar_videos([publicacao.id_na_plataforma]).get(publicacao.id_na_plataforma)
    if video is None:
        return None  # apagado, ou deixou de ser público
    return {
        "visualizacoes": video.get("view_count"),
        "curtidas": video.get("like_count"),
        "comentarios": video.get("comment_count"),
        "compartilhamentos": video.get("share_count"),
    }


# ------------------------------------------------------------------ o executor


def coletar_metricas(
    sessao: Session, tarefa: Tarefa, cfg: Config, contexto: Contexto | None = None
) -> None:
    """Executor da tarefa ``coletar_metricas`` (registrado em ``sistema.servico``)."""
    publicacao = sessao.get(Publicacao, tarefa.publicacao_id) if tarefa.publicacao_id else None
    if publicacao is None:
        raise FalhaDefinitivaError(f"A tarefa {tarefa.id} não aponta para uma publicação.")
    if not cfg.engajamento.coletar:
        log.info("Coleta de métricas desligada ([engajamento] coletar): tarefa %s.", tarefa.id)
        return
    agora_ = agora()
    youtube_ = publicacao.plataforma is Plataforma.YOUTUBE
    try:
        if youtube_:
            cliente = criar_youtube(cfg)
        else:
            cliente = criar_tiktok(cfg)
            ainda_sem_video = publicacao.status is not StatusPublicacao.PUBLICADA
            if ainda_sem_video and not _ligar_ao_video_do_tiktok(
                sessao, cliente, publicacao, cfg, agora_
            ):
                return  # passou do prazo para achar o vídeo
        _coletar_a_que_venceu(sessao, cliente, publicacao, cfg, agora_)
    except (
        youtube.PrecisaEntrarError,
        tiktok.PrecisaEntrarError,
        tiktok.CredencialDoAppAusenteError,
    ) as erro:
        comando = "youtube-entrar" if youtube_ else "tiktok-entrar"
        raise FalhaDefinitivaError(
            f"{erro} Depois de 'clipador {comando}', devolva esta tarefa com "
            f"'clipador refazer {tarefa.id}'."
        ) from erro
    except (youtube.CotaEsgotadaError, tiktok.MuitosPedidosError) as erro:
        raise TentarDepois(str(erro), agora_ + timedelta(hours=1)) from erro


def _ligar_ao_video_do_tiktok(
    sessao: Session, cliente: Any, publicacao: Publicacao, cfg: Config, agora_: datetime
) -> bool:
    """Acha o vídeo que você postou; ``False`` = desistiu (passou do prazo)."""
    video = _procurar_no_tiktok(sessao, cliente, publicacao)
    if video is None:
        prazo = publicacao.enviada_em + timedelta(days=cfg.engajamento.dias_para_achar_no_tiktok)
        if agora_ >= prazo:
            log.info(
                "Publicação %s: o vídeo não apareceu no TikTok em %d dias (não postado, ou "
                "privado); sem métricas para ela.",
                publicacao.id, cfg.engajamento.dias_para_achar_no_tiktok,
            )  # fmt: skip
            return False
        raise TentarDepois(
            "o vídeo ainda não apareceu entre os seus vídeos públicos do TikTok",
            min(agora_ + PROCURAR_DE_NOVO, prazo),
        )
    publicacao.id_na_plataforma = str(video["id"])
    publicacao.link = video.get("share_url") or None
    publicacao.agendada_para = datetime.fromtimestamp(int(video["create_time"]), UTC)
    publicacao.status = StatusPublicacao.PUBLICADA
    sessao.add(publicacao)
    sessao.commit()
    log.info("Publicação %s ligada ao vídeo %s do TikTok.", publicacao.id, video["id"])
    return True


def _coletar_a_que_venceu(
    sessao: Session, cliente: Any, publicacao: Publicacao, cfg: Config, agora_: datetime
) -> None:
    feitas = set(
        sessao.exec(select(Metrica.coleta).where(Metrica.publicacao_id == publicacao.id)).all()
    )
    no_ar = publicacao.agendada_para
    pendentes = [(c, no_ar + d) for c, d in DEPOIS_DE_IR_AO_AR.items() if c not in feitas]
    vencidas = [c for c, quando in pendentes if quando <= agora_]
    if not vencidas:
        if pendentes:
            proxima, quando = pendentes[0]
            raise TentarDepois(f"a próxima coleta é a de {proxima.value}", quando)
        return
    coleta = vencidas[-1]  # a mais recente que venceu; as anteriores ficam sem dado
    for pulada in vencidas[:-1]:
        log.info("Publicação %s: coleta de %s pulada (o PC estava desligado).",
                 publicacao.id, pulada.value)  # fmt: skip
    youtube_ = publicacao.plataforma is Plataforma.YOUTUBE
    if youtube_ and cfg.youtube.modo == "real":
        uso = cota.cota_youtube(sessao, agora_)
        if not uso.cabem_unidades(cota.CUSTO_DAS_ESTATISTICAS):
            raise TentarDepois("cota do YouTube de hoje esgotada", uso.renova_em)
    numeros = (
        _do_youtube(cliente, publicacao, coleta, agora_)
        if youtube_
        else _do_tiktok(cliente, publicacao)
    )
    if numeros is None:
        log.info("Publicação %s: o vídeo sumiu (apagado ou privado); coletas encerradas.",
                 publicacao.id)  # fmt: skip
        return
    sessao.add(Metrica(publicacao_id=publicacao.id, coleta=coleta, coletada_em=agora_, **numeros))
    if youtube_ and publicacao.status is StatusPublicacao.AGENDADA:
        publicacao.status = StatusPublicacao.PUBLICADA  # o horário passou: já foi ao ar
        sessao.add(publicacao)
    sessao.commit()
    log.info("Publicação %s: coleta de %s (%s visualizações).", publicacao.id, coleta.value,
             numeros.get("visualizacoes"))  # fmt: skip
    seguintes = [(c, quando) for c, quando in pendentes if quando > agora_]
    if seguintes:
        proxima, quando = seguintes[0]
        raise TentarDepois(f"a próxima coleta é a de {proxima.value}", quando)
