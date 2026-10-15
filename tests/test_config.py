from pathlib import Path

import pytest
from pydantic import ValidationError

from clipador.config import ConfigNaoEncontradaError, carregar_config

RAIZ_DO_PROJETO = Path(__file__).resolve().parent.parent


def test_config_de_exemplo_carrega_sem_erro(tmp_path):
    # arquivo_env aponta para um arquivo que não existe, para o teste não ler um .env
    # real que esteja na raiz do projeto.
    cfg = carregar_config(RAIZ_DO_PROJETO / "config.example.toml", arquivo_env=tmp_path / "sem.env")
    assert cfg.limite_diario == 15
    assert cfg.nivel_log == "INFO"
    # Caminho relativo vira absoluto, contado a partir da pasta do config.
    assert cfg.pasta_dados == RAIZ_DO_PROJETO / "dados"


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


def test_env_ao_lado_do_config_tem_prioridade_sobre_o_toml(escrever_config, tmp_path):
    (tmp_path / ".env").write_text("CLIPADOR_NIVEL_LOG=DEBUG\n", encoding="utf-8")
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/g"\nnivel_log = "INFO"\n'))
    assert cfg.nivel_log == "DEBUG"


def test_variavel_de_ambiente_tem_prioridade_sobre_o_env(escrever_config, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CLIPADOR_LIMITE_DIARIO=30\n", encoding="utf-8")
    monkeypatch.setenv("CLIPADOR_LIMITE_DIARIO", "40")
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/g"\n'))
    assert cfg.limite_diario == 40


def test_env_ignora_linhas_de_outros_programas(escrever_config, tmp_path):
    (tmp_path / ".env").write_text("OUTRO_PROGRAMA_CHAVE=123\n", encoding="utf-8")
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/g"\n'))
    assert cfg.limite_diario == 15


def test_env_com_chave_do_app_digitada_errado_e_rejeitado(escrever_config, tmp_path):
    (tmp_path / ".env").write_text("CLIPADOR_NIVEL_LOGG=DEBUG\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        carregar_config(escrever_config('pasta_gravacoes = "C:/g"\n'))


def test_sem_env_a_config_carrega_normalmente(escrever_config, tmp_path):
    assert not (tmp_path / ".env").exists()
    cfg = carregar_config(escrever_config('pasta_gravacoes = "C:/g"\n'))
    assert cfg.nivel_log == "INFO"


def test_caminhos_relativos_partem_da_pasta_do_config(tmp_path, monkeypatch):
    # O comando roda de outra pasta (como o Agendador de Tarefas, que usa System32)...
    pasta_config = tmp_path / "projeto"
    pasta_config.mkdir()
    outra_pasta = tmp_path / "outro_lugar"
    outra_pasta.mkdir()
    monkeypatch.chdir(outra_pasta)
    caminho = pasta_config / "config.toml"
    caminho.write_text(
        'pasta_gravacoes = "gravacoes"\npasta_dados = "dados"\npasta_backups = "bkp"\n',
        encoding="utf-8",
    )

    cfg = carregar_config(caminho)

    # ...e mesmo assim os caminhos apontam para a pasta do config.
    assert cfg.pasta_dados == pasta_config / "dados"
    assert cfg.pasta_gravacoes == pasta_config / "gravacoes"
    assert cfg.pasta_backups == pasta_config / "bkp"
    assert cfg.caminho_banco == pasta_config / "dados" / "clipador.db"


def test_caminho_absoluto_continua_igual(escrever_config, tmp_path):
    absoluto = (tmp_path / "em_outro_disco").as_posix()
    cfg = carregar_config(
        escrever_config(f'pasta_gravacoes = "C:/g"\npasta_dados = "{absoluto}"\n')
    )
    assert cfg.pasta_dados == tmp_path / "em_outro_disco"
