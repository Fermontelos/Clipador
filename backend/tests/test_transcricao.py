"""Etapa 6: transcrição em blocos, retomada, pausa, legenda e o carregamento do Whisper.

Nada aqui precisa de placa de vídeo: o Whisper é trocado por um transcritor falso que
"ouve" um roteiro de falas. A transcrição real é testada à mão (ver docs/stack.html).
"""

import re
from pathlib import Path

import numpy as np
import pytest
import soundfile
from sqlmodel import select

from clipador.banco import fila
from clipador.banco.fila import Contexto, TarefaPausada
from clipador.banco.modelos import Gravacao, StatusTarefa, Tarefa, TipoTarefa, Transcricao, agora
from clipador.config import ConfigTranscricao, config_sem_arquivo
from clipador.ingestao.audio import caminho_da_voz
from clipador.transcricao import legenda, transcrever, whisper
from clipador.transcricao.transcrever import emendar
from clipador.transcricao.whisper import Palavra, Segmento

TAXA = 16_000

# Roteiro de falas (início e fim na live, em segundos). A 2ª atravessa a emenda dos blocos
# de 1 min (60 s) e a 4ª, a de 120 s.
FALAS = [
    (5.0, 9.0, "bora começar"),
    (55.0, 63.0, "essa frase passa da emenda do bloco"),
    (80.0, 84.0, "calma que agora vai"),
    (118.0, 125.0, "NÃO ACREDITO NO QUE ESSE YASUO FEZ"),
    (170.0, 174.0, "valeu pessoal"),
]
DURACAO_S = 180.0


# ------------------------------------------------------------------ o transcritor falso


class TranscritorFalso:
    """Faz o papel do Whisper: devolve as falas do roteiro que caem no trecho recebido.

    O WAV de teste guarda em cada amostra o próprio instante (amostra / total), então o
    primeiro valor do trecho diz em que ponto da live ele começa. Uma fala que passa do
    fim do trecho sai cortada, como sairia do Whisper.
    """

    def __init__(self, falhar_na_chamada: int | None = None) -> None:
        self.chamadas: list[float] = []  # onde começou cada trecho recebido
        self.falhar_na_chamada = falhar_na_chamada
        self.fechado = False

    @property
    def nome(self) -> str:
        return "falso (cpu, int8)"

    def transcrever(self, audio: np.ndarray) -> list[Segmento]:
        inicio = round(float(audio[0]) * DURACAO_S * TAXA) / TAXA
        fim = inicio + len(audio) / TAXA
        self.chamadas.append(inicio)
        if self.falhar_na_chamada == len(self.chamadas):
            raise RuntimeError("o PC desligou")
        segmentos = []
        for fala_inicio, fala_fim, texto in FALAS:
            if fala_fim <= inicio or fala_inicio >= fim:
                continue
            cortada = fala_fim > fim
            fala_fim = min(fala_fim, fim)
            palavras = texto.split()
            if cortada:
                palavras = palavras[: len(palavras) // 2] + ["(cortada)"]
            passo = (fala_fim - fala_inicio) / len(palavras)
            segmentos.append(
                Segmento(
                    inicio_s=fala_inicio - inicio,
                    fim_s=fala_fim - inicio,
                    texto=" ".join(palavras),
                    palavras=tuple(
                        Palavra(
                            p,
                            fala_inicio - inicio + i * passo,
                            fala_inicio - inicio + (i + 1) * passo,
                            0.9,
                        )
                        for i, p in enumerate(palavras)
                    ),  # fmt: skip
                )
            )
        return segmentos

    def fechar(self) -> None:
        self.fechado = True


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(
        pasta_gravacoes=tmp_path / "gravacoes",
        pasta_dados=tmp_path / "dados",
        transcricao=ConfigTranscricao(minutos_por_bloco=1),
    )


@pytest.fixture
def tarefa(cfg, sessao) -> Tarefa:
    """Uma gravação com o WAV da voz já extraído (o "relógio" descrito no falso)."""
    gravacao = Gravacao(
        arquivo=str(cfg.pasta_gravacoes / "2026-10-14 20-00-00.mkv"), inicio=agora()
    )
    sessao.add(gravacao)
    sessao.flush()
    tarefa = Tarefa(tipo=TipoTarefa.TRANSCREVER, gravacao_id=gravacao.id)
    sessao.add(tarefa)
    sessao.commit()
    wav = caminho_da_voz(cfg, gravacao.id)
    wav.parent.mkdir(parents=True)
    total = int(DURACAO_S * TAXA)
    relogio = (np.arange(total, dtype=np.float64) / total).astype(np.float32)
    soundfile.write(str(wav), relogio, TAXA, subtype="FLOAT")
    return tarefa


@pytest.fixture
def falso(monkeypatch):
    transcritor = TranscritorFalso()
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: transcritor)
    return transcritor


def transcricao_de(sessao, tarefa) -> Transcricao:
    return sessao.exec(
        select(Transcricao).where(Transcricao.gravacao_id == tarefa.gravacao_id)
    ).one()


def falas_salvas(transcricao: Transcricao) -> list[tuple[float, float, str]]:
    return [(s["inicio_s"], s["fim_s"], s["texto"]) for s in transcricao.segmentos]


# ------------------------------------------------------------------ emenda dos blocos


def seg(inicio: float, fim: float) -> Segmento:
    return Segmento(inicio, fim, "x", ())


def test_emenda_aceita_so_frases_que_terminam_antes_da_margem():
    aceitos, proximo = emendar([seg(1, 10), seg(50, 58), seg(58.5, 60)], 100, 160, False)
    assert aceitos == [seg(1, 10)]  # 58 > 60 - 3: pode ter sido cortada
    assert proximo == 110  # o próximo bloco começa no fim da última frase aceita


def test_emenda_no_ultimo_bloco_aceita_tudo():
    aceitos, proximo = emendar([seg(1, 10), seg(57, 60)], 100, 160, True)
    assert len(aceitos) == 2 and proximo == 160


def test_emenda_sem_frase_aceita_recomeca_na_primeira():
    assert emendar([seg(20, 59)], 100, 160, False) == ([], 120)


def test_emenda_em_silencio_anda_ate_a_margem():
    assert emendar([], 100, 160, False) == ([], 157)


def test_emenda_sempre_anda():
    # Uma frase que começa no início do bloco e não termina a tempo (não deveria existir).
    assert emendar([seg(0, 59)], 100, 160, False) == ([], 157)


# ------------------------------------------------------------------ executor


def test_transcreve_em_blocos_sem_cortar_nem_repetir_frases(cfg, sessao, tarefa, falso):
    transcrever.transcrever(sessao, tarefa, cfg)

    transcricao = transcricao_de(sessao, tarefa)
    assert falas_salvas(transcricao) == FALAS  # cada fala uma vez, inteira, no tempo certo
    assert len(falso.chamadas) > 3  # foram vários blocos de 1 min
    assert transcricao.concluida and transcricao.transcrito_ate_s == DURACAO_S
    assert transcricao.modelo == "falso (cpu, int8)"
    assert falso.fechado  # a memória (da placa de vídeo) foi liberada
    primeira = transcricao.palavras[0]
    assert primeira == {"palavra": "bora", "inicio_s": 5.0, "fim_s": 7.0, "probabilidade": 0.9}
    assert all("(cortada)" not in p["palavra"] for p in transcricao.palavras)


def test_exporta_a_legenda_srt(cfg, sessao, tarefa, falso):
    transcrever.transcrever(sessao, tarefa, cfg)

    srt = (cfg.pasta_transcricoes / "2026-10-14 20-00-00.srt").read_text(encoding="utf-8-sig")
    assert srt.startswith("1\n00:00:05,000 --> 00:00:09,000\nbora começar\n")
    assert "4\n00:01:58,000 --> 00:02:05,000\nNÃO ACREDITO NO QUE ESSE YASUO FEZ\n" in srt
    assert srt.count(" --> ") == len(FALAS)


def test_continua_de_onde_parou_se_o_pc_desligar(cfg, sessao, tarefa, monkeypatch):
    caiu = TranscritorFalso(falhar_na_chamada=3)
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: caiu)
    with pytest.raises(RuntimeError, match="o PC desligou"):
        transcrever.transcrever(sessao, tarefa, cfg)
    sessao.rollback()
    salvo = transcricao_de(sessao, tarefa)
    ate = salvo.transcrito_ate_s
    assert 60 < ate < 180 and not salvo.concluida  # os 2 primeiros blocos ficaram salvos
    assert caiu.fechado  # mesmo caindo, liberou a placa de vídeo

    de_novo = TranscritorFalso()
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: de_novo)
    transcrever.transcrever(sessao, tarefa, cfg)

    assert de_novo.chamadas[0] == pytest.approx(ate, abs=1e-3)  # recomeçou de onde parou
    assert falas_salvas(transcricao_de(sessao, tarefa)) == FALAS  # sem repetir nada


def test_pausa_entre_blocos_se_comecar_uma_live(cfg, sessao, tarefa, falso):
    perguntas = []

    def live_comecou() -> bool:
        perguntas.append(1)
        return len(perguntas) > 2  # começa depois do 2º bloco

    with pytest.raises(TarefaPausada, match="pausada"):
        transcrever.transcrever(sessao, tarefa, cfg, Contexto(deve_pausar=live_comecou))
    assert len(falso.chamadas) == 2
    assert falso.fechado
    assert 60 < transcricao_de(sessao, tarefa).transcrito_ate_s < 180


def test_transcricao_pronta_so_exporta_a_legenda(cfg, sessao, tarefa, monkeypatch):
    # Ex.: a demo, que já vem com a transcrição. Não pode nem abrir o Whisper.
    sessao.add(
        Transcricao(
            gravacao_id=tarefa.gravacao_id,
            segmentos=[{"inicio_s": 1.0, "fim_s": 2.0, "texto": "pronta"}],
            concluida=True,
        )
    )
    sessao.commit()
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: pytest.fail("abriu"))

    transcrever.transcrever(sessao, tarefa, cfg)

    srt = (cfg.pasta_transcricoes / "2026-10-14 20-00-00.srt").read_text(encoding="utf-8-sig")
    assert "pronta" in srt


def test_sem_o_audio_da_voz_extrai_de_novo(cfg, sessao, tarefa, falso, monkeypatch):
    # Ex.: alguém apagou dados/trabalho. A tarefa extrair_audio já está "feita" (o
    # "clipador refazer" não a aceita), então a própria transcrição extrai de novo.
    wav = caminho_da_voz(cfg, tarefa.gravacao_id)
    relogio = wav.read_bytes()
    wav.unlink()
    extraidas = []

    def extrair_de_novo(sessao_, tarefa_, cfg_, contexto=None):
        extraidas.append(tarefa_.gravacao_id)
        wav.write_bytes(relogio)

    monkeypatch.setattr(transcrever.audio, "extrair_audio", extrair_de_novo)
    transcrever.transcrever(sessao, tarefa, cfg)

    assert extraidas == [tarefa.gravacao_id]
    assert falas_salvas(transcricao_de(sessao, tarefa)) == FALAS


# ------------------------------------------------------------------ pelo serviço


def test_servico_devolve_a_tarefa_pausada_sem_contar_tentativa(cfg, sessao, tarefa, falso):
    from clipador.sistema.servico import EXECUTORES, Servico

    assert EXECUTORES[TipoTarefa.TRANSCREVER] is transcrever.transcrever
    servico = Servico(cfg, executores={TipoTarefa.TRANSCREVER: transcrever.transcrever})
    servico.iniciar()
    try:
        # A live começa no meio da transcrição (depois do 1º bloco).
        respostas = iter([False, True])
        servico.ha_gravacao_em_andamento = lambda: next(respostas, True)
        resultado = servico.passo()
    finally:
        servico.encerrar()

    assert resultado.tarefa_pausada and not resultado.tarefa_falhou
    sessao.refresh(tarefa)
    assert (tarefa.status, tarefa.tentativas) == (StatusTarefa.PENDENTE, 0)


def test_devolver_so_aceita_tarefa_em_andamento(sessao):
    pendente = fila.adicionar(sessao, TipoTarefa.PUBLICAR)
    with pytest.raises(fila.EstadoInvalidoError):
        fila.devolver(sessao, pendente)


# ------------------------------------------------------------------ legenda


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [(0, "00:00:00,000"), (5.5, "00:00:05,500"), (3725.123, "01:02:05,123"), (-1, "00:00:00,000")],
)
def test_tempo_no_formato_srt(segundos, esperado):
    assert legenda.tempo_srt(segundos) == esperado


def test_srt_numera_e_pula_frases_vazias():
    srt = legenda.gerar_srt(
        [
            {"inicio_s": 1, "fim_s": 2, "texto": "oi"},
            {"inicio_s": 3, "fim_s": 4, "texto": "  "},
            {"inicio_s": 5, "fim_s": 6, "texto": "tchau"},
        ]
    )
    assert (
        srt == "1\n00:00:01,000 --> 00:00:02,000\noi\n\n2\n00:00:05,000 --> 00:00:06,000\ntchau\n"
    )


def test_frases_montadas_das_palavras_quebram_nas_pausas():
    palavras = [
        {"palavra": "vai", "inicio_s": 1.0, "fim_s": 1.5},
        {"palavra": "entra", "inicio_s": 1.6, "fim_s": 2.0},
        {"palavra": "NÃO", "inicio_s": 3.5, "fim_s": 4.0},  # pausa de 1,5 s
    ]
    assert legenda.frases_das_palavras(palavras) == [
        {"inicio_s": 1.0, "fim_s": 2.0, "texto": "vai entra"},
        {"inicio_s": 3.5, "fim_s": 4.0, "texto": "NÃO"},
    ]


# ------------------------------------------------------------------ o Whisper (com um modelo falso)


class ModeloFalso:
    """Faz o papel do WhisperModel do faster-whisper."""

    criados: list[str] = []
    sem_memoria_ao_abrir: set[str] = set()
    sem_memoria_ao_transcrever: set[str] = set()
    ultimos_argumentos: dict = {}

    def __init__(self, tamanho, device, compute_type, download_root):
        ModeloFalso.criados.append(tamanho)
        if tamanho in ModeloFalso.sem_memoria_ao_abrir:
            raise RuntimeError("CUDA failed with error out of memory")
        self.tamanho = tamanho
        self.model = self  # o faster-whisper guarda o modelo do ctranslate2 em .model
        self.descarregado = False

        class Tokenizador:
            def encode(self, texto):
                return type("Codificado", (), {"ids": texto.split()})()

        self.hf_tokenizer = Tokenizador()

    def unload_model(self):
        self.descarregado = True

    def transcribe(self, audio, **argumentos):
        ModeloFalso.ultimos_argumentos = argumentos
        if self.tamanho in ModeloFalso.sem_memoria_ao_transcrever:
            raise RuntimeError("CUDA failed with error out of memory")
        palavra = type("W", (), {"word": " oi", "start": 0.5, "end": 0.9, "probability": 0.8})
        segmento = type("S", (), {"start": 0.5, "end": 0.9, "text": " oi", "words": [palavra]})
        return iter([segmento]), None


@pytest.fixture
def modelo_falso(monkeypatch):
    import faster_whisper

    ModeloFalso.criados = []
    ModeloFalso.sem_memoria_ao_abrir = set()
    ModeloFalso.sem_memoria_ao_transcrever = set()
    monkeypatch.setattr(faster_whisper, "WhisperModel", ModeloFalso)
    monkeypatch.setattr(whisper, "tem_placa_de_video", lambda: True)
    return ModeloFalso


def test_whisper_usa_a_placa_o_vocabulario_e_o_filtro_de_silencio(tmp_path, modelo_falso):
    transcritor = whisper.carregar(ConfigTranscricao(vocabulario=["Yasuo", " gank "]), tmp_path)
    segmentos = transcritor.transcrever(np.zeros(TAXA, dtype=np.float32))

    assert transcritor.nome == "medium (cuda, int8)"
    assert segmentos == [Segmento(0.5, 0.9, "oi", (Palavra("oi", 0.5, 0.9, 0.8),))]
    argumentos = modelo_falso.ultimos_argumentos
    assert argumentos["hotwords"] == "Yasuo, gank"
    assert argumentos["language"] == "pt"
    assert argumentos["vad_filter"] and argumentos["word_timestamps"]
    assert argumentos["condition_on_previous_text"] is False


def test_sem_memoria_ao_abrir_usa_o_modelo_reserva(tmp_path, modelo_falso):
    modelo_falso.sem_memoria_ao_abrir = {"medium"}
    transcritor = whisper.carregar(ConfigTranscricao(), tmp_path)
    assert transcritor.nome == "small (cuda, int8)"


def test_sem_memoria_no_meio_troca_para_o_reserva_e_continua(tmp_path, modelo_falso):
    modelo_falso.sem_memoria_ao_transcrever = {"medium"}
    transcritor = whisper.carregar(ConfigTranscricao(), tmp_path)
    medium = transcritor._modelo

    assert transcritor.transcrever(np.zeros(TAXA, dtype=np.float32))[0].texto == "oi"
    assert transcritor.nome == "small (cuda, int8)"
    assert medium.descarregado  # tirou o medium da placa antes de abrir o small


def test_outro_erro_da_placa_nao_troca_de_modelo(tmp_path, modelo_falso, monkeypatch):
    transcritor = whisper.carregar(ConfigTranscricao(), tmp_path)

    def quebra(audio, **argumentos):
        raise RuntimeError("Library cublas64_12.dll is not found")

    monkeypatch.setattr(transcritor._modelo, "transcribe", quebra)
    with pytest.raises(RuntimeError, match="cublas64_12"):
        transcritor.transcrever(np.zeros(TAXA, dtype=np.float32))


def test_sem_placa_de_video_usa_o_processador(tmp_path, modelo_falso, monkeypatch, caplog):
    monkeypatch.setattr(whisper, "tem_placa_de_video", lambda: False)
    transcritor = whisper.carregar(ConfigTranscricao(), tmp_path)
    assert transcritor.nome == "medium (cpu, int8)"
    assert "processador" in caplog.text


def test_fechar_tira_o_modelo_da_placa(tmp_path, modelo_falso):
    transcritor = whisper.carregar(ConfigTranscricao(), tmp_path)
    modelo = transcritor._modelo
    transcritor.fechar()
    transcritor.fechar()  # fechar duas vezes não dá erro
    assert modelo.descarregado


def test_ctranslate2_instalado_usa_cuda_12():
    # A GTX 1050 Ti (Pascal) não roda CUDA 13. Se uma atualização do ctranslate2 trocar a
    # versão do CUDA, este teste avisa antes de a transcrição quebrar de madrugada.
    import ctranslate2

    dll = Path(ctranslate2.__file__).with_name("ctranslate2.dll").read_bytes()
    assert set(re.findall(rb"cublas64_(\d+)\.dll", dll)) == {b"12"}


def test_sem_internet_na_primeira_vez_da_erro_claro(tmp_path, monkeypatch):
    # O modelo é baixado na primeira transcrição. Sem internet, a mensagem do Hugging Face
    # ("Cannot find an appropriate cached snapshot folder...") não diz nada a quem lê.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper, "tem_placa_de_video", lambda: False)
    import huggingface_hub.constants

    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_OFFLINE", True)
    with pytest.raises(whisper.ModeloNaoBaixadoError, match="sem internet"):
        whisper.carregar(ConfigTranscricao(), tmp_path / "modelos")


def test_todos_os_blocos_salvos_nao_abre_o_whisper(cfg, sessao, tarefa, monkeypatch):
    # O PC caiu depois de salvar o último bloco e antes de marcar como concluída.
    sessao.add(
        Transcricao(gravacao_id=tarefa.gravacao_id, transcrito_ate_s=DURACAO_S, concluida=False)
    )
    sessao.commit()
    monkeypatch.setattr(transcrever, "carregar_transcritor", lambda cfg: pytest.fail("abriu"))

    transcrever.transcrever(sessao, tarefa, cfg)

    assert transcricao_de(sessao, tarefa).concluida


def test_cuda_forcado_sem_placa_da_erro_claro(tmp_path, modelo_falso, monkeypatch):
    monkeypatch.setattr(whisper, "tem_placa_de_video", lambda: False)
    with pytest.raises(RuntimeError, match="nenhuma placa de vídeo NVIDIA"):
        whisper.carregar(ConfigTranscricao(dispositivo="cuda"), tmp_path)
