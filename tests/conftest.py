from pathlib import Path

import pytest


@pytest.fixture
def escrever_config(tmp_path):
    """Cria um config.toml temporário com o conteúdo dado e devolve o caminho."""

    def _escrever(conteudo: str) -> Path:
        caminho = tmp_path / "config.toml"
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    return _escrever


@pytest.fixture
def config_valida(escrever_config, tmp_path):
    """Um config.toml válido que aponta tudo para pastas temporárias."""
    pasta_dados = (tmp_path / "dados").as_posix()
    pasta_gravacoes = (tmp_path / "gravacoes").as_posix()
    return escrever_config(
        f'pasta_gravacoes = "{pasta_gravacoes}"\npasta_dados = "{pasta_dados}"\n'
    )
