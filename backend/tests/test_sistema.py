"""Energia (não suspender), trava de instância única e abertura no login."""

from pathlib import Path

import pytest

from clipador.sistema import inicializacao
from clipador.sistema.energia import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ManterAcordado
from clipador.sistema.trava import ServicoJaRodandoError, TravaDoServico, servico_esta_rodando


class WindowsFalso:
    """Registra os pedidos que seriam feitos ao Windows."""

    def __init__(self, resposta: int = 1) -> None:
        self.pedidos: list[int] = []
        self.resposta = resposta

    def __call__(self, flags: int) -> int:
        self.pedidos.append(flags)
        return self.resposta


def test_ativar_e_desativar_mandam_os_pedidos_certos():
    windows = WindowsFalso()
    energia = ManterAcordado(windows)
    energia.ativar()
    energia.ativar()  # repetir não manda de novo
    energia.desativar()
    energia.desativar()
    assert windows.pedidos == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]


def test_pedido_recusado_pelo_windows_nao_marca_como_ativo():
    energia = ManterAcordado(WindowsFalso(resposta=0))
    energia.ativar()
    assert energia.ativo is False


def test_pedido_real_ao_windows_funciona():
    energia = ManterAcordado()  # chama o SetThreadExecutionState de verdade
    energia.ativar()
    assert energia.ativo is True
    energia.desativar()
    assert energia.ativo is False


def test_so_um_servico_por_vez(tmp_path):
    primeira = TravaDoServico(tmp_path)
    primeira.adquirir()
    try:
        assert servico_esta_rodando(tmp_path) is True
        with pytest.raises(ServicoJaRodandoError):
            TravaDoServico(tmp_path).adquirir()
    finally:
        primeira.liberar()
    assert servico_esta_rodando(tmp_path) is False


def test_trava_pode_ser_pega_de_novo_depois_de_liberada(tmp_path):
    for _ in range(2):
        trava = TravaDoServico(tmp_path)
        trava.adquirir()
        trava.liberar()


def test_comando_do_login_usa_pythonw_sem_janela_e_o_config_absoluto(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    comando = inicializacao.comando_do_servico(Path("config.toml"))
    assert "pythonw.exe" in comando
    assert "-m clipador iniciar --arquivo" in comando
    assert f'"{(tmp_path / "config.toml").resolve()}"' in comando


# ------------------------------------------------------------------ registro (chave de teste)
# O conftest troca a chave "Run" por uma chave de teste que começa sem existir.


def test_sem_a_chave_run_nada_esta_cadastrado():
    # Windows recém-instalado ou a máquina da CI: a chave "Run" nem existe.
    assert inicializacao.comando_instalado() is None
    assert inicializacao.remover() is False


def test_instalar_cria_a_chave_e_remover_desfaz(tmp_path):
    comando = inicializacao.instalar(tmp_path / "config.toml")
    assert inicializacao.comando_instalado() == comando
    assert str((tmp_path / "config.toml").resolve()) in comando
    assert inicializacao.remover() is True
    assert inicializacao.comando_instalado() is None
    assert inicializacao.remover() is False
