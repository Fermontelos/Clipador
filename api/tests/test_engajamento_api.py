"""As rotas do engajamento (etapa 17): ver os rankings e a sugestão, e aceitar."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from clipador.banco.modelos import (
    Clipe,
    ColetaMetrica,
    Decisao,
    FormatoClipe,
    Gravacao,
    Metrica,
    Momento,
    PesosNota,
    Plataforma,
    Publicacao,
    StatusGravacao,
    StatusPublicacao,
)

NO_AR = datetime(2026, 10, 1, 18, tzinfo=UTC)


@pytest.fixture
def publicados(sessao):
    """60 cortes no YouTube e 20 no TikTok com 7 dias; o público gosta dos eventos do LoL."""
    sorteio = random.Random(3)
    for n in range(80):
        plataforma = Plataforma.YOUTUBE if n < 60 else Plataforma.TIKTOK
        gravacao = Gravacao(arquivo=f"C:/g/{n}.mkv", inicio=NO_AR + timedelta(days=n % 8),
                            status=StatusGravacao.CONCLUIDA)  # fmt: skip
        sessao.add(gravacao)
        sessao.flush()
        sinais = {"volume_voz": sorteio.random(), "fala": sorteio.random(), "lol": sorteio.random()}
        momento = Momento(gravacao_id=gravacao.id, inicio_s=0, fim_s=20, nota=50, sinais=sinais)
        sessao.add(momento)
        sessao.flush()
        clipe = Clipe(momento_id=momento.id, formato=FormatoClipe.VERTICAL_A, arquivo="x.mp4")
        sessao.add(clipe)
        sessao.add(Decisao(momento_id=momento.id, aprovado=True, titulo=f"corte {n}",
                           hashtags=["#lol", "#pentakill"] if sinais["lol"] > 0.7 else ["#lol"],
                           tipo_titulo="exagero"))  # fmt: skip
        sessao.flush()
        pub = Publicacao(clipe_id=clipe.id, plataforma=plataforma, id_na_plataforma=f"v{n}",
                         status=StatusPublicacao.PUBLICADA, agendada_para=NO_AR)  # fmt: skip
        sessao.add(pub)
        sessao.flush()
        visualizacoes = int(100 + 3000 * sinais["lol"] + 100 * sorteio.random())
        sessao.add(Metrica(publicacao_id=pub.id, coleta=ColetaMetrica.D7,
                           visualizacoes=visualizacoes, curtidas=visualizacoes // 20,
                           comentarios=2, retencao=0.5 if plataforma is Plataforma.YOUTUBE
                           else None))  # fmt: skip
    sessao.commit()


def test_sem_metricas(cliente):
    corpo = cliente.get("/api/engajamento").json()
    assert corpo["com_metricas"] == 0 and corpo["cortes"] == []
    assert corpo["sugestao"]["pesos_novos"] is None
    assert "pelo menos 50" in corpo["sugestao"]["sem_pesos_porque"]


def test_rankings_e_comparacao_entre_redes(cliente, publicados):
    corpo = cliente.get("/api/engajamento").json()
    assert corpo["com_7_dias"] == 80
    indices = [c["indice"] for c in corpo["cortes"]]
    assert indices == sorted(indices, reverse=True)
    assert corpo["cortes"][0]["tipo_de_momento"] == "evento do LoL"
    pentakill = next(h for h in corpo["hashtags"] if h["nome"] == "#pentakill")
    assert pentakill["confiavel"] and pentakill["efeito"] > 0
    assert corpo["por_tipo_de_momento"][0]["nome"] == "evento do LoL"
    redes = {r["plataforma"]: r for r in corpo["por_rede"]}
    assert redes["youtube"]["cortes"] == 60 and redes["tiktok"]["cortes"] == 20
    assert redes["tiktok"]["mediana_retencao"] is None  # o TikTok não dá retenção


def test_aceitar_os_pesos_do_engajamento(cliente, sessao, publicados):
    sugestao = cliente.get("/api/engajamento").json()["sugestao"]
    assert sugestao["pesos_novos"]["lol"] > sugestao["pesos_atuais"]["lol"]
    assert cliente.post("/api/engajamento/aceitar", json={"cortes": 79}).status_code == 409
    resposta = cliente.post("/api/engajamento/aceitar", json={"cortes": 80})
    assert resposta.status_code == 200, resposta.text
    versao = sessao.exec(select(PesosNota)).one()
    assert versao.origem == "engajamento" and versao.valores == sugestao["pesos_novos"]
    # Os pesos novos já valem: a próxima sugestão parte deles.
    depois = resposta.json()["sugestao"]
    assert depois["pesos_atuais"] == sugestao["pesos_novos"]


def test_aceitar_de_outro_site_e_recusado(cliente, publicados):
    resposta = cliente.post(
        "/api/engajamento/aceitar", json={"cortes": 80}, headers={"Origin": "https://mau.example"}
    )
    assert resposta.status_code == 403
