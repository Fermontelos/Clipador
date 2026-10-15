import logging

from clipador.logs import NOME_LOGGER, configurar_logs


def test_cria_o_arquivo_de_log_e_escreve_nele(tmp_path):
    arquivo = configurar_logs(tmp_path, "INFO")
    logging.getLogger("clipador.teste").info("mensagem de teste")

    assert arquivo == tmp_path / "logs" / "clipador.log"
    assert "mensagem de teste" in arquivo.read_text(encoding="utf-8")


def test_respeita_o_nivel_configurado(tmp_path):
    arquivo = configurar_logs(tmp_path, "WARNING")
    logging.getLogger("clipador.teste").info("detalhe que nao deve aparecer")
    logging.getLogger("clipador.teste").warning("aviso que deve aparecer")

    conteudo = arquivo.read_text(encoding="utf-8")
    assert "detalhe que nao deve aparecer" not in conteudo
    assert "aviso que deve aparecer" in conteudo


def test_chamar_duas_vezes_nao_duplica_as_linhas(tmp_path):
    configurar_logs(tmp_path, "INFO")
    arquivo = configurar_logs(tmp_path, "INFO")
    logging.getLogger("clipador.teste").info("linha unica")

    assert len(logging.getLogger(NOME_LOGGER).handlers) == 1
    assert arquivo.read_text(encoding="utf-8").count("linha unica") == 1


def test_cada_processo_pode_ter_o_seu_arquivo(tmp_path):
    arquivo = configurar_logs(tmp_path, "INFO", nome_arquivo="servico.log")
    logging.getLogger("clipador.teste").info("linha do servico")
    assert arquivo == tmp_path / "logs" / "servico.log"
    assert "linha do servico" in arquivo.read_text(encoding="utf-8")
