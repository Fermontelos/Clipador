"""O "pronto quando" da etapa 12: revisar a demo inteira pela API (como pelo /docs)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from clipador import ffmpeg
from clipador.cli import app as cli
from clipador.cortes import comandos
from clipador.demo import roteiro
from clipador.demo.gerar import config_da_demo
from clipador_api.app import criar_app


@pytest.mark.lento
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg não instalado neste PC")
def test_revisar_a_demo_inteira(tmp_path, monkeypatch):
    # Aqui importa o fluxo, não a qualidade da imagem: o preset mais rápido do x264.
    original = comandos.argumentos_de_video
    monkeypatch.setattr(
        comandos,
        "argumentos_de_video",
        lambda encoder: ["ultrafast" if a == "medium" else a for a in original(encoder)],
    )
    runner = CliRunner()
    sem_config = str(tmp_path / "config.toml")
    assert runner.invoke(cli, ["demo", "--arquivo", sem_config]).exit_code == 0
    assert runner.invoke(cli, ["processar", "--demo", "--arquivo", sem_config]).exit_code == 0

    cfg = config_da_demo(tmp_path / "dados")
    with TestClient(criar_app(cfg), base_url="http://127.0.0.1") as cliente:
        # A demo também traz lives antigas já revisadas (etapa 14): conta-se o que mudou.
        estatisticas_antes = cliente.get("/api/estatisticas").json()
        lista = cliente.get("/api/momentos").json()
        assert lista["dia"] == "2026-01-10"  # o dia da live falsa
        momentos = lista["momentos"]
        assert len(momentos) == len(roteiro.PICOS_VOZ)
        assert lista["restantes"] == 0

        for momento in momentos:
            assert {c["formato"] for c in momento["clipes"]} == {
                "horizontal",
                "vertical_a",
                "vertical_b",
            }
            assert len(momento["titulos_sugeridos"]) == 3  # os títulos prontos da demo
            assert cliente.get(momento["thumbnail"]).status_code == 200
            video = cliente.get(momento["clipes"][0]["video"], headers={"Range": "bytes=0-1023"})
            assert video.status_code == 206 and len(video.content) == 1024

        # Aprova todos menos o último, com os destinos padrão e o 1º título sugerido.
        for momento in momentos[:-1]:
            resposta = cliente.post(
                f"/api/momentos/{momento['id']}/decisao",
                json={
                    "aprovado": True,
                    "destinos": momento["destinos_padrao"],
                    "titulo": momento["titulos_sugeridos"][0]["titulo"],
                    "hashtags": momento["hashtags_sugeridas"],
                },
            )
            assert resposta.status_code == 200, resposta.text
        ultimo = momentos[-1]
        cliente.post(f"/api/momentos/{ultimo['id']}/decisao", json={"aprovado": False})

        # Ajusta o corte do 1º: 3 s a mais no fim.
        primeiro = momentos[0]
        ajuste = cliente.post(
            f"/api/momentos/{primeiro['id']}/ajuste",
            json={"inicio_s": primeiro["inicio_s"], "fim_s": primeiro["fim_s"] + 3},
        ).json()
        assert ajuste["momento"]["recortando"] is True

    # O serviço roda o novo corte (a tarefa "recortar" que o ajuste pôs na fila).
    assert runner.invoke(cli, ["processar", "--demo", "--arquivo", sem_config]).exit_code == 0

    with TestClient(criar_app(cfg), base_url="http://127.0.0.1") as cliente:
        depois = cliente.get(f"/api/momentos/{primeiro['id']}").json()
        assert depois["recortando"] is False
        for clipe in depois["clipes"]:
            arquivo = next(cfg.pasta_clipes.glob(f"*_{primeiro['id']}_{clipe['formato']}.mp4"))
            duracao = ffmpeg.inspecionar(Path(arquivo)).duracao_s
            assert duracao == pytest.approx(primeiro["duracao_s"] + 3, abs=0.1)
        estatisticas = cliente.get("/api/estatisticas").json()
        mudou = {k: estatisticas[k] - estatisticas_antes[k] for k in ("aprovados", "recusados")}
        assert mudou == {"aprovados": len(momentos) - 1, "recusados": 1}
        assert depois["decisao"]["ajuste_fim_s"] == 3
        assert cliente.get("/api/dias").json()[0]["pendentes"] == 0  # a live da demo
        # O aprendizado já tem o que mostrar: as decisões antigas e as de agora.
        aprendizado = cliente.get("/api/aprendizado").json()
        assert aprendizado["decisoes"] == estatisticas["aprovados"] + estatisticas["recusados"]
        assert aprendizado["pesos_novos"]["fala"] > aprendizado["pesos_atuais"]["fala"]
