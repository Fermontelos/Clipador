from pathlib import Path

import pytest

from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, agora


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


@pytest.fixture
def caminho_banco(tmp_path) -> Path:
    return tmp_path / "dados" / "clipador.db"


@pytest.fixture
def engine(caminho_banco):
    """Banco temporário criado pelas migrações, igual ao do app real."""
    engine = abrir_banco(caminho_banco)
    yield engine
    # Fecha as conexões: no Windows, um banco aberto impede apagar a pasta temporária.
    engine.dispose()


@pytest.fixture
def sessao(engine):
    with nova_sessao(engine) as sessao:
        yield sessao


@pytest.fixture
def gravacao(sessao) -> Gravacao:
    """Uma gravação qualquer, para as tarefas terem a que se referir."""
    gravacao = Gravacao(arquivo="C:/gravacoes/2026-10-14 20-00-00.mkv", inicio=agora())
    sessao.add(gravacao)
    sessao.commit()
    sessao.refresh(gravacao)
    return gravacao
