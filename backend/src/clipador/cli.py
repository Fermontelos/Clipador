"""Linha de comando do app (``clipador ...``)."""

import contextlib
import logging
import sys
import threading
import time
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlmodel import col, select

from clipador import __version__, ffmpeg
from clipador.aprendizado import sugestao as aprendizado
from clipador.banco import fila
from clipador.banco.backup import fazer_backup, listar_backups
from clipador.banco.conexao import BancoMaisNovoQueOCodigoError, abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, Momento, StatusTarefa, Tarefa
from clipador.config import (
    CAMINHO_PADRAO,
    Config,
    ConfigMalEscritaError,
    ConfigNaoEncontradaError,
    carregar_config,
)
from clipador.cortes import comandos
from clipador.demo import roteiro
from clipador.demo.gerar import config_da_demo, preparar_demo
from clipador.ingestao.monitor import SemMonitor
from clipador.jogo import alinhamento
from clipador.jogo.coletor import INTERVALO_S as INTERVALO_DO_COLETOR_S
from clipador.jogo.coletor import ColetorLol
from clipador.logs import configurar_logs
from clipador.sistema import inicializacao, ociosidade
from clipador.sistema.desligamento import Desligamento
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


def config_do_comando(arquivo: Path, demo: bool) -> Config:
    """A configuração de um comando com ``--demo``: a da demo (que precisa existir) ou a sua."""
    if demo:
        cfg = config_da_demo(_pasta_dados_da_demo(arquivo))
        _exigir_demo(cfg)
        return cfg
    return _carregar_ou_sair(arquivo)


def abrir_banco_ou_sair(cfg: Config):
    """Abre o banco; um banco de uma versão mais nova do app vira mensagem, não traceback."""
    try:
        return abrir_banco(cfg.caminho_banco, cfg.pasta_backups_efetiva)
    except BancoMaisNovoQueOCodigoError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro


def _estado_do_desligamento(cfg: Config) -> str:
    """Ex.: "ligado, em modo de teste (só escreve no log) · PC ocioso há 3 min"."""
    regras = cfg.desligamento
    if not regras.desligar_ao_terminar:
        estado = "desligado"
    elif regras.modo_teste:
        estado = "ligado, em modo de teste (só escreve no log)"
    else:
        estado = f"ligado (depois de {regras.minutos_ocioso:.0f} min sem mexer no PC)"
    with contextlib.suppress(OSError):  # fora da sua sessão do Windows, não dá para medir
        estado += f" · PC ocioso há {ociosidade.segundos_ocioso() / 60:.0f} min"
    return estado


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
    if cfg.caminho_banco.is_file():  # sem banco ainda, não há versão aceita
        engine = abrir_banco_ou_sair(cfg)
        try:
            with nova_sessao(engine) as sessao:
                versao = aprendizado.versao_atual(sessao)
        finally:
            engine.dispose()
        if versao is not None:
            janela = (
                f"janela {_br(versao.segundos_antes)} s / {_br(versao.segundos_depois or 0)} s"
                if versao.segundos_antes is not None
                else "janela do config"
            )
            typer.echo(
                f"Atenção: vale a versão {versao.versao} do aprendizado (pesos "
                f"{versao.valores or 'do config'}, {janela}), no lugar dos de [deteccao]. "
                "Veja com 'clipador aprender'."
            )
    if not cfg.pasta_gravacoes.is_dir():
        typer.echo(f"Aviso: a pasta de gravações {cfg.pasta_gravacoes} ainda não existe.", err=True)


OpcaoDemo = Annotated[
    bool,
    typer.Option("--demo", help="Usa o banco da demo (dados/demo/) em vez do real."),
]


@app.command()
def status(arquivo: OpcaoArquivo = CAMINHO_PADRAO, demo: OpcaoDemo = False) -> None:
    """Mostra a fila de tarefas: quantas há em cada situação, as próximas e os erros."""
    cfg = config_do_comando(arquivo, demo)
    caminho_banco, pasta_backups = cfg.caminho_banco, cfg.pasta_backups_efetiva
    engine = abrir_banco_ou_sair(cfg)
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
        typer.echo(f"Desligar ao terminar: {_estado_do_desligamento(cfg)}")
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
        f"  Banco da demo: a live, {c['eventos']} eventos do LoL, {c['transcricoes']} "
        f"transcrição pronta, {c['tarefas_pendentes']} tarefa(s) na fila; e "
        f"{c['gravacoes'] - 1} lives antigas com {c['decisoes']} decisões (para o aprendizado)"
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
        desligamento = Desligamento(cfg.desligamento, ociosidade.segundos_ocioso)
        Servico(cfg, desligamento=desligamento).rodar(parar)
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
                motivo = resultado.motivo_da_pausa or "pausada (começou uma gravação)"
                typer.echo(f"  {resultado.tarefa_executada}: {motivo}")
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
    cfg = config_do_comando(arquivo, demo)
    engine = abrir_banco_ou_sair(cfg)
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
    engine = abrir_banco_ou_sair(cfg)
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
    cfg = config_do_comando(arquivo, demo)
    engine = abrir_banco_ou_sair(cfg)
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


@app.command()
def momentos(
    gravacao_id: Annotated[
        int | None,
        typer.Option("--gravacao", "-g", help="Número da gravação (padrão: a mais recente)."),
    ] = None,
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
) -> None:
    """Mostra os momentos achados numa gravação, do de maior nota para o de menor."""
    cfg = config_do_comando(arquivo, demo)
    engine = abrir_banco_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            consulta = select(Gravacao).order_by(col(Gravacao.inicio).desc())
            if gravacao_id is not None:
                consulta = select(Gravacao).where(Gravacao.id == gravacao_id)
            gravacao = sessao.exec(consulta).first()
            if gravacao is None:
                typer.echo("Nenhuma gravação encontrada.", err=True)
                raise typer.Exit(code=1)
            lista = sessao.exec(
                select(Momento)
                .where(Momento.gravacao_id == gravacao.id)
                .order_by(col(Momento.nota).desc(), col(Momento.inicio_s))
            ).all()
    finally:
        engine.dispose()
    typer.echo(f"Gravação {gravacao.id}: {Path(gravacao.arquivo).name}")
    if not lista:
        typer.echo("Nenhum momento ainda (a detecção roda depois da transcrição).")
        return
    for posicao, momento in enumerate(lista, start=1):
        typer.echo(
            f"{posicao:3d}. nota {momento.nota:5.1f} · {_mmss(momento.inicio_s)}-"
            f"{_mmss(momento.fim_s)} ({momento.fim_s - momento.inicio_s:.0f} s) · {momento.motivo}"
        )
        if momento.titulos_sugeridos:
            titulo = momento.titulos_sugeridos[0]["titulo"]
            hashtags = " ".join(momento.hashtags_sugeridas)
            typer.echo(f"       “{titulo}” {hashtags}".rstrip())


@app.command()
def calibrar(
    gravacao_id: Annotated[
        int | None,
        typer.Option("--gravacao", "-g", help="Número da gravação (padrão: a mais recente)."),
    ] = None,
    segundo: Annotated[
        float | None,
        typer.Option("--segundo", "-s", help="Que segundo da live usar (padrão: o meio)."),
    ] = None,
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
) -> None:
    """Salva um quadro da live com uma grade, para achar a posição da câmera (vertical B)."""
    cfg = config_do_comando(arquivo, demo)
    engine = abrir_banco_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            consulta = select(Gravacao).order_by(col(Gravacao.inicio).desc())
            if gravacao_id is not None:
                consulta = select(Gravacao).where(Gravacao.id == gravacao_id)
            gravacao = sessao.exec(consulta).first()
    finally:
        engine.dispose()
    if gravacao is None:
        typer.echo("Nenhuma gravação encontrada (grave uma live antes).", err=True)
        raise typer.Exit(code=1)
    video = Path(gravacao.arquivo)
    if not video.is_file():
        typer.echo(f"Erro: a gravação {video} não existe mais.", err=True)
        raise typer.Exit(code=1)
    info = ffmpeg.inspecionar(video)
    if info.largura is None or info.altura is None:
        typer.echo(f"Erro: não deu para ler o tamanho da imagem de {video.name}.", err=True)
        raise typer.Exit(code=1)
    duracao = info.duracao_s or gravacao.duracao_s or 0
    instante = segundo if segundo is not None else duracao / 2
    if duracao and not 0 <= instante < duracao:
        typer.echo(f"Erro: a gravação tem {duracao:.0f} s; use --segundo entre 0 e isso.", err=True)
        raise typer.Exit(code=1)
    destino = cfg.pasta_dados / "calibracao.png"
    destino.parent.mkdir(parents=True, exist_ok=True)
    vertical_b = cfg.cortes.vertical_b
    ffmpeg.rodar(
        comandos.comando_de_calibracao(
            video, instante, info.largura, info.altura, vertical_b, destino
        ),
        tempo_limite_s=60,
    )
    typer.echo(f"Quadro salvo em {destino}")
    typer.echo(f"  {video.name}, aos {_mmss(instante)}; a imagem tem {info.largura}×{info.altura}.")
    typer.echo("  Grade a cada 100 px (os números nas bordas são os pixels).")
    jogo = comandos.regiao_do_jogo(info.largura, info.altura, vertical_b)
    typer.echo(f"  Verde: o recorte do jogo (x de {jogo.x} a {jogo.x + jogo.largura}).")
    if vertical_b.camera is None:
        typer.echo("  Sem câmera no config: o vertical B sai só com o jogo. Para pôr a câmera:")
    else:
        c = vertical_b.camera
        typer.echo(f"  Vermelho: a câmera do config ({c.largura}×{c.altura} em {c.x}, {c.y}).")
        try:
            comandos.validar_regiao(c, info.largura, info.altura)
        except ValueError as erro:
            typer.echo(f"  Atenção: {erro}", err=True)
        typer.echo("  Para mudar a câmera:")
    typer.echo("    [cortes.vertical_b]")
    typer.echo("    camera = { x = 1500, y = 760, largura = 400, altura = 300 }")
    typer.echo("  (x, y = o canto de cima à esquerda da câmera; troque pelos números do quadro).")


def _porcento(valor: float | None) -> str:
    return "—" if valor is None else f"{valor * 100:.0f}%"


def _br(valor: float, sinal: bool = False) -> str:
    """Número com vírgula decimal (11,5), como se escreve em português."""
    texto = f"{valor:+g}" if sinal else f"{valor:g}"
    return texto.replace(".", ",")


@app.command()
def aprender(
    aceitar: Annotated[
        bool, typer.Option("--aceitar", help="Aceita a sugestão sem perguntar.")
    ] = False,
    arquivo: OpcaoArquivo = CAMINHO_PADRAO,
    demo: OpcaoDemo = False,
) -> None:
    """Mostra o que as suas decisões ensinam (pesos da nota e janela do corte) e pede o aceite."""
    cfg = config_do_comando(arquivo, demo)
    engine = abrir_banco_ou_sair(cfg)
    try:
        with nova_sessao(engine) as sessao:
            s = aprendizado.sugerir(sessao, cfg)
            typer.echo(f"Decisões: {s.decisoes} ({s.aprovadas} aprovadas).")
            for dia in s.precisao_por_dia[-7:]:
                typer.echo(
                    f"  {dia['dia']}: aprovou {dia['aprovados']} de {dia['decididos']} "
                    f"({_porcento(dia['precisao'])})"
                )
            typer.echo(
                "\nPesos da nota"
                + (f" (versão {s.versao_atual})" if s.versao_atual else " (do config)")
                + ":"
            )
            nomes = sorted(set(s.pesos_atuais) | set(s.pesos_novos or {}))
            for nome in nomes:
                novo = f" -> {s.pesos_novos[nome]:.3f}" if s.pesos_novos else ""
                typer.echo(f"  {nome:12s} {s.pesos_atuais.get(nome, 0):.3f}{novo}")
            if s.pesos_novos:
                typer.echo(
                    f"  Precisão nos de maior nota, em decisões que o treino não viu: "
                    f"{_porcento(s.precisao_antes)} com os atuais, "
                    f"{_porcento(s.precisao_depois)} com os sugeridos."
                )
            else:
                typer.echo(f"  Sem sugestão: {s.sem_pesos_porque}")
            antes, depois = s.janela_atual
            typer.echo(
                f"\nJanela do corte: {_br(antes)} s antes e {_br(depois)} s depois do sinal."
            )
            if s.janela_nova:
                j = s.janela_nova
                typer.echo(
                    f"  Sugestão: {_br(j.segundos_antes)} s antes e {_br(j.segundos_depois)} s "
                    f"depois (você moveu o início {_br(j.media_inicio_s, True)} s e o fim "
                    f"{_br(j.media_fim_s, True)} s, "
                    f"em média, em {j.aprovados} aprovados)."
                )
            else:
                typer.echo(f"  Sem sugestão: {s.sem_janela_porque}")
            if not s.tem_sugestao:
                return
            if not aceitar:
                # O typer.confirm pergunta "[y/N]", em inglês: um "s" seria lido como "não".
                resposta = typer.prompt(
                    "\nAceitar esta sugestão? (s/N)", default="n", show_default=False
                )
                if resposta.strip().lower() not in ("s", "sim", "y", "yes"):
                    typer.echo("Nada mudou.")
                    return
            versao = aprendizado.aceitar(sessao, cfg, s.decisoes)
            typer.echo(
                f"Versão {versao.versao} aceita: vale a partir da próxima detecção "
                "(os momentos já detectados ficam como estão)."
            )
    finally:
        engine.dispose()


@app.command("youtube-entrar")
def youtube_entrar(arquivo: OpcaoArquivo = CAMINHO_PADRAO) -> None:
    """Liga o app ao seu canal do YouTube (uma vez): abre o navegador para você autorizar."""
    from clipador_integracoes import youtube

    cfg = _carregar_ou_sair(arquivo)
    cliente = youtube.ClienteYoutube(cfg.pasta_segredos)
    typer.echo(
        "Abrindo o navegador: entre na conta do canal e autorize o app (o Google avisa que o "
        "app não é verificado; para uso pessoal, continue em 'Avançado')."
    )
    try:
        cliente.entrar()
    except youtube.CredencialDoAppAusenteError as erro:
        typer.echo(f"Erro: {erro}", err=True)
        raise typer.Exit(code=1) from erro
    typer.echo(f"Pronto: login salvo em {cliente.arquivo_do_token} (fora do git).")
    typer.echo("As publicações que esperavam o login: 'clipador status' e 'clipador refazer N'.")


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


def registrar_comandos_de_fora() -> None:
    """Comandos que outras partes do projeto oferecem (ex.: ``clipador web``, da API).

    O backend não pode importar a API (ADR 0003): é a API que se cadastra aqui, pelo grupo
    de *entry points* ``clipador.comandos`` do ``pyproject.toml`` dela. Sem a API instalada,
    o comando só não aparece. Roda no ``main`` (e não ao importar este módulo), porque o
    comando da API importa este módulo: seria um import circular.
    """
    ja_registrados = {comando.name for comando in app.registered_commands}
    for ponto in entry_points(group="clipador.comandos"):
        if ponto.name in ja_registrados:
            continue
        try:
            app.command(ponto.name)(ponto.load())
        except Exception:
            log.exception("Não foi possível carregar o comando '%s' (%s).", ponto.name, ponto.value)


def main() -> None:
    registrar_comandos_de_fora()
    app()
