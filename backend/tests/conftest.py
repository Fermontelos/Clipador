import contextlib
import winreg
from pathlib import Path

import pytest

from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, agora
from clipador.sistema import inicializacao
from clipador.transcricao import transcrever

CHAVE_DE_TESTE = r"Software\ClipadorTestes"


class TranscritorMudo:
    """Um Whisper falso que não ouve nada (cada trecho é silêncio)."""

    nome = "mudo (teste)"

    def transcrever(self, audio):
        return []

    def fechar(self):
        pass


@pytest.fixture(autouse=True)
def sem_whisper_de_verdade(monkeypatch):
    """Nenhum teste abre o Whisper de verdade (baixaria 1,5 GB e precisaria da placa de vídeo).

    Os testes da etapa 6 trocam por um transcritor falso com falas; os que rodam a fila
    inteira (ex.: ``clipador processar``) recebem este, que só encontra silêncio.
    """
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: TranscritorMudo())


@pytest.fixture(autouse=True)
def registro_de_teste(monkeypatch):
    """Nenhum teste lê ou mexe na abertura no login de verdade.

    Todos usam uma chave de teste do registro que começa **sem existir**, como na máquina
    da CI do GitHub (onde nem a chave "Run" existe). Ela é apagada no fim do teste.
    """
    monkeypatch.setattr(inicializacao, "CHAVE_RUN", CHAVE_DE_TESTE + r"\Run")
    yield
    for chave in (CHAVE_DE_TESTE + r"\Run", CHAVE_DE_TESTE):
        with contextlib.suppress(FileNotFoundError):  # o teste não chegou a criá-la
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, chave)


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
