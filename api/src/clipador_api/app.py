"""A API da revisão (etapa 12): o que a interface web (etapa 13) usa para revisar os clipes.

Rotas (todas documentadas, e testáveis no navegador, em ``/docs``):

- ``GET /api/momentos``: os momentos de um dia, da maior nota para a menor, até o limite
  diário (``limite_diario``, padrão 15). ``ver_mais=true`` traz o resto;
- ``GET /api/momentos/{id}``: um momento;
- ``POST`` / ``DELETE /api/momentos/{id}/decisao``: aprovar ou recusar / desfazer;
- ``POST /api/momentos/{id}/ajuste``: novo início e fim (põe um novo corte na fila);
- ``GET /api/clipes/{id}/video``: o vídeo, com ``Range`` (o player avança e volta);
- ``GET /api/momentos/{id}/thumbnail``: o quadro do pico (clipes horizontais);
- ``GET /api/aprendizado`` e ``POST /api/aprendizado/aceitar``: o que as suas decisões
  ensinam (pesos da nota e janela do corte, etapa 14) e o aceite;
- ``GET /api/dias``, ``GET /api/status``, ``GET /api/estatisticas`` e ``GET /api/config``
  (o que a interface precisa saber do config: limites do corte, formatos...);
- ``/``: a interface de revisão (etapa 13), se ela foi construída (``npm run build`` em
  ``frontend/``). Sem ela, a página inicial explica como construir e aponta o ``/docs``.

Segurança (a API não tem senha, então só o próprio PC pode usá-la):

- o servidor só escuta em ``127.0.0.1`` (``comando.py``): ninguém na rede alcança;
- só aceita os nomes ``127.0.0.1`` e ``localhost`` no cabeçalho ``Host``: um site
  malicioso não consegue se passar pela API trocando o próprio DNS (*DNS rebinding*);
- pedidos que mudam algo vindos de **outra origem** (um site aberto no navegador tentando
  aprovar um clipe por você) são recusados.

A regra de negócio fica no backend (``clipador.revisao``); aqui só se traduz HTTP.
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clipador import __version__
from clipador.aprendizado import sugestao as aprendizado
from clipador.banco import fila
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Gravacao,
    Momento,
    Plataforma,
    StatusTarefa,
    Tarefa,
    agora,
)
from clipador.config import Config
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador.publicacao import cota, pedido, planejar
from clipador.revisao import consultas, decisoes
from clipador.titulos.texto import LIMITE_TITULO_YOUTUBE
from clipador_api import esquemas

HOSTS_PERMITIDOS = ["127.0.0.1", "localhost"]
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
SEM_CACHE_VELHO = {"Cache-Control": "no-cache"}
# O build da interface (etapa 13): frontend/dist, na raiz do repositório.
PASTA_DA_INTERFACE = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def criar_app(
    cfg: Config, engine: Engine | None = None, pasta_interface: Path = PASTA_DA_INTERFACE
) -> FastAPI:
    """Monta a API para a configuração dada (a sua ou a da demo).

    ``engine``: um banco já aberto (os testes passam o deles); sem ele, abre o do config,
    aplicando as migrações pendentes, como o resto do app. ``pasta_interface``: o build da
    interface (os testes passam um falso).
    """

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        proprio = engine is None
        app.state.engine = (
            abrir_banco(cfg.caminho_banco, cfg.pasta_backups_efetiva) if proprio else engine
        )
        try:
            yield
        finally:
            if proprio:
                app.state.engine.dispose()

    app = FastAPI(
        title="Clipador: revisão",
        version=__version__,
        description=(
            "Revise os momentos da live: aprove ou recuse, escolha formatos, destinos, título "
            "e hashtags, e ajuste o corte. Nada é publicado sem a sua aprovação."
        ),
        lifespan=ciclo_de_vida,
    )
    app.state.cfg = cfg
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS_PERMITIDOS)

    @app.middleware("http")
    async def so_da_mesma_origem(request: Request, chamar_rota):
        origem = request.headers.get("origin")
        if request.method not in METODOS_SEGUROS and origem is not None:
            esperada = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origem != esperada:
                return JSONResponse(
                    {"detail": f"Pedido de outra origem ({origem}) recusado."}, status_code=403
                )
        return await chamar_rota(request)

    _registrar_erros(app)
    _registrar_rotas(app)
    _registrar_interface(app, pasta_interface)  # por último: pega o que nenhuma rota pegou
    return app


# --------------------------------------------------------------------------- dependências


def sessao_do_banco(request: Request) -> Iterator[Session]:
    with nova_sessao(request.app.state.engine) as sessao:
        yield sessao


def config_da_api(request: Request) -> Config:
    return request.app.state.cfg


Sessao = Annotated[Session, Depends(sessao_do_banco)]
Cfg = Annotated[Config, Depends(config_da_api)]


def _registrar_erros(app: FastAPI) -> None:
    def responder(status: int):
        async def tratar(_request: Request, erro: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(erro)}, status_code=status)

        return tratar

    app.add_exception_handler(decisoes.MomentoNaoEncontradoError, responder(404))
    app.add_exception_handler(decisoes.DecisaoNaoEncontradaError, responder(404))
    app.add_exception_handler(decisoes.RevisaoBloqueadaError, responder(409))
    app.add_exception_handler(decisoes.DecisaoInvalidaError, responder(422))
    app.add_exception_handler(aprendizado.SugestaoMudouError, responder(409))
    app.add_exception_handler(aprendizado.NadaParaAceitarError, responder(409))

    async def pedido_invalido(_request: Request, erro: RequestValidationError) -> JSONResponse:
        # Igual ao padrão do FastAPI, mas sem devolver o valor recebido: um "NaN" ou
        # "Infinity" no pedido quebraria a resposta (JSON não aceita), virando um erro 500.
        detalhes = [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in erro.errors()]
        return JSONResponse({"detail": detalhes}, status_code=422)

    app.add_exception_handler(RequestValidationError, pedido_invalido)


# --------------------------------------------------------------------------- montagem


def _url_do_video(clipe: Clipe) -> str:
    return f"/api/clipes/{clipe.id}/video"


def montar_momento(
    sessao: Session, cfg: Config, momento: Momento, posicao: int
) -> esquemas.MomentoSaida:
    gravacao = sessao.get(Gravacao, momento.gravacao_id)
    clipes = consultas.clipes_do_momento(sessao, momento.id)
    decisao = sessao.exec(select(Decisao).where(Decisao.momento_id == momento.id)).first()
    formatos = [clipe.formato.value for clipe in clipes]
    clipe_por_id = {clipe.id: clipe for clipe in clipes}
    return esquemas.MomentoSaida(
        id=momento.id,
        gravacao_id=momento.gravacao_id,
        dia=consultas.dia_da_gravacao(gravacao),
        posicao=posicao,
        nota=momento.nota,
        motivo=momento.motivo,
        sinais=momento.sinais,
        inicio_s=momento.inicio_s,
        fim_s=momento.fim_s,
        duracao_s=round(momento.fim_s - momento.inicio_s, 3),
        pico_s=momento.pico_s,
        duracao_gravacao_s=gravacao.duracao_s,
        inicio_detectado_s=momento.inicio_detectado_s,
        fim_detectado_s=momento.fim_detectado_s,
        titulos_sugeridos=momento.titulos_sugeridos,
        hashtags_sugeridas=momento.hashtags_sugeridas,
        clipes=[
            esquemas.ClipeSaida(
                id=clipe.id,
                formato=clipe.formato.value,
                duracao_s=clipe.duracao_s,
                video=_url_do_video(clipe),
                disponivel=Path(clipe.arquivo).is_file(),
            )
            for clipe in clipes
        ],
        thumbnail=(
            f"/api/momentos/{momento.id}/thumbnail"
            if caminho_da_thumbnail(cfg, gravacao, momento).is_file()
            else None
        ),
        destinos_padrao={f: list(cfg.revisao.destinos_padrao.get(f, [])) for f in formatos},
        decisao=esquemas.DecisaoSaida.model_validate(decisao, from_attributes=True)
        if decisao
        else None,
        recortando=consultas.recorte_pendente(sessao, momento.id),
        publicado=consultas.publicado(sessao, momento.id),
        publicacoes=[
            esquemas.PublicacaoSaida(
                id=p.id,
                plataforma=p.plataforma.value,
                formato=clipe_por_id[p.clipe_id].formato.value,
                short=pedido.e_short(
                    clipe_por_id[p.clipe_id].formato.value.startswith("vertical"),
                    clipe_por_id[p.clipe_id].duracao_s,
                ),
                status=planejar.status_visivel(sessao, p),
                agendada_para=p.agendada_para,
                link=p.link,
                enviada_em=p.enviada_em,
            )
            for p in planejar.publicacoes_do_momento(sessao, momento.id)
        ],
    )


def _posicao_no_dia(sessao: Session, momento: Momento) -> int:
    gravacao = sessao.get(Gravacao, momento.gravacao_id)
    do_dia = consultas.momentos_do_dia(sessao, consultas.dia_da_gravacao(gravacao))
    ids = [m.id for m in do_dia]
    # Um momento de uma gravação ainda em processamento não está na lista do dia.
    return ids.index(momento.id) + 1 if momento.id in ids else 0


def _momento_ou_404(sessao: Session, momento_id: int) -> Momento:
    momento = sessao.get(Momento, momento_id)
    if momento is None:
        raise decisoes.MomentoNaoEncontradoError(f"Não existe o momento {momento_id}.")
    return momento


def _tarefa(tarefa: Tarefa) -> esquemas.TarefaSaida:
    return esquemas.TarefaSaida(
        id=tarefa.id,
        tipo=tarefa.tipo.value,
        status=tarefa.status.value,
        gravacao_id=tarefa.gravacao_id,
        momento_id=tarefa.momento_id,
        tentativas=tarefa.tentativas,
        ultimo_erro=tarefa.ultimo_erro,
        atualizada_em=tarefa.atualizada_em,
    )


def _dia(resumo: consultas.ResumoDia) -> esquemas.DiaSaida:
    return esquemas.DiaSaida(
        dia=resumo.dia,
        momentos=resumo.momentos,
        decididos=resumo.decididos,
        pendentes=resumo.pendentes,
    )


# --------------------------------------------------------------------------- rotas


def _registrar_rotas(app: FastAPI) -> None:
    @app.get("/api/dias", tags=["revisão"], summary="Dias com momentos para revisar")
    def dias(sessao: Sessao) -> list[esquemas.DiaSaida]:
        return [_dia(resumo) for resumo in consultas.dias_com_momentos(sessao)]

    @app.get("/api/momentos", tags=["revisão"], summary="Momentos do dia, por nota")
    def listar_momentos(
        sessao: Sessao,
        cfg: Cfg,
        dia: Annotated[
            date | None, Query(description="Dia da live (AAAA-MM-DD). Padrão: o mais recente.")
        ] = None,
        ver_mais: Annotated[
            bool, Query(description="Traz também os que passam do limite diário.")
        ] = False,
    ) -> esquemas.ListaMomentos:
        if dia is None:
            dias_com_momentos = consultas.dias_com_momentos(sessao)
            dia = dias_com_momentos[0].dia if dias_com_momentos else None
        todos = consultas.momentos_do_dia(sessao, dia) if dia else []
        mostrar = todos if ver_mais else todos[: cfg.limite_diario]
        decididos = set(
            sessao.exec(
                select(Decisao.momento_id).where(col(Decisao.momento_id).in_([m.id for m in todos]))
            ).all()
        )
        return esquemas.ListaMomentos(
            dia=dia,
            limite_diario=cfg.limite_diario,
            total=len(todos),
            pendentes=sum(m.id not in decididos for m in todos),
            restantes=len(todos) - len(mostrar),
            momentos=[
                montar_momento(sessao, cfg, momento, posicao)
                for posicao, momento in enumerate(mostrar, start=1)
            ],
        )

    @app.get("/api/momentos/{momento_id}", tags=["revisão"], summary="Um momento")
    def ver_momento(momento_id: int, sessao: Sessao, cfg: Cfg) -> esquemas.MomentoSaida:
        momento = _momento_ou_404(sessao, momento_id)
        return montar_momento(sessao, cfg, momento, _posicao_no_dia(sessao, momento))

    @app.post("/api/momentos/{momento_id}/decisao", tags=["revisão"], summary="Aprovar ou recusar")
    def decidir(
        momento_id: int, pedido: esquemas.PedidoDecisao, sessao: Sessao, cfg: Cfg
    ) -> esquemas.MomentoSaida:
        decisoes.decidir(
            sessao,
            momento_id,
            aprovado=pedido.aprovado,
            destinos=pedido.destinos,
            titulo=pedido.titulo,
            hashtags=pedido.hashtags,
            tipo_titulo=pedido.tipo_titulo,
            cfg=cfg,  # a aprovação já põe as publicações na fila (etapa 15)
        )
        return ver_momento(momento_id, sessao, cfg)

    @app.delete(
        "/api/momentos/{momento_id}/decisao", tags=["revisão"], summary="Desfazer a decisão"
    )
    def desfazer(momento_id: int, sessao: Sessao, cfg: Cfg) -> esquemas.MomentoSaida:
        decisoes.desfazer(sessao, momento_id)
        return ver_momento(momento_id, sessao, cfg)

    @app.post("/api/momentos/{momento_id}/ajuste", tags=["revisão"], summary="Ajustar o corte")
    def ajustar(
        momento_id: int, pedido: esquemas.PedidoAjuste, sessao: Sessao, cfg: Cfg
    ) -> esquemas.RespostaAjuste:
        tarefa = decisoes.ajustar(
            sessao,
            momento_id,
            pedido.inicio_s,
            pedido.fim_s,
            duracao_minima_s=cfg.deteccao.duracao_minima_s,
            duracao_maxima_s=cfg.deteccao.duracao_maxima_s,
        )
        return esquemas.RespostaAjuste(
            tarefa_id=tarefa.id if tarefa else None, momento=ver_momento(momento_id, sessao, cfg)
        )

    @app.get(
        "/api/clipes/{clipe_id}/video",
        tags=["arquivos"],
        summary="Vídeo do clipe (com Range)",
        response_class=FileResponse,
    )
    def video(clipe_id: int, sessao: Sessao) -> FileResponse:
        clipe = sessao.get(Clipe, clipe_id)
        if clipe is None:
            raise HTTPException(404, f"Não existe o clipe {clipe_id}.")
        arquivo = Path(clipe.arquivo)
        if not arquivo.is_file():
            raise HTTPException(404, f"O arquivo do clipe {clipe_id} não existe mais.")
        # O FileResponse do Starlette já atende o Range (responde 206 com o pedaço pedido).
        # no-cache: depois de um ajuste o endereço é o mesmo e o arquivo é outro; assim o
        # navegador confere (pelo ETag) antes de usar a cópia guardada.
        return FileResponse(arquivo, media_type="video/mp4", headers=SEM_CACHE_VELHO)

    @app.get(
        "/api/momentos/{momento_id}/thumbnail",
        tags=["arquivos"],
        summary="Thumbnail do momento",
        response_class=FileResponse,
    )
    def thumbnail(momento_id: int, sessao: Sessao, cfg: Cfg) -> FileResponse:
        momento = _momento_ou_404(sessao, momento_id)
        arquivo = caminho_da_thumbnail(cfg, sessao.get(Gravacao, momento.gravacao_id), momento)
        if not arquivo.is_file():
            raise HTTPException(404, f"O momento {momento_id} não tem thumbnail.")
        return FileResponse(arquivo, media_type="image/jpeg", headers=SEM_CACHE_VELHO)

    @app.get("/api/status", tags=["painel"], summary="Fila, erros e cota do YouTube")
    def status(sessao: Sessao) -> esquemas.StatusSaida:
        resumo = fila.resumo(sessao)
        uso = cota.cota_youtube(sessao, agora())
        return esquemas.StatusSaida(
            fila={situacao.value: resumo.por_status[situacao] for situacao in StatusTarefa},
            em_andamento=[_tarefa(t) for t in resumo.em_andamento],
            erros=[_tarefa(t) for t in resumo.com_erro],
            cota_youtube=esquemas.CotaSaida(**vars(uso)),
            dias=[_dia(resumo_dia) for resumo_dia in consultas.dias_com_momentos(sessao)],
        )

    @app.get("/api/estatisticas", tags=["painel"], summary="Dados dos gráficos")
    def estatisticas(sessao: Sessao) -> esquemas.EstatisticasSaida:
        return esquemas.EstatisticasSaida(**vars(consultas.estatisticas(sessao)))

    @app.get("/api/aprendizado", tags=["aprendizado"], summary="O que as decisões ensinam")
    def ver_aprendizado(sessao: Sessao, cfg: Cfg) -> esquemas.AprendizadoSaida:
        return _aprendizado(aprendizado.sugerir(sessao, cfg))

    @app.post("/api/aprendizado/aceitar", tags=["aprendizado"], summary="Aceitar a sugestão")
    def aceitar(
        pedido: esquemas.PedidoAceite, sessao: Sessao, cfg: Cfg
    ) -> esquemas.AprendizadoSaida:
        aprendizado.aceitar(sessao, cfg, pedido.decisoes)
        return _aprendizado(aprendizado.sugerir(sessao, cfg))

    @app.get("/api/config", tags=["painel"], summary="O que a interface precisa do config")
    def config(cfg: Cfg) -> esquemas.ConfigSaida:
        return esquemas.ConfigSaida(
            limite_diario=cfg.limite_diario,
            duracao_minima_s=cfg.deteccao.duracao_minima_s,
            duracao_maxima_s=cfg.deteccao.duracao_maxima_s,
            formatos=list(cfg.cortes.formatos),
            plataformas=[p.value for p in Plataforma],
            destinos_padrao={f: list(d) for f, d in cfg.revisao.destinos_padrao.items()},
            max_hashtags=cfg.titulos.max_hashtags,
            limite_titulo=LIMITE_TITULO_YOUTUBE,
        )


def _aprendizado(s: aprendizado.Sugestao) -> esquemas.AprendizadoSaida:
    j = s.janela_nova
    return esquemas.AprendizadoSaida(
        decisoes=s.decisoes,
        aprovadas=s.aprovadas,
        minimo_para_pesos=s.minimo_para_pesos,
        versao_atual=s.versao_atual,
        pesos_atuais=s.pesos_atuais,
        pesos_novos=s.pesos_novos,
        precisao_antes=s.precisao_antes,
        precisao_depois=s.precisao_depois,
        sem_pesos_porque=s.sem_pesos_porque,
        janela_atual=s.janela_atual,
        janela_nova=esquemas.JanelaSugerida(**vars(j)) if j else None,
        sem_janela_porque=s.sem_janela_porque,
        precisao_por_dia=s.precisao_por_dia,
        versoes=s.versoes,
    )


# --------------------------------------------------------------------------- interface

SEM_INTERFACE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Clipador</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:3rem auto">
<h1>A interface de revisão ainda não foi construída</h1>
<p>Para construí-la (uma vez só; precisa do Node.js):</p>
<pre>cd frontend
npm ci
npm run build</pre>
<p>Enquanto isso, dá para revisar tudo pela <a href="/docs">documentação interativa</a>.</p>
</body></html>"""


def _registrar_interface(app: FastAPI, pasta: Path) -> None:
    """Serve o build da interface (``frontend/dist``): os arquivos e, para o resto, o index.

    Os arquivos de ``assets/`` têm o conteúdo no nome (ex.: ``index-3f2a1b.js``), então o
    navegador pode guardá-los para sempre; o ``index.html`` não (ele aponta para os novos).
    """
    pasta = pasta.resolve()

    @app.get("/{caminho:path}", include_in_schema=False)
    def interface(caminho: str) -> Response:
        if caminho == "api" or caminho.startswith("api/"):
            raise HTTPException(404, f"Não existe a rota /{caminho}.")
        indice = pasta / "index.html"
        if not indice.is_file():
            return HTMLResponse(SEM_INTERFACE)
        arquivo = (pasta / caminho).resolve()
        # is_relative_to: um "../../config.toml" no endereço não sai da pasta do build.
        if caminho and arquivo.is_relative_to(pasta) and arquivo.is_file():
            if arquivo.parent == pasta / "assets":
                cache = {"Cache-Control": "public, max-age=31536000, immutable"}
            else:
                cache = SEM_CACHE_VELHO
            return FileResponse(arquivo, headers=cache)
        # Qualquer outro endereço (ex.: /estatisticas) é uma tela da interface.
        return FileResponse(indice, headers=SEM_CACHE_VELHO)
