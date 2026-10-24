"""Etapa 9: cortes dos momentos em clipes.

Os comandos do FFmpeg são conferidos sem rodar nada (rápido, roda em qualquer lugar). O
corte de verdade é testado na demo (marcador ``lento``) e num MKV pequeno com as 4 faixas.
"""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import select

from clipador import ffmpeg
from clipador.banco.fila import Contexto, FalhaDefinitivaError, TarefaPausada
from clipador.banco.modelos import Clipe, Gravacao, Momento, Tarefa, TipoTarefa, Transcricao
from clipador.config import (
    ConfigCortes,
    ConfigVerticalB,
    Faixas,
    Regiao,
    config_sem_arquivo,
)
from clipador.cortes import comandos, cortar
from clipador.cortes.legenda import linhas_do_clipe
from clipador.demo import gerar
from clipador.demo.roteiro import Pico

precisa_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg não instalado neste PC"
)
CORTES = ConfigCortes()


@pytest.fixture(autouse=True)
def sem_nvenc(monkeypatch):
    """Os testes cortam no processador: a CI não tem placa de vídeo, e a deste PC não tem
    NVENC com o FFmpeg 9 (ADR 0006)."""
    monkeypatch.setattr(comandos, "nvenc_funciona", lambda: False)


# ------------------------------------------------------------------ comandos (sem FFmpeg)


def test_audio_mistura_voz_e_jogo_sem_a_musica():
    filtro = comandos.filtro_de_audio(Faixas(), 4, CORTES)
    assert "[0:a:1]volume=1.0[voz]" in filtro  # faixa 2 do OBS
    assert "[0:a:2]volume=0.6[jogo]" in filtro  # faixa 3
    assert "0:a:3" not in filtro  # a faixa 4, da música, nunca entra
    assert "amix=inputs=2:duration=longest:normalize=0" in filtro
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in filtro  # -14 LUFS, o padrão
    assert filtro.endswith("[a]")


def test_gravacao_sem_a_faixa_do_jogo_sai_so_com_a_voz():
    filtro = comandos.filtro_de_audio(Faixas(), 2, CORTES)
    assert "jogo" not in filtro and "amix" not in filtro
    assert filtro.startswith("[0:a:1]volume=1.0[voz];[voz]loudnorm")


def test_faixas_e_volumes_vem_do_config():
    cfg = ConfigCortes(volume_voz=1.5, volume_jogo=0.3, lufs=-16)
    filtro = comandos.filtro_de_audio(Faixas(voz=3, jogo=2, musica=4), 4, cfg)
    assert "[0:a:2]volume=1.5[voz]" in filtro and "[0:a:1]volume=0.3[jogo]" in filtro
    assert "loudnorm=I=-16.0" in filtro


def test_vertical_a_tem_fundo_desfocado_e_video_no_centro():
    filtro = comandos.filtro_de_video("vertical_a")
    assert "boxblur" in filtro and "scale=1080:1920[fundo]" in filtro
    assert "[c]scale=1080:-2[frente]" in filtro
    assert "overlay=(W-w)/2:(H-h)/2[v]" in filtro
    assert "subtitles" not in filtro
    com_legenda = comandos.filtro_de_video("vertical_a", "legenda_7.srt")
    assert "subtitles=legenda_7.srt:force_style='" in com_legenda


def test_horizontal_mantem_o_video():
    assert comandos.filtro_de_video("horizontal") == "[0:v:0]null[v]"
    with pytest.raises(ValueError, match="Formato desconhecido"):
        comandos.filtro_de_video("quadrado")


def test_comando_do_clipe():
    argumentos = comandos.comando_do_clipe(
        Path("live.mkv"), 70.5, 21.0, "horizontal", Faixas(), 4, CORTES, "libx264", Path("c.mp4")
    )
    texto = " ".join(argumentos)
    # -ss antes do -i, com recodificação: corte preciso ao quadro.
    assert argumentos.index("-ss") < argumentos.index("-i")
    assert "-ss 70.500 -i live.mkv -t 21.000" in texto
    assert "-map [v] -map [a]" in texto
    assert "-c:v libx264 -preset medium -crf 21 -profile:v high -pix_fmt yuv420p" in texto
    assert "-c:a aac -b:a 160k -ar 48000 -movflags +faststart" in texto
    assert argumentos[-1] == "c.mp4"


def test_encoder_nvenc_usa_qualidade_constante():
    assert comandos.argumentos_de_video("h264_nvenc")[:8] == [
        "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"
    ]  # fmt: skip


def test_escolha_do_encoder(monkeypatch, caplog):
    assert comandos.escolher_encoder("libx264") == "libx264"
    assert comandos.escolher_encoder("auto") == "libx264"  # sem NVENC: o processador
    assert "NVENC indisponível" in caplog.text
    with pytest.raises(RuntimeError, match="ADR 0006"):
        comandos.escolher_encoder("h264_nvenc")
    monkeypatch.setattr(comandos, "nvenc_funciona", lambda: True)
    assert comandos.escolher_encoder("auto") == "h264_nvenc"


def test_thumbnail_pega_um_quadro_do_pico():
    argumentos = comandos.comando_da_thumbnail(Path("live.mkv"), 26.25, Path("t.jpg"))
    assert (
        " ".join(argumentos)
        == "-y -ss 26.250 -i live.mkv -frames:v 1 -vf scale=1280:-2 -q:v 2 t.jpg"
    )


# ------------------------------------------------------------------ legenda do clipe


def palavra(texto: str, inicio: float, fim: float) -> dict:
    return {"palavra": texto, "inicio_s": inicio, "fim_s": fim}


def test_legenda_comeca_do_zero_e_so_tem_o_trecho():
    palavras = [
        palavra("antes", 9.0, 9.5),
        palavra("NÃO", 25.0, 25.4),
        palavra("ACREDITO", 25.4, 26.0),
        palavra("depois", 40.0, 40.5),
    ]
    assert linhas_do_clipe(palavras, 20.0, 35.0) == [
        {"inicio_s": 5.0, "fim_s": 6.0, "texto": "NÃO ACREDITO"}
    ]


def test_legenda_quebra_em_linhas_curtas_e_nas_pausas():
    frase = "eu juro que apertei o flash agora"
    palavras = [palavra(p, 10 + i * 0.3, 10.3 + i * 0.3) for i, p in enumerate(frase.split())]
    palavras.append(palavra("caramba", 14.0, 14.5))  # depois de uma pausa
    linhas = linhas_do_clipe(palavras, 10.0, 25.0)
    assert [linha["texto"] for linha in linhas] == [
        "eu juro que apertei", "o flash agora", "caramba"
    ]  # fmt: skip
    assert all(len(linha["texto"]) <= 28 for linha in linhas)


def test_palavra_cortada_na_borda_fica_dentro_do_clipe():
    linhas = linhas_do_clipe([palavra("oi", 19.5, 20.5), palavra("tchau", 34.5, 35.5)], 20, 35)
    assert linhas[0]["inicio_s"] == 0.0 and linhas[-1]["fim_s"] == 15.0


# ------------------------------------------------------------------ vertical B (sem FFmpeg)

CAMERA = Regiao(x=1010, y=30, largura=240, altura=135)


def test_vertical_b_sem_camera_e_so_o_centro_do_jogo():
    filtro = comandos.filtro_de_video("vertical_b")
    # A largura do recorte vem da altura (proporção 9:16), centralizada e sem sair da tela.
    assert (
        "crop=w=min(trunc(ih*1080/1920/2)*2\\,iw):h=ih:x=clip(iw*0.5-ow/2\\,0\\,iw-ow):y=0"
        in filtro
    )
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in filtro
    assert "split" not in filtro and "vstack" not in filtro
    assert filtro.endswith("[v]")


def test_vertical_b_com_camera_em_cima_e_jogo_embaixo():
    vertical_b = ConfigVerticalB(camera=CAMERA, centro_x=0.4)
    filtro = comandos.filtro_de_video("vertical_b", vertical_b=vertical_b)
    assert "[0:v:0]split[cam][jogo]" in filtro
    # A câmera do config, ampliada até preencher 1080×640 (o terço de cima), sem distorcer.
    assert (
        "[cam]crop=240:135:1010:30,scale=1080:640:force_original_aspect_ratio=increase,"
        "crop=1080:640" in filtro
    )
    # O jogo nos 1280 de baixo, na proporção do espaço, centrado em 40% da largura.
    assert "crop=w=min(trunc(ih*1080/1280/2)*2\\,iw):h=ih:x=clip(iw*0.4-ow/2" in filtro
    assert "scale=1080:1280" in filtro
    assert "[c][j]vstack[v]" in filtro


def test_altura_da_camera_e_par_e_soma_1920():
    for fracao in (0.2, 0.25, 1 / 3, 0.37, 0.5):
        camera, jogo = comandos.alturas_do_vertical_b(
            ConfigVerticalB(camera=CAMERA, altura_camera=fracao)
        )
        assert camera % 2 == 0 and jogo % 2 == 0  # o H.264 em yuv420p exige
        assert camera + jogo == 1920
    assert comandos.alturas_do_vertical_b(ConfigVerticalB()) == (0, 1920)


def test_legenda_do_vertical_b():
    # Sem câmera: na altura de sempre (como no vertical A).
    sem = comandos.filtro_de_video("vertical_b", "l.srt")
    assert "Alignment=2,MarginV=75" in sem
    # Com câmera: logo abaixo dela, na emenda (no alto, centralizada: 6 na numeração do SSA).
    com = comandos.filtro_de_video("vertical_b", "l.srt", ConfigVerticalB(camera=CAMERA))
    assert "Alignment=6,MarginV=102" in com  # 640/1920 da tela de 288 = 96, mais 6
    assert com.index("vstack") < com.index("subtitles")  # a legenda vai depois de juntar


def test_validar_regiao_da_camera():
    comandos.validar_regiao(CAMERA, 1280, 720)  # cabe
    comandos.validar_regiao(Regiao(x=1040, y=585, largura=240, altura=135), 1280, 720)
    with pytest.raises(ValueError, match="vai até \\(1290, 165\\).*1280×720"):
        comandos.validar_regiao(Regiao(x=1050, y=30, largura=240, altura=135), 1280, 720)
    comandos.validar_regiao(Regiao(x=5000, y=0, largura=1, altura=1), None, None)  # sem medida


@pytest.mark.parametrize(
    ("centro_x", "x_esperado"),
    # Sem câmera, numa gravação 1280×720: 404 de largura (720 × 1080/1920 = 405, para baixo
    # no par). Perto da borda, o recorte encosta nela em vez de sair da tela.
    [(0.5, 640 - 202), (0.0, 0), (0.1, 0), (1.0, 1280 - 404), (0.95, 1280 - 404)],
)
def test_regiao_do_jogo(centro_x, x_esperado):
    jogo = comandos.regiao_do_jogo(1280, 720, ConfigVerticalB(centro_x=centro_x))
    assert (jogo.x, jogo.y, jogo.largura, jogo.altura) == (x_esperado, 0, 404, 720)


def test_comando_de_calibracao():
    argumentos = comandos.comando_de_calibracao(
        Path("live.mkv"), 30, 1280, 720, ConfigVerticalB(camera=CAMERA), Path("q.png")
    )
    filtros = argumentos[argumentos.index("-vf") + 1]
    assert "drawgrid=w=100:h=100" in filtros
    assert "drawbox=x=337:y=0:w=606:h=720:color=lime" in filtros  # o recorte do jogo
    assert "drawbox=x=1010:y=30:w=240:h=135:color=red" in filtros  # a câmera
    assert argumentos[argumentos.index("-frames:v") + 1] == "1"
    sem_camera = comandos.comando_de_calibracao(
        Path("live.mkv"), 30, 1280, 720, ConfigVerticalB(), Path("q.png")
    )
    assert "color=red" not in sem_camera[sem_camera.index("-vf") + 1]


def test_formatos_padrao_e_repetidos():
    assert ConfigCortes().formatos == ["horizontal", "vertical_a", "vertical_b"]
    with pytest.raises(ValueError, match="repetido"):
        ConfigCortes(formatos=["vertical_b", "vertical_b"])


def test_camera_no_config_toml(escrever_config, tmp_path):
    from clipador.config import carregar_config

    caminho = escrever_config(
        f'pasta_gravacoes = "{tmp_path.as_posix()}"\n'
        "[cortes.vertical_b]\n"
        "camera = { x = 1500, y = 760, largura = 400, altura = 300 }\n"
        "centro_x = 0.45\n"
    )
    vertical_b = carregar_config(caminho).cortes.vertical_b
    assert vertical_b.camera == Regiao(x=1500, y=760, largura=400, altura=300)
    assert vertical_b.centro_x == 0.45
    with pytest.raises(ValueError):
        ConfigVerticalB(camera=Regiao(x=0, y=0, largura=0, altura=10))


# ------------------------------------------------------------------ executor (com FFmpeg)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # Nos testes do executor importa o fluxo (retomar, pausar, recortar), não a qualidade
    # da imagem: o preset mais rápido do x264 deixa estes testes umas 2 vezes mais rápidos.
    original = comandos.argumentos_de_video
    monkeypatch.setattr(
        comandos,
        "argumentos_de_video",
        lambda encoder: ["ultrafast" if a == "medium" else a for a in original(encoder)],
    )
    # Os 2 formatos de antes: estes testes são do fluxo; o vertical B tem os testes dele.
    return config_sem_arquivo(
        pasta_gravacoes=tmp_path / "g",
        pasta_dados=tmp_path / "dados",
        cortes=ConfigCortes(formatos=["horizontal", "vertical_a"]),
    )


@pytest.fixture
def tarefa(cfg, sessao) -> Tarefa:
    """Uma gravação de 12 s (a receita da demo, com as 4 faixas) e dois momentos."""
    arquivo = cfg.pasta_gravacoes / "2026-10-14 20-00-00.mkv"
    gerar.gerar_gravacao(arquivo, 12, (Pico(3.0, 5.0),))
    gravacao = Gravacao(
        arquivo=str(arquivo), inicio=datetime(2026, 10, 14, 23, 0, tzinfo=UTC), duracao_s=12
    )
    sessao.add(gravacao)
    sessao.flush()
    sessao.add(Momento(gravacao_id=gravacao.id, inicio_s=1, fim_s=7, pico_s=4, nota=87.4))
    sessao.add(Momento(gravacao_id=gravacao.id, inicio_s=6, fim_s=11, nota=40))
    sessao.add(
        Transcricao(
            gravacao_id=gravacao.id, palavras=[palavra("CARAMBA", 3.0, 4.0)], concluida=True
        )
    )
    tarefa = Tarefa(tipo=TipoTarefa.CORTAR, gravacao_id=gravacao.id)
    sessao.add(tarefa)
    sessao.commit()
    return tarefa


def clipes(sessao) -> list[Clipe]:
    return list(sessao.exec(select(Clipe).order_by(Clipe.id)).all())


@precisa_ffmpeg
def test_corta_cada_momento_em_cada_formato(cfg, sessao, tarefa):
    cortar.cortar(sessao, tarefa, cfg)

    lista = clipes(sessao)
    assert [(c.momento_id, c.formato.value) for c in lista] == [
        (1, "horizontal"), (1, "vertical_a"), (2, "horizontal"), (2, "vertical_a")
    ]  # fmt: skip
    nomes = sorted(p.name for p in cfg.pasta_clipes.iterdir())
    assert "2026-10-14_087_1_horizontal.mp4" in nomes  # data_nota_id_formato
    assert "2026-10-14_087_1_thumb.jpg" in nomes
    assert not [n for n in nomes if ".parcial." in n]
    horizontal = ffmpeg.inspecionar(Path(lista[0].arquivo))
    vertical = ffmpeg.inspecionar(Path(lista[1].arquivo))
    assert (horizontal.largura, horizontal.altura) == (1280, 720)  # a resolução da gravação
    assert (vertical.largura, vertical.altura) == (1080, 1920)
    assert lista[0].duracao_s == pytest.approx(6, abs=0.1)
    assert horizontal.faixas_audio == 1  # voz + jogo misturados numa faixa só
    assert list((cfg.pasta_trabalho / "1").glob("*.srt")) == []  # a legenda temporária saiu


@precisa_ffmpeg
def test_cortar_de_novo_pula_o_que_ja_existe(cfg, sessao, tarefa, monkeypatch):
    cortar.cortar(sessao, tarefa, cfg)
    monkeypatch.setattr(ffmpeg, "rodar", lambda *a, **k: pytest.fail("cortou de novo"))
    cortar.cortar(sessao, tarefa, cfg)  # o PC caiu e a tarefa voltou: nada a refazer
    assert len(clipes(sessao)) == 4


@precisa_ffmpeg
def test_pausa_entre_clipes_se_comecar_uma_live(cfg, sessao, tarefa):
    respostas = iter([False, True])
    contexto = Contexto(deve_pausar=lambda: next(respostas, True))
    with pytest.raises(TarefaPausada):
        cortar.cortar(sessao, tarefa, cfg, contexto)
    assert len(clipes(sessao)) == 1  # o 1º ficou salvo


@precisa_ffmpeg
def test_recortar_usa_o_novo_inicio_e_fim(cfg, sessao, tarefa):
    cortar.cortar(sessao, tarefa, cfg)
    momento = sessao.get(Momento, 1)
    momento.inicio_s, momento.fim_s = 2.0, 10.0  # ajustado na revisão (etapa 12)
    sessao.add(momento)
    recorte = Tarefa(tipo=TipoTarefa.RECORTAR, momento_id=1)
    sessao.add(recorte)
    sessao.commit()

    cortar.recortar(sessao, recorte, cfg)

    duracoes = {c.formato.value: c.duracao_s for c in clipes(sessao) if c.momento_id == 1}
    assert duracoes == {
        "horizontal": pytest.approx(8, abs=0.1),
        "vertical_a": pytest.approx(8, abs=0.1),
    }
    assert len(clipes(sessao)) == 4  # substituiu, não duplicou
    assert not (cfg.pasta_trabalho / "1").exists()  # a legenda temporária não deixou pasta


@precisa_ffmpeg
def test_thumbnail_do_recorte_fica_dentro_do_clipe(cfg, sessao, tarefa, monkeypatch):
    momento = sessao.get(Momento, 1)  # o pico dele é aos 4 s
    momento.inicio_s, momento.fim_s = 6.0, 11.0  # encurtado na revisão: o pico ficou de fora
    sessao.add(momento)
    sessao.commit()
    quadros = []
    original = comandos.comando_da_thumbnail
    monkeypatch.setattr(
        comandos,
        "comando_da_thumbnail",
        lambda arquivo, instante, destino: (
            quadros.append(instante) or original(arquivo, instante, destino)
        ),
    )
    recorte = Tarefa(tipo=TipoTarefa.RECORTAR, momento_id=1)
    sessao.add(recorte)
    sessao.commit()
    cortar.recortar(sessao, recorte, cfg)
    assert quadros == [6.0]


def test_trecho_invalido_e_falha_definitiva_com_mensagem_clara(cfg, sessao, tarefa):
    momento = sessao.get(Momento, 1)
    momento.inicio_s, momento.fim_s = 8.0, 7.5  # fim antes do início
    sessao.add(momento)
    recorte = Tarefa(tipo=TipoTarefa.RECORTAR, momento_id=1)
    sessao.add(recorte)
    sessao.commit()
    with pytest.raises(FalhaDefinitivaError, match="pelo menos 1 s"):
        cortar.recortar(sessao, recorte, cfg)


def test_recortar_sem_momento_e_falha_definitiva(cfg, sessao):
    tarefa = Tarefa(tipo=TipoTarefa.RECORTAR)
    sessao.add(tarefa)
    sessao.commit()
    with pytest.raises(FalhaDefinitivaError, match="não aponta para um momento"):
        cortar.recortar(sessao, tarefa, cfg)


def test_nvenc_forcado_sem_placa_e_falha_definitiva(tmp_path, sessao, monkeypatch):
    cfg = config_sem_arquivo(
        pasta_gravacoes=tmp_path,
        pasta_dados=tmp_path / "d",
        cortes=ConfigCortes(encoder="h264_nvenc"),
    )
    arquivo = tmp_path / "live.mkv"
    arquivo.write_bytes(b"x")
    monkeypatch.setattr(
        ffmpeg, "inspecionar", lambda caminho: ffmpeg.InfoMidia(10, 4, 1280, 720, ())
    )
    gravacao = Gravacao(arquivo=str(arquivo), inicio=datetime.now(UTC))
    sessao.add(gravacao)
    sessao.commit()
    with pytest.raises(FalhaDefinitivaError, match="NVENC não funciona"):
        cortar.Cortador(sessao, cfg, gravacao)


def cor_no_quadro(arquivo: Path, x: int, y: int, segundo: float = 1) -> np.ndarray:
    """A cor (R, G, B) de um ponto do quadro, numa versão de 108×192 do vertical."""
    bruto = subprocess.run(
        [ffmpeg.localizar(), "-v", "error", "-ss", str(segundo), "-i", str(arquivo),
         "-frames:v", "1", "-vf", "scale=108:192", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    return np.frombuffer(bruto, np.uint8).reshape(192, 108, 3)[y, x].astype(int)


AZUL_DA_CAMERA = np.array([0x39, 0x87, 0xE5])  # gerar.COR_CAMERA
FUNDO_DA_DEMO = np.array([0x1E, 0x1E, 0x2E])  # gerar.COR_FUNDO


@precisa_ffmpeg
def test_vertical_b_com_a_camera_da_demo(cfg, sessao, tarefa):
    cfg.cortes = ConfigCortes(
        formatos=["vertical_b"], vertical_b=ConfigVerticalB(camera=gerar.CAMERA_DA_DEMO)
    )
    cortar.cortar(sessao, tarefa, cfg)
    clipe = clipes(sessao)[0]
    info = ffmpeg.inspecionar(Path(clipe.arquivo))
    assert (info.largura, info.altura) == (1080, 1920)
    # No terço de cima, a "câmera" azul da demo; embaixo, o fundo do jogo (fora do relógio).
    assert np.abs(cor_no_quadro(Path(clipe.arquivo), 10, 30) - AZUL_DA_CAMERA).max() < 30
    assert np.abs(cor_no_quadro(Path(clipe.arquivo), 10, 170) - FUNDO_DA_DEMO).max() < 30


@precisa_ffmpeg
def test_vertical_b_sem_camera_e_so_o_jogo(cfg, sessao, tarefa):
    cfg.cortes = ConfigCortes(formatos=["vertical_b"])
    cortar.cortar(sessao, tarefa, cfg)
    clipe = clipes(sessao)[0]
    info = ffmpeg.inspecionar(Path(clipe.arquivo))
    assert (info.largura, info.altura) == (1080, 1920)
    # O recorte do centro não pega o canto da câmera: em cima também é o fundo do jogo.
    assert np.abs(cor_no_quadro(Path(clipe.arquivo), 10, 30) - FUNDO_DA_DEMO).max() < 30


@precisa_ffmpeg
def test_vertical_b_de_gravacao_estreita(tmp_path):
    """Uma gravação mais estreita que o recorte (ex.: vertical, 320×480) ainda sai 1080×1920.

    (O som é um tom: silêncio digital puro vira NaN no loudnorm. Na vida real isso não chega
    aqui: uma voz muda já é recusada na extração do áudio, etapa 5.)
    """
    gravacao = tmp_path / "estreita.mkv"
    ffmpeg.rodar(
        ["-f", "lavfi", "-i", "color=c=red:s=320x480:d=1", "-f", "lavfi",
         "-i", "sine=frequency=220:sample_rate=48000", "-t", "1", "-c:v", "libx264",
         "-preset", "ultrafast", "-c:a", "libopus", str(gravacao)],
        tempo_limite_s=60,
    )  # fmt: skip
    cortes = ConfigCortes(
        vertical_b=ConfigVerticalB(camera=Regiao(x=0, y=0, largura=100, altura=100))
    )
    destino = tmp_path / "b.mp4"
    ffmpeg.rodar(
        comandos.comando_do_clipe(
            gravacao, 0, 1, "vertical_b", Faixas(voz=1, jogo=2, musica=3), 1, cortes,
            "libx264", destino,
        ),
        tempo_limite_s=60,
    )  # fmt: skip
    info = ffmpeg.inspecionar(destino)
    assert (info.largura, info.altura) == (1080, 1920)


@precisa_ffmpeg
def test_camera_fora_da_tela_e_falha_definitiva(cfg, sessao, tarefa):
    cfg.cortes = ConfigCortes(
        formatos=["vertical_b"],
        vertical_b=ConfigVerticalB(camera=Regiao(x=1200, y=0, largura=240, altura=135)),
    )
    with pytest.raises(FalhaDefinitivaError, match="clipador calibrar"):
        cortar.cortar(sessao, tarefa, cfg)
    # Sem o vertical B nos formatos, uma câmera errada no config não atrapalha os outros.
    cfg.cortes = ConfigCortes(
        formatos=["horizontal"],
        vertical_b=ConfigVerticalB(camera=Regiao(x=1200, y=0, largura=240, altura=135)),
    )
    cortar.cortar(sessao, tarefa, cfg)
    assert [c.formato.value for c in clipes(sessao)] == ["horizontal", "horizontal"]


@precisa_ffmpeg
def test_clipador_calibrar(tmp_path, escrever_config, sessao, tarefa, cfg):
    from typer.testing import CliRunner

    from clipador.cli import app

    gravacao = sessao.get(Gravacao, tarefa.gravacao_id)
    gravacao.duracao_s = 12
    sessao.add(gravacao)
    sessao.commit()
    banco = Path(str(sessao.get_bind().url.database))
    config = escrever_config(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{banco.parent.as_posix()}"\n'
        "[cortes.vertical_b]\n"
        "camera = { x = 1010, y = 30, largura = 240, altura = 135 }\n"
    )
    resultado = CliRunner().invoke(app, ["calibrar", "--arquivo", str(config)])
    assert resultado.exit_code == 0, resultado.output
    quadro = banco.parent / "calibracao.png"
    info = ffmpeg.inspecionar(quadro)
    assert (info.largura, info.altura) == (1280, 720)
    assert "1280×720" in resultado.output
    assert "Vermelho: a câmera do config (240×135 em 1010, 30)" in resultado.output
    # Um segundo fora da gravação explica, em vez de gerar um quadro preto.
    resultado = CliRunner().invoke(app, ["calibrar", "--arquivo", str(config), "-s", "99"])
    assert resultado.exit_code == 1
    assert "entre 0 e isso" in resultado.output


def test_servico_tem_os_executores_de_corte():
    from clipador.sistema.servico import EXECUTORES

    assert EXECUTORES[TipoTarefa.CORTAR] is cortar.cortar
    assert EXECUTORES[TipoTarefa.RECORTAR] is cortar.recortar


# ------------------------------------------------------------------ a demo de verdade


def energia(arquivo: Path, frequencia: float) -> float:
    bruto = subprocess.run(
        [ffmpeg.localizar(), "-v", "error", "-i", str(arquivo), "-ac", "1", "-ar", "8000",
         "-f", "s16le", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    amostras = np.frombuffer(bruto, np.int16).astype(float)
    espectro = np.abs(np.fft.rfft(amostras))
    frequencias = np.fft.rfftfreq(len(amostras), 1 / 8000)
    return float(espectro[np.abs(frequencias - frequencia) < 5].max())


@pytest.mark.lento
@precisa_ffmpeg
def test_clipes_da_demo(tmp_path, monkeypatch):
    # Teste do roteiro: duração e resolução certas, sem a música, MP4 válido e a legenda
    # começando do zero e batendo com a fala. Nada disso depende da qualidade da imagem: o
    # preset mais rápido do x264 leva o teste de 81 s para bem menos (são 12 clipes).
    original = comandos.argumentos_de_video
    monkeypatch.setattr(
        comandos,
        "argumentos_de_video",
        lambda encoder: ["ultrafast" if a == "medium" else a for a in original(encoder)],
    )
    from typer.testing import CliRunner

    from clipador.banco.conexao import abrir_banco, nova_sessao
    from clipador.cli import app
    from clipador.demo import roteiro

    runner = CliRunner()
    sem_config = str(tmp_path / "config.toml")
    runner.invoke(app, ["demo", "--arquivo", sem_config])
    assert runner.invoke(app, ["processar", "--demo", "--arquivo", sem_config]).exit_code == 0

    pasta = gerar.pasta_da_demo(tmp_path / "dados")
    engine = abrir_banco(pasta / "clipador.db")
    with nova_sessao(engine) as sessao:
        momentos = {m.id: m for m in sessao.exec(select(Momento)).all()}
        lista = sessao.exec(select(Clipe)).all()
        palavras = sessao.exec(select(Transcricao)).one().palavras
    engine.dispose()

    assert len(lista) == 3 * len(roteiro.PICOS_VOZ)  # horizontal, vertical A e B
    for clipe in lista:
        momento = momentos[clipe.momento_id]
        info = ffmpeg.inspecionar(Path(clipe.arquivo))  # abre: é um MP4 válido
        assert info.duracao_s == pytest.approx(momento.fim_s - momento.inicio_s, abs=0.1)
        esperado = (1280, 720) if clipe.formato.value == "horizontal" else (1080, 1920)
        assert (info.largura, info.altura) == esperado
        # Na demo, o jogo é um tom de 220 Hz e a música, um de 440 Hz.
        assert energia(Path(clipe.arquivo), 220) > 20 * energia(Path(clipe.arquivo), 440)
        primeira = linhas_do_clipe(palavras, momento.inicio_s, momento.fim_s)[0]
        fala = next(p for p in palavras if p["fim_s"] > momento.inicio_s)
        assert primeira["inicio_s"] == pytest.approx(max(fala["inicio_s"] - momento.inicio_s, 0))
