from pathlib import Path

import pytest
from pydantic import ValidationError

from clipador.config import ConfigNaoEncontradaError, carregar_config

RAIZ_DO_PROJETO = Path(__file__).resolve().parent.parent


def test_config_de_exemplo_carrega_sem_erro():
    cfg = carregar_config(RAIZ_DO_PROJETO / "config.example.toml")
    assert cfg.limite_diario == 15
    assert cfg.nivel_log == "INFO"
    assert cfg.pasta_dados == Path("dados")


def test_valores_padrao_quando_o_campo_nao_aparece(escrever_config):
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/gravacoes"\n'))
    assert cfg.limite_diario == 15
    assert cfg.nivel_log == "INFO"


def test_limite_diario_negativo_e_rejeitado(escrever_config):
    caminho = escrever_config('pasta_gravacoes = "C:/gravacoes"\nlimite_diario = -1\n')
    with pytest.raises(ValidationError) as erro:
        carregar_config(caminho)
    assert erro.value.errors()[0]["loc"] == ("limite_diario",)


def test_nivel_de_log_invalido_e_rejeitado(escrever_config):
    caminho = escrever_config('pasta_gravacoes = "C:/gravacoes"\nnivel_log = "BARULHENTO"\n')
    with pytest.raises(ValidationError):
        carregar_config(caminho)


def test_campo_com_nome_errado_e_rejeitado(escrever_config):
    # Um erro de digitação não pode ser ignorado em silêncio.
    caminho = escrever_config('pasta_gravacoes = "C:/gravacoes"\nlimte_diario = 10\n')
    with pytest.raises(ValidationError) as erro:
        carregar_config(caminho)
    assert erro.value.errors()[0]["loc"] == ("limte_diario",)


def test_pasta_de_gravacoes_e_obrigatoria(escrever_config):
    with pytest.raises(ValidationError) as erro:
        carregar_config(escrever_config("limite_diario = 10\n"))
    assert erro.value.errors()[0]["loc"] == ("pasta_gravacoes",)


def test_arquivo_inexistente_gera_erro_claro(tmp_path):
    with pytest.raises(ConfigNaoEncontradaError, match="config.example.toml"):
        carregar_config(tmp_path / "nao_existe.toml")


def test_variavel_de_ambiente_tem_prioridade_sobre_o_arquivo(escrever_config, monkeypatch):
    monkeypatch.setenv("CLIPADOR_LIMITE_DIARIO", "20")
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/g"\nlimite_diario = 10\n'))
    assert cfg.limite_diario == 20
