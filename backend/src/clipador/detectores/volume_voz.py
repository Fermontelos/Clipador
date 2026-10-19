"""Detector de picos de volume da voz: o sinal principal (gritos, risadas altas).

Como funciona:

1. O WAV da voz (etapa 5) é medido em **janelas de 0,5 s**: o volume de cada uma em dBFS
   (decibéis em relação ao máximo do arquivo; 0 dBFS é o mais alto possível, e a fala
   costuma ficar entre -35 e -15).
2. O **seu volume normal** em cada instante é a mediana das janelas com voz dos **60 s
   anteriores**. Com a mediana, alguns gritos no meio não puxam o "normal" para cima, e um
   aumento lento (você se empolgando ao longo de minutos) acompanha o normal e não conta.
3. **Pico** = ficar pelo menos ``db_acima_do_normal`` (10 dB) acima do normal por pelo
   menos ``duracao_minima_s`` (1 s). 10 dB a mais é cerca de 3 vezes mais "pressão" sonora,
   o que o ouvido percebe como uns 2 vezes mais alto.

Dois cuidados para o "normal" não sair baixo demais:

- janelas abaixo de ``SILENCIO_DB`` (-60 dBFS: o noise gate do OBS ou silêncio real) não
  são voz e não entram na mediana;
- depois de um tempo calado (jogando concentrado), sobram poucas janelas com voz no último
  minuto, e uma fala normal pareceria um grito. Por isso o normal nunca fica mais de
  ``MARGEM_DO_NORMAL_DB`` abaixo do seu volume típico na live inteira.

A força do sinal: exatamente no limiar (10 dB acima) vale 0,5; o dobro (20 dB) ou mais
vale 1.
"""

import numpy as np
import soundfile

from clipador.config import ConfigVolumeVoz
from clipador.detectores.base import Entrada, Sinal

JANELA_S = 0.5
MINUTO_EM_JANELAS = 120  # 60 s / 0,5 s
SILENCIO_DB = -60.0
MARGEM_DO_NORMAL_DB = 6.0
MIN_JANELAS_COM_VOZ = 10  # menos que isto no último minuto: usa o volume típico da live


def volumes_db(wav, bloco_janelas: int = 1200) -> np.ndarray:
    """Volume (dBFS) de cada janela de 0,5 s, lido aos poucos (10 min, ~40 MB, de cada vez)."""
    info = soundfile.info(str(wav))
    tamanho = int(info.samplerate * JANELA_S)
    volumes = []
    for bloco in soundfile.blocks(str(wav), blocksize=tamanho * bloco_janelas, dtype="float32"):
        if bloco.ndim > 1:
            bloco = bloco.mean(axis=1)
        inteiras = len(bloco) // tamanho
        if inteiras == 0:
            continue
        janelas = bloco[: inteiras * tamanho].reshape(inteiras, tamanho).astype(np.float64)
        rms = np.sqrt((janelas**2).mean(axis=1))
        volumes.append(20 * np.log10(np.maximum(rms, 1e-10)))
    return np.concatenate(volumes) if volumes else np.array([])


def volume_normal(volumes: np.ndarray) -> np.ndarray:
    """O seu volume normal em cada janela (ver a explicação no topo do arquivo)."""
    com_voz = volumes > SILENCIO_DB
    if not com_voz.any():
        return np.full_like(volumes, np.inf)  # só silêncio: nada vira pico
    tipico = float(np.median(volumes[com_voz]))
    piso = tipico - MARGEM_DO_NORMAL_DB
    normal = np.empty_like(volumes)
    for i in range(len(volumes)):
        inicio = max(0, i - MINUTO_EM_JANELAS)
        anteriores = volumes[inicio:i][com_voz[inicio:i]]
        local = float(np.median(anteriores)) if len(anteriores) >= MIN_JANELAS_COM_VOZ else tipico
        normal[i] = max(local, piso)
    return normal


def picos(volumes: np.ndarray, cfg: ConfigVolumeVoz) -> list[tuple[int, int, float]]:
    """Trechos de pico: (primeira janela, janela depois da última, maior excesso em dB)."""
    excesso = volumes - volume_normal(volumes)
    alto = (excesso >= cfg.db_acima_do_normal) & (volumes > SILENCIO_DB)
    minimo = max(1, round(cfg.duracao_minima_s / JANELA_S))
    trechos = []
    bordas = np.flatnonzero(np.diff(np.concatenate(([0], alto.astype(np.int8), [0]))))
    for inicio, fim in zip(bordas[::2], bordas[1::2], strict=True):
        if fim - inicio >= minimo:
            trechos.append((int(inicio), int(fim), float(excesso[inicio:fim].max())))
    return trechos


class VolumeVoz:
    nome = "volume_voz"

    def __init__(self, cfg: ConfigVolumeVoz) -> None:
        self.cfg = cfg

    def disponivel(self, entrada: Entrada) -> bool:
        return entrada.voz_wav is not None and entrada.voz_wav.is_file()

    def detectar(self, entrada: Entrada) -> list[Sinal]:
        volumes = volumes_db(entrada.voz_wav)
        limiar = self.cfg.db_acima_do_normal
        return [
            Sinal(
                detector=self.nome,
                inicio_s=inicio * JANELA_S,
                fim_s=fim * JANELA_S,
                forca=min(1.0, excesso / (2 * limiar)),
                motivo=f"voz alta (+{excesso:.0f} dB)",
            )
            for inicio, fim, excesso in picos(volumes, self.cfg)
        ]
