"""Etapa 8: detectores e nota (testes obrigatórios do roteiro).

O volume é testado com WAVs sintéticos: uma "voz" de ruído cujo volume varia um pouco a
cada meio segundo (como a fala de verdade), com gritos, silêncios e rampas em lugares
conhecidos.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile
from sqlmodel import select
from typer.testing import CliRunner

from clipador.banco.modelos import (
    Decisao,
    EventoJogo,
    Gravacao,
    Momento,
    PapelNoEvento,
    PesosNota,
    Tarefa,
    TipoTarefa,
    Transcricao,
    agora,
)
from clipador.config import (
    ConfigDeteccao,
    ConfigFala,
    ConfigLolDetector,
    ConfigVolumeVoz,
    config_sem_arquivo,
)
from clipador.detectores import detectar, nota
from clipador.detectores.base import Entrada, Sinal
from clipador.detectores.fala import Fala, achar_expressoes, normalizar
from clipador.detectores.lol import Lol
from clipador.detectores.volume_voz import VolumeVoz
from clipador.ingestao.audio import caminho_da_voz

TAXA = 16_000
gerador = np.random.default_rng(42)


# ------------------------------------------------------------------ ajudantes


def wav_de_voz(destino: Path, volumes_db: list[float] | np.ndarray) -> Path:
    """Um WAV com um nível (dBFS) por janela de 0,5 s; -inf = silêncio digital."""
    partes = []
    for nivel in volumes_db:
        amplitude = 0.0 if nivel == -np.inf else 10 ** (nivel / 20) * np.sqrt(3)
        partes.append(gerador.uniform(-1, 1, TAXA // 2) * amplitude)
    destino.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(destino), np.concatenate(partes).astype(np.float32), TAXA)
    return destino


def fala_normal(segundos: float, nivel: float = -30.0) -> np.ndarray:
    """O seu volume normal, variando ±2 dB a cada meio segundo, como a fala de verdade."""
    return nivel + gerador.uniform(-2, 2, int(segundos * 2))


def entrada(wav=None, palavras=None, eventos=None, duracao_s=180.0) -> Entrada:
    return Entrada(
        gravacao=None,
        duracao_s=duracao_s,
        voz_wav=wav,
        palavras=palavras or [],
        eventos=eventos or [],
    )


def picos(wav: Path, **cfg) -> list[tuple[float, float]]:
    return [(s.inicio_s, s.fim_s) for s in VolumeVoz(ConfigVolumeVoz(**cfg)).detectar(entrada(wav))]


def palavras_de(*falas: tuple[float, float, str]) -> list[dict]:
    """Falas (início, fim, texto) quebradas em palavras com tempos iguais."""
    palavras = []
    for inicio, fim, texto in falas:
        partes = texto.split()
        passo = (fim - inicio) / len(partes)
        for i, parte in enumerate(partes):
            palavras.append(
                {
                    "palavra": parte,
                    "inicio_s": inicio + i * passo,
                    "fim_s": inicio + (i + 1) * passo,
                }
            )
    return palavras


def evento_lol(tipo: str, papel=None, **dados) -> EventoJogo:
    momento = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)
    return EventoJogo(
        tipo=tipo, horario_real=momento, tempo_jogo_s=0, partida_inicio=momento, event_id=1,
        papel=papel, dados=dados,
    )  # fmt: skip


# ------------------------------------------------------------------ volume da voz


def test_volume_acha_o_grito_e_ignora_a_variacao_normal(tmp_path):
    volumes = np.concatenate([fala_normal(60), [-16.0] * 4, fala_normal(60)])  # +14 dB por 2 s
    assert picos(wav_de_voz(tmp_path / "v.wav", volumes)) == [(60.0, 62.0)]


def test_pico_curto_demais_nao_conta(tmp_path):
    volumes = np.concatenate([fala_normal(60), [-14.0], fala_normal(60)])  # só 0,5 s
    assert picos(wav_de_voz(tmp_path / "v.wav", volumes)) == []
    assert picos(wav_de_voz(tmp_path / "v.wav", volumes), duracao_minima_s=0.5) == [(60.0, 60.5)]


def test_aumento_lento_de_volume_nao_e_pico(tmp_path):
    # +20 dB ao longo de 10 min (a empolgação crescendo): o "normal" acompanha.
    rampa = np.linspace(-40, -20, 1200) + gerador.uniform(-2, 2, 1200)
    assert picos(wav_de_voz(tmp_path / "v.wav", rampa)) == []


def test_voltar_a_falar_depois_de_um_silencio_nao_e_pico(tmp_path):
    # 60 s falando, 90 s calado (noise gate: silêncio digital), e volta a falar normal.
    volumes = np.concatenate([fala_normal(60), [-np.inf] * 180, fala_normal(60)])
    assert picos(wav_de_voz(tmp_path / "v.wav", volumes)) == []


def test_grito_logo_depois_de_um_silencio_conta(tmp_path):
    volumes = np.concatenate([fala_normal(60), [-np.inf] * 180, [-15.0] * 4, fala_normal(30)])
    assert picos(wav_de_voz(tmp_path / "v.wav", volumes)) == [(150.0, 152.0)]


def test_forca_do_volume_cresce_com_o_excesso(tmp_path):
    def forca(nivel):
        volumes = np.concatenate([np.full(120, -30.0), [nivel] * 4, np.full(20, -30.0)])
        return (
            VolumeVoz(ConfigVolumeVoz())
            .detectar(entrada(wav_de_voz(tmp_path / "v.wav", volumes)))[0]
            .forca
        )

    assert forca(-19.5) == pytest.approx(0.5, abs=0.05)  # ~10 dB acima: no limiar
    assert forca(-15.0) == pytest.approx(0.75, abs=0.05)
    assert forca(-5.0) == 1.0  # 25 dB acima: o máximo


def test_so_silencio_nao_da_pico(tmp_path):
    assert picos(wav_de_voz(tmp_path / "v.wav", [-np.inf] * 100)) == []


# ------------------------------------------------------------------ fala


def test_normalizar_ignora_acento_maiuscula_e_pontuacao():
    assert normalizar("NÃO, acredito!!") == "nao  acredito"


def test_acha_expressoes_de_varias_palavras_em_sequencia():
    palavras = palavras_de((10, 12, "Meu Deus do céu"), (40, 42, "meu irmão Deus"))
    achados = achar_expressoes(palavras, ["meu deus", "caramba"])
    assert [(round(a), e) for a, _, e in achados] == [(10, "meu deus")]  # a 2ª não é seguida


def test_expressoes_proximas_valem_mais():
    fala = Fala(ConfigFala(expressoes=["meu deus", "não acredito"]))
    sinais = fala.detectar(entrada(palavras=palavras_de(
        (10, 11, "meu deus"), (14, 15, "NÃO ACREDITO"), (100, 101, "meu deus"),
    )))  # fmt: skip
    forcas = sorted((s.inicio_s, s.forca) for s in sinais if "“" in s.motivo)
    assert forcas == [(10, 1.0), (14, 1.0), (100, 0.8)]


def test_fala_acelerada_acima_da_sua_media():
    calmo = [(t, t + 5, "uma duas tres quatro cinco") for t in range(0, 300, 5)]  # 1 palavra/s
    rapido = (300, 305, " ".join(["palavra"] * 15))  # 3 palavras/s
    sinais = Fala(ConfigFala(expressoes=[])).detectar(entrada(palavras=palavras_de(*calmo, rapido)))
    assert [s.motivo.split(" (")[0] for s in sinais] == ["fala acelerada"]
    assert 296 <= sinais[0].inicio_s <= 300 and sinais[0].forca == 1.0


def test_fala_no_ritmo_normal_nao_e_acelerada():
    calmo = [(t, t + 5, "uma duas tres quatro cinco seis sete oito") for t in range(0, 300, 5)]
    assert Fala(ConfigFala(expressoes=[])).detectar(entrada(palavras=palavras_de(*calmo))) == []


# ------------------------------------------------------------------ LoL


@pytest.mark.parametrize(
    ("evento", "forca", "motivo"),
    [
        (evento_lol("Multikill", PapelNoEvento.ABATE, KillStreak=5), 1.0, "pentakill seu"),
        (evento_lol("Multikill", PapelNoEvento.ABATE, KillStreak=2), 0.6, "abate duplo seu"),
        (evento_lol("BaronKill", PapelNoEvento.ABATE, Stolen="True"), 0.9, "barão roubado seu"),
        (evento_lol("BaronKill", PapelNoEvento.ASSISTENCIA, Stolen="False"), 0.49,
         "barão (sua assistência)"),
        (evento_lol("DragonKill", PapelNoEvento.ABATE, DragonType="Elder"), 0.7,
         "dragão ancião seu"),
        (evento_lol("ChampionKill", PapelNoEvento.MORTE), 0.32, "sua morte"),
        (evento_lol("ChampionKill", None), 0.12, "abate"),  # de outro jogador
        (evento_lol("GameEnd", None, Result="Win"), 0.5, "vitória"),
    ],
)  # fmt: skip
def test_forca_de_cada_evento_do_lol(evento, forca, motivo):
    sinal = Lol(ConfigLolDetector()).detectar(entrada(eventos=[(evento, 42.0)]))[0]
    assert (sinal.forca, sinal.motivo, sinal.inicio_s) == (pytest.approx(forca), motivo, 42.0)


def test_eventos_seus_valem_mais_que_os_dos_outros():
    lol = Lol(ConfigLolDetector())
    meu = lol.detectar(entrada(eventos=[(evento_lol("ChampionKill", PapelNoEvento.ABATE), 1)]))
    outro = lol.detectar(entrada(eventos=[(evento_lol("ChampionKill", None), 1)]))
    assert meu[0].forca > outro[0].forca


# ------------------------------------------------------------------ nota


def sinal(detector: str, inicio: float, forca: float = 1.0, fim: float | None = None) -> Sinal:
    return Sinal(detector, inicio, inicio if fim is None else fim, forca, f"{detector}@{inicio}")


CFG = ConfigDeteccao()
PESOS = {"volume_voz": 0.5, "fala": 0.25, "lol": 0.25}
TODOS = ["volume_voz", "fala", "lol"]


def test_sinais_proximos_viram_um_momento_so():
    sinais = [sinal("volume_voz", 100), sinal("lol", 104), sinal("fala", 110)]
    momentos = nota.calcular(sinais, TODOS, PESOS, CFG, 3600)
    assert len(momentos) == 1
    assert (momentos[0].inicio_s, momentos[0].fim_s) == (90, 115)  # 10 s antes, 5 s depois


def test_momentos_sobrepostos_viram_um_so():
    # Dois grupos (12 s entre eles) cujas janelas se sobrepõem e cabem em 60 s juntos.
    sinais = [sinal("volume_voz", 100), sinal("volume_voz", 112)]
    momentos = nota.calcular(sinais, TODOS, PESOS, CFG, 3600)
    assert [(m.inicio_s, m.fim_s) for m in momentos] == [(90, 117)]


def test_sobrepostos_que_nao_cabem_juntos_sao_cortados_sem_sobrepor():
    longo = [sinal("volume_voz", t) for t in range(100, 150, 5)]  # 45 s de gritos seguidos
    sinais = [*longo, sinal("lol", 160)]
    momentos = sorted(nota.calcular(sinais, TODOS, PESOS, CFG, 3600), key=lambda m: m.inicio_s)
    for anterior, seguinte in zip(momentos, momentos[1:], strict=False):
        assert anterior.fim_s <= seguinte.inicio_s
    assert all(m.duracao_s <= CFG.duracao_maxima_s for m in momentos)


def test_janela_respeita_15_e_60_segundos():
    curto = nota.calcular([sinal("lol", 500)], TODOS, PESOS, CFG, 3600)[0]
    assert curto.duracao_s == pytest.approx(15)  # 10 + 5 = 15 já é o mínimo
    cfg_curta = ConfigDeteccao(segundos_antes=2, segundos_depois=1)
    minimo = nota.calcular([sinal("lol", 500)], TODOS, PESOS, cfg_curta, 3600)[0]
    assert minimo.duracao_s == pytest.approx(15)  # 3 s viraram 15, crescendo dos dois lados
    assert (minimo.inicio_s, minimo.fim_s) == (492, 507)  # [498, 501] + 6 s de cada lado
    # Um sinal só, longo demais (100 s de fala acelerada): a janela fica ancorada nele.
    longo = nota.calcular([sinal("fala", 100, 0.6, fim=200)], TODOS, PESOS, CFG, 3600)[0]
    assert (longo.inicio_s, longo.fim_s) == (90, 150)  # 10 s de contexto e 60 s no total


def test_luta_longa_vira_varios_momentos_sem_perder_os_picos():
    # 3 min de gritos a cada 5 s (um grupo só), com dois picos bem distintos: antes, o grupo
    # virava um clipe de 60 s em volta do maior pico, e o outro se perdia.
    gritos = [sinal("volume_voz", t, 0.5) for t in range(100, 280, 5)]
    gritos += [sinal("volume_voz", 122, 1.0), sinal("volume_voz", 251, 0.9)]
    momentos = nota.calcular(gritos, TODOS, PESOS, CFG, 3600)
    assert len(momentos) >= 3
    assert all(m.duracao_s <= CFG.duracao_maxima_s for m in momentos)
    for pico in (122, 251):
        assert any(m.inicio_s <= pico <= m.fim_s for m in momentos), pico
    ordenados = sorted(momentos, key=lambda m: m.inicio_s)
    for anterior, seguinte in zip(ordenados, ordenados[1:], strict=False):
        assert anterior.fim_s <= seguinte.inicio_s


def test_janela_nunca_passa_do_comeco_nem_do_fim_da_gravacao():
    inicio = nota.calcular([sinal("lol", 3)], TODOS, PESOS, CFG, 300)[0]
    assert (inicio.inicio_s, inicio.fim_s) == (0, 15)
    fim = nota.calcular([sinal("lol", 298)], TODOS, PESOS, CFG, 300)[0]
    assert (fim.inicio_s, fim.fim_s) == (285, 300)
    curta = nota.calcular([sinal("lol", 5)], TODOS, PESOS, CFG, 10)[0]
    assert (curta.inicio_s, curta.fim_s) == (0, 10)  # gravação menor que o mínimo


def test_nota_respeita_os_pesos():
    sinais = [sinal("volume_voz", 100, 1.0), sinal("lol", 102, 0.4)]
    momento = nota.calcular(sinais, TODOS, PESOS, CFG, 3600)[0]
    # 100 × (0,5×1 + 0,25×0 + 0,25×0,4) / (0,5 + 0,25 + 0,25) = 60
    assert momento.nota == pytest.approx(60)
    assert momento.forcas == {"volume_voz": 1.0, "fala": 0.0, "lol": 0.4}
    so_lol = {"volume_voz": 0.0, "fala": 0.0, "lol": 1.0}
    assert nota.calcular(sinais, TODOS, so_lol, CFG, 3600)[0].nota == pytest.approx(40)


def test_motivo_lista_o_que_mais_pesou_primeiro():
    sinais = [sinal("fala", 100, 0.8), sinal("volume_voz", 101, 1.0), sinal("lol", 103, 0.2)]
    motivo = nota.calcular(sinais, TODOS, PESOS, CFG, 3600)[0].motivo
    assert motivo == "volume_voz@101 + fala@100 + lol@103"


def test_live_sem_lol_funciona_so_com_voz_e_fala():
    # Sem eventos, o detector do LoL não está disponível e sai da conta: a nota máxima
    # continua sendo 100, e não 75.
    sinais = [sinal("volume_voz", 100, 1.0), sinal("fala", 101, 1.0)]
    momento = nota.calcular(sinais, ["volume_voz", "fala"], PESOS, CFG, 3600)[0]
    assert momento.nota == pytest.approx(100)
    assert "lol" not in momento.forcas


def test_nota_minima_descarta_o_ruido():
    sinais = [sinal("volume_voz", 100, 1.0), sinal("lol", 500, 0.12)]  # só a torre de um aliado
    momentos = nota.calcular(sinais, TODOS, PESOS, CFG, 3600)
    assert [round(m.nota) for m in momentos] == [50]


def test_guarda_so_os_melhores():
    sinais = [sinal("volume_voz", t * 100, 0.5 + t / 200) for t in range(50)]
    momentos = nota.calcular(sinais, TODOS, PESOS, CFG, 10_000)
    assert len(momentos) == 30
    assert [m.nota for m in momentos] == sorted((m.nota for m in momentos), reverse=True)


def test_pesos_aprovados_valem_no_lugar_do_config(sessao):
    assert nota.pesos_atuais(sessao, CFG) == PESOS
    sessao.add(PesosNota(versao=1, valores={"volume_voz": 0.2, "lol": 0.7}))
    sessao.commit()
    assert nota.pesos_atuais(sessao, CFG) == {"volume_voz": 0.2, "fala": 0.25, "lol": 0.7}


# ------------------------------------------------------------------ executor


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "dados")


@pytest.fixture
def tarefa(cfg, sessao) -> Tarefa:
    """Uma gravação de 4 min com um grito aos 120 s, um "caramba" e um pentakill seu."""
    inicio = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)
    gravacao = Gravacao(arquivo="C:/g/2026-10-14 20-00-00.mkv", inicio=inicio, duracao_s=240)
    sessao.add(gravacao)
    sessao.flush()
    volumes = np.concatenate([fala_normal(120), [-15.0] * 4, fala_normal(118)])
    wav_de_voz(caminho_da_voz(cfg, gravacao.id), volumes)
    sessao.add(Transcricao(gravacao_id=gravacao.id, palavras=palavras_de((121, 122, "caramba"))))
    sessao.add(
        EventoJogo(
            tipo="Multikill", horario_real=inicio.replace(minute=2, second=3), tempo_jogo_s=60,
            partida_inicio=inicio, event_id=1, papel=PapelNoEvento.ABATE, dados={"KillStreak": 5},
        )
    )  # fmt: skip
    tarefa = Tarefa(tipo=TipoTarefa.DETECTAR, gravacao_id=gravacao.id)
    sessao.add(tarefa)
    sessao.commit()
    return tarefa


def momentos_salvos(sessao) -> list[Momento]:
    return list(sessao.exec(select(Momento).order_by(Momento.nota.desc())).all())


def test_executor_salva_os_momentos_com_nota_e_motivo(cfg, sessao, tarefa):
    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()

    [momento] = momentos_salvos(sessao)
    forcas = momento.sinais
    # Nota = 100 × (0,5 × volume + 0,25 × fala + 0,25 × lol), com fala 0,8 e lol 1.
    assert momento.nota == pytest.approx(100 * (0.5 * forcas["volume_voz"] + 0.2 + 0.25), abs=0.1)
    assert momento.motivo == "voz alta (+15 dB) + pentakill seu + “caramba”"
    assert momento.sinais == {"volume_voz": pytest.approx(0.75, abs=0.05), "fala": 0.8, "lol": 1.0}
    assert momento.inicio_s == 110.0 and momento.fim_s == pytest.approx(128, abs=0.5)


def test_detectar_de_novo_substitui_os_momentos(cfg, sessao, tarefa):
    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()
    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()
    assert len(momentos_salvos(sessao)) == 1


def test_detectar_de_novo_nao_apaga_a_sua_revisao(cfg, sessao, tarefa):
    from clipador.banco.fila import FalhaDefinitivaError

    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()
    sessao.add(Decisao(momento_id=momentos_salvos(sessao)[0].id, aprovado=True, criada_em=agora()))
    sessao.commit()
    with pytest.raises(FalhaDefinitivaError, match="apagaria a sua revisão"):
        detectar.detectar(sessao, tarefa, cfg)


def test_detector_desligado_fica_de_fora(tmp_path, sessao, tarefa):
    from clipador.config import ConfigDeteccao as Deteccao

    cfg = config_sem_arquivo(
        pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "dados",
        deteccao=Deteccao(lol=ConfigLolDetector(ligado=False)),
    )  # fmt: skip
    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()
    assert "lol" not in momentos_salvos(sessao)[0].sinais


def test_servico_tem_o_executor_de_detectar():
    from clipador.sistema.servico import EXECUTORES

    assert EXECUTORES[TipoTarefa.DETECTAR] is detectar.detectar


def test_comando_momentos(cfg, sessao, tarefa, tmp_path):
    from clipador.cli import app

    detectar.detectar(sessao, tarefa, cfg)
    sessao.commit()
    config = tmp_path / "config.toml"
    config.write_text(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "dados").as_posix()}"\n',
        encoding="utf-8",
    )
    saida = CliRunner().invoke(app, ["momentos", "--arquivo", str(config)])
    assert saida.exit_code == 0, saida.output
    assert "  1. nota " in saida.output and "· 1:50-2:08 (18 s) ·" in saida.output
    assert "pentakill seu" in saida.output
