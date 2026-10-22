"""Etapa 10: títulos e hashtags com a IA local (testes do roteiro, com o modelo simulado)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from clipador.banco.fila import Contexto, TarefaPausada
from clipador.banco.modelos import (
    Decisao,
    Gravacao,
    Momento,
    StatusGravacao,
    Tarefa,
    TipoTarefa,
    Transcricao,
)
from clipador.config import ConfigTitulos, config_sem_arquivo
from clipador.titulos import gerador, gerar, texto
from clipador.titulos.gerador import Gerador, Pedido

# ------------------------------------------------------------------ o Ollama falso


class OllamaFalso:
    """Faz o papel do ``ollama.Client``: responde em sequência o que o teste mandar.

    Cada resposta é um dict (vira o JSON da resposta), um texto qualquer ou uma exceção.
    """

    def __init__(self, *respostas) -> None:
        self.respostas = list(respostas)
        self.pedidos: list[dict] = []
        self.descarregados: list[str] = []

    def chat(self, **pedido):
        self.pedidos.append(pedido)
        resposta = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        if isinstance(resposta, Exception):
            raise resposta
        conteudo = resposta if isinstance(resposta, str) else json.dumps(resposta)
        mensagem = type("Mensagem", (), {"content": conteudo})()
        return type("Resposta", (), {"message": mensagem})()

    def generate(self, model, keep_alive):
        assert keep_alive == 0
        self.descarregados.append(model)


BOA = {
    "titulos": [
        {"titulo": "EU NÃO ACREDITO NO QUE ESSE YASUO FEZ", "tipo": "frase_sua"},
        {"titulo": "O time desistiu. O Yasuo não.", "tipo": "exagero"},
        {"titulo": "Como ele fez isso?", "tipo": "pergunta"},
    ],
    "hashtags": ["#Yasuo", "pentakill", "#LolBR"],
}
PEDIDO = Pedido(
    falas="vai vai vai entra NÃO ACREDITO NO QUE ESSE YASUO FEZ",
    motivo="voz alta (+24 dB) + pentakill seu",
    eventos=["pentakill seu"],
    frase_marcante="NÃO ACREDITO NO QUE ESSE YASUO FEZ",
)


@pytest.fixture
def ollama(monkeypatch):
    """Instala um Ollama falso; o teste define as respostas com ``ollama(...)``."""
    falsos = []

    def instalar(*respostas):
        falso = OllamaFalso(*respostas)
        falsos.append(falso)
        monkeypatch.setattr(gerador, "criar_cliente", lambda cfg: falso)
        return falso

    return instalar


# ------------------------------------------------------------------ o pedido e a resposta


def test_pedido_leva_a_transcricao_os_eventos_e_o_estilo(ollama):
    falso = ollama(BOA)
    pedido = Pedido(
        **{**PEDIDO.__dict__, "exemplos": [{"titulo": "Eu sou o melhor", "tipo": "exagero"}]}
    )
    Gerador(ConfigTitulos()).sugerir(pedido)

    enviado = falso.pedidos[0]
    sistema, usuario = enviado["messages"]
    assert "melhores momentos #12" in sistema["content"]  # o que não fazer
    assert "EU NÃO ACREDITO NO QUE ESSE YASUO FEZ" in sistema["content"]  # exemplo do roteiro
    assert "vai vai vai entra NÃO ACREDITO" in usuario["content"]  # o que você falou
    assert "pentakill (fui eu)" in usuario["content"]  # o que aconteceu
    assert '"Eu sou o melhor" (exagero)' in usuario["content"]  # o seu estilo
    assert enviado["model"] == "qwen3:4b"
    assert enviado["format"] == gerador.ESQUEMA  # o formato JSON é exigido
    assert enviado["think"] is False  # sem o modo "raciocínio"
    assert enviado["keep_alive"] == "10m"


def test_motivo_vai_ao_modelo_em_linguagem_de_gente(ollama):
    # Mandado como está ("pentakill seu", "+24 dB"), o modelo copiava isso para o título.
    falso = ollama(BOA)
    motivo = "voz alta (+24 dB) + barão roubado seu + “meu deus” + fala acelerada (2.1x)"
    Gerador(ConfigTitulos()).sugerir(Pedido(falas="x", motivo=motivo, eventos=["sua morte"]))
    conteudo = falso.pedidos[0]["messages"][1]["content"]
    assert "O que aconteceu: eu gritei; barão roubado (fui eu); " in conteudo
    assert 'eu disse "meu deus"; eu falei rápido, empolgado' in conteudo
    assert "Eventos do jogo no clipe: eu morri" in conteudo
    assert "dB" not in conteudo and " seu" not in conteudo and "streamer" not in conteudo


@pytest.mark.parametrize(
    ("motivo", "texto_esperado"),
    [
        ("abate (sua assistência)", "abate, com a minha ajuda"),
        ("torre", "torre"),
        ("vitória", "vitória"),
        ("", ""),
    ],
)
def test_para_o_modelo(motivo, texto_esperado):
    assert gerador.para_o_modelo(motivo) == texto_esperado


def test_resposta_valida_vira_3_titulos_e_as_hashtags(ollama):
    ollama(BOA)
    sugestoes = Gerador(ConfigTitulos()).sugerir(PEDIDO)
    assert sugestoes.origem == "ollama (qwen3:4b)"
    assert [t["tipo"] for t in sugestoes.titulos] == ["frase_sua", "exagero", "pergunta"]
    # As fixas do config primeiro, depois as do modelo, normalizadas e sem repetir.
    assert sugestoes.hashtags == ["#leagueoflegends", "#lolbr", "#yasuo", "#pentakill"]


def test_tipo_repetido_no_titulo_e_hashtag_do_pedido_sao_limpos(ollama):
    ollama({
        "titulos": [{"titulo": "CARAMBA, É REAL? (frase_sua)", "tipo": "frase_sua"}],
        "hashtags": ["#loleventos", "#duploabate", "#streamer"],
    })  # fmt: skip
    sugestoes = Gerador(ConfigTitulos()).sugerir(PEDIDO)
    assert sugestoes.titulos[0]["titulo"] == "CARAMBA, É REAL?"
    assert sugestoes.hashtags == ["#leagueoflegends", "#lolbr", "#duploabate"]


def test_instrucoes_pedem_a_primeira_pessoa():
    assert "1ª pessoa" in gerador.INSTRUCOES
    assert 'Nunca escreva "o streamer"' in gerador.INSTRUCOES


def test_titulo_acima_de_100_caracteres_e_cortado(ollama):
    longo = "Eu não acredito " * 12
    ollama({"titulos": [{"titulo": longo, "tipo": "frase_sua"}], "hashtags": []})
    titulo = Gerador(ConfigTitulos()).sugerir(PEDIDO).titulos[0]["titulo"]
    assert len(titulo) <= 100 and titulo.endswith("…") and not titulo.endswith(" …")


def test_resposta_invalida_uma_vez_e_tentada_de_novo(ollama):
    falso = ollama("isto não é json", BOA)
    sugestoes = Gerador(ConfigTitulos()).sugerir(PEDIDO)
    assert sugestoes.origem.startswith("ollama") and len(falso.pedidos) == 2


def test_resposta_invalida_duas_vezes_cai_no_titulo_simples(ollama):
    ollama({"titulos": [{"titulo": "x", "tipo": "piada"}]})  # tipo fora da lista
    sugestoes = Gerador(ConfigTitulos()).sugerir(PEDIDO)
    assert sugestoes.origem == "simples"


def test_ollama_fechado_cai_no_titulo_simples_e_nao_tenta_mais(ollama):
    falso = ollama(ConnectionError("Failed to connect to Ollama"))
    gera = Gerador(ConfigTitulos())
    primeira = gera.sugerir(PEDIDO)
    gera.sugerir(PEDIDO)  # o próximo momento nem tenta (já sabe que o Ollama falhou)

    assert primeira.origem == "simples"
    assert primeira.titulos == [
        {"titulo": "NÃO ACREDITO NO QUE ESSE YASUO FEZ", "tipo": "frase_sua"},
        {"titulo": "Pentakill", "tipo": "descricao"},
    ]
    assert primeira.hashtags == ["#leagueoflegends", "#lolbr", "#pentakill"]  # 3 a 5
    assert len(falso.pedidos) == 1


@pytest.mark.parametrize(
    ("motivo", "esperado"),
    [
        ("voz alta (+24 dB) + barão roubado seu + “meu deus”", "Barão roubado"),
        ("voz alta (+20 dB) + fala acelerada (2.5x) + sua morte", "Morri"),
        ("“caramba” + abate duplo seu", "Abate duplo"),
        ("voz alta (+20 dB) + fala acelerada (2.2x)", None),  # sem evento de jogo
        ("dragão (sua assistência)", "Dragão"),
    ],
)
def test_titulo_simples_usa_so_o_evento_de_jogo(motivo, esperado):
    assert gerador.evento_em_primeira_pessoa(motivo) == esperado
    sugestoes = gerador.simples(
        Pedido(falas="", motivo=motivo, frase_marcante="Oi"), ConfigTitulos()
    )
    descricoes = [t["titulo"] for t in sugestoes.titulos if t["tipo"] == "descricao"]
    assert descricoes == ([esperado] if esperado else [])


def test_sem_memoria_troca_para_o_modelo_reserva(ollama):
    falso = ollama(RuntimeError("CUDA error: out of memory"), BOA)
    gera = Gerador(ConfigTitulos())
    assert gera.sugerir(PEDIDO).origem == "ollama (qwen3:1.7b)"
    assert [p["model"] for p in falso.pedidos] == ["qwen3:4b", "qwen3:1.7b"]
    gera.fechar()
    assert falso.descarregados == ["qwen3:1.7b"]


def test_fechar_descarrega_o_modelo_mesmo_depois_de_falhar(ollama):
    falso = ollama("lixo")
    gera = Gerador(ConfigTitulos())
    gera.sugerir(PEDIDO)  # duas respostas inválidas: plano B
    gera.fechar()
    assert falso.descarregados == ["qwen3:4b"]  # o modelo não fica 10 min na placa


def test_fonte_simples_nem_chama_o_ollama(ollama):
    falso = ollama(BOA)
    assert Gerador(ConfigTitulos(fonte="simples")).sugerir(PEDIDO).origem == "simples"
    assert falso.pedidos == []


# ------------------------------------------------------------------ regras de texto


def test_cortar_titulo():
    assert texto.cortar_titulo("  curto\n demais ") == "curto demais"
    cortado = texto.cortar_titulo("palavra " * 30, limite=20)
    assert cortado == "palavra palavra…" and len(cortado) <= 20


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [("Pentakill!", "#pentakill"), ("#Barão Roubado", "#barãoroubado"), ("###", None), ("", None)],
)
def test_normalizar_hashtag(entrada, saida):
    assert texto.normalizar_hashtag(entrada) == saida


def test_juntar_hashtags_respeita_o_maximo_e_nao_repete():
    juntas = texto.juntar_hashtags(["#lolbr", "#LoL"], ["lol", "#yasuo", "#a", "#b", "#c"], 4)
    assert juntas == ["#lolbr", "#lol", "#yasuo", "#a"]


def palavras_de(*falas):
    palavras = []
    for inicio, fim, frase in falas:
        partes = frase.split()
        passo = (fim - inicio) / len(partes)
        for i, parte in enumerate(partes):
            palavras.append({"palavra": parte, "inicio_s": inicio + i * passo,
                             "fim_s": inicio + (i + 1) * passo})  # fmt: skip
    return palavras


def test_frase_marcante_e_a_dita_perto_do_pico():
    palavras = palavras_de((10, 12, "bora pessoal"), (20, 23, "EU JURO QUE APERTEI O FLASH"),
                           (27, 28, "enfim"))  # fmt: skip
    assert texto.frase_marcante(palavras, 5, 30, pico_s=21) == "EU JURO QUE APERTEI O FLASH"
    assert texto.frase_marcante(palavras, 5, 30, pico_s=27.5) == "Enfim"
    assert texto.frase_marcante(palavras, 40, 50, pico_s=45) is None  # sem fala no trecho


# ------------------------------------------------------------------ executor


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "dados")


@pytest.fixture
def tarefa(sessao) -> Tarefa:
    gravacao = Gravacao(arquivo="C:/g/live.mkv", inicio=datetime(2026, 10, 14, 23, 0, tzinfo=UTC))
    sessao.add(gravacao)
    sessao.flush()
    for inicio, nota in ((10, 90), (100, 70)):
        sessao.add(Momento(gravacao_id=gravacao.id, inicio_s=inicio, fim_s=inicio + 20,
                           pico_s=inicio + 15, nota=nota, motivo="voz alta (+20 dB)"))  # fmt: skip
    sessao.add(Transcricao(gravacao_id=gravacao.id, palavras=palavras_de((24, 26, "NÃO ACREDITO"))))
    tarefa = Tarefa(tipo=TipoTarefa.GERAR_TITULOS, gravacao_id=gravacao.id)
    sessao.add(tarefa)
    sessao.commit()
    return tarefa


def momentos(sessao) -> list[Momento]:
    return list(sessao.exec(select(Momento).order_by(Momento.id)).all())


def test_executor_salva_titulos_e_hashtags_de_cada_momento(cfg, sessao, tarefa, ollama):
    falso = ollama(BOA)
    gerar.gerar_titulos(sessao, tarefa, cfg)

    lista = momentos(sessao)
    assert all(len(m.titulos_sugeridos) == 3 and m.hashtags_sugeridas for m in lista)
    assert "NÃO ACREDITO" in falso.pedidos[0]["messages"][1]["content"]  # a fala do 1º momento
    assert falso.descarregados == ["qwen3:4b"]  # tirou o modelo da placa no fim


def test_executor_aprende_com_os_titulos_que_voce_escolheu(cfg, sessao, tarefa, ollama):
    agora = datetime.now(UTC)
    for minutos, titulo, aprovado in ((3, "Antigo", True), (1, "O mais novo", True),
                                       (0, "Recusado", False)):  # fmt: skip
        sessao.add(Momento(gravacao_id=1, inicio_s=500 + minutos, fim_s=520, nota=1,
                           titulos_sugeridos=[{"titulo": "x", "tipo": "descricao"}]))  # fmt: skip
        sessao.flush()
        momento = sessao.exec(select(Momento).order_by(Momento.id.desc())).first()
        quando = agora - timedelta(minutes=minutos)
        sessao.add(Decisao(momento_id=momento.id, aprovado=aprovado, titulo=titulo,
                           tipo_titulo="exagero", decidida_em=quando))  # fmt: skip
    sessao.commit()
    falso = ollama(BOA)
    cfg.titulos.exemplos = 1

    gerar.gerar_titulos(sessao, tarefa, cfg)

    conteudo = falso.pedidos[0]["messages"][1]["content"]
    assert '"O mais novo" (exagero)' in conteudo  # o mais recente que você aprovou
    assert "Antigo" not in conteudo and "Recusado" not in conteudo


def test_executor_e_retomavel(cfg, sessao, tarefa, ollama):
    primeiro = sessao.get(Momento, 1)
    primeiro.titulos_sugeridos = [{"titulo": "Já tinha", "tipo": "descricao"}]
    sessao.add(primeiro)
    sessao.commit()
    falso = ollama(BOA)

    gerar.gerar_titulos(sessao, tarefa, cfg)

    assert len(falso.pedidos) == 1  # só o momento que faltava
    assert sessao.get(Momento, 1).titulos_sugeridos[0]["titulo"] == "Já tinha"


def test_executor_pausa_entre_momentos_se_comecar_uma_live(cfg, sessao, tarefa, ollama):
    falso = ollama(BOA)
    respostas = iter([False, True])
    with pytest.raises(TarefaPausada):
        gerar.gerar_titulos(sessao, tarefa, cfg, Contexto(deve_pausar=lambda: next(respostas)))
    assert [bool(m.titulos_sugeridos) for m in momentos(sessao)] == [True, False]
    assert falso.descarregados  # pausou, mas tirou o modelo da placa


def test_sem_ollama_a_cadeia_termina_com_titulo_simples(cfg, sessao, tarefa):
    # O conftest deixa o Ollama "fechado" em todos os testes.
    from clipador.sistema.servico import EXECUTORES, Servico

    assert EXECUTORES[TipoTarefa.GERAR_TITULOS] is gerar.gerar_titulos
    servico = Servico(cfg, executores={TipoTarefa.GERAR_TITULOS: gerar.gerar_titulos})
    servico.iniciar()
    try:
        assert servico.passo().tarefa_executada == "gerar_titulos"
    finally:
        servico.encerrar()

    sessao.expire_all()
    assert momentos(sessao)[0].titulos_sugeridos[0] == {
        "titulo": "NÃO ACREDITO",
        "tipo": "frase_sua",
    }
    assert sessao.get(Gravacao, 1).status is StatusGravacao.CONCLUIDA  # a última da cadeia


def test_demo_usa_os_titulos_prontos(tmp_path, sessao):
    from clipador.demo import roteiro
    from clipador.demo.gerar import config_da_demo

    cfg = config_da_demo(tmp_path / "dados")
    momento = Momento(gravacao_id=1, inicio_s=20, fim_s=35, nota=90)
    sugestoes = gerar.titulos_da_demo(momento, cfg)
    assert sugestoes.titulos == [dict(t) for t in roteiro.TITULOS[0]]  # o pico de 0:25
    assert cfg.titulos.fonte == "demo"


def test_pedido_leva_so_os_5_eventos_mais_fortes():
    from clipador.banco.modelos import EventoJogo, PapelNoEvento
    from clipador.config import ConfigLolDetector
    from clipador.detectores.lol import Lol

    inicio = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)

    def evento(tipo, papel=None, **dados):
        return EventoJogo(tipo=tipo, horario_real=inicio, tempo_jogo_s=0, partida_inicio=inicio,
                          event_id=1, papel=papel, dados=dados)  # fmt: skip

    luta = [(evento("ChampionKill"), s) for s in range(20)]  # 20 abates de outros
    luta.append((evento("Multikill", PapelNoEvento.ABATE, KillStreak=5), 21))
    nomes = gerar.eventos_mais_fortes(Lol(ConfigLolDetector()), None, luta)
    assert nomes == ["pentakill seu", "abate"]  # o mais forte primeiro, sem repetir
