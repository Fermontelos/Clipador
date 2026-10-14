"""Linha de comando do app (``clipador ...``)."""

import logging
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from clipador import __version__
from clipador.config import CAMINHO_PADRAO, ConfigNaoEncontradaError, carregar_config
from clipador.logs import configurar_logs

app = typer.Typer(
    help="Clipador de lives: transforma gravações da live em clipes.",
    no_args_is_help=True,
)
log = logging.getLogger("clipador.cli")

OpcaoArquivo = Annotated[
    Path,
    typer.Option("--arquivo", "-a", help="Arquivo de configuração a usar."),
]


@app.command()
def versao() -> None:
    """Mostra a versão do app."""
    typer.echo(f"clipador {__version__}")


@app.command()
def config(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Confere se a configuração é válida e mostra os valores carregados."""
    try:
        cfg = carregar_config(arquivo)
    except ConfigNaoEncontradaError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    except ValidationError as erro:
        typer.echo(f"Erro: a configuração em {arquivo} tem valores inválidos:", err=True)
        for problema in erro.errors():
            campo = ".".join(str(parte) for parte in problema["loc"])
            typer.echo(f"  - {campo}: {problema['msg']}", err=True)
        raise typer.Exit(code=1) from erro

    arquivo_log = configurar_logs(cfg.pasta_dados, cfg.nivel_log)
    log.info("Configuração válida carregada de %s", arquivo)

    typer.echo(f"Configuração válida ({arquivo}):")
    for campo, valor in cfg.model_dump().items():
        typer.echo(f"  {campo} = {valor}")
    typer.echo(f"Log em: {arquivo_log}")
    if not cfg.pasta_gravacoes.is_dir():
        typer.echo(f"Aviso: a pasta de gravações {cfg.pasta_gravacoes} ainda não existe.", err=True)


def main() -> None:
    app()
