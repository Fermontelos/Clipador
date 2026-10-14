from typer.testing import CliRunner

from clipador import __version__
from clipador.cli import app

runner = CliRunner()


def test_versao_mostra_a_versao_do_app():
    resultado = runner.invoke(app, ["versao"])
    assert resultado.exit_code == 0
    assert __version__ in resultado.output


def test_config_valida_termina_sem_erro(config_valida, tmp_path):
    resultado = runner.invoke(app, ["config", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0
    assert "Configuração válida" in resultado.output
    assert (tmp_path / "dados" / "logs" / "clipador.log").is_file()


def test_config_invalida_explica_o_problema(escrever_config):
    caminho = escrever_config('pasta_gravacoes = "C:/g"\nlimite_diario = -1\n')
    resultado = runner.invoke(app, ["config", "--arquivo", str(caminho)])
    assert resultado.exit_code == 1
    assert "limite_diario" in resultado.output


def test_config_inexistente_explica_como_criar(tmp_path):
    resultado = runner.invoke(app, ["config", "--arquivo", str(tmp_path / "nada.toml")])
    assert resultado.exit_code == 1
    assert "config.example.toml" in resultado.output
