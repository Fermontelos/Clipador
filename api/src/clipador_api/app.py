"""A API da revisão (etapa 12): o que a interface web (etapa 13) usa para revisar os clipes.

Rotas (todas documentadas, e testáveis no navegador, em ``/docs``):

- ``GET /api/momentos``: os momentos de um dia, da maior nota para a menor, até o limite
  diário (``limite_diario``, padrão 15). ``ver_mais=true`` traz o resto;
- ``GET /api/momentos/{id}``: um momento;
- ``POST`` / ``DELETE /api/momentos/{id}/decisao``: aprovar ou recusar / desfazer;
- ``POST /api/momentos/{id}/ajuste``: novo início e fim (põe um novo corte na fila);
- ``GET /api/clipes/{id}/video``: o vídeo, com ``Range`` (o player avança e volta);
- ``GET /api/momentos/{id}/thumbnail``: o quadro do pico (clipes horizontais);
- ``GET /api/dias``, ``GET /api/status`` e ``GET /api/estatisticas``.

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
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clipador import __version__
from clipador.banco import fila
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Gravacao,
    Momento,
    StatusTarefa,
    Tarefa,
    agora,
)
from clipador.config import Config
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador.revisao import consultas, cota, decisoes
from clipador_api import esquemas

HOSTS_PERMITIDOS = ["127.0.0.1", "localhost"]
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
SEM_CACHE_VELHO = {"Cache-Control": "no-cache"}


def criar_app(cfg: Config, engine: Engine | None = None) -> FastAPI:
    """Monta a API para a configuração dada (a sua ou a da demo).

    ``engine``: um banco já aberto (os testes passam o deles); sem ele, abre o do config,
    aplicando as migrações pendentes, como o resto do app.
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
        tarefa = decisoes.ajustar(sessao, momento_id, pedido.inicio_s, pedido.fim_s)
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
