"""Quem pode falar com a API, o comando ``clipador web`` e a cota do YouTube."""

import socket
from datetime import UTC, datetime

import pytest
import typer
from typer.testing import CliRunner

from clipador import cli
from clipador.config import ConfigRevisao
from clipador.revisao import cota
from clipador_api import comando

# ----------------------------------------------------------------- segurança


def test_recusa_outro_nome_no_host(cliente):
    # DNS rebinding: um site cujo domínio passa a apontar para 127.0.0.1 manda Host: ele.
    resposta = cliente.get("/api/momentos", headers={"Host": "site-malicioso.com"})
    assert resposta.status_code == 400


def test_aceita_localhost(cliente):
    assert cliente.get("/api/dias", headers={"Host": "localhost:8765"}).status_code == 200


def test_recusa_pedido_que_muda_algo_vindo_de_outra_origem(cliente, um_momento):
    # Um site aberto no navegador tentando aprovar um clipe por você.
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/decisao",
        json={"aprovado": False},
        headers={"Origin": "https://site-malicioso.com"},
    )
    assert resposta.status_code == 403
    assert cliente.get(f"/api/momentos/{um_momento.id}").json()["decisao"] is None
    # Da mesma origem (a interface da etapa 13), passa.
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/decisao",
        json={"aprovado": False},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert resposta.status_code == 200


def test_recusa_formulario_disfarcado(cliente, um_momento):
    # Um formulário HTML de outro site manda text/plain sem perguntar ao navegador (sem
    # "preflight"); mesmo sem o Origin, o corpo não é aceito como JSON.
    resposta = cliente.post(
        f"/api/momentos/{um_momento.id}/decisao",
        content='{"aprovado": false}',
        headers={"Content-Type": "text/plain"},
    )
    assert resposta.status_code == 422


def test_leitura_de_outra_origem_nao_libera_cors(cliente, um_momento):
    # Sem cabeçalho CORS, o navegador não deixa outro site ler as respostas.
    resposta = cliente.get("/api/dias", headers={"Origin": "https://site-malicioso.com"})
    assert "access-control-allow-origin" not in resposta.headers


# ----------------------------------------------------------------- clipador web


def test_web_aparece_no_cli():
    cli.registrar_comandos_de_fora()
    cli.registrar_comandos_de_fora()  # de novo não duplica
    nomes = [c.name for c in cli.app.registered_commands]
    assert nomes.count("web") == 1


def test_web_sobe_so_em_127_0_0_1(monkeypatch, tmp_path):
    chamadas = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **opcoes: chamadas.update(opcoes))
    abertos = []
    monkeypatch.setattr(comando.webbrowser, "open", abertos.append)
    monkeypatch.setattr(comando.threading, "Timer", lambda _s, f, args: _Agora(f, args))
    config = tmp_path / "config.toml"
    config.write_text(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n[revisao]\nporta = 8799\n',
        encoding="utf-8",
    )
    resultado = _rodar_web(["--arquivo", str(config)])
    assert resultado.exit_code == 0, resultado.output
    assert chamadas["host"] == "127.0.0.1"
    assert chamadas["port"] == 8799
    assert abertos == ["http://127.0.0.1:8799/docs"]


def test_web_sem_navegador_e_outra_porta(monkeypatch, tmp_path):
    chamadas = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **opcoes: chamadas.update(opcoes))
    monkeypatch.setattr(comando.webbrowser, "open", lambda url: pytest.fail("abriu"))
    config = tmp_path / "config.toml"
    config.write_text(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n',
        encoding="utf-8",
    )
    resultado = _rodar_web(["--arquivo", str(config), "--porta", "8798", "--sem-navegador"])
    assert resultado.exit_code == 0, resultado.output
    assert chamadas["port"] == 8798


def test_web_com_a_porta_ocupada_explica_e_sai(monkeypatch, tmp_path):
    monkeypatch.setattr("uvicorn.run", lambda app, **opcoes: pytest.fail("subiu"))
    config = tmp_path / "config.toml"
    config.write_text(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n',
        encoding="utf-8",
    )
    with socket.socket() as ocupada:
        ocupada.bind(("127.0.0.1", 0))
        ocupada.listen()
        porta = ocupada.getsockname()[1]
        assert not comando.porta_livre(porta)
        resultado = _rodar_web(["--arquivo", str(config), "--porta", str(porta)])
    assert resultado.exit_code == 1
    assert "já está em uso" in resultado.output


@pytest.mark.parametrize("porta", ["0", "80", "65536", "99999"])
def test_web_recusa_porta_fora_da_faixa(monkeypatch, tmp_path, porta):
    monkeypatch.setattr("uvicorn.run", lambda app, **opcoes: pytest.fail("subiu"))
    resultado = _rodar_web(["--arquivo", str(tmp_path / "config.toml"), "--porta", porta])
    assert resultado.exit_code == 2  # erro de uso do typer, com mensagem, sem traceback
    assert "1024" in resultado.output


def test_web_demo_sem_demo_pede_para_criar(monkeypatch, tmp_path):
    monkeypatch.setattr("uvicorn.run", lambda app, **opcoes: pytest.fail("subiu"))
    resultado = _rodar_web(["--arquivo", str(tmp_path / "config.toml"), "--demo"])
    assert resultado.exit_code == 1
    assert "clipador demo" in resultado.output


class _Agora:
    """Um Timer que roda na hora (o teste não espera 1,5 s)."""

    def __init__(self, funcao, args):
        self.funcao, self.args = funcao, args
        self.daemon = False

    def start(self):
        assert self.daemon, "o Timer do navegador precisa ser daemon"
        self.funcao(*self.args)


def _rodar_web(argumentos: list[str]):
    app = typer.Typer()
    app.command()(comando.web)
    app.command("outro")(lambda: None)  # com 2 comandos, o typer exige o nome "web"
    return CliRunner().invoke(app, ["web", *argumentos])


# ----------------------------------------------------------------- config


def test_destino_padrao_desconhecido_da_erro():
    with pytest.raises(ValueError, match="instagram"):
        ConfigRevisao(destinos_padrao={"vertical_a": ["instagram"]})


def test_porta_baixa_da_erro():
    with pytest.raises(ValueError):
        ConfigRevisao(porta=80)


# ----------------------------------------------------------------- cota do YouTube


@pytest.mark.parametrize(
    ("agora", "inicio_esperado", "renova_esperado"),
    [
        # Horário de verão no Pacífico (UTC-7): o dia da cota começa às 07:00 UTC.
        (datetime(2026, 7, 10, 12, tzinfo=UTC), datetime(2026, 7, 10, 7, tzinfo=UTC),
         datetime(2026, 7, 11, 7, tzinfo=UTC)),
        # Antes das 07:00 UTC ainda é o dia anterior lá.
        (datetime(2026, 7, 10, 6, tzinfo=UTC), datetime(2026, 7, 9, 7, tzinfo=UTC),
         datetime(2026, 7, 10, 7, tzinfo=UTC)),
        # Inverno (UTC-8): às 08:00 UTC.
        (datetime(2026, 12, 10, 12, tzinfo=UTC), datetime(2026, 12, 10, 8, tzinfo=UTC),
         datetime(2026, 12, 11, 8, tzinfo=UTC)),
        # Dia em que o horário de verão acaba (1/11/2026): o dia lá tem 25 h.
        (datetime(2026, 11, 1, 12, tzinfo=UTC), datetime(2026, 11, 1, 7, tzinfo=UTC),
         datetime(2026, 11, 2, 8, tzinfo=UTC)),
    ],
)  # fmt: skip
def test_dia_da_cota_vira_a_meia_noite_do_pacifico(sessao, agora, inicio_esperado, renova_esperado):
    assert cota.inicio_do_dia_da_cota(agora) == inicio_esperado
    assert cota.cota_youtube(sessao, agora).renova_em == renova_esperado
