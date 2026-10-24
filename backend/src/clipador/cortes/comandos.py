"""Monta os comandos do FFmpeg que cortam um momento da gravação (MKV do OBS) em clipe.

Montar o comando é separado de rodar: assim os testes conferem o comando sem precisar do
FFmpeg (e a CI roda rápido). Tudo sai em **MP4** (H.264 + AAC, ``yuv420p``, com o índice
no começo do arquivo, ``+faststart``), o formato que YouTube, TikTok e celulares aceitam.

**Áudio.** Só a voz e o jogo, misturados com o volume de cada um no config; a faixa da
música nunca entra (direitos autorais). Depois, ``loudnorm`` leva o volume final a -14 LUFS,
o padrão de YouTube e TikTok (em uma passada só: fica perto do alvo, não exato). Uma
gravação sem a faixa do jogo sai só com a voz.

**Vídeo.** O corte é recodificado, e por isso preciso ao quadro (``-ss`` antes do ``-i``
com recodificação: o FFmpeg pula até perto do ponto e decodifica o resto até o quadro certo).

- ``horizontal``: 16:9, na resolução da gravação.
- ``vertical_a``: 1080×1920. O vídeo inteiro no centro, e o mesmo vídeo ampliado e
  desfocado preenchendo o fundo. O desfoque é feito numa versão pequena (270×480) e depois
  ampliado: fica igual e é bem mais rápido que desfocar 1080×1920.
- ``vertical_b`` (ADR 0008): 1080×1920, aproximado. Com a câmera configurada
  (``[cortes.vertical_b] camera``), ela ocupa o terço de cima e o centro do jogo os dois
  terços de baixo; sem câmera, o centro do jogo ocupa a tela toda. O recorte do jogo tem a
  proporção do espaço que vai ocupar (sem distorcer) e a altura inteira da gravação, e é
  escrito com as medidas do próprio vídeo (``iw``, ``ih``): serve para qualquer resolução.
  A câmera é recortada e ampliada até preencher o espaço dela (sobra cortada nas bordas).
  Com a câmera, a legenda fica na emenda entre ela e o jogo, deixando o meio do jogo livre.

**Legenda** (só no vertical): o arquivo ``.srt`` do clipe é passado pelo nome, com o FFmpeg
rodando na pasta dele, porque dentro de um filtro os ":" e as barras invertidas de um
caminho do Windows precisariam de escapes.

**Encoder** (ADR 0006): o NVENC (a placa de vídeo) é testado uma vez; se não funcionar,
vai o libx264 (o processador). Com a GTX 1050 Ti no driver 580 e o FFmpeg 9, o NVENC não
funciona: o FFmpeg 9 exige a API 13.1 do NVENC, que só vem no driver 610, e a série 580 é
a última que a placa recebe.
"""

import functools
import logging
from pathlib import Path

from clipador import ffmpeg
from clipador.config import ConfigCortes, ConfigVerticalB, Faixas, Regiao

log = logging.getLogger(__name__)

LARGURA_VERTICAL, ALTURA_VERTICAL = 1080, 1920
# Estilo da legenda queimada, em unidades do libass (no SRT, a tela tem 288 de altura):
# texto branco com contorno preto, centralizado embaixo (Alignment=2), a 75/288 = 26% do
# pé da tela. Mais baixo que isso, os botões e a descrição do TikTok e dos Shorts cobrem
# a legenda; assim ela fica logo abaixo do vídeo, que ocupa o meio da tela.
ESTILO_LEGENDA = (
    "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=75"
)
ALTURA_DA_TELA_DA_LEGENDA = 288  # a tela do SRT para o libass


def estilo_na_emenda(altura_camera: int) -> str:
    """Legenda logo abaixo da câmera, na emenda com o jogo (vertical B com câmera).

    O centro do jogo, onde está a ação, fica livre. Alignment=6 é "no alto, centralizada"
    na numeração antiga do SSA, que é a que o FFmpeg usa com legenda .srt (na do ASS, a do
    teclado numérico, seria 8; testado: o 8 põe a legenda no meio, à esquerda). A
    ``MarginV`` passa a contar do topo da tela.
    """
    margem = round(altura_camera / ALTURA_VERTICAL * ALTURA_DA_TELA_DA_LEGENDA) + 6
    return ESTILO_LEGENDA.replace("Alignment=2,MarginV=75", f"Alignment=6,MarginV={margem}")


@functools.cache
def nvenc_funciona() -> bool:
    """O FFmpeg consegue codificar com a placa de vídeo? (Testado uma vez por processo.)"""
    try:
        ffmpeg.rodar(
            ["-f", "lavfi", "-i", "color=s=256x256:d=0.1", "-c:v", "h264_nvenc",
             "-f", "null", "-"],
            tempo_limite_s=30,
        )  # fmt: skip
    except (ffmpeg.FFmpegFalhouError, ffmpeg.FFmpegNaoEncontradoError):
        return False
    return True


def escolher_encoder(preferido: str) -> str:
    """O encoder a usar: ``h264_nvenc`` ou ``libx264`` (ver o topo do arquivo)."""
    if preferido == "libx264":
        return "libx264"
    if nvenc_funciona():
        return "h264_nvenc"
    if preferido == "h264_nvenc":
        raise RuntimeError(
            '[cortes] encoder = "h264_nvenc", mas o NVENC não funciona neste PC (com a GTX '
            "1050 Ti, o FFmpeg 9 precisa de um driver que a placa não recebe; ver ADR 0006). "
            'Use encoder = "auto" para cortar no processador.'
        )
    log.warning(
        "NVENC indisponível (placa de vídeo): cortando no processador (libx264), mais devagar."
    )
    return "libx264"


def argumentos_de_video(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        # p5 = equilíbrio entre qualidade e velocidade; -cq = qualidade constante.
        qualidade = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"]
    else:
        # crf 21: perto do "visualmente sem perda" para clipe de rede social.
        qualidade = ["-c:v", "libx264", "-preset", "medium", "-crf", "21"]
    return [*qualidade, "-profile:v", "high", "-pix_fmt", "yuv420p"]


def filtro_de_audio(faixas: Faixas, faixas_na_gravacao: int, cfg: ConfigCortes) -> str:
    """Voz + jogo (sem a música), normalizado para ``cfg.lufs``. Sai como ``[a]``."""
    voz = f"[0:a:{faixas.voz - 1}]volume={cfg.volume_voz}[voz]"
    normalizar = f"loudnorm=I={cfg.lufs}:TP=-1.5:LRA=11,aresample=48000[a]"
    if faixas_na_gravacao >= faixas.jogo:
        jogo = f"[0:a:{faixas.jogo - 1}]volume={cfg.volume_jogo}[jogo]"
        # normalize=0: o amix não divide o volume pelo número de entradas.
        mistura = f"[voz][jogo]amix=inputs=2:duration=longest:normalize=0,{normalizar}"
        return f"{voz};{jogo};{mistura}"
    return f"{voz};[voz]{normalizar}"


def _par(n: float) -> int:
    """O inteiro par mais próximo (o H.264 em yuv420p exige largura e altura pares)."""
    return max(2, round(n / 2) * 2)


def alturas_do_vertical_b(cfg: ConfigVerticalB) -> tuple[int, int]:
    """(altura da câmera, altura do jogo) no clipe de 1920. Sem câmera, (0, 1920)."""
    if cfg.camera is None:
        return 0, ALTURA_VERTICAL
    camera = _par(ALTURA_VERTICAL * cfg.altura_camera)
    return camera, ALTURA_VERTICAL - camera


def recorte_do_jogo(altura_saida: int, centro_x: float) -> str:
    """O ``crop`` do centro do jogo, na proporção 1080 × ``altura_saida``, altura inteira.

    A largura vem da altura da gravação (``ih``), e o recorte nunca sai da tela: perto da
    borda, ele encosta nela em vez de passar (``clip``).
    """
    largura = f"trunc(ih*{LARGURA_VERTICAL}/{altura_saida}/2)*2"
    x = f"clip(iw*{centro_x:g}-ow/2\\,0\\,iw-ow)"
    return f"crop=w={largura}:h=ih:x={x}:y=0"


def validar_regiao(regiao: Regiao, largura: int | None, altura: int | None) -> None:
    """A região da câmera cabe na tela da gravação? (``ValueError`` com a explicação.)"""
    if largura is None or altura is None:
        return  # o ffprobe não disse: o próprio FFmpeg reclama se não couber
    if regiao.x + regiao.largura > largura or regiao.y + regiao.altura > altura:
        raise ValueError(
            f"[cortes.vertical_b] camera vai até ({regiao.x + regiao.largura}, "
            f"{regiao.y + regiao.altura}), mas a gravação tem {largura}×{altura}. "
            "Use 'clipador calibrar' para achar a posição certa da câmera."
        )


def _filtro_vertical_b(cfg: ConfigVerticalB) -> str:
    altura_camera, altura_jogo = alturas_do_vertical_b(cfg)
    jogo = (
        f"{recorte_do_jogo(altura_jogo, cfg.centro_x)},"
        f"scale={LARGURA_VERTICAL}:{altura_jogo},setsar=1"
    )
    if cfg.camera is None:
        return f"[0:v:0]{jogo}"
    c = cfg.camera
    camera = (
        f"crop={c.largura}:{c.altura}:{c.x}:{c.y},"
        f"scale={LARGURA_VERTICAL}:{altura_camera}:force_original_aspect_ratio=increase,"
        f"crop={LARGURA_VERTICAL}:{altura_camera},setsar=1"
    )
    return f"[0:v:0]split[cam][jogo];[cam]{camera}[c];[jogo]{jogo}[j];[c][j]vstack"


def filtro_de_video(
    formato: str, legenda: str | None = None, vertical_b: ConfigVerticalB | None = None
) -> str:
    """O vídeo do formato pedido, com a legenda queimada se houver. Sai como ``[v]``."""
    estilo = f",subtitles={legenda}:force_style='{ESTILO_LEGENDA}'" if legenda else ""
    if formato == "horizontal":
        return "[0:v:0]null[v]"
    if formato == "vertical_b":
        vertical_b = vertical_b or ConfigVerticalB()
        altura_camera, _ = alturas_do_vertical_b(vertical_b)
        if legenda and altura_camera:
            # Com a câmera, a legenda vai para a emenda (o meio do jogo é a ação); sem ela,
            # fica na altura de sempre, como no vertical A.
            estilo = f",subtitles={legenda}:force_style='{estilo_na_emenda(altura_camera)}'"
        return f"{_filtro_vertical_b(vertical_b)}{estilo}[v]"
    if formato == "vertical_a":
        largura, altura = LARGURA_VERTICAL, ALTURA_VERTICAL
        fundo = (
            f"[f]scale={largura // 4}:{altura // 4}:force_original_aspect_ratio=increase,"
            f"crop={largura // 4}:{altura // 4},boxblur=10:2,scale={largura}:{altura}[fundo]"
        )
        frente = f"[c]scale={largura}:-2[frente]"
        juntar = f"[fundo][frente]overlay=(W-w)/2:(H-h)/2{estilo}"
        return f"[0:v:0]split[f][c];{fundo};{frente};{juntar}[v]"
    raise ValueError(f"Formato desconhecido: {formato}")


def comando_do_clipe(
    gravacao: Path,
    inicio_s: float,
    duracao_s: float,
    formato: str,
    faixas: Faixas,
    faixas_na_gravacao: int,
    cfg: ConfigCortes,
    encoder: str,
    destino: Path,
    legenda: str | None = None,
) -> list[str]:
    """Argumentos do FFmpeg para cortar um clipe (sem o nome do programa)."""
    video = filtro_de_video(formato, legenda, cfg.vertical_b)
    filtros = f"{video};{filtro_de_audio(faixas, faixas_na_gravacao, cfg)}"
    return [
        "-y",
        "-ss", f"{inicio_s:.3f}",
        "-i", str(gravacao),
        "-t", f"{duracao_s:.3f}",
        "-filter_complex", filtros,
        "-map", "[v]",
        "-map", "[a]",
        *argumentos_de_video(encoder),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        str(destino),
    ]  # fmt: skip


def comando_da_thumbnail(gravacao: Path, instante_s: float, destino: Path) -> list[str]:
    """Um quadro do instante pedido, em JPG de 1280 de largura (o tamanho que o YouTube pede)."""
    return [
        "-y",
        "-ss", f"{instante_s:.3f}",
        "-i", str(gravacao),
        "-frames:v", "1",
        "-vf", "scale=1280:-2",
        "-q:v", "2",
        str(destino),
    ]  # fmt: skip


def tempo_limite_s(duracao_s: float) -> float:
    """No processador, um clipe leva perto do tempo real; 2 min + 20 vezes só estoura se travar."""
    return 120 + duracao_s * 20


# ------------------------------------------------------------------ calibração (vertical B)

FONTE = Path("C:/Windows/Fonts/arial.ttf")
PASSO_DA_GRADE = 100  # pixels entre as linhas da grade
PASSO_DOS_NUMEROS = 200  # pixels entre os números escritos nas bordas


def regiao_do_jogo(largura: int, altura: int, cfg: ConfigVerticalB) -> Regiao:
    """Que parte da gravação o vertical B usa como jogo (a mesma conta do ``crop``)."""
    _, altura_jogo = alturas_do_vertical_b(cfg)
    largura_jogo = min(int(altura * LARGURA_VERTICAL / altura_jogo / 2) * 2, largura)
    x = min(max(largura * cfg.centro_x - largura_jogo / 2, 0), largura - largura_jogo)
    return Regiao(x=int(x), y=0, largura=largura_jogo, altura=altura)


def comando_de_calibracao(
    gravacao: Path,
    instante_s: float,
    largura: int,
    altura: int,
    cfg: ConfigVerticalB,
    destino: Path,
) -> list[str]:
    """Um quadro da live com uma grade e as regiões do vertical B, para achar os números.

    Grade a cada 100 px, com os números a cada 200 px nas bordas (é só ler onde a câmera
    começa e termina). Verde: o recorte do jogo. Vermelho: a câmera do config, se houver.
    """
    filtros = [f"drawgrid=w={PASSO_DA_GRADE}:h={PASSO_DA_GRADE}:t=1:c=white@0.35"]
    jogo = regiao_do_jogo(largura, altura, cfg)
    filtros.append(f"drawbox=x={jogo.x}:y={jogo.y}:w={jogo.largura}:h={jogo.altura}:color=lime:t=4")
    if cfg.camera is not None:
        c = cfg.camera
        filtros.append(f"drawbox=x={c.x}:y={c.y}:w={c.largura}:h={c.altura}:color=red:t=4")
    if FONTE.is_file():
        fonte = FONTE.as_posix().replace(":", r"\:")
        texto = f"drawtext=fontfile='{fonte}':fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6"
        filtros += [f"{texto}:text='{x}':x={x}+4:y=4" for x in range(0, largura, PASSO_DOS_NUMEROS)]
        filtros += [
            f"{texto}:text='{y}':x=4:y={y}+4"
            for y in range(PASSO_DOS_NUMEROS, altura, PASSO_DOS_NUMEROS)
        ]
    return [
        "-y",
        "-ss", f"{instante_s:.3f}",
        "-i", str(gravacao),
        "-frames:v", "1",
        "-vf", ",".join(filtros),
        str(destino),
    ]  # fmt: skip
