"""As rotas do aprendizado (etapa 14): ver a sugestão e aceitar."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from clipador.banco.modelos import Decisao, Gravacao, Momento, StatusGravacao


@pytest.fixture
def decisoes(sessao):
    """120 decisões em 6 lives: aprova quando a fala é forte (o volume é ruído)."""
    sorteio = random.Random(5)
    for dia in range(6):
        gravacao = Gravacao(
            arquivo=f"C:/g/{dia}.mkv",
            inicio=datetime(2026, 10, 10, 15, tzinfo=UTC) + timedelta(days=dia),
            status=StatusGravacao.CONCLUIDA,
        )
        sessao.add(gravacao)
        sessao.flush()
        for i in range(20):
            fala = sorteio.random()
            momento = Momento(
                gravacao_id=gravacao.id, inicio_s=60 * i, fim_s=60 * i + 20, nota=50,
                sinais={"fala": fala, "volume_voz": sorteio.random()},
            )  # fmt: skip
            sessao.add(momento)
            sessao.flush()
            aprovado = fala > 0.5
            sessao.add(
                Decisao(
                    momento_id=momento.id, aprovado=aprovado,
                    ajuste_inicio_s=-2.0 if aprovado else None,
                )
            )  # fmt: skip
    sessao.commit()


def test_sem_decisoes(cliente):
    corpo = cliente.get("/api/aprendizado").json()
    assert corpo["decisoes"] == 0 and corpo["minimo_para_pesos"] == 100
    assert corpo["pesos_novos"] is None and "pelo menos 100" in corpo["sem_pesos_porque"]
    assert corpo["janela_atual"] == [10, 5]
    assert corpo["versoes"] == []
    resposta = cliente.post("/api/aprendizado/aceitar", json={"decisoes": 0})
    assert resposta.status_code == 409
    assert "Não há sugestão" in resposta.json()["detail"]


def test_ver_e_aceitar_a_sugestao(cliente, decisoes):
    corpo = cliente.get("/api/aprendizado").json()
    assert corpo["decisoes"] == 120
    assert corpo["pesos_novos"]["fala"] > corpo["pesos_novos"]["volume_voz"]
    assert corpo["precisao_depois"] > corpo["precisao_antes"]
    assert corpo["janela_nova"]["segundos_antes"] == 12  # adiantou 2 s em todos os aprovados
    assert len(corpo["precisao_por_dia"]) == 6

    # Viu com 119 decisões (chegou uma depois): recusado, para não aceitar às cegas.
    resposta = cliente.post("/api/aprendizado/aceitar", json={"decisoes": 119})
    assert resposta.status_code == 409
    assert "Agora são 120" in resposta.json()["detail"]

    depois = cliente.post("/api/aprendizado/aceitar", json={"decisoes": 120}).json()
    assert depois["versao_atual"] == 1
    assert depois["pesos_atuais"] == corpo["pesos_novos"]
    assert depois["janela_atual"] == [12, 5]
    assert depois["pesos_novos"] is None and depois["janela_nova"] is None
    assert depois["versoes"][0]["precisao"] == corpo["precisao_depois"]
    # Aceitar de novo: não há mais nada.
    assert cliente.post("/api/aprendizado/aceitar", json={"decisoes": 120}).status_code == 409


def test_aceitar_de_outro_site_e_recusado(cliente, decisoes):
    resposta = cliente.post(
        "/api/aprendizado/aceitar",
        json={"decisoes": 120},
        headers={"Origin": "https://site-malicioso.com"},
    )
    assert resposta.status_code == 403
    assert cliente.get("/api/aprendizado").json()["versao_atual"] is None
