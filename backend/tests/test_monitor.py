"""Monitor da pasta do OBS e leitura do horário pelo nome do arquivo."""

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from clipador.banco.modelos import Gravacao, Tarefa, TipoTarefa
from clipador.ingestao.monitor import MonitorDeGravacoes, eh_gravacao, registrar_gravacao
from clipador.ingestao.nomes import horario_de_inicio, inicio_pelo_nome

AGORA = datetime(2026, 10, 14, 23, 30, tzinfo=UTC)


def criar(pasta: Path, nome: str, modificado_ha: timedelta, tamanho: int = 1000) -> Path:
    """Cria um arquivo com a data de modificação no passado (como uma gravação antiga)."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / nome
    arquivo.write_bytes(b"x" * tamanho)
    instante = (AGORA - modificado_ha).timestamp()
    os.utime(arquivo, (instante, instante))
    return arquivo


@pytest.fixture
def pasta(tmp_path) -> Path:
    return tmp_path / "gravacoes"


# ------------------------------------------------------------------ nomes


def test_horario_vem_do_nome_padrao_do_obs():
    esperado = datetime(2026, 10, 14, 20, 0, 0).astimezone().astimezone(UTC)
    assert inicio_pelo_nome(Path("2026-10-14 20-00-00.mkv")) == esperado
    assert inicio_pelo_nome(Path("Replay 2026-10-14_20-00-00.mp4")) == esperado


def test_nome_fora_do_padrao_nao_tem_horario():
    assert inicio_pelo_nome(Path("minha live.mkv")) is None
    assert inicio_pelo_nome(Path("2026-13-40 99-99-99.mkv")) is None  # parece data, mas não é


def test_sem_horario_no_nome_usa_a_criacao_do_arquivo_e_avisa(pasta, caplog):
    arquivo = criar(pasta, "minha live.mkv", timedelta(hours=1))
    with caplog.at_level(logging.WARNING):
        inicio = horario_de_inicio(arquivo)
    assert inicio.tzinfo is not None
    assert "não segue o padrão do OBS" in caplog.text


# ------------------------------------------------------------------ quais arquivos


def test_so_videos_contam_como_gravacao():
    assert eh_gravacao(Path("2026-10-14 20-00-00.mkv"))
    assert eh_gravacao(Path("LIVE.MP4"))
    assert not eh_gravacao(Path("anotacoes.txt"))
    assert not eh_gravacao(Path("thumb.png"))
    assert not eh_gravacao(Path("video.parcial.mkv"))  # arquivo temporário
    assert not eh_gravacao(Path(".escondido.mkv"))
    assert not eh_gravacao(Path("Replay 2026-10-14 20-31-07.mkv"))  # buffer de replay do OBS


def test_mkv_e_mp4_da_mesma_live_contam_uma_vez_so(pasta):
    # Com "remuxar para MP4" ligado, o OBS deixa as duas versões da mesma live.
    criar(pasta, "2026-10-14 20-00-00.mkv", timedelta(hours=1))
    criar(pasta, "2026-10-14 20-00-00.mp4", timedelta(hours=1))
    criar(pasta, "outra.mp4", timedelta(hours=1))
    nomes = [p.name for p in MonitorDeGravacoes(pasta, so_nomes_do_obs=False).candidatos()]
    assert nomes == ["2026-10-14 20-00-00.mkv", "outra.mp4"]


def test_pasta_que_nao_existe_nao_quebra(tmp_path):
    monitor = MonitorDeGravacoes(tmp_path / "nao_existe", so_nomes_do_obs=False)
    assert monitor.verificar(AGORA).prontas == []


# ------------------------------------------------------------------ pronta x em andamento


def test_gravacao_antiga_ja_esta_pronta_na_varredura_ao_abrir(pasta):
    criar(pasta, "antiga.mkv", timedelta(hours=5))
    resultado = MonitorDeGravacoes(pasta, so_nomes_do_obs=False).verificar(AGORA)
    assert [p.name for p in resultado.prontas] == ["antiga.mkv"]
    assert resultado.em_andamento == []


def test_gravacao_recente_fica_em_andamento_ate_2_minutos_parada(pasta):
    criar(pasta, "live.mkv", timedelta(seconds=10))
    monitor = MonitorDeGravacoes(pasta, so_nomes_do_obs=False)

    assert [p.name for p in monitor.verificar(AGORA).em_andamento] == ["live.mkv"]
    # 1 minuto depois, sem mudar: ainda em andamento.
    assert monitor.verificar(AGORA + timedelta(minutes=1)).prontas == []
    # 2 minutos depois da última mudança: pronta.
    assert [p.name for p in monitor.verificar(AGORA + timedelta(minutes=2)).prontas] == ["live.mkv"]


def test_arquivo_crescendo_nunca_fica_pronto(pasta):
    arquivo = criar(pasta, "live.mkv", timedelta(seconds=5))
    monitor = MonitorDeGravacoes(pasta, so_nomes_do_obs=False)
    momento = AGORA
    for _ in range(5):
        momento += timedelta(minutes=1)
        with arquivo.open("ab") as escrita:  # o OBS continua gravando
            escrita.write(b"mais video")
        resultado = monitor.verificar(momento)
        assert resultado.prontas == []
        assert len(resultado.em_andamento) == 1


def test_arquivo_apagado_sai_do_acompanhamento(pasta):
    arquivo = criar(pasta, "live.mkv", timedelta(seconds=5))
    monitor = MonitorDeGravacoes(pasta, so_nomes_do_obs=False)
    monitor.verificar(AGORA)
    arquivo.unlink()
    resultado = monitor.verificar(AGORA + timedelta(minutes=3))
    assert resultado.prontas == [] and resultado.em_andamento == []


# ------------------------------------------------------------------ entrada no banco


def test_gravacao_pronta_entra_na_fila_uma_vez_so(sessao, pasta):
    arquivo = criar(pasta, "2026-10-14 20-00-00.mkv", timedelta(hours=1))

    primeira = registrar_gravacao(sessao, arquivo)
    segunda = registrar_gravacao(sessao, arquivo)

    assert primeira is not None and segunda is None
    assert len(sessao.exec(select(Gravacao)).all()) == 1
    tarefas = sessao.exec(select(Tarefa)).all()
    assert [(t.tipo, t.gravacao_id) for t in tarefas] == [(TipoTarefa.EXTRAIR_AUDIO, primeira.id)]
    # O horário de início veio do nome do arquivo.
    assert primeira.inicio == inicio_pelo_nome(arquivo)


def test_por_padrao_so_entram_nomes_no_padrao_do_obs(pasta, caplog):
    # O OBS grava em Vídeos por padrão, junto com outros vídeos da pessoa.
    criar(pasta, "2026-10-14 20-00-00.mkv", timedelta(hours=1))
    criar(pasta, "video da viagem.mp4", timedelta(days=30))
    monitor = MonitorDeGravacoes(pasta)
    with caplog.at_level(logging.INFO):
        nomes = [p.name for p in monitor.verificar(AGORA).prontas]
        monitor.verificar(AGORA)  # o aviso sobre o arquivo ignorado sai uma vez só
    assert nomes == ["2026-10-14 20-00-00.mkv"]
    assert caplog.text.count("Ignorando video da viagem.mp4") == 1


def test_lista_de_pedacos_do_hls_nao_e_gravacao():
    assert not eh_gravacao(Path("2026-10-14 20-00-00.m3u8"))
