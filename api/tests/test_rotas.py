"""Cada rota da API da revisão (etapa 12), com um banco montado à mão."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import select

from clipador.banco.modelos import (
    Clipe,
    Decisao,
    Plataforma,
    Publicacao,
    StatusGravacao,
    StatusPublicacao,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
)
from clipador.revisao import decisoes

APROVACAO = {
    "aprovado": True,
    "destinos": {"vertical_a": ["youtube", "tiktok"], "horizontal": ["youtube"]},
    "titulo": "NÃO ACREDITO NESSA",
    "hashtags": ["#leagueoflegends", "lolbr", "#LolBR"],
}


# ----------------------------------------------------------------- lista do dia


def test_lista_o_dia_mais_recente_por_nota_ate_o_limite_diario(cliente, dois_dias):
    resposta = cliente.get("/api/momentos")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["dia"] == "2026-10-23"
    assert corpo["limite_diario"] == 15
    assert corpo["total"] == 20
    assert corpo["pendentes"] == 20
    assert corpo["restantes"] == 5
    notas = [m["nota"] for m in corpo["momentos"]]
    assert len(notas) == 15
    assert notas == sorted(notas, reverse=True)
    # Os 15 mostrados são os de maior nota do dia.
    todas = sorted((m.nota for m in dois_dias["momentos_hoje"]), reverse=True)
    assert notas == todas[:15]
    assert [m["posicao"] for m in corpo["momentos"]] == list(range(1, 16))


def test_ver_mais_traz_o_resto(cliente, dois_dias):
    corpo = cliente.get("/api/momentos", params={"ver_mais": True}).json()
    assert len(corpo["momentos"]) == 20
    assert corpo["restantes"] == 0
    assert [m["posicao"] for m in corpo["momentos"]] == list(range(1, 21))


def test_limite_diario_vem_do_config(cfg, engine, dois_dias):
    from fastapi.testclient import TestClient

    from clipador_api.app import criar_app

    cfg.limite_diario = 3
    with TestClient(criar_app(cfg, engine), base_url="http://127.0.0.1") as cliente:
        corpo = cliente.get("/api/momentos").json()
    assert len(corpo["momentos"]) == 3
    assert corpo["restantes"] == 17


def test_escolhe_o_dia(cliente, dois_dias):
    corpo = cliente.get("/api/momentos", params={"dia": "2026-10-22"}).json()
    assert corpo["dia"] == "2026-10-22"
    assert [m["nota"] for m in corpo["momentos"]] == [70, 40]
    vazio = cliente.get("/api/momentos", params={"dia": "2020-01-01"}).json()
    assert vazio["momentos"] == [] and vazio["total"] == 0


def test_dia_invalido_da_422(cliente, dois_dias):
    assert cliente.get("/api/momentos", params={"dia": "ontem"}).status_code == 422


def test_sem_momentos(cliente):
    corpo = cliente.get("/api/momentos").json()
    assert corpo["dia"] is None
    assert corpo["momentos"] == []
    assert cliente.get("/api/dias").json() == []


def test_dias(cliente, dois_dias, um_momento):
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json={"aprovado": False})
    dias = cliente.get("/api/dias").json()
    assert dias == [
        {"dia": "2026-10-23", "momentos": 21, "decididos": 1, "pendentes": 20},
        {"dia": "2026-10-22", "momentos": 2, "decididos": 0, "pendentes": 2},
    ]


def test_gravacao_em_processamento_fica_de_fora(cliente, criar_gravacao, criar_momento):
    gravacao = criar_gravacao(
        datetime(2026, 10, 24, 15, tzinfo=UTC), status=StatusGravacao.PROCESSANDO
    )
    momento = criar_momento(gravacao, nota=99)
    assert cliente.get("/api/momentos").json()["momentos"] == []
    # Dá para ver o momento pelo id, mas não ajustar o corte: a etapa 8 ainda pode trocá-lo.
    assert cliente.get(f"/api/momentos/{momento.id}").json()["posicao"] == 0
    resposta = cliente.post(
        f"/api/momentos/{momento.id}/ajuste", json={"inicio_s": 90, "fim_s": 120}
    )
    assert resposta.status_code == 409


def test_momento_traz_o_que_a_revisao_precisa(cliente, um_momento):
    momento = cliente.get(f"/api/momentos/{um_momento.id}").json()
    assert momento["posicao"] == 1
    assert momento["duracao_s"] == 20
    assert [t["titulo"] for t in momento["titulos_sugeridos"]][0] == "NÃO ACREDITO NESSA"
    assert momento["hashtags_sugeridas"] == ["#leagueoflegends", "#lolbr", "#pentakill"]
    assert {c["formato"] for c in momento["clipes"]} == {"horizontal", "vertical_a"}
    assert all(c["video"] == f"/api/clipes/{c['id']}/video" for c in momento["clipes"])
    assert momento["thumbnail"] == f"/api/momentos/{um_momento.id}/thumbnail"
    # Destinos padrão do config, só para os formatos que foram cortados.
    assert momento["destinos_padrao"] == {
        "horizontal": ["youtube"],
        "vertical_a": ["youtube", "tiktok"],
    }
    assert momento["decisao"] is None
    assert momento["recortando"] is False
    assert momento["publicado"] is False


def test_momento_sem_clipes(cliente, criar_gravacao, criar_momento):
    gravacao = criar_gravacao(datetime(2026, 10, 23, 15, tzinfo=UTC), status=StatusGravacao.ERRO)
    momento = criar_momento(gravacao, nota=50, formatos=())
    corpo = cliente.get(f"/api/momentos/{momento.id}").json()
    assert corpo["clipes"] == [] and corpo["thumbnail"] is None
    assert corpo["destinos_padrao"] == {}


@pytest.mark.parametrize(
    ("metodo", "rota"),
    [
        ("get", "/api/momentos/999"),
        ("get", "/api/momentos/999/thumbnail"),
        ("delete", "/api/momentos/999/decisao"),
        ("get", "/api/clipes/999/video"),
    ],
)
def test_inexistente_da_404(cliente, metodo, rota):
    assert getattr(cliente, metodo)(rota).status_code == 404


def test_decisao_e_ajuste_de_momento_inexistente_dao_404(cliente):
    assert cliente.post("/api/momentos/999/decisao", json={"aprovado": False}).status_code == 404
    resposta = cliente.post("/api/momentos/999/ajuste", json={"inicio_s": 1, "fim_s": 20})
    assert resposta.status_code == 404
    assert "999" in resposta.json()["detail"]


# ----------------------------------------------------------------- decisão


def test_aprovar_salva_formatos_destinos_titulo_e_hashtags(cliente, sessao, um_momento):
    resposta = cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=APROVACAO)
    assert resposta.status_code == 200
    decisao = resposta.json()["decisao"]
    assert decisao["aprovado"] is True
    assert decisao["destinos"] == APROVACAO["destinos"]
    assert decisao["formatos"] == ["vertical_a", "horizontal"]
    assert decisao["titulo"] == "NÃO ACREDITO NESSA"
    # "#" acrescentado e repetida (sem diferenciar maiúsculas) removida.
    assert decisao["hashtags"] == ["#leagueoflegends", "#lolbr"]
    # O título é o da 1ª sugestão: o tipo dela vai junto (exemplo de estilo na etapa 10).
    assert decisao["tipo_titulo"] == "frase_sua"
    salva = sessao.exec(select(Decisao).where(Decisao.momento_id == um_momento.id)).one()
    assert salva.aprovado and salva.titulo == "NÃO ACREDITO NESSA"


def test_titulo_editado_fica_sem_tipo(cliente, um_momento):
    pedido = {**APROVACAO, "titulo": "  meu   título\n editado "}
    decisao = cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=pedido).json()["decisao"]
    assert decisao["titulo"] == "meu título editado"
    assert decisao["tipo_titulo"] is None


def test_recusar_nao_guarda_titulo_nem_destinos(cliente, um_momento):
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=APROVACAO)
    decisao = cliente.post(
        f"/api/momentos/{um_momento.id}/decisao", json={**APROVACAO, "aprovado": False}
    ).json()["decisao"]
    assert decisao == {
        **decisao,
        "aprovado": False,
        "formatos": [],
        "destinos": {},
        "titulo": None,
        "hashtags": [],
        "tipo_titulo": None,
    }


def test_decidir_de_novo_troca_a_decisao(cliente, sessao, um_momento):
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json={"aprovado": False})
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=APROVACAO)
    decisoes_salvas = sessao.exec(select(Decisao).where(Decisao.momento_id == um_momento.id)).all()
    assert len(decisoes_salvas) == 1 and decisoes_salvas[0].aprovado


@pytest.mark.parametrize(
    ("mudanca", "trecho_do_erro"),
    [
        ({"titulo": ""}, "título"),
        ({"titulo": None}, "título"),
        ({"titulo": "x" * 101}, "101 caracteres"),
        ({"destinos": {}}, "ao menos um formato"),
        ({"destinos": {"vertical_a": []}}, "ao menos um formato"),
        ({"destinos": {"vertical_b": ["youtube"]}}, "vertical_b"),
        ({"destinos": {"quadrado": ["youtube"]}}, "quadrado"),
        ({"destinos": {"vertical_a": ["instagram"]}}, "instagram"),
        ({"hashtags": ["#com espaço"]}, "Hashtag inválida"),
        ({"hashtags": [f"#h{i}" for i in range(16)]}, "No máximo 15"),
        ({"tipo_titulo": "meme"}, "Tipo de título desconhecido"),
    ],
)
def test_aprovacao_invalida_da_422_e_nao_salva(
    cliente, sessao, um_momento, mudanca, trecho_do_erro
):
    resposta = cliente.post(f"/api/momentos/{um_momento.id}/decisao", json={**APROVACAO, **mudanca})
    assert resposta.status_code == 422
    assert trecho_do_erro in str(resposta.json()["detail"])
    assert sessao.exec(select(Decisao)).all() == []


def test_erro_ao_mudar_nao_estraga_a_decisao_salva(sessao, um_momento):
    """Na mesma sessão (como na etapa 15), uma mudança recusada não vaza para o próximo commit."""
    decisoes.decidir(sessao, um_momento.id, aprovado=True, destinos=APROVACAO["destinos"],
                     titulo="Título bom")  # fmt: skip
    with pytest.raises(decisoes.DecisaoInvalidaError):
        decisoes.decidir(sessao, um_momento.id, aprovado=True, destinos={}, titulo="Outro")
    sessao.commit()  # qualquer commit depois, de outra coisa
    sessao.expire_all()
    salva = sessao.exec(select(Decisao)).one()
    assert salva.aprovado and salva.titulo == "Título bom"
    assert salva.destinos == APROVACAO["destinos"]


def test_pedido_mal_formado_da_422(cliente, um_momento):
    assert cliente.post(f"/api/momentos/{um_momento.id}/decisao", json={}).status_code == 422


def test_desfazer(cliente, sessao, um_momento):
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=APROVACAO)
    resposta = cliente.delete(f"/api/momentos/{um_momento.id}/decisao")
    assert resposta.status_code == 200
    assert resposta.json()["decisao"] is None
    assert sessao.exec(select(Decisao)).all() == []
    # Desfazer o que não existe.
    assert cliente.delete(f"/api/momentos/{um_momento.id}/decisao").status_code == 404


def _publicar(sessao, momento_id: int) -> None:
    clipe = sessao.exec(select(Clipe).where(Clipe.momento_id == momento_id)).first()
    sessao.add(Publicacao(clipe_id=clipe.id, plataforma=Plataforma.YOUTUBE))
    sessao.commit()


def test_depois_de_publicado_nada_muda(cliente, sessao, um_momento):
    cliente.post(f"/api/momentos/{um_momento.id}/decisao", json=APROVACAO)
    _publicar(sessao, um_momento.id)
    rota = f"/api/momentos/{um_momento.id}"
    assert cliente.get(rota).json()["publicado"] is True
    assert cliente.delete(f"{rota}/decisao").status_code == 409
    assert cliente.post(f"{rota}/decisao", json={"aprovado": False}).status_code == 409
    resposta = cliente.post(f"{rota}/ajuste", json={"inicio_s": 95, "fim_s": 125})
    assert resposta.status_code == 409
    assert "publicação" in resposta.json()["detail"]


# ----------------------------------------------------------------- ajuste do corte


def test_ajuste_muda_o_trecho_e_cria_a_tarefa_de_novo_corte(cliente, sessao, um_momento):
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/ajuste", json={"inicio_s": 95.5, "fim_s": 130}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    tarefa = sessao.get(Tarefa, corpo["tarefa_id"])
    assert tarefa.tipo is TipoTarefa.RECORTAR
    assert tarefa.momento_id == um_momento.id
    assert tarefa.gravacao_id == um_momento.gravacao_id
    assert tarefa.status is StatusTarefa.PENDENTE
    momento = corpo["momento"]
    assert (momento["inicio_s"], momento["fim_s"], momento["duracao_s"]) == (95.5, 130, 34.5)
    # O trecho do detector fica guardado (a etapa 14 aprende com a diferença).
    assert (momento["inicio_detectado_s"], momento["fim_detectado_s"]) == (100, 120)
    assert momento["recortando"] is True


def test_segundo_ajuste_reaproveita_a_tarefa_que_espera(cliente, sessao, um_momento):
    rota = f"/api/momentos/{um_momento.id}/ajuste"
    primeira = cliente.post(rota, json={"inicio_s": 95, "fim_s": 125}).json()
    segunda = cliente.post(rota, json={"inicio_s": 90, "fim_s": 125}).json()
    assert primeira["tarefa_id"] == segunda["tarefa_id"]
    assert len(sessao.exec(select(Tarefa)).all()) == 1
    # O trecho detectado continua o original, não o do 1º ajuste.
    assert segunda["momento"]["inicio_detectado_s"] == 100


def test_ajuste_com_o_corte_rodando_cria_outra_tarefa(cliente, sessao, um_momento):
    rota = f"/api/momentos/{um_momento.id}/ajuste"
    primeira = cliente.post(rota, json={"inicio_s": 95, "fim_s": 125}).json()
    tarefa = sessao.get(Tarefa, primeira["tarefa_id"])
    tarefa.status = StatusTarefa.EM_ANDAMENTO  # o serviço pegou: corta o trecho de antes
    sessao.add(tarefa)
    sessao.commit()
    segunda = cliente.post(rota, json={"inicio_s": 90, "fim_s": 125}).json()
    assert segunda["tarefa_id"] != primeira["tarefa_id"]


@pytest.mark.parametrize("valor", ["NaN", "Infinity"])
def test_ajuste_com_nan_ou_infinito_da_422(cliente, um_momento, valor):
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/ajuste",
        content=f'{{"inicio_s": 10, "fim_s": {valor}}}',
        headers={"Content-Type": "application/json"},
    )
    assert resposta.status_code == 422


def test_ajuste_igual_nao_cria_tarefa(cliente, sessao, um_momento):
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/ajuste", json={"inicio_s": 100, "fim_s": 120}
    )
    assert resposta.status_code == 200
    assert resposta.json()["tarefa_id"] is None
    assert sessao.exec(select(Tarefa)).all() == []


@pytest.mark.parametrize(
    ("inicio_s", "fim_s", "trecho_do_erro"),
    [
        (-1, 20, "antes do começo"),
        (3590, 3601, "passa do fim"),
        (100, 100.5, "0.5 s"),
        (130, 100, "-30.0 s"),
        (100, 300, "200.0 s"),
    ],
)
def test_ajuste_invalido_da_422(cliente, sessao, um_momento, inicio_s, fim_s, trecho_do_erro):
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/ajuste", json={"inicio_s": inicio_s, "fim_s": fim_s}
    )
    assert resposta.status_code == 422
    assert trecho_do_erro in resposta.json()["detail"]
    assert sessao.exec(select(Tarefa)).all() == []
    sessao.refresh(um_momento)
    assert (um_momento.inicio_s, um_momento.fim_s) == (100, 120)


def test_ajuste_vai_para_a_decisao_antes_ou_depois_dela(cliente, sessao, um_momento):
    rota = f"/api/momentos/{um_momento.id}"
    cliente.post(f"{rota}/ajuste", json={"inicio_s": 97, "fim_s": 125})
    decisao = cliente.post(f"{rota}/decisao", json=APROVACAO).json()["decisao"]
    assert (decisao["ajuste_inicio_s"], decisao["ajuste_fim_s"]) == (-3, 5)
    # Um ajuste depois de aprovar atualiza a decisão (a publicação espera o novo corte).
    momento = cliente.post(f"{rota}/ajuste", json={"inicio_s": 101, "fim_s": 118}).json()
    assert (momento["momento"]["decisao"]["ajuste_inicio_s"]) == 1
    assert (momento["momento"]["decisao"]["ajuste_fim_s"]) == -2
    assert momento["momento"]["decisao"]["aprovado"] is True


def test_publicacao_espera_o_novo_corte(sessao, um_momento):
    """A regra que a etapa 15 vai usar: com um recorte na fila, não publica ainda."""
    from clipador.revisao.consultas import recorte_pendente

    assert not recorte_pendente(sessao, um_momento.id)
    tarefa = decisoes.ajustar(sessao, um_momento.id, 95, 125)
    assert recorte_pendente(sessao, um_momento.id)
    tarefa.status = StatusTarefa.FEITA
    sessao.add(tarefa)
    sessao.commit()
    assert not recorte_pendente(sessao, um_momento.id)


# ----------------------------------------------------------------- arquivos


def test_video_inteiro(cliente, um_momento):
    clipe = cliente.get(f"/api/momentos/{um_momento.id}").json()["clipes"][0]
    resposta = cliente.get(clipe["video"])
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "video/mp4"
    assert resposta.headers["accept-ranges"] == "bytes"
    assert resposta.headers["cache-control"] == "no-cache"  # o recorte troca o arquivo
    assert len(resposta.content) == 10240


def test_video_com_range_devolve_206_e_so_o_pedaco(cliente, sessao, um_momento):
    clipe = sessao.exec(select(Clipe).where(Clipe.momento_id == um_momento.id)).first()
    conteudo = Path(clipe.arquivo).read_bytes()
    resposta = cliente.get(f"/api/clipes/{clipe.id}/video", headers={"Range": "bytes=100-199"})
    assert resposta.status_code == 206
    assert resposta.headers["content-range"] == f"bytes 100-199/{len(conteudo)}"
    assert resposta.content == conteudo[100:200]
    # "Do byte 10000 até o fim" (o player pede assim ao pular para o fim do vídeo).
    resposta = cliente.get(f"/api/clipes/{clipe.id}/video", headers={"Range": "bytes=10000-"})
    assert resposta.status_code == 206
    assert resposta.content == conteudo[10000:]
    # Um pedaço que não existe.
    resposta = cliente.get(f"/api/clipes/{clipe.id}/video", headers={"Range": "bytes=99999-"})
    assert resposta.status_code == 416


def test_thumbnail(cliente, um_momento):
    resposta = cliente.get(f"/api/momentos/{um_momento.id}/thumbnail")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/jpeg"


def test_video_apagado_da_404(cliente, sessao, um_momento):
    clipe = sessao.exec(select(Clipe).where(Clipe.momento_id == um_momento.id)).first()
    Path(clipe.arquivo).unlink()
    resposta = cliente.get(f"/api/clipes/{clipe.id}/video")
    assert resposta.status_code == 404
    assert "não existe mais" in resposta.json()["detail"]
    momento = cliente.get(f"/api/momentos/{um_momento.id}").json()
    disponiveis = {c["id"]: c["disponivel"] for c in momento["clipes"]}
    assert disponiveis[clipe.id] is False
    assert sum(disponiveis.values()) == 1  # o outro formato continua lá


# ----------------------------------------------------------------- painel


def test_status(cliente, sessao, um_momento):
    decisoes.ajustar(sessao, um_momento.id, 95, 125)  # uma tarefa pendente
    erro = Tarefa(
        tipo=TipoTarefa.TRANSCREVER, status=StatusTarefa.ERRO, ultimo_erro="sem faixa da voz"
    )
    sessao.add(erro)
    clipe = sessao.exec(select(Clipe).where(Clipe.momento_id == um_momento.id)).first()
    sessao.add(
        Publicacao(
            clipe_id=clipe.id,
            plataforma=Plataforma.YOUTUBE,
            status=StatusPublicacao.AGENDADA,
            unidades_cota=1650,
        )
    )
    sessao.add(Publicacao(clipe_id=clipe.id, plataforma=Plataforma.TIKTOK, unidades_cota=0))
    sessao.commit()
    corpo = cliente.get("/api/status").json()
    assert corpo["fila"] == {"pendente": 1, "em_andamento": 0, "feita": 0, "erro": 1}
    assert [t["ultimo_erro"] for t in corpo["erros"]] == ["sem faixa da voz"]
    assert corpo["cota_youtube"]["envios"] == 1
    assert corpo["cota_youtube"]["limite_envios"] == 100
    assert corpo["cota_youtube"]["unidades"] == 1650
    assert corpo["cota_youtube"]["limite_unidades"] == 10_000
    assert corpo["dias"][0]["pendentes"] == 1


def test_estatisticas(cliente, dois_dias):
    ids = [m["id"] for m in cliente.get("/api/momentos").json()["momentos"]]
    for momento_id in ids[:3]:
        cliente.post(f"/api/momentos/{momento_id}/decisao", json=APROVACAO)
    cliente.post(f"/api/momentos/{ids[-1]}/decisao", json={"aprovado": False})
    corpo = cliente.get("/api/estatisticas").json()
    assert corpo["momentos"] == 22
    assert (corpo["aprovados"], corpo["recusados"]) == (3, 1)
    assert corpo["precisao"] == 0.75
    assert corpo["formatos"] == {"vertical_a": 3, "horizontal": 3}
    assert corpo["plataformas"] == {"youtube": 3, "tiktok": 3}
    assert [d["dia"] for d in corpo["por_dia"]] == ["2026-10-22", "2026-10-23"]
    assert sum(f["momentos"] for f in corpo["por_nota"]) == 22
    assert [f["faixa"] for f in corpo["por_nota"]] == [
        "0-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90-100"
    ]  # fmt: skip
    assert corpo["ajuste_medio_inicio_s"] is None


def test_estatisticas_vazias(cliente):
    corpo = cliente.get("/api/estatisticas").json()
    assert corpo["momentos"] == 0 and corpo["precisao"] is None
    assert corpo["por_nota"] == [] and corpo["por_dia"] == []


def test_docs_abre(cliente):
    assert cliente.get("/docs").status_code == 200
    rotas = cliente.get("/openapi.json").json()["paths"]
    assert {
        "/api/momentos",
        "/api/momentos/{momento_id}/decisao",
        "/api/momentos/{momento_id}/ajuste",
        "/api/clipes/{clipe_id}/video",
        "/api/status",
        "/api/estatisticas",
    } <= set(rotas)
