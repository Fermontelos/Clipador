"""Etapa 5: extração do áudio da voz."""

import logging
import os
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import numpy as np
import pytest
import soundfile
from typer.testing import CliRunner

from clipador import ffmpeg
from clipador.banco.fila import FalhaDefinitivaError
from clipador.banco.modelos import Gravacao, StatusGravacao, Tarefa, TipoTarefa, agora
from clipador.cli import app
from clipador.config import Faixas, config_sem_arquivo
from clipador.demo import gerar, roteiro
from clipador.demo.roteiro import Pico
from clipador.ingestao import audio

precisa_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg não instalado neste PC"
)
runner = CliRunner()


# ------------------------------------------------------------------ ajudantes


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(
        pasta_gravacoes=tmp_path / "gravacoes", pasta_dados=tmp_path / "dados"
    )


def tarefa_para(sessao, arquivo: Path) -> Tarefa:
    gravacao = Gravacao(arquivo=str(arquivo), inicio=agora())
    sessao.add(gravacao)
    sessao.flush()
    tarefa = Tarefa(tipo=TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    sessao.add(tarefa)
    sessao.commit()
    return tarefa


PICO_CURTO = (Pico(2.0, 3.0),)


def gravacao_curta(pasta: Path, picos=PICO_CURTO, duracao_s: float = 6) -> Path:
    """Uma gravação de poucos segundos com as 4 faixas do OBS (a mesma receita da demo)."""
    arquivo = pasta / "2026-10-14 20-00-00.mkv"
    gerar.gerar_gravacao(arquivo, duracao_s, picos)
    return arquivo


def video_com_faixas(destino: Path, *fontes_de_audio: str, duracao_s: float = 2) -> Path:
    """Um MKV com vídeo e as faixas de áudio dadas (fontes de som do FFmpeg)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    entradas = ["-f", "lavfi", "-i", f"color=s=320x240:r=10:d={duracao_s}"]
    mapas = ["-map", "0:v"]
    for numero, fonte in enumerate(fontes_de_audio, start=1):
        entradas += ["-f", "lavfi", "-i", fonte]
        mapas += ["-map", f"{numero}:a"]
    ffmpeg.rodar([*entradas, *mapas, "-c:v", "libx264", "-preset", "ultrafast",
                  "-c:a", "libopus", "-t", str(duracao_s), "-y", str(destino)])  # fmt: skip
    return destino


def live_terminada_ha_1_hora(arquivo: Path) -> None:
    """Muda a data do vídeo para 1 h atrás: o monitor o vê como uma live que já acabou."""
    instante = time.time() - 3600
    os.utime(arquivo, (instante, instante))


SOM = "sine=frequency=300:sample_rate=48000"
SILENCIO = "anullsrc=sample_rate=48000"


def trechos_altos(wav: Path, fator: float = 5.0) -> list[tuple[float, float]]:
    """Trechos (em s) em que o volume fica `fator` vezes acima da mediana."""
    amostras, taxa = soundfile.read(str(wav))
    janela = taxa // 4
    volumes = np.sqrt(
        (amostras[: len(amostras) // janela * janela].reshape(-1, janela) ** 2).mean(1)
    )
    alto = (volumes > np.median(volumes) * fator).astype(int)
    bordas = np.flatnonzero(np.diff(np.r_[0, alto, 0]))
    return [(float(a / 4), float(b / 4)) for a, b in zip(bordas[::2], bordas[1::2], strict=True)]


def assert_um_pico(wav: Path, inicio: float, fim: float) -> None:
    """Um pico só, no lugar certo, com tolerância de uma janela de medida (0,25 s): o ruído
    da "voz" sintética muda a cada geração, e a compressão Opus espalha um pouco o fim."""
    [(medido_inicio, medido_fim)] = trechos_altos(wav)
    assert medido_inicio == pytest.approx(inicio, abs=0.25)
    assert medido_fim == pytest.approx(fim, abs=0.25)


# ------------------------------------------------------------------ sem rodar o FFmpeg


def test_comando_pega_a_faixa_certa_em_mono_16khz():
    comando = audio.comando_extrair_voz(Path("live.mkv"), 2, Path("voz.wav"))
    texto = " ".join(comando)
    assert "-map 0:a:1" in texto  # faixa 2 do OBS = 2º áudio, contado do zero
    assert "-ac 1" in texto and "-ar 16000" in texto and "-c:a pcm_s16le" in texto
    assert "-vn" in comando
    assert audio.comando_extrair_voz(Path("x.mkv"), 3, Path("v.wav"))[4] == "0:a:2"


def test_tempo_limite_cresce_com_a_duracao():
    assert audio.tempo_limite_s(0) == 120
    assert audio.tempo_limite_s(3 * 3600) == pytest.approx(120 + 2160)


def info(faixas_audio: int) -> ffmpeg.InfoMidia:
    return ffmpeg.InfoMidia(
        duracao_s=10, faixas_audio=faixas_audio, largura=1920, altura=1080, titulos_faixas=()
    )


def test_sem_a_faixa_da_voz_e_falha_definitiva():
    with pytest.raises(FalhaDefinitivaError, match=r"1 faixa\(s\).*faixa 2 \(voz\)"):
        audio.conferir_faixas(info(1), Faixas(), "live.mkv")


def test_sem_jogo_ou_musica_so_avisa(caplog):
    with caplog.at_level(logging.WARNING):
        audio.conferir_faixas(info(2), Faixas(), "live.mkv")
    assert "3 (jogo), 4 (música)" in caplog.text
    caplog.clear()
    audio.conferir_faixas(info(4), Faixas(), "live.mkv")
    assert caplog.text == ""


def test_falta_de_espaco_da_erro_claro(tmp_path, monkeypatch):
    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(audio.shutil, "disk_usage", lambda _: Uso(0, 0, 10_000_000))
    with pytest.raises(OSError, match="Falta espaço em disco"):
        audio.conferir_espaco(tmp_path, duracao_s=3 * 3600)  # ~380 MB
    audio.conferir_espaco(tmp_path, duracao_s=60)  # ~2 MB: cabe


def test_fps_do_ffprobe():
    assert ffmpeg._fps("60/1") == 60
    assert ffmpeg._fps("30000/1001") == pytest.approx(29.97)
    assert ffmpeg._fps("0/0") is None
    assert ffmpeg._fps(None) is None


def test_gravacao_que_sumiu_nao_e_falha_definitiva(cfg, sessao):
    # Pode ser um HD externo desconectado: vale tentar de novo.
    tarefa = tarefa_para(sessao, cfg.pasta_gravacoes / "sumiu.mkv")
    with pytest.raises(FileNotFoundError, match="não foi encontrada"):
        audio.extrair_audio(sessao, tarefa, cfg)


def test_limpar_trabalho_apaga_so_a_pasta_da_gravacao(cfg):
    (cfg.pasta_trabalho / "1").mkdir(parents=True)
    (cfg.pasta_trabalho / "1" / "voz.wav").write_bytes(b"x")
    (cfg.pasta_trabalho / "2").mkdir()
    audio.limpar_trabalho(cfg, 1)
    assert not (cfg.pasta_trabalho / "1").exists()
    assert (cfg.pasta_trabalho / "2").exists()
    audio.limpar_trabalho(cfg, 1)  # já apagada: não dá erro


# ------------------------------------------------------------------ com o FFmpeg


@precisa_ffmpeg
def test_extrai_a_voz_e_registra_os_dados_do_video(cfg, sessao):
    arquivo = gravacao_curta(cfg.pasta_gravacoes)
    tarefa = tarefa_para(sessao, arquivo)

    audio.extrair_audio(sessao, tarefa, cfg)

    wav = audio.caminho_da_voz(cfg, tarefa.gravacao_id)
    dados = soundfile.info(str(wav))
    assert (dados.samplerate, dados.channels, dados.subtype) == (16000, 1, "PCM_16")
    assert dados.duration == pytest.approx(6, abs=0.1)
    assert_um_pico(wav, 2.0, 3.0)  # o pico da voz está no lugar certo
    gravacao = sessao.get(Gravacao, tarefa.gravacao_id)
    assert (gravacao.largura, gravacao.altura, gravacao.fps) == (1280, 720, 30)
    assert gravacao.duracao_s == pytest.approx(6, abs=0.1)
    assert gravacao.status is StatusGravacao.PROCESSANDO
    assert list(wav.parent.iterdir()) == [wav]  # nenhum temporário sobrou


@precisa_ffmpeg
def test_faixa_da_voz_vem_do_config(tmp_path, sessao):
    # Voz na faixa 3: o pico tem de vir da faixa 3, e não da 2 (que aqui é um tom constante).
    cfg = config_sem_arquivo(
        pasta_gravacoes=tmp_path / "g",
        pasta_dados=tmp_path / "d",
        faixas=Faixas(voz=3, jogo=2, musica=4),
    )
    arquivo = gravacao_curta(cfg.pasta_gravacoes)
    tarefa = tarefa_para(sessao, arquivo)
    audio.extrair_audio(sessao, tarefa, cfg)
    assert trechos_altos(audio.caminho_da_voz(cfg, tarefa.gravacao_id)) == []


@precisa_ffmpeg
def test_gravacao_com_uma_faixa_so_da_erro_claro(cfg, sessao):
    arquivo = video_com_faixas(cfg.pasta_gravacoes / "2026-02-21 16-41-13.mkv", SOM)
    tarefa = tarefa_para(sessao, arquivo)
    with pytest.raises(FalhaDefinitivaError, match="não tem a faixa 2 \\(voz\\): confira no OBS"):
        audio.extrair_audio(sessao, tarefa, cfg)


@precisa_ffmpeg
def test_faixa_da_voz_muda_da_erro_claro(cfg, sessao):
    # O erro mais comum de configuração do OBS: o microfone não foi marcado na faixa 2.
    arquivo = video_com_faixas(cfg.pasta_gravacoes / "live.mkv", SOM, SILENCIO)
    tarefa = tarefa_para(sessao, arquivo)
    with pytest.raises(FalhaDefinitivaError, match="está em silêncio"):
        audio.extrair_audio(sessao, tarefa, cfg)


@precisa_ffmpeg
def test_ffmpeg_que_falha_nao_deixa_wav_pela_metade(cfg, sessao, monkeypatch):
    arquivo = video_com_faixas(cfg.pasta_gravacoes / "live.mkv", SOM, SOM)
    tarefa = tarefa_para(sessao, arquivo)

    def falha_no_meio(argumentos, tempo_limite_s=None):
        Path(argumentos[-1]).write_bytes(b"RIFF pela metade")
        raise ffmpeg.FFmpegFalhouError("o PC desligou")

    monkeypatch.setattr(audio.ffmpeg, "rodar", falha_no_meio)
    with pytest.raises(ffmpeg.FFmpegFalhouError):
        audio.extrair_audio(sessao, tarefa, cfg)
    assert list(audio.caminho_da_voz(cfg, tarefa.gravacao_id).parent.iterdir()) == []


@precisa_ffmpeg
def test_processar_demo_extrai_a_voz(tmp_path, monkeypatch):
    # A demo de verdade tem 3 min (ver o teste lento abaixo); aqui, o mesmo roteiro em 6 s.
    monkeypatch.setattr(roteiro, "DURACAO_S", 6)
    monkeypatch.setattr(roteiro, "PICOS_VOZ", PICO_CURTO)
    sem_config = str(tmp_path / "config.toml")  # não existe: a demo funciona sem ele
    assert runner.invoke(app, ["demo", "--arquivo", sem_config]).exit_code == 0
    pasta = gerar.pasta_da_demo(tmp_path / "dados")

    saida = runner.invoke(app, ["processar", "--demo", "--arquivo", sem_config])

    assert saida.exit_code == 0, saida.output
    assert "extrair_audio: feita" in saida.output
    assert (pasta / "trabalho" / "1" / "voz.wav").is_file()


# ------------------------------------------------------------------ a demo de verdade


@pytest.mark.lento
@precisa_ffmpeg
def test_na_demo_de_3_min_a_voz_tem_a_duracao_e_os_picos_certos(tmp_path):
    gerar.preparar_demo(tmp_path / "dados")
    saida = runner.invoke(app, ["processar", "--demo", "--arquivo", str(tmp_path / "config.toml")])
    assert saida.exit_code == 0, saida.output

    wav = gerar.pasta_da_demo(tmp_path / "dados") / "trabalho" / "1" / "voz.wav"
    assert soundfile.info(str(wav)).duration == pytest.approx(roteiro.DURACAO_S, abs=0.5)
    # Tolerância de uma janela de medida (0,25 s): a compressão Opus espalha um pouco o
    # fim de cada pico.
    esperado = [(p.inicio_s, p.fim_s) for p in roteiro.PICOS_VOZ]
    medido = trechos_altos(wav)
    assert len(medido) == len(esperado)
    for (inicio, fim), (inicio_esperado, fim_esperado) in zip(medido, esperado, strict=True):
        assert inicio == pytest.approx(inicio_esperado, abs=0.25)
        assert fim == pytest.approx(fim_esperado, abs=0.25)


# ------------------------------------------------------------------ revisão


@precisa_ffmpeg
def test_funciona_em_pastas_com_acento(tmp_path, sessao):
    # "Vídeos" é o nome da pasta de vídeos no Windows em português.
    cfg = config_sem_arquivo(
        pasta_gravacoes=tmp_path / "Vídeos", pasta_dados=tmp_path / "Ação dados"
    )
    tarefa = tarefa_para(sessao, gravacao_curta(cfg.pasta_gravacoes))
    audio.extrair_audio(sessao, tarefa, cfg)
    assert_um_pico(audio.caminho_da_voz(cfg, tarefa.gravacao_id), 2.0, 3.0)


def test_ffprobe_travado_vira_erro_e_nao_trava_a_fila(tmp_path, monkeypatch):
    def travado(comando, tempo_limite_s):
        raise ffmpeg.subprocess.TimeoutExpired(cmd=comando, timeout=tempo_limite_s)

    monkeypatch.setattr(ffmpeg, "localizar", lambda programa="ffmpeg": programa)
    monkeypatch.setattr(ffmpeg, "_executar", travado)
    with pytest.raises(ffmpeg.FFmpegFalhouError, match="passou do limite de 60 s"):
        ffmpeg.inspecionar(tmp_path / "corrompido.mkv")


@precisa_ffmpeg
def test_erro_refazer_e_processar_pela_linha_de_comando(tmp_path):
    # O caminho completo de quem erra o [faixas]: erro claro, corrige, refaz, funciona.
    gravacoes = tmp_path / "gravacoes"
    video_com_faixas(gravacoes / "2026-10-14 20-00-00.mkv", SOM, SILENCIO, SOM)
    caminho = tmp_path / "config.toml"
    base = (
        f'pasta_gravacoes = "{gravacoes.as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n'
    )
    caminho.write_text(base, encoding="utf-8")
    live_terminada_ha_1_hora(gravacoes / "2026-10-14 20-00-00.mkv")

    saida = runner.invoke(app, ["processar", "--arquivo", str(caminho)])
    assert saida.exit_code == 1
    status = runner.invoke(app, ["status", "--arquivo", str(caminho)]).output
    assert "está em silêncio" in status and "clipador refazer <número>" in status

    caminho.write_text(base + "[faixas]\nvoz = 3\njogo = 1\nmusica = 2\n", encoding="utf-8")
    assert runner.invoke(app, ["refazer", "1", "--arquivo", str(caminho)]).exit_code == 0
    saida = runner.invoke(app, ["processar", "--arquivo", str(caminho)])
    assert saida.exit_code == 0, saida.output
    assert "extrair_audio: feita" in saida.output
    assert runner.invoke(app, ["refazer", "1", "--arquivo", str(caminho)]).exit_code == 1


@precisa_ffmpeg
def test_gravacao_interrompida_sem_duracao_e_aproveitada(cfg, sessao, caplog):
    # Se o OBS trava ou falta luz, o MKV continua legível, mas fica sem a duração no
    # cabeçalho (ela só é escrita no fim). Gravar por um "pipe" produz o mesmo arquivo:
    # o FFmpeg não consegue voltar ao começo para escrever a duração.
    arquivo = cfg.pasta_gravacoes / "2026-10-14 20-00-00.mkv"
    arquivo.parent.mkdir(parents=True)
    with arquivo.open("wb") as saida:
        subprocess.run(
            [ffmpeg.localizar(), "-v", "error",
             "-f", "lavfi", "-i", "color=s=320x240:r=10:d=3",
             "-f", "lavfi", "-i", f"{SOM}:duration=3",
             "-f", "lavfi", "-i", f"{SOM}:duration=3",
             "-map", "0:v", "-map", "1:a", "-map", "2:a",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "libopus",
             "-f", "matroska", "-"],
            stdout=saida, check=True,
        )  # fmt: skip
    assert ffmpeg.inspecionar(arquivo).duracao_s is None
    tarefa = tarefa_para(sessao, arquivo)

    with caplog.at_level(logging.WARNING):
        audio.extrair_audio(sessao, tarefa, cfg)

    assert sessao.get(Gravacao, tarefa.gravacao_id).duracao_s == pytest.approx(3, abs=0.1)
    assert "não informa a duração" in caplog.text
