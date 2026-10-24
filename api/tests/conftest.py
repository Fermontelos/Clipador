"""Um banco pequeno, montado à mão, para testar cada rota da API sem cortar vídeo nenhum.

(O teste ``lento`` em ``test_demo_pela_api.py`` faz o caminho inteiro com a demo de verdade.)
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Clipe, FormatoClipe, Gravacao, Momento, StatusGravacao
from clipador.config import config_sem_arquivo
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador_api.app import criar_app

# Duas lives em dias diferentes (horário do meio-dia: o mesmo dia em qualquer fuso do Brasil).
INICIO_ONTEM = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
INICIO_HOJE = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
CONTEUDO_DO_VIDEO = bytes(range(256)) * 40  # 10 KB "de vídeo", para conferir o Range


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(
        pasta_gravacoes=tmp_path / "gravacoes", pasta_dados=tmp_path / "dados"
    )


@pytest.fixture
def engine(cfg):
    engine = abrir_banco(cfg.caminho_banco)
    yield engine
    engine.dispose()


@pytest.fixture
def sessao(engine):
    with nova_sessao(engine) as sessao:
        yield sessao


@pytest.fixture
def cliente(cfg, engine):
    # base_url com 127.0.0.1: a API recusa outros nomes no cabeçalho Host.
    with TestClient(criar_app(cfg, engine), base_url="http://127.0.0.1") as cliente:
        yield cliente


def _criar_gravacao(sessao, inicio, status=StatusGravacao.CONCLUIDA, nome="live") -> Gravacao:
    gravacao = Gravacao(
        arquivo=f"C:/gravacoes/{nome}.mkv", inicio=inicio, duracao_s=3600, status=status
    )
    sessao.add(gravacao)
    sessao.commit()
    sessao.refresh(gravacao)
    return gravacao


def _criar_momento(
    sessao, cfg, gravacao, nota, inicio_s=100.0, formatos=("horizontal", "vertical_a")
) -> Momento:
    """Um momento de 20 s com clipes (arquivos falsos) e thumbnail."""
    momento = Momento(
        gravacao_id=gravacao.id,
        inicio_s=inicio_s,
        fim_s=inicio_s + 20,
        pico_s=inicio_s + 10,
        nota=nota,
        motivo="grito",
        sinais={"volume_voz": 0.9},
        titulos_sugeridos=[
            {"titulo": "NÃO ACREDITO NESSA", "tipo": "frase_sua"},
            {"titulo": "Que jogada", "tipo": "exagero"},
            {"titulo": "Eu avisei", "tipo": "descricao"},
        ],
        hashtags_sugeridas=["#leagueoflegends", "#lolbr", "#pentakill"],
    )
    sessao.add(momento)
    sessao.commit()
    sessao.refresh(momento)
    for formato in formatos:
        arquivo = cfg.pasta_clipes / f"{momento.id}_{formato}.mp4"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(CONTEUDO_DO_VIDEO)
        sessao.add(
            Clipe(
                momento_id=momento.id,
                formato=FormatoClipe(formato),
                arquivo=str(arquivo),
                duracao_s=20,
            )
        )
    if "horizontal" in formatos:
        caminho_da_thumbnail(cfg, gravacao, momento).write_bytes(b"\xff\xd8 jpg falso")
    sessao.commit()
    return momento


@pytest.fixture
def dois_dias(sessao, cfg):
    """Ontem: 2 momentos. Hoje: 20 momentos (passa do limite de 15), notas de 5 a 100."""
    ontem = _criar_gravacao(sessao, INICIO_ONTEM, nome="ontem")
    hoje = _criar_gravacao(sessao, INICIO_HOJE, nome="hoje")
    for i, nota in enumerate((40, 70)):
        _criar_momento(sessao, cfg, ontem, nota, inicio_s=100 + 60 * i)
    # Notas fora de ordem de propósito: a API é que ordena.
    momentos_hoje = [
        _criar_momento(sessao, cfg, hoje, nota=(i * 37) % 96 + 5, inicio_s=100 + 60 * i)
        for i in range(20)
    ]
    return {"ontem": ontem, "hoje": hoje, "momentos_hoje": momentos_hoje}


@pytest.fixture
def um_momento(sessao, cfg):
    gravacao = _criar_gravacao(sessao, INICIO_HOJE)
    return _criar_momento(sessao, cfg, gravacao, nota=80)


@pytest.fixture
def criar_gravacao(sessao):
    """Para os testes criarem gravações: ``criar_gravacao(inicio, status=...)``."""
    return lambda inicio, **opcoes: _criar_gravacao(sessao, inicio, **opcoes)


@pytest.fixture
def criar_momento(sessao, cfg):
    """Para os testes criarem momentos: ``criar_momento(gravacao, nota, ...)``."""
    return lambda gravacao, nota, **opcoes: _criar_momento(sessao, cfg, gravacao, nota, **opcoes)
