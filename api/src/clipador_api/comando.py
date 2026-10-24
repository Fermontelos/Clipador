"""``clipador web``: sobe a API da revisão em 127.0.0.1 e abre o navegador.

A API se cadastra no CLI do backend pelo grupo de *entry points* ``clipador.comandos``
(``api/pyproject.toml``), porque o backend não pode importar a API (ADR 0003).
"""

import errno
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from clipador.cli import CAMINHO_PADRAO, OpcaoArquivo, OpcaoDemo, config_do_comando

ENDERECO = "127.0.0.1"  # só este PC (nunca 0.0.0.0, que abriria para a rede)


def porta_livre(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as teste:
        try:
            teste.bind((ENDERECO, porta))
        except OSError as erro:
            if erro.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(erro, "winerror", 0) in (
                10013,  # WSAEACCES: porta reservada pelo Windows (ex.: Hyper-V)
                10048,  # WSAEADDRINUSE
            ):
                return False
            raise
    return True


def web(
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
    porta: Annotated[
        int | None,
        # "\\[" para o typer não ler "[revisao]" como formatação.
        typer.Option(
            "--porta",
            "-p",
            min=1024,  # as mesmas regras de [revisao] porta no config
            max=65535,
            help="Porta (padrão: \\[revisao] porta do config).",
        ),
    ] = None,
    sem_navegador: Annotated[
        bool, typer.Option("--sem-navegador", help="Não abre o navegador sozinho.")
    ] = False,
) -> None:
    """Abre a revisão dos clipes no navegador (por enquanto, a documentação em /docs)."""
    import uvicorn  # só aqui: o resto do CLI não precisa esperar o FastAPI carregar

    from clipador_api.app import criar_app

    cfg = config_do_comando(Path(arquivo), demo)
    porta = porta or cfg.revisao.porta
    if not porta_livre(porta):
        typer.echo(
            f"Erro: a porta {porta} já está em uso (o 'clipador web' já está aberto?). "
            "Use --porta ou mude [revisao] porta no config.toml.",
            err=True,
        )
        raise typer.Exit(code=1)
    endereco = f"http://{ENDERECO}:{porta}/docs"
    typer.echo(f"Revisão em {endereco} (Ctrl+C para fechar).")
    if cfg.revisao.abrir_navegador and not sem_navegador:
        # Espera o servidor subir para o navegador não abrir numa página de erro.
        # daemon: um Ctrl+C logo de cara fecha na hora, sem abrir o navegador depois.
        abrir = threading.Timer(1.5, webbrowser.open, args=[endereco])
        abrir.daemon = True
        abrir.start()
    uvicorn.run(criar_app(cfg), host=ENDERECO, port=porta, log_level="warning")
