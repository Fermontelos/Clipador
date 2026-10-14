"""Log em arquivo rotativo.

O app roda de madrugada, sem ninguém olhando: o log é a forma de saber depois o que
aconteceu. O arquivo gira ao chegar em 5 MB e guarda os 5 anteriores.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

NOME_LOGGER = "clipador"
TAMANHO_MAXIMO = 5 * 1024 * 1024
ARQUIVOS_ANTIGOS = 5
FORMATO = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configurar_logs(pasta_dados: Path, nivel: str = "INFO") -> Path:
    """Liga o log em ``<pasta_dados>/logs/clipador.log`` e devolve o caminho do arquivo.

    Pode ser chamada mais de uma vez: os handlers antigos são trocados, sem duplicar
    as linhas do log.
    """
    pasta_logs = pasta_dados / "logs"
    pasta_logs.mkdir(parents=True, exist_ok=True)
    arquivo = pasta_logs / "clipador.log"

    logger = logging.getLogger(NOME_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    no_arquivo = RotatingFileHandler(
        arquivo, maxBytes=TAMANHO_MAXIMO, backupCount=ARQUIVOS_ANTIGOS, encoding="utf-8"
    )
    no_arquivo.setFormatter(logging.Formatter(FORMATO))
    logger.addHandler(no_arquivo)
    logger.setLevel(nivel)
    # Não repassa para o logger raiz, para não imprimir tudo duas vezes.
    logger.propagate = False
    return arquivo
