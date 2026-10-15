"""Linha de comando do app (``clipador ...``)."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from clipador import __version__, ffmpeg
from clipador.banco import fila
from clipador.banco.backup import fazer_backup, listar_backups
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import StatusTarefa, Tarefa
from clipador.config import CAMINHO_PADRAO, Config, ConfigNaoEncontradaError, carregar_config
from clipador.demo import roteiro
from clipador.demo.gerar import pasta_da_demo, preparar_demo
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


def _carregar_ou_sair(arquivo: Path) -> Config:
    """Carrega a configuração; se houver problema, explica e encerra com código 1."""
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
    configurar_logs(cfg.pasta_dados, cfg.nivel_log)
    return cfg


def _pasta_dados_da_demo(arquivo: Path) -> Path:
    """Pasta de dados para a demo, que funciona mesmo sem ``config.toml``.

    Quem acabou de clonar o repositório (um recrutador, por exemplo) ainda não tem
    ``config.toml``. Nesse caso a demo usa ``dados/`` ao lado de onde o config ficaria.
    Um ``config.toml`` que existe mas é inválido continua dando o erro de sempre.
    """
    if arquivo.is_file():
        return _carregar_ou_sair(arquivo).pasta_dados
    pasta_dados = arquivo.resolve().parent / "dados"
    configurar_logs(pasta_dados)
    typer.echo(f"Sem {arquivo.name}: a demo usa a pasta padrão {pasta_dados}.")
    return pasta_dados


def _resumir_erro(erro: str | None, limite: int = 150) -> str:
    """Primeira linha do erro, encurtada: a mensagem inteira fica no banco e no log."""
    primeira = (erro or "").strip().splitlines()[0] if (erro or "").strip() else "(sem mensagem)"
    return primeira if len(primeira) <= limite else primeira[: limite - 1] + "…"


def _restantes(mostradas: int, total: int) -> str:
    return f" (mostrando {mostradas} de {total})" if total > mostradas else ""


def _hora_local(momento: datetime) -> str:
    """Horário do banco (UTC) convertido para o fuso do PC."""
    return momento.astimezone().strftime("%d/%m %H:%M")


def _descrever(tarefa: Tarefa) -> str:
    alvo = []
    if tarefa.gravacao_id is not None:
        alvo.append(f"gravação {tarefa.gravacao_id}")
    if tarefa.momento_id is not None:
        alvo.append(f"momento {tarefa.momento_id}")
    if tarefa.publicacao_id is not None:
        alvo.append(f"publicação {tarefa.publicacao_id}")
    sufixo = f" ({', '.join(alvo)})" if alvo else ""
    return f"#{tarefa.id} {tarefa.tipo.value}{sufixo}"


@app.command()
def versao() -> None:
    """Mostra a versão do app."""
    typer.echo(f"clipador {__version__}")


@app.command()
def config(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Confere se a configuração é válida e mostra os valores carregados."""
    cfg = _carregar_ou_sair(arquivo)
    log.info("Configuração válida carregada de %s", arquivo)

    typer.echo(f"Configuração válida ({arquivo}):")
    for campo, valor in cfg.model_dump().items():
        if valor is None:
            valor = "(não definido: usa o padrão)"
        typer.echo(f"  {campo} = {valor}")
    typer.echo(f"Banco: {cfg.caminho_banco}")
    typer.echo(f"Backups: {cfg.pasta_backups_efetiva}")
    typer.echo(f"Log em: {cfg.pasta_dados / 'logs' / 'clipador.log'}")
    if not cfg.pasta_gravacoes.is_dir():
        typer.echo(f"Aviso: a pasta de gravações {cfg.pasta_gravacoes} ainda não existe.", err=True)


OpcaoDemo = Annotated[
    bool,
    typer.Option("--demo", help="Usa o banco da demo (dados/demo/) em vez do real."),
]


@app.command()
def status(arquivo: OpcaoArquivo = CAMINHO_PADRAO, demo: OpcaoDemo = False) -> None:
    """Mostra a fila de tarefas: quantas há em cada situação, as próximas e os erros."""
    if demo:
        pasta = pasta_da_demo(_pasta_dados_da_demo(arquivo))
        caminho_banco, pasta_backups = pasta / "clipador.db", pasta / "backups"
        if not caminho_banco.is_file():
            typer.echo("A demo ainda não existe. Rode 'clipador demo' primeiro.", err=True)
            raise typer.Exit(code=1)
    else:
        cfg = _carregar_ou_sair(arquivo)
        caminho_banco, pasta_backups = cfg.caminho_banco, cfg.pasta_backups_efetiva
    engine = abrir_banco(caminho_banco, pasta_backups)
    try:
        with nova_sessao(engine) as sessao:
            resumo = fila.resumo(sessao)
    finally:
        engine.dispose()

    typer.echo(f"Banco: {caminho_banco}")
    contagens = " | ".join(
        f"{situacao.value.replace('_', ' ')}: {resumo.por_status[situacao]}"
        for situacao in StatusTarefa
    )
    typer.echo(f"Fila: {contagens}")

    total = resumo.por_status
    if resumo.em_andamento:
        mostradas = len(resumo.em_andamento)
        typer.echo(f"Em andamento{_restantes(mostradas, total[StatusTarefa.EM_ANDAMENTO])}:")
        for tarefa in resumo.em_andamento:
            typer.echo(f"  {_descrever(tarefa)} · desde {_hora_local(tarefa.atualizada_em)}")

    if resumo.proximas:
        mostradas = len(resumo.proximas)
        typer.echo(f"Próximas{_restantes(mostradas, total[StatusTarefa.PENDENTE])}:")
        for tarefa in resumo.proximas:
            typer.echo(
                f"  {_descrever(tarefa)} · a partir de {_hora_local(tarefa.executar_a_partir_de)}"
            )
    else:
        typer.echo("Nenhuma tarefa pendente.")

    if resumo.com_erro:
        mostradas = len(resumo.com_erro)
        typer.echo(f"Com erro{_restantes(mostradas, total[StatusTarefa.ERRO])}:")
        for tarefa in resumo.com_erro:
            typer.echo(
                f"  {_descrever(tarefa)} · {tarefa.tentativas} tentativas · "
                f"{_resumir_erro(tarefa.ultimo_erro)}"
            )
    backups = listar_backups(pasta_backups)
    ultimo = backups[-1].name if backups else "nenhum ainda"
    typer.echo(f"Backups: {len(backups)} (último: {ultimo})")


@app.command()
def backup(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Faz agora uma cópia do banco (as 14 mais recentes são mantidas)."""
    cfg = _carregar_ou_sair(arquivo)
    try:
        destino = fazer_backup(cfg.caminho_banco, cfg.pasta_backups_efetiva)
    except FileNotFoundError as erro:
        typer.echo(f"Erro: {erro}. Rode 'clipador status' uma vez para criar o banco.", err=True)
        raise typer.Exit(code=1) from erro
    total = len(listar_backups(cfg.pasta_backups_efetiva))
    typer.echo(f"Backup criado: {destino}")
    typer.echo(f"Backups guardados: {total}")


def _mmss(segundos: float) -> str:
    return f"{int(segundos) // 60}:{int(segundos) % 60:02d}"


@app.command()
def demo(
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    do_zero: Annotated[
        bool, typer.Option("--do-zero", help="Apaga a demo anterior e cria tudo de novo.")
    ] = False,
) -> None:
    """Cria uma live falsa de 3 minutos para testar e demonstrar o app (não precisa de GPU)."""
    pasta_dados = _pasta_dados_da_demo(arquivo)
    typer.echo("Preparando a demo (a primeira vez leva uns 20 segundos)...")
    try:
        resultado = preparar_demo(pasta_dados, do_zero=do_zero)
    except ffmpeg.FFmpegNaoEncontradoError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    except ffmpeg.FFmpegFalhouError as erro:
        typer.echo(f"Erro ao gerar a gravação da demo: {erro}", err=True)
        raise typer.Exit(code=1) from erro

    info = resultado.info
    if resultado.video_criado or resultado.banco_preenchido:
        typer.echo(f"Demo pronta em {resultado.segundos:.1f} s, em {resultado.pasta}")
    else:
        typer.echo("A demo já existia: nada foi duplicado. Use --do-zero para recriar.")
    typer.echo(
        f"  Gravação: {resultado.arquivo.name} ({_mmss(info.duracao_s)}, "
        f"{info.largura}x{info.altura}, {info.faixas_audio} faixas: "
        f"{', '.join(info.titulos_faixas)})"
    )
    picos = ", ".join(_mmss(pico.inicio_s) for pico in roteiro.PICOS_VOZ)
    typer.echo(f"  Picos de voz em: {picos}")
    c = resultado.contagens
    typer.echo(
        f"  Banco da demo: {c['gravacoes']} gravação, {c['eventos']} eventos do LoL, "
        f"{c['transcricoes']} transcrição pronta, {c['tarefas']} tarefa na fila"
    )
    typer.echo("Veja a fila da demo com: clipador status --demo")


def main() -> None:
    app()
