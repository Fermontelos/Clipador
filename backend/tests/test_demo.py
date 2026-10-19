"""Modo demo: comando gerado, faixas, picos medidos no áudio e idempotência."""

import math
import shutil
import statistics
import subprocess
from array import array
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clipador import ffmpeg
from clipador.cli import app
from clipador.demo import gerar, roteiro
from clipador.demo.roteiro import Pico

precisa_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg não instalado neste PC"
)
runner = CliRunner()
JANELA_S = 0.25


# ------------------------------------------------------------------ ajudantes


def volumes_da_faixa(arquivo: Path, faixa: int, taxa: int = 8000) -> list[float]:
    """Volume (RMS) de uma faixa de áudio em janelas de 0,25 s, decodificado pelo FFmpeg."""
    bruto = subprocess.run(
        [ffmpeg.localizar(), "-v", "error", "-i", str(arquivo), "-map", f"0:a:{faixa}",
         "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    amostras = array("h")
    amostras.frombytes(bruto)
    tamanho = int(taxa * JANELA_S)
    return [
        math.sqrt(sum(x * x for x in amostras[i : i + tamanho]) / tamanho)
        for i in range(0, len(amostras) - tamanho + 1, tamanho)
    ]


def trechos_altos(volumes: list[float], fator: float = 4.0) -> list[tuple[float, float]]:
    """Trechos em que o volume fica `fator` vezes acima da mediana (o "normal")."""
    limite = statistics.median(volumes) * fator
    trechos, inicio = [], None
    for indice, volume in enumerate(volumes):
        if volume > limite and inicio is None:
            inicio = indice
        elif volume <= limite and inicio is not None:
            trechos.append((inicio * JANELA_S, indice * JANELA_S))
            inicio = None
    if inicio is not None:
        trechos.append((inicio * JANELA_S, len(volumes) * JANELA_S))
    return trechos


def confere_picos(medidos, esperados, tolerancia_s=0.3):
    assert len(medidos) == len(esperados), (medidos, esperados)
    for (inicio, fim), pico in zip(medidos, esperados, strict=True):
        assert abs(inicio - pico.inicio_s) <= tolerancia_s, (inicio, pico)
        assert abs(fim - pico.fim_s) <= tolerancia_s, (fim, pico)


@pytest.fixture
def demo_curta(monkeypatch):
    """Encurta o roteiro para 12 s (mesmo código, teste rápido)."""
    monkeypatch.setattr(roteiro, "DURACAO_S", 12)
    monkeypatch.setattr(roteiro, "PICOS_VOZ", (Pico(3.0, 5.0), Pico(8.0, 9.5)))


# ------------------------------------------------------------------ sem FFmpeg


def test_comando_mapeia_as_4_faixas_na_ordem_do_obs():
    argumentos = gerar.comando_gerar(Path("x.mkv"), 10, roteiro.PICOS_VOZ)
    mapas = [argumentos[i + 1] for i, a in enumerate(argumentos) if a == "-map"]
    assert mapas == ["0:v", "[mistura]", "[voz]", "[jogo]", "[musica]"]
    titulos = [argumentos[i + 1] for i, a in enumerate(argumentos) if a.startswith("-metadata:s:a")]
    assert titulos == ["title=Mistura", "title=Voz", "title=Jogo", "title=Música"]
    # A música só entra na mistura e na própria faixa, nunca na voz ou no jogo.
    grafo = argumentos[argumentos.index("-filter_complex") + 1]
    assert "[voz_m][jogo_m][musica_m]amix" in grafo


def test_volume_da_voz_sobe_so_nos_picos():
    expressao = gerar._volume_da_voz((Pico(25, 28), Pico(70, 72)))
    assert "between(t,25,28)" in expressao
    assert "between(t,70,72)" in expressao
    assert gerar._volume_da_voz(()) == str(gerar.VOLUME_FALA)


def test_roteiro_e_coerente():
    duracao = roteiro.DURACAO_S
    # Picos dentro da gravação, em ordem e sem se sobrepor.
    for anterior, atual in zip(roteiro.PICOS_VOZ, roteiro.PICOS_VOZ[1:], strict=False):
        assert anterior.fim_s < atual.inicio_s
    assert all(0 <= p.inicio_s < p.fim_s <= duracao for p in roteiro.PICOS_VOZ)
    # Um conjunto de títulos para cada pico.
    assert len(roteiro.TITULOS) == len(roteiro.PICOS_VOZ)
    # Eventos dentro da gravação e depois do início da partida.
    assert all(roteiro.PARTIDA_COMECA_S <= e.gravacao_s <= duracao for e in roteiro.EVENTOS_LOL)
    # O nome do arquivo segue o padrão do OBS.
    assert roteiro.NOME_ARQUIVO == "2026-01-10 20-00-00.mkv"


def test_transcricao_pronta_tem_palavras_em_ordem_e_dentro_das_falas():
    palavras = roteiro.palavras_da_transcricao()
    assert [p["palavra"] for p in palavras[:4]] == ["bora", "começar", "a", "partida"]
    inicios = [p["inicio_s"] for p in palavras]
    assert inicios == sorted(inicios)
    assert all(p["inicio_s"] < p["fim_s"] for p in palavras)
    # As palavras do grito caem dentro do 1º pico de voz.
    grito = [p for p in palavras if p["palavra"] == "YASUO"][0]
    pico = roteiro.PICOS_VOZ[0]
    assert pico.inicio_s <= grito["inicio_s"] < grito["fim_s"] <= pico.fim_s


# ------------------------------------------------------------------ com FFmpeg


@precisa_ffmpeg
def test_gravacao_tem_4_faixas_a_duracao_certa_e_os_titulos(tmp_path):
    arquivo = tmp_path / "curta.mkv"
    gerar.gerar_gravacao(arquivo, 12, (Pico(3.0, 5.0),))
    info = ffmpeg.inspecionar(arquivo)
    assert info.faixas_audio == 4
    assert abs(info.duracao_s - 12) < 0.5
    assert (info.largura, info.altura) == (gerar.LARGURA, gerar.ALTURA)
    assert info.titulos_faixas == gerar.TITULOS_FAIXAS
    # Não sobra o arquivo temporário.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["curta.mkv"]


@precisa_ffmpeg
def test_picos_ficam_nos_horarios_esperados(tmp_path):
    picos = (Pico(3.0, 5.0), Pico(8.0, 9.5))
    arquivo = tmp_path / "curta.mkv"
    gerar.gerar_gravacao(arquivo, 12, picos)

    # Na faixa de voz (2), os trechos altos são exatamente os picos.
    confere_picos(trechos_altos(volumes_da_faixa(arquivo, faixa=1)), picos)
    # Na mistura (1), os picos da voz também aparecem.
    confere_picos(trechos_altos(volumes_da_faixa(arquivo, faixa=0)), picos)
    # No jogo (3) e na música (4), não há pico nenhum.
    assert trechos_altos(volumes_da_faixa(arquivo, faixa=2)) == []
    assert trechos_altos(volumes_da_faixa(arquivo, faixa=3)) == []


@precisa_ffmpeg
def test_rodar_a_demo_duas_vezes_nao_duplica_nada(tmp_path, demo_curta):
    primeira = gerar.preparar_demo(tmp_path / "dados")
    segunda = gerar.preparar_demo(tmp_path / "dados")

    assert primeira.video_criado and primeira.banco_preenchido
    assert not segunda.video_criado and not segunda.banco_preenchido
    esperado = {"gravacoes": 1, "eventos": len(roteiro.EVENTOS_LOL), "transcricoes": 1}
    for resultado in (primeira, segunda):
        assert {k: resultado.contagens[k] for k in esperado} == esperado
        assert resultado.contagens["tarefas"] == 1


@precisa_ffmpeg
def test_demo_fica_separada_dos_dados_reais(tmp_path, demo_curta):
    resultado = gerar.preparar_demo(tmp_path / "dados")
    assert resultado.banco == tmp_path / "dados" / "demo" / "clipador.db"
    assert not (tmp_path / "dados" / "clipador.db").exists()


@precisa_ffmpeg
def test_do_zero_recria_tudo(tmp_path, demo_curta):
    gerar.preparar_demo(tmp_path / "dados")
    lixo = tmp_path / "dados" / "demo" / "arquivo_velho.txt"
    lixo.write_text("x", encoding="utf-8")

    resultado = gerar.preparar_demo(tmp_path / "dados", do_zero=True)

    assert resultado.video_criado and resultado.banco_preenchido
    assert not lixo.exists()


@precisa_ffmpeg
def test_video_invalido_e_gerado_de_novo(tmp_path, demo_curta):
    resultado = gerar.preparar_demo(tmp_path / "dados")
    resultado.arquivo.write_bytes(b"isto nao e um video")
    assert gerar.gravacao_valida(resultado.arquivo) is False
    assert gerar.preparar_demo(tmp_path / "dados").video_criado is True


@precisa_ffmpeg
def test_comando_demo_e_status_da_demo(config_valida, demo_curta):
    resultado = runner.invoke(app, ["demo", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert "Demo pronta" in resultado.output
    assert "4 faixas: Mistura, Voz, Jogo, Música" in resultado.output
    assert "0:03, 0:08" in resultado.output  # os picos do roteiro curto

    de_novo = runner.invoke(app, ["demo", "--arquivo", str(config_valida)])
    assert "nada foi duplicado" in de_novo.output

    status = runner.invoke(app, ["status", "--demo", "--arquivo", str(config_valida)])
    assert status.exit_code == 0, status.output
    assert "demo" in status.output
    assert "#1 extrair_audio (gravação 1)" in status.output


def test_status_da_demo_sem_demo_explica_o_que_fazer(config_valida):
    resultado = runner.invoke(app, ["status", "--demo", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "clipador demo" in resultado.output


def test_demo_sem_ffmpeg_explica_como_instalar(config_valida, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda programa: None)
    resultado = runner.invoke(app, ["demo", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "winget install Gyan.FFmpeg" in resultado.output


@precisa_ffmpeg
def test_mover_a_pasta_do_projeto_nao_duplica_a_demo(tmp_path, demo_curta):
    antes = tmp_path / "antes"
    gerar.preparar_demo(antes / "dados")
    depois = tmp_path / "depois"
    shutil.move(str(antes), str(depois))  # como renomear a pasta do projeto

    resultado = gerar.preparar_demo(depois / "dados")

    assert not resultado.banco_preenchido
    assert resultado.contagens["gravacoes"] == 1
    assert resultado.contagens["eventos"] == len(roteiro.EVENTOS_LOL)
    assert resultado.contagens["tarefas"] == 1


def test_falha_do_ffmpeg_nao_deixa_arquivo_pela_metade(tmp_path, monkeypatch):
    destino = tmp_path / "x.mkv"

    def ffmpeg_que_falha(argumentos, tempo_limite_s=None):
        Path(argumentos[-1]).write_bytes(b"metade")  # escreveu um pedaço e falhou
        raise ffmpeg.FFmpegFalhouError("falhou")

    monkeypatch.setattr(ffmpeg, "rodar", ffmpeg_que_falha)
    with pytest.raises(ffmpeg.FFmpegFalhouError):
        gerar.gerar_gravacao(destino, 5, ())
    assert list(tmp_path.iterdir()) == []


@precisa_ffmpeg
def test_ffmpeg_travado_e_encerrado_pelo_limite_de_tempo():
    # Uma entrada de áudio sem fim: sem limite de tempo, este comando nunca terminaria.
    sem_fim = ["-f", "lavfi", "-i", "anullsrc", "-f", "null", "-"]
    with pytest.raises(ffmpeg.FFmpegFalhouError, match="limite de 1 s"):
        ffmpeg.rodar(sem_fim, tempo_limite_s=1)


@precisa_ffmpeg
def test_eventos_da_demo_tem_os_campos_da_api_real(tmp_path, demo_curta):
    from sqlmodel import select

    from clipador.banco.conexao import abrir_banco, nova_sessao
    from clipador.banco.modelos import EventoJogo

    resultado = gerar.preparar_demo(tmp_path / "dados")
    engine = abrir_banco(resultado.banco)
    try:
        with nova_sessao(engine) as sessao:
            eventos = sessao.exec(select(EventoJogo)).all()
    finally:
        engine.dispose()
    ids = [e.dados["EventID"] for e in eventos]
    assert len(ids) == len(set(ids)), "EventID não pode repetir"
    for evento in eventos:
        assert evento.dados["EventName"] == evento.tipo
        assert evento.dados["EventTime"] == evento.tempo_jogo_s


@precisa_ffmpeg
def test_inspecionar_arquivo_que_nao_e_video_da_erro_claro(tmp_path):
    falso = tmp_path / "falso.mkv"
    falso.write_bytes(b"isto nao e um video")
    with pytest.raises(ffmpeg.FFmpegFalhouError):
        ffmpeg.inspecionar(falso)


@precisa_ffmpeg
def test_demo_funciona_sem_config_toml(tmp_path, demo_curta, monkeypatch):
    # Quem acabou de clonar ainda não tem config.toml.
    monkeypatch.chdir(tmp_path)
    resultado = runner.invoke(app, ["demo"])
    assert resultado.exit_code == 0, resultado.output
    assert "a demo usa a pasta padrão" in resultado.output
    assert (tmp_path / "dados" / "demo" / "clipador.db").is_file()

    status = runner.invoke(app, ["status", "--demo"])
    assert status.exit_code == 0, status.output
    assert "#1 extrair_audio" in status.output


def test_demo_com_config_invalido_continua_dando_erro(escrever_config):
    caminho = escrever_config('pasta_gravacoes = "C:/g"\nlimite_diario = -1\n')
    resultado = runner.invoke(app, ["demo", "--arquivo", str(caminho)])
    assert resultado.exit_code == 1
    assert "limite_diario" in resultado.output


# ------------------------------------------------------------------ demo real (lento)


@pytest.mark.lento
@precisa_ffmpeg
def test_demo_real_de_3_minutos(tmp_path):
    resultado = gerar.preparar_demo(tmp_path / "dados")
    assert resultado.segundos < 60, "o roteiro pede menos de 1 minuto"
    assert abs(resultado.info.duracao_s - roteiro.DURACAO_S) < 1
    assert resultado.info.faixas_audio == 4
    confere_picos(trechos_altos(volumes_da_faixa(resultado.arquivo, faixa=1)), roteiro.PICOS_VOZ)


@precisa_ffmpeg
def test_demo_mostra_so_as_tarefas_pendentes(tmp_path, demo_curta):
    # Depois de processar, o banco tem 3 tarefas (2 feitas e a próxima pendente): a mensagem
    # "na fila" não pode contar as que já foram feitas.
    sem_config = str(tmp_path / "config.toml")
    runner.invoke(app, ["demo", "--arquivo", sem_config])
    assert runner.invoke(app, ["processar", "--demo", "--arquivo", sem_config]).exit_code == 0

    saida = runner.invoke(app, ["demo", "--arquivo", sem_config]).output

    assert "1 tarefa(s) na fila" in saida
    assert "clipador processar --demo" in saida
    resultado = gerar.preparar_demo(tmp_path / "dados")
    assert (resultado.contagens["tarefas"], resultado.contagens["tarefas_pendentes"]) == (3, 1)


def _quantos_ffmpeg_rodando() -> int:
    lista = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="replace", timeout=30,
    ).stdout  # fmt: skip
    return lista.lower().count('"ffmpeg.exe"')


@precisa_ffmpeg
def test_limite_de_tempo_encerra_tambem_o_ffmpeg_aberto_por_um_shim(tmp_path, monkeypatch):
    # Chocolatey e Scoop instalam um "shim": um ffmpeg.exe pequeno que abre o verdadeiro
    # como processo filho. Encerrar só o shim deixava o FFmpeg verdadeiro rodando e o
    # Python esperando a saída dele para sempre (travou a CI por 5 h). Um .bat faz o
    # mesmo papel do shim aqui.
    shim = tmp_path / "ffmpeg.bat"
    shim.write_text(f'@"{ffmpeg.localizar()}" %*\r\n', encoding="utf-8")
    monkeypatch.setattr(ffmpeg, "localizar", lambda programa="ffmpeg": str(shim))
    antes = _quantos_ffmpeg_rodando()
    sem_fim = ["-f", "lavfi", "-i", "anullsrc", "-f", "null", "-"]

    with pytest.raises(ffmpeg.FFmpegFalhouError, match="limite de 1 s"):
        ffmpeg.rodar(sem_fim, tempo_limite_s=1)

    assert _quantos_ffmpeg_rodando() == antes  # nenhum FFmpeg ficou para trás
