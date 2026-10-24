"""Proteções que valem para TODOS os testes do projeto (backend, API e integrações).

Ficam na raiz para que nenhum teste, em nenhuma pasta, abra o Whisper ou o Ollama de
verdade, mexa na abertura no login ou desligue o PC. (O LoL falso fica no conftest do
backend: os testes das integrações testam justamente o cliente de verdade.)
"""

import contextlib
import winreg

import pytest

from clipador.sistema import inicializacao
from clipador.titulos import gerador
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
def sem_ollama_de_verdade(monkeypatch):
    """Nenhum teste chama o Ollama de verdade (se ele estiver rodando no PC, levaria minutos).

    Para o gerador, o Ollama está sempre fechado (cai no título simples); os testes da etapa
    10 usam um Ollama falso que responde o que eles quiserem.
    """

    def ollama_fechado(cfg):
        raise ConnectionError("Ollama falso dos testes: fechado")

    monkeypatch.setattr(gerador, "criar_cliente", ollama_fechado)


@pytest.fixture(autouse=True)
def nunca_desligar_de_verdade(monkeypatch):
    """Nenhum teste chama o ``shutdown`` do Windows: se algum tentar, ele falha na hora."""
    from clipador.sistema import desligamento

    def proibido(argumentos):
        pytest.fail(f"Um teste tentou rodar 'shutdown {' '.join(argumentos)}' de verdade.")

    monkeypatch.setattr(desligamento, "rodar_shutdown", proibido)


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
