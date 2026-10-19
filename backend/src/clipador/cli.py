"""Linha de comando do app (``clipador ...``)."""

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlmodel import col, select

from clipador import __version__, ffmpeg
from clipador.banco import fila
from clipador.banco.backup import fazer_backup, listar_backups
from clipador.banco.conexao import BancoMaisNovoQueOCodigoError, abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, StatusTarefa, Tarefa
from clipador.config import (
    CAMINHO_PADRAO,
    Config,
    ConfigMalEscritaError,
    ConfigNaoEncontradaError,
    carregar_config,
)
from clipador.demo import roteiro
from clipador.demo.gerar import config_da_demo, preparar_demo
from clipador.ingestao.monitor import SemMonitor
from clipador.jogo import alinhamento
from clipador.jogo.coletor import INTERVALO_S as INTERVALO_DO_COLETOR_S
from clipador.jogo.coletor import ColetorLol
from clipador.logs import configurar_logs
from clipador.sistema import inicializacao
from clipador.sistema.servico import Servico
from clipador.sistema.trava import ServicoJaRodandoError, servico_esta_rodando

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
    except (ConfigNaoEncontradaError, ConfigMalEscritaError) as erro:
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


def _exigir_demo(cfg: Config) -> None:
    if not cfg.caminho_banco.is_file():
        typer.echo("A demo ainda não existe. Rode 'clipador demo' primeiro.", err=True)
        raise typer.Exit(code=1)


def _abrir_ou_sair(cfg: Config):
    """Abre o banco; um banco de uma versão mais nova do app vira mensagem, não traceback."""
    try:
        return abrir_banco(cfg.caminho_banco, cfg.pasta_backups_efetiva)
    except BancoMaisNovoQueOCodigoError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro


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
        cfg = config_da_demo(_pasta_dados_da_demo(arquivo))
        _exigir_demo(cfg)
    else:
        cfg = _carregar_ou_sair(arquivo)
    caminho_banco, pasta_backups = cfg.caminho_banco, cfg.pasta_backups_efetiva
    engine = _abrir_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            resumo = fila.resumo(sessao)
    finally:
        engine.dispose()

    typer.echo(f"Banco: {caminho_banco}")
    if not demo:
        rodando = "rodando agora" if servico_esta_rodando(cfg.pasta_dados) else "parado"
        cadastrado = inicializacao.comando_instalado()
        if cadastrado is None:
            no_login = "não"
        elif str(arquivo.resolve()).lower() in cadastrado.lower():
            no_login = "sim"
        else:
            # Ex.: a pasta do projeto foi movida ou renomeada depois de instalar.
            no_login = "aponta para outro config.toml (rode 'clipador instalar-servico' de novo)"
        typer.echo(f"Serviço: {rodando} · abre sozinho no login: {no_login}")
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
            plural = "" if tarefa.tentativas == 1 else "s"
            typer.echo(
                f"  {_descrever(tarefa)} · {tarefa.tentativas} tentativa{plural} · "
                f"{_resumir_erro(tarefa.ultimo_erro)}"
            )
        opcao_demo = " --demo" if demo else ""
        typer.echo(f"Corrigiu a causa? Tente de novo com: clipador refazer <número>{opcao_demo}")
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
        f"  Gravação: {resultado.arquivo.name} ({_mmss(info.duracao_s or 0)}, "
        f"{info.largura}x{info.altura}, {info.faixas_audio} faixas: "
        f"{', '.join(info.titulos_faixas)})"
    )
    picos = ", ".join(_mmss(pico.inicio_s) for pico in roteiro.PICOS_VOZ)
    typer.echo(f"  Picos de voz em: {picos}")
    c = resultado.contagens
    typer.echo(
        f"  Banco da demo: {c['gravacoes']} gravação, {c['eventos']} eventos do LoL, "
        f"{c['transcricoes']} transcrição pronta, {c['tarefas_pendentes']} tarefa(s) na fila"
    )
    typer.echo("Próximos passos: clipador processar --demo  e  clipador status --demo")


def _avisar_sem_janela(arquivo: Path, mensagem: str) -> None:
    """Sem janela (pythonw, no login), uma mensagem na tela sumiria: vai para um arquivo."""
    if sys.stdout is not None:
        return
    destino = arquivo.resolve().parent / "clipador-servico-erro.txt"
    momento = datetime.now().isoformat(timespec="seconds")
    destino.write_text(f"{momento}\n{mensagem}\n", encoding="utf-8")


@app.command()
def iniciar(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Roda o serviço: pega as gravações do OBS sozinho e processa a fila (Ctrl+C para)."""
    try:
        cfg = carregar_config(arquivo)
    except (ConfigNaoEncontradaError, ConfigMalEscritaError, ValidationError) as erro:
        _avisar_sem_janela(arquivo, f"O serviço não abriu: configuração com problema.\n{erro}")
        _carregar_ou_sair(arquivo)  # mostra o erro detalhado e encerra
        raise typer.Exit(code=1) from erro  # (não chega aqui; só por garantia)
    # Abriu bem: um aviso de erro antigo (de antes de corrigir o config) não vale mais.
    (arquivo.resolve().parent / "clipador-servico-erro.txt").unlink(missing_ok=True)
    # O serviço tem o próprio arquivo de log: no Windows, dois processos escrevendo no
    # mesmo arquivo travam a rotação dele (ver logs.py).
    arquivo_log = configurar_logs(cfg.pasta_dados, cfg.nivel_log, nome_arquivo="servico.log")
    typer.echo(f"Serviço rodando. Log em {arquivo_log}. Ctrl+C para parar.")
    parar = threading.Event()
    try:
        Servico(cfg).rodar(parar)
    except (ServicoJaRodandoError, BancoMaisNovoQueOCodigoError) as erro:
        _avisar_sem_janela(arquivo, str(erro))
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    except KeyboardInterrupt:
        parar.set()
        typer.echo("Serviço encerrado.")


@app.command()
def processar(arquivo: OpcaoArquivo = CAMINHO_PADRAO, demo: OpcaoDemo = False) -> None:
    """Processa agora as tarefas da fila, sem o serviço, e sai quando não houver mais."""
    if demo:
        cfg = config_da_demo(_pasta_dados_da_demo(arquivo))
        _exigir_demo(cfg)
        servico = Servico(cfg, monitor=SemMonitor())
    else:
        cfg = _carregar_ou_sair(arquivo)
        servico = Servico(cfg)
    try:
        servico.iniciar()
    except ServicoJaRodandoError as erro:
        typer.echo(f"{erro} Ele já processa a fila sozinho.", err=True)
        raise typer.Exit(code=1) from erro
    except BancoMaisNovoQueOCodigoError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    feitas = falhas = 0
    try:
        while True:
            resultado = servico.passo()
            for nome in resultado.gravacoes_novas:
                typer.echo(f"Gravação nova na fila: {nome}")
            if resultado.pausado_por_gravacao:
                typer.echo("Há uma gravação em andamento: a fila espera ela terminar.")
                break
            if resultado.tarefa_executada is None:
                break
            if resultado.tarefa_pausada:
                typer.echo(f"  {resultado.tarefa_executada}: pausada (começou uma gravação)")
            elif resultado.tarefa_falhou:
                falhas += 1
                typer.echo(f"  {resultado.tarefa_executada}: falhou (veja 'clipador status')")
            else:
                feitas += 1
                typer.echo(f"  {resultado.tarefa_executada}: feita")
    finally:
        servico.encerrar()
    typer.echo(f"Fim: {feitas} tarefa(s) feita(s), {falhas} com falha.")
    if falhas:
        raise typer.Exit(code=1)


@app.command()
def refazer(
    numero: Annotated[int, typer.Argument(help="Número da tarefa (o '#' do clipador status).")],
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
) -> None:
    """Devolve para a fila uma tarefa que deu erro, depois de corrigir a causa."""
    if demo:
        cfg = config_da_demo(_pasta_dados_da_demo(arquivo))
        _exigir_demo(cfg)
    else:
        cfg = _carregar_ou_sair(arquivo)
    engine = _abrir_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            tarefa = fila.refazer(sessao, numero)
            descricao = _descrever(tarefa)
    except (LookupError, fila.EstadoInvalidoError) as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    finally:
        engine.dispose()
    typer.echo(f"Tarefa {descricao} de volta na fila.")
    typer.echo(
        "O serviço a pega sozinho; para rodar agora: clipador processar"
        + (" --demo" if demo else "")
    )


@app.command("coletar-lol")
def coletar_lol(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Coleta os eventos do LoL agora, sem o serviço (para testar numa partida). Ctrl+C para."""
    cfg = _carregar_ou_sair(arquivo)
    if servico_esta_rodando(cfg.pasta_dados):
        typer.echo("O serviço está rodando e já coleta os eventos do LoL.", err=True)
        raise typer.Exit(code=1)
    engine = _abrir_ou_sair(cfg)
    coletor = ColetorLol(engine)
    typer.echo("Esperando uma partida do LoL (consulta a cada 2 s). Ctrl+C para parar.")
    em_partida = False
    ultimo_erro = None
    try:
        while True:
            try:
                novos = coletor.passo()
            except Exception as erro:
                # Como no serviço: um erro numa consulta não para a coleta (e aparece uma vez).
                if repr(erro) != ultimo_erro:
                    typer.echo(f"Erro numa consulta (continuando): {erro}", err=True)
                    ultimo_erro = repr(erro)
                novos = []
            if coletor.em_partida != em_partida:
                em_partida = coletor.em_partida
                typer.echo("Partida detectada." if em_partida else "Partida encerrada.")
            for evento in novos:
                seu = f" (seu: {evento.papel.value})" if evento.papel else ""
                typer.echo(
                    f"  {_mmss(evento.tempo_jogo_s)} de jogo · {_hora_local(evento.horario_real)}"
                    f" · {evento.tipo}{seu}"
                )
            time.sleep(INTERVALO_DO_COLETOR_S)
    except KeyboardInterrupt:
        typer.echo("Coleta encerrada.")
    finally:
        coletor.cliente.fechar()
        engine.dispose()


@app.command()
def eventos(
    gravacao_id: Annotated[
        int | None,
        typer.Option("--gravacao", "-g", help="Número da gravação (padrão: a mais recente)."),
    ] = None,
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
) -> None:
    """Mostra os eventos do LoL de uma gravação, com o segundo de cada um no vídeo."""
    if demo:
        cfg = config_da_demo(_pasta_dados_da_demo(arquivo))
        _exigir_demo(cfg)
    else:
        cfg = _carregar_ou_sair(arquivo)
    engine = _abrir_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            consulta = select(Gravacao).order_by(col(Gravacao.inicio).desc())
            if gravacao_id is not None:
                consulta = select(Gravacao).where(Gravacao.id == gravacao_id)
            gravacao = sessao.exec(consulta).first()
            if gravacao is None:
                typer.echo("Nenhuma gravação encontrada.", err=True)
                raise typer.Exit(code=1)
            lista = alinhamento.eventos_da_gravacao(sessao, gravacao)
    finally:
        engine.dispose()
    typer.echo(f"Gravação {gravacao.id}: {Path(gravacao.arquivo).name}")
    if gravacao.duracao_s is None:
        typer.echo(
            "Aviso: a duração desta gravação ainda não foi medida ('clipador processar'); "
            "mostrando as 12 h seguintes ao início."
        )
    if not lista:
        typer.echo("Nenhum evento do LoL durante esta gravação.")
        return
    for evento, segundo in lista:
        seu = f" (seu: {evento.papel.value})" if evento.papel else ""
        typer.echo(f"  {_mmss(segundo)} no vídeo · {evento.tipo}{seu}")


@app.command("instalar-servico")
def instalar_servico(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Faz o serviço abrir sozinho quando você entra no Windows (sem janela)."""
    cfg = _carregar_ou_sair(arquivo)  # só cadastra uma configuração que funciona
    comando = inicializacao.instalar(arquivo)
    log.info("Serviço cadastrado para abrir no login: %s", comando)
    typer.echo("Pronto: o serviço vai abrir sozinho no próximo login do Windows.")
    typer.echo(f"  Comando cadastrado: {comando}")
    typer.echo(f"  Log do serviço: {cfg.pasta_dados / 'logs' / 'servico.log'}")
    typer.echo("Para abrir agora, sem reiniciar: clipador iniciar")
    typer.echo("Para desfazer: clipador remover-servico")


@app.command("remover-servico")
def remover_servico() -> None:
    """Faz o serviço parar de abrir sozinho no login."""
    if inicializacao.remover():
        typer.echo("Pronto: o serviço não abre mais sozinho no login.")
    else:
        typer.echo("O serviço não estava cadastrado para abrir no login.")


def main() -> None:
    app()
