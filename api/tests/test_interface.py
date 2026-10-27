"""O que a etapa 13 acrescentou à API: /api/config, a interface servida pelo FastAPI e os
limites do ajuste vindos do config."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from clipador.banco.modelos import StatusGravacao
from clipador_api.app import criar_app


@pytest.fixture
def build(tmp_path):
    """Um build falso da interface (o que o 'npm run build' gera em frontend/dist)."""
    pasta = tmp_path / "dist"
    (pasta / "assets").mkdir(parents=True)
    (pasta / "index.html").write_text("<!doctype html><title>Clipador</title>", encoding="utf-8")
    (pasta / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (pasta / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("não pode sair", encoding="utf-8")
    return pasta


@pytest.fixture
def cliente_com_interface(cfg, engine, build):
    with TestClient(criar_app(cfg, engine, build), base_url="http://127.0.0.1") as cliente:
        yield cliente


def test_config_para_a_interface(cliente):
    corpo = cliente.get("/api/config").json()
    assert corpo == {
        "limite_diario": 15,
        "duracao_minima_s": 15,
        "duracao_maxima_s": 60,
        "formatos": ["horizontal", "vertical_a", "vertical_b"],
        "plataformas": ["youtube", "tiktok"],
        "plataformas_por_formato": {  # o TikTok é vertical (etapa 16)
            "horizontal": ["youtube"],
            "vertical_a": ["youtube", "tiktok"],
            "vertical_b": ["youtube", "tiktok"],
        },
        "destinos_padrao": {
            "horizontal": ["youtube"],
            "vertical_a": ["youtube", "tiktok"],
            "vertical_b": ["youtube", "tiktok"],
        },
        "max_hashtags": 5,
        "limite_titulo": 100,
    }
    # Nada de caminhos do PC nem de chaves no que vai para o navegador.
    assert "pasta" not in str(corpo) and "C:" not in str(corpo)


def test_momento_traz_a_duracao_da_gravacao(cliente, um_momento):
    assert cliente.get(f"/api/momentos/{um_momento.id}").json()["duracao_gravacao_s"] == 3600


# ----------------------------------------------------------------- interface servida


def test_raiz_sem_build_explica_como_construir(cfg, engine, tmp_path):
    with TestClient(
        criar_app(cfg, engine, tmp_path / "nao-existe"), base_url="http://127.0.0.1"
    ) as cliente:
        resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "npm run build" in resposta.text and "/docs" in resposta.text


def test_serve_o_index_e_os_arquivos(cliente_com_interface):
    indice = cliente_com_interface.get("/")
    assert indice.status_code == 200 and "<title>Clipador</title>" in indice.text
    assert indice.headers["cache-control"] == "no-cache"
    js = cliente_com_interface.get("/assets/index-abc123.js")
    assert js.text == "console.log(1)"
    assert "immutable" in js.headers["cache-control"]  # nome com hash: guarda para sempre
    assert cliente_com_interface.get("/favicon.svg").headers["cache-control"] == "no-cache"


def test_telas_da_interface_caem_no_index(cliente_com_interface):
    for tela in ("/fila", "/estatisticas", "/qualquer/coisa"):
        assert "<title>Clipador</title>" in cliente_com_interface.get(tela).text


def test_nao_sai_da_pasta_do_build(cliente_com_interface):
    for tentativa in ("/../segredo.txt", "/%2e%2e/segredo.txt", "/assets/../../segredo.txt"):
        resposta = cliente_com_interface.get(tentativa)
        assert "não pode sair" not in resposta.text


def test_rota_da_api_inexistente_continua_404_em_json(cliente_com_interface):
    resposta = cliente_com_interface.get("/api/nao-existe")
    assert resposta.status_code == 404
    assert resposta.headers["content-type"] == "application/json"


def test_docs_continua_funcionando_com_a_interface(cliente_com_interface):
    assert "swagger" in cliente_com_interface.get("/docs").text.lower()
    assert cliente_com_interface.get("/api/dias").json() == []


# ----------------------------------------------------------------- limites do ajuste


def test_ajuste_respeita_a_janela_do_config(cfg, engine, um_momento):
    cfg.deteccao.duracao_minima_s, cfg.deteccao.duracao_maxima_s = 10, 30
    with TestClient(criar_app(cfg, engine), base_url="http://127.0.0.1") as cliente:
        rota = f"/api/momentos/{um_momento.id}/ajuste"
        assert cliente.post(rota, json={"inicio_s": 100, "fim_s": 109}).status_code == 422
        assert cliente.post(rota, json={"inicio_s": 100, "fim_s": 131}).status_code == 422
        resposta = cliente.post(rota, json={"inicio_s": 100, "fim_s": 110})
        assert resposta.status_code == 200, resposta.text
        assert "de 10 a 30 s" in cliente.post(rota, json={"inicio_s": 0, "fim_s": 5}).text


def test_gravacao_curta_aceita_a_gravacao_inteira(cliente, criar_gravacao, criar_momento, sessao):
    gravacao = criar_gravacao(datetime(2026, 10, 23, 15, tzinfo=UTC), status=StatusGravacao.ERRO)
    gravacao.duracao_s = 12  # menos que os 15 s mínimos
    sessao.add(gravacao)
    sessao.commit()
    momento = criar_momento(gravacao, nota=50, inicio_s=1)  # 1 a 21 s (do fixture)
    rota = f"/api/momentos/{momento.id}/ajuste"
    assert cliente.post(rota, json={"inicio_s": 0, "fim_s": 12}).status_code == 200
    assert cliente.post(rota, json={"inicio_s": 0, "fim_s": 11}).status_code == 422


# ----------------------------------------------------------------- sinais nas estatísticas


def test_estatisticas_por_detector(cliente, criar_gravacao, criar_momento, sessao):
    gravacao = criar_gravacao(datetime(2026, 10, 23, 15, tzinfo=UTC))
    bom = criar_momento(gravacao, nota=90)
    ruim = criar_momento(gravacao, nota=30, inicio_s=500)
    bom.sinais, ruim.sinais = {"volume_voz": 0.9, "lol": 1.0}, {"volume_voz": 0.5}
    sessao.add_all([bom, ruim])
    sessao.commit()
    cliente.post(
        f"/api/momentos/{bom.id}/decisao",
        json={"aprovado": True, "destinos": {"vertical_a": ["tiktok"]}, "titulo": "Bom"},
    )
    cliente.post(f"/api/momentos/{ruim.id}/decisao", json={"aprovado": False})
    sinais = cliente.get("/api/estatisticas").json()["sinais"]
    assert sinais == [
        {"detector": "lol", "aprovados": 1.0, "recusados": None},
        {"detector": "volume_voz", "aprovados": 0.9, "recusados": 0.5},
    ]
