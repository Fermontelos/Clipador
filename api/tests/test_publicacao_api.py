"""Etapa 15 pela API: aprovar põe as publicações na fila; desfazer as cancela."""

from sqlmodel import select

from clipador.banco.modelos import Publicacao, StatusPublicacao, Tarefa, TipoTarefa

APROVACAO = {
    "aprovado": True,
    "destinos": {"vertical_a": ["youtube", "tiktok"], "horizontal": ["youtube"]},
    "titulo": "NÃO ACREDITO NESSA",
}


def test_aprovar_mostra_as_publicacoes_e_desfazer_cancela(cliente, sessao, um_momento):
    rota = f"/api/momentos/{um_momento.id}"
    momento = cliente.post(f"{rota}/decisao", json=APROVACAO).json()
    publicacoes = momento["publicacoes"]
    assert {(p["plataforma"], p["formato"]) for p in publicacoes} == {
        ("youtube", "vertical_a"),
        ("youtube", "horizontal"),
    }  # o TikTok fica para a etapa 16
    assert all(p["status"] == "pendente" and p["agendada_para"] for p in publicacoes)
    assert momento["publicado"] is False  # pendente não trava: ainda dá para desfazer
    assert len(sessao.exec(select(Tarefa).where(Tarefa.tipo == TipoTarefa.PUBLICAR)).all()) == 2

    depois = cliente.delete(f"{rota}/decisao").json()
    assert depois["publicacoes"] == []
    assert sessao.exec(select(Publicacao)).all() == []
    assert sessao.exec(select(Tarefa).where(Tarefa.tipo == TipoTarefa.PUBLICAR)).all() == []


def test_depois_de_enviada_trava(cliente, sessao, um_momento):
    rota = f"/api/momentos/{um_momento.id}"
    cliente.post(f"{rota}/decisao", json=APROVACAO)
    enviada = sessao.exec(select(Publicacao)).first()
    enviada.status = StatusPublicacao.AGENDADA
    enviada.link = "https://youtu.be/abc"
    sessao.add(enviada)
    sessao.commit()
    momento = cliente.get(rota).json()
    assert momento["publicado"] is True
    assert "https://youtu.be/abc" in {p["link"] for p in momento["publicacoes"]}
    assert cliente.delete(f"{rota}/decisao").status_code == 409
