"""Gera a live falsa e prepara o banco da demo.

Tudo fica em ``<pasta_dados>/demo/``, separado dos dados reais:

    demo/
    ├─ gravacoes/2026-01-10 20-00-00.mkv   a "live" de 3 minutos
    ├─ clipador.db                         banco só da demo
    └─ backups/

O vídeo usa o encoder da CPU (``libx264``), e não o da placa: a demo precisa rodar em
qualquer notebook, inclusive um sem placa NVIDIA (o da entrevista, por exemplo).

Escolhas medidas para a demo ficar rápida (o roteiro pede menos de 1 minuto):
- imagem: fundo liso com o tempo da gravação escrito no centro (13 s; o padrão de teste
  ``testsrc`` levava 28 s). De quebra, quem assiste vê em que segundo está cada pico;
- áudio em Opus (3 s para as 4 faixas; o AAC levava 24 s). Uma gravação real do OBS vem
  em AAC, mas o FFmpeg lê os dois, então o resto do app não muda.

Rodar de novo não duplica nada: um vídeo válido é reaproveitado e o banco só é
preenchido se a gravação ainda não estiver nele. ``do_zero=True`` apaga e recria tudo.
"""

import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from pathlib import Path

from sqlmodel import func, select

from clipador import ffmpeg
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import (
    EventoJogo,
    Gravacao,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
    Transcricao,
)
from clipador.config import Config, config_sem_arquivo
from clipador.demo import roteiro
from clipador.demo.roteiro import Pico
from clipador.jogo import eventos

log = logging.getLogger(__name__)

LARGURA, ALTURA, QUADROS_POR_SEGUNDO = 1280, 720, 30
TAXA_AUDIO = 48_000
# Volume da "voz": baixo na fala normal e bem mais alto nos picos (grito, risada).
VOLUME_FALA = 0.04
VOLUME_PICO = 0.6
# Mesma ordem do OBS: faixa 1 = mistura, 2 = voz, 3 = jogo, 4 = música.
TITULOS_FAIXAS = ("Mistura", "Voz", "Jogo", "Música")
FONTE_RELOGIO = Path("C:/Windows/Fonts/arial.ttf")
COR_FUNDO = "0x1e1e2e"


def pasta_da_demo(pasta_dados: Path) -> Path:
    return pasta_dados / "demo"


def config_da_demo(pasta_dados: Path) -> Config:
    """Configuração para processar a demo: pastas dela e faixas no padrão do OBS."""
    pasta = pasta_da_demo(pasta_dados)
    return config_sem_arquivo(pasta_gravacoes=pasta / "gravacoes", pasta_dados=pasta)


def _volume_da_voz(picos: tuple[Pico, ...]) -> str:
    """Expressão de volume: baixo na fala normal, bem mais alto nos picos."""
    if not picos:
        return str(VOLUME_FALA)
    trechos = "+".join(f"between(t,{p.inicio_s},{p.fim_s})" for p in picos)
    return f"{VOLUME_FALA}+{VOLUME_PICO - VOLUME_FALA}*min(1,{trechos})"


def comando_gerar(destino: Path, duracao_s: float, picos: tuple[Pico, ...]) -> list[str]:
    """Argumentos do FFmpeg para gerar a gravação falsa (separado para poder testar).

    Os sons usam os geradores do próprio FFmpeg, e o volume é calculado uma vez por bloco
    de amostras (``eval=frame``), e não amostra por amostra: isso deixa a geração bem
    mais rápida.
    """
    duracao = f"duration={duracao_s}"
    estereo = "aformat=channel_layouts=stereo"
    grafo = ";".join(
        [
            # Voz: ruído rosa (parecido com fala) com o volume subindo nos picos.
            f"anoisesrc=color=pink:sample_rate={TAXA_AUDIO}:{duracao},"
            f"volume=volume='{_volume_da_voz(picos)}':eval=frame,{estereo},asplit=2[voz][voz_m]",
            # Jogo: um tom grave constante.
            f"sine=frequency=220:sample_rate={TAXA_AUDIO}:{duracao},"
            f"volume=0.08,{estereo},asplit=2[jogo][jogo_m]",
            # Música: um tom que "pulsa".
            f"sine=frequency=440:sample_rate={TAXA_AUDIO}:{duracao},"
            f"volume=volume='0.12*(0.5+0.5*sin(2*PI*0.5*t))':eval=frame,{estereo},"
            "asplit=2[musica][musica_m]",
            # Faixa 1: a mistura dos três, como a que vai para a Twitch.
            "[voz_m][jogo_m][musica_m]amix=inputs=3:normalize=0[mistura]",
        ]
    )
    imagem = f"color=c={COR_FUNDO}:s={LARGURA}x{ALTURA}:r={QUADROS_POR_SEGUNDO}:d={duracao_s}"
    if FONTE_RELOGIO.is_file():
        # O ":" do caminho e do formato precisa de "\" para não ser lido como separador.
        fonte = FONTE_RELOGIO.as_posix().replace(":", r"\:")
        imagem += (
            f",drawtext=fontfile='{fonte}':text='%{{pts\\:hms}}':fontsize=120"
            ":fontcolor=white:x=(w-tw)/2:y=(h-th)/2"
        )
    argumentos = [
        "-y",
        "-f", "lavfi", "-i", imagem,
        "-filter_complex", grafo,
        "-map", "0:v", "-map", "[mistura]", "-map", "[voz]", "-map", "[jogo]", "-map", "[musica]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "libopus", "-b:a", "96k",
    ]  # fmt: skip
    for indice, titulo in enumerate(TITULOS_FAIXAS):
        argumentos += [f"-metadata:s:a:{indice}", f"title={titulo}"]
    argumentos += ["-t", str(duracao_s), str(destino)]
    return argumentos


def gerar_gravacao(destino: Path, duracao_s: float, picos: tuple[Pico, ...]) -> None:
    """Cria o MKV falso. Escreve num arquivo temporário e só renomeia no fim, para uma
    interrupção não deixar um arquivo pela metade com cara de pronto."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.stem + ".parcial.mkv")
    try:
        # Limite folgado (a demo leva ~15 s): só para não ficar presa se o FFmpeg travar.
        ffmpeg.rodar(comando_gerar(temporario, duracao_s, picos), tempo_limite_s=300)
    except Exception:
        temporario.unlink(missing_ok=True)  # não deixa o arquivo pela metade para trás
        raise
    temporario.replace(destino)
    log.info("Gravação da demo criada em %s", destino)


def gravacao_valida(arquivo: Path) -> bool:
    """O arquivo existe, tem as 4 faixas de áudio e a duração do roteiro?"""
    if not arquivo.is_file():
        return False
    try:
        info = ffmpeg.inspecionar(arquivo)
    except ffmpeg.FFmpegFalhouError:
        return False
    return (
        info.faixas_audio == len(TITULOS_FAIXAS)
        and info.duracao_s is not None
        and abs(info.duracao_s - roteiro.DURACAO_S) < 1
    )


def _popular_banco(sessao, arquivo: Path) -> bool:
    """Grava a gravação, a transcrição, os eventos e a 1ª tarefa. Tudo num commit só.

    Devolve ``False`` se a gravação da demo já estava no banco. Ela é reconhecida pelo
    **nome** do arquivo, e não pelo caminho completo: se a pasta do projeto for movida ou
    renomeada, o caminho muda, e comparar o caminho faria a demo ser inserida de novo
    (duplicando gravação, eventos e tarefas). Nesse caso só o caminho é atualizado.
    """
    for existente in sessao.exec(select(Gravacao)).all():
        if Path(existente.arquivo).name == arquivo.name:
            if existente.arquivo != str(arquivo):
                existente.arquivo = str(arquivo)
                sessao.add(existente)
                sessao.commit()
                log.info("Caminho da gravação da demo atualizado para %s", arquivo)
            return False
    gravacao = Gravacao(
        arquivo=str(arquivo),
        inicio=roteiro.INICIO.astimezone(UTC),
        duracao_s=roteiro.DURACAO_S,
    )
    sessao.add(gravacao)
    sessao.flush()  # gera o id da gravação, ainda sem gravar de vez
    sessao.add(
        Transcricao(
            gravacao_id=gravacao.id,
            palavras=roteiro.palavras_da_transcricao(),
            segmentos=roteiro.frases_da_transcricao(),
            # Já vem pronta: a etapa 6 só exporta a legenda, sem precisar do Whisper (e
            # da placa de vídeo), para a demo rodar em qualquer PC.
            transcrito_ate_s=roteiro.DURACAO_S,
            concluida=True,
            modelo="demo (transcrição pronta)",
        )
    )
    partida_inicio = (roteiro.INICIO + timedelta(seconds=roteiro.PARTIDA_COMECA_S)).astimezone(UTC)
    for numero, evento in enumerate(roteiro.EVENTOS_LOL, start=1):
        tempo_jogo_s = evento.gravacao_s - roteiro.PARTIDA_COMECA_S
        dados_da_api = {
            "EventID": numero,
            "EventName": evento.tipo,
            "EventTime": tempo_jogo_s,
            **evento.dados,
        }
        sessao.add(
            EventoJogo(
                tipo=evento.tipo,
                horario_real=(roteiro.INICIO + timedelta(seconds=evento.gravacao_s)).astimezone(
                    UTC
                ),
                tempo_jogo_s=tempo_jogo_s,
                # Os mesmos campos que o coletor (etapa 7) salva de uma partida real.
                partida_inicio=partida_inicio,
                event_id=numero,
                papel=eventos.papel(dados_da_api, roteiro.SEU_RIOT_ID),
                dados={**dados_da_api, "demo": True},
            )
        )
    # A mesma 1ª tarefa que o monitor de pasta (etapa 4) cria para uma gravação real.
    sessao.add(Tarefa(tipo=TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id))
    sessao.commit()
    return True


@dataclass
class ResultadoDemo:
    pasta: Path
    arquivo: Path
    banco: Path
    info: ffmpeg.InfoMidia
    video_criado: bool
    banco_preenchido: bool
    segundos: float
    contagens: dict[str, int] = field(default_factory=dict)


def preparar_demo(pasta_dados: Path, do_zero: bool = False) -> ResultadoDemo:
    """Cria (ou reaproveita) a live falsa e o banco da demo."""
    inicio = time.perf_counter()
    pasta = pasta_da_demo(pasta_dados)
    if do_zero and pasta.exists():
        # Trava de segurança: só apaga uma pasta chamada "demo" dentro de pasta_dados.
        if pasta.name != "demo" or pasta.resolve().parent != pasta_dados.resolve():
            raise RuntimeError(f"Recusado apagar {pasta}: não é a pasta da demo.")
        shutil.rmtree(pasta)

    arquivo = pasta / "gravacoes" / roteiro.NOME_ARQUIVO
    video_criado = False
    if not gravacao_valida(arquivo):
        gerar_gravacao(arquivo, roteiro.DURACAO_S, roteiro.PICOS_VOZ)
        video_criado = True

    banco = pasta / "clipador.db"
    engine = abrir_banco(banco, pasta / "backups")
    try:
        with nova_sessao(engine) as sessao:
            banco_preenchido = _popular_banco(sessao, arquivo)
            contagens = {
                nome: sessao.exec(select(func.count()).select_from(tabela)).one()
                for nome, tabela in (
                    ("gravacoes", Gravacao),
                    ("eventos", EventoJogo),
                    ("transcricoes", Transcricao),
                    ("tarefas", Tarefa),
                )
            }
            contagens["tarefas_pendentes"] = sessao.exec(
                select(func.count())
                .select_from(Tarefa)
                .where(Tarefa.status == StatusTarefa.PENDENTE)
            ).one()
    finally:
        engine.dispose()

    return ResultadoDemo(
        pasta=pasta,
        arquivo=arquivo,
        banco=banco,
        info=ffmpeg.inspecionar(arquivo),
        video_criado=video_criado,
        banco_preenchido=banco_preenchido,
        segundos=time.perf_counter() - inicio,
        contagens=contagens,
    )
