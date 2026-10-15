"""O config.toml do jeito que o Windows salva: com BOM, ou com erro de digitação."""

import pytest
from typer.testing import CliRunner

from clipador.cli import app
from clipador.config import ConfigMalEscritaError, carregar_config

BOM = b"\xef\xbb\xbf"
runner = CliRunner()


def test_config_salvo_com_bom_funciona(tmp_path):
    # O PowerShell e o Bloco de Notas antigo salvam UTF-8 com BOM.
    caminho = tmp_path / "config.toml"
    caminho.write_bytes(BOM + b'pasta_gravacoes = "C:/g"\nlimite_diario = 7\n')
    assert carregar_config(caminho).limite_diario == 7


def test_erro_de_digitacao_no_toml_da_erro_claro(tmp_path):
    caminho = tmp_path / "config.toml"
    caminho.write_text('pasta_gravacoes = "C:/g\n', encoding="utf-8")  # aspas sem fechar
    with pytest.raises(ConfigMalEscritaError, match="erro de escrita"):
        carregar_config(caminho)


def test_cli_mostra_mensagem_e_nao_o_traceback(tmp_path):
    caminho = tmp_path / "config.toml"
    caminho.write_text("pasta_gravacoes C:/g\n", encoding="utf-8")  # faltou o "="
    for comando in (["config"], ["status"], ["iniciar"], ["demo"]):
        resultado = runner.invoke(app, [*comando, "--arquivo", str(caminho)])
        assert resultado.exit_code == 1, comando
        assert "erro de escrita" in resultado.output, comando
        assert "Traceback" not in resultado.output, comando
