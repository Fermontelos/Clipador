"""Etapa 7: coletor do LoL e alinhamento de tempo (testes obrigatórios do roteiro).

O jogo é trocado por um falso, que responde como a Live Client Data API responderia num
certo instante. Nada aqui precisa do LoL aberto.
"""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from clipador.banco.conexao import nova_sessao
from clipador.banco.modelos import EventoJogo, Gravacao, PapelNoEvento
from clipador.ingestao.nomes import inicio_pelo_nome
from clipador.jogo import alinhamento, coletor, eventos
from clipador.jogo.coletor import ColetorLol
from clipador_integracoes.lol import Leitura

EU = "Riot Tuxedo#TXC1"  # como o activeplayername responde (Riot ID com #TAG)


def evento(event_id: int, tempo: float, nome: str = "ChampionKill", **campos) -> dict:
    return {"EventID": event_id, "EventName": nome, "EventTime": tempo, **campos}


class JogoFalso:
    """Faz o papel do ClienteLol: responde o estado de uma partida falsa."""

    def __init__(self) -> None:
        self.partida_inicio: datetime | None = None  # None = jogo fechado
        self.tempo_jogo_s = 0.0
        self.eventos: list[dict] = []
        self.atraso_s = 0.0  # quanto a consulta "demorou" (ruído no horário do PC)
        self.pedidos_de_nome = 0
        self.fechado = False

    def comecar(self, inicio: datetime) -> None:
        self.partida_inicio, self.tempo_jogo_s, self.eventos = inicio, 0.0, []

    def ler(self, com_nome: bool = False) -> Leitura | None:
        if self.partida_inicio is None:
            return None
        self.pedidos_de_nome += com_nome
        horario = self.partida_inicio + timedelta(seconds=self.tempo_jogo_s + self.atraso_s)
        return Leitura(
            horario_pc=horario.timestamp(),
            tempo_jogo_s=self.tempo_jogo_s,
            eventos=list(self.eventos),
            nome_jogador=EU if com_nome else None,
        )

    def fechar(self) -> None:
        self.fechado = True


@pytest.fixture
def jogo() -> JogoFalso:
    return JogoFalso()


@pytest.fixture
def coleta(engine, jogo) -> ColetorLol:
    return ColetorLol(engine, cliente=jogo)


def avancar(coleta: ColetorLol, jogo: JogoFalso, ate_s: float, passo_s: float = 2.0) -> None:
    """Joga a partida falsa até ``ate_s``, com o coletor consultando a cada ``passo_s``."""
    while jogo.tempo_jogo_s < ate_s:
        jogo.tempo_jogo_s = min(jogo.tempo_jogo_s + passo_s, ate_s)
        coleta.passo()


def salvos(engine) -> list[EventoJogo]:
    with nova_sessao(engine) as sessao:
        return list(sessao.exec(select(EventoJogo).order_by(EventoJogo.horario_real)).all())


def gravacao(sessao, inicio: datetime, duracao_s: float | None = 3600) -> Gravacao:
    registro = Gravacao(
        arquivo=f"C:/g/{inicio.timestamp()}.mkv", inicio=inicio, duracao_s=duracao_s
    )
    sessao.add(registro)
    sessao.commit()
    return registro


def segundos(sessao, registro: Gravacao) -> list[tuple[str, float]]:
    return [(e.tipo, round(s, 2)) for e, s in alinhamento.eventos_da_gravacao(sessao, registro)]


# ------------------------------------------------------------------ alinhamento


def test_inicio_da_partida_pela_mediana_das_amostras():
    inicio = datetime(2026, 10, 14, 23, 5, 30, tzinfo=UTC).timestamp()
    amostras = [(inicio + t, t) for t in (10.0, 12.0, 14.0, 16.0)]
    amostras.append((inicio + 18.0 + 0.9, 18.0))  # uma consulta que demorou 0,9 s
    assert alinhamento.estimar_inicio(amostras).timestamp() == pytest.approx(inicio)


def test_partida_que_comeca_depois_da_gravacao(engine, sessao, jogo, coleta):
    # A gravação começa às 23:00; a partida, às 23:05:30 (5 min de fila e seleção).
    registro = gravacao(sessao, datetime(2026, 10, 14, 23, 0, tzinfo=UTC))
    jogo.comecar(datetime(2026, 10, 14, 23, 5, 30, tzinfo=UTC))
    avancar(coleta, jogo, 58)
    jogo.eventos = [evento(1, 60.0, KillerName="Riot Tuxedo", VictimName="Inimigo")]
    avancar(coleta, jogo, 62)

    # 5 min 30 s até a partida + 60 s de jogo = 390 s no vídeo.
    assert segundos(sessao, registro) == [("ChampionKill", 390.0)]


def test_gravacao_que_passa_da_meia_noite(engine, sessao, jogo, coleta):
    # O OBS nomeia o arquivo no horário do PC: começou às 23:50 do dia 14.
    inicio = inicio_pelo_nome(Path("2026-10-14 23-50-00.mkv"))
    registro = gravacao(sessao, inicio)
    # A partida começa à 00:05 do dia 15 e o abate sai aos 5 min de jogo (00:10).
    jogo.comecar(datetime(2026, 10, 15, 0, 5).astimezone(UTC))
    avancar(coleta, jogo, 298)
    jogo.eventos = [evento(1, 300.0, KillerName="Riot Tuxedo")]
    avancar(coleta, jogo, 302)

    assert segundos(sessao, registro) == [("ChampionKill", 1200.0)]  # 20 min no vídeo


def test_evento_fora_do_intervalo_da_gravacao_fica_de_fora(engine, sessao, jogo, coleta):
    # Gravação das 23:00 às 23:10; a partida começa às 22:58.
    registro = gravacao(sessao, datetime(2026, 10, 14, 23, 0, tzinfo=UTC), duracao_s=600)
    jogo.comecar(datetime(2026, 10, 14, 22, 58, tzinfo=UTC))
    jogo.eventos = [
        evento(1, 60.0),  # 22:59: antes de a gravação começar
        evento(2, 420.0),  # 23:05: dentro
        evento(3, 780.0),  # 23:11: depois de a gravação terminar
    ]
    avancar(coleta, jogo, 800)

    assert len(salvos(engine)) == 3  # o coletor salva todos...
    assert segundos(sessao, registro) == [("ChampionKill", 300.0)]  # ...só um é do vídeo


def test_varias_partidas_na_mesma_live(engine, sessao, jogo, coleta):
    registro = gravacao(sessao, datetime(2026, 10, 14, 23, 0, tzinfo=UTC), duracao_s=3 * 3600)
    # 1ª partida às 23:02. O EventID recomeça do zero a cada partida.
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [evento(1, 100.0), evento(2, 200.0, "BaronKill")]
    avancar(coleta, jogo, 1800)
    jogo.partida_inicio = None  # a partida acabou: o jogo fechou
    coleta.passo()
    # 2ª partida às 23:40, com os mesmos EventIDs.
    jogo.comecar(datetime(2026, 10, 14, 23, 40, tzinfo=UTC))
    jogo.eventos = [evento(1, 50.0), evento(2, 90.0, "DragonKill")]
    avancar(coleta, jogo, 120)

    assert segundos(sessao, registro) == [
        ("ChampionKill", 220.0),
        ("BaronKill", 320.0),
        ("ChampionKill", 2450.0),
        ("DragonKill", 2490.0),
    ]
    assert len({e.partida_inicio for e in salvos(engine)}) == 2


def test_outra_partida_sem_o_jogo_fechar_no_meio(engine, jogo, coleta):
    # Ex.: o jogo seguinte começou antes de uma consulta pegar o jogo fechado.
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [evento(1, 100.0)]
    avancar(coleta, jogo, 1500)
    jogo.comecar(datetime(2026, 10, 14, 23, 30, tzinfo=UTC))  # o tempo de jogo volta a 0
    jogo.eventos = [evento(1, 30.0)]
    avancar(coleta, jogo, 40)

    assert len(salvos(engine)) == 2


def test_evento_repetido_nao_e_duplicado(engine, jogo, coleta):
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [evento(1, 10.0), evento(2, 20.0)]
    avancar(coleta, jogo, 300)  # a API devolve a lista inteira a cada consulta
    assert len(salvos(engine)) == 2


def test_app_reaberto_no_meio_da_partida_continua_sem_duplicar(engine, jogo, coleta):
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [evento(1, 10.0), evento(2, 20.0)]
    avancar(coleta, jogo, 30)
    # O PC reiniciou: um coletor novo, cuja conta do início sai 0,3 s diferente.
    jogo.atraso_s = 0.3
    jogo.eventos.append(evento(3, 40.0))
    reaberto = ColetorLol(engine, cliente=jogo)
    avancar(reaberto, jogo, 60)

    lista = salvos(engine)
    assert [e.event_id for e in lista] == [1, 2, 3]
    assert len({e.partida_inicio for e in lista}) == 1  # continuou na mesma partida


def test_banco_recusa_o_mesmo_evento_duas_vezes(sessao):
    inicio = datetime(2026, 10, 14, 23, 2, tzinfo=UTC)
    for _ in range(2):
        sessao.add(
            EventoJogo(tipo="ChampionKill", horario_real=inicio, tempo_jogo_s=0,
                       partida_inicio=inicio, event_id=1)
        )  # fmt: skip
    with pytest.raises(IntegrityError):
        sessao.commit()


# ------------------------------------------------------------------ evento seu ou não


@pytest.mark.parametrize("meu_nome", ["Riot Tuxedo#TXC1", "Riot Tuxedo", "riot tuxedo#outra"])
def test_evento_seu_pelo_nome_com_e_sem_tag(meu_nome):
    assert eventos.papel({"KillerName": "Riot Tuxedo"}, meu_nome) is PapelNoEvento.ABATE
    assert eventos.papel({"KillerName": "Riot Tuxedo#TXC1"}, meu_nome) is PapelNoEvento.ABATE


def test_papel_de_cada_tipo_de_evento():
    papel = eventos.papel
    assert papel({"KillerName": "X", "VictimName": "Riot Tuxedo"}, EU) is PapelNoEvento.MORTE
    assert papel({"KillerName": "X", "Assisters": ["Y", "Riot Tuxedo"]}, EU) is (
        PapelNoEvento.ASSISTENCIA
    )
    assert papel({"Acer": "Riot Tuxedo", "AcingTeam": "ORDER"}, EU) is PapelNoEvento.ABATE
    assert papel({"Recipient": "Riot Tuxedo"}, EU) is PapelNoEvento.ABATE  # FirstBlood
    assert papel({"KillerName": "Outro", "VictimName": "Mais um"}, EU) is None
    assert papel({"KillerName": "Turret_T1_L_03_A", "VictimName": "X"}, EU) is None
    assert papel({"KillerName": "Riot Tuxedo"}, None) is None  # sem saber o seu nome
    assert papel({"KillerName": ""}, "") is None


def test_coletor_salva_o_papel_e_os_dados_do_evento(engine, jogo, coleta):
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [
        evento(1, 10.0, KillerName="Riot Tuxedo", VictimName="Inimigo", Assisters=[]),
        evento(2, 20.0, "DragonKill", KillerName="Aliado", Assisters=["Riot Tuxedo"],
               DragonType="Earth", Stolen="False"),
        evento(3, 30.0, KillerName="Inimigo", VictimName="Riot Tuxedo"),
    ]  # fmt: skip
    avancar(coleta, jogo, 40)

    lista = salvos(engine)
    assert [e.papel for e in lista] == [
        PapelNoEvento.ABATE,
        PapelNoEvento.ASSISTENCIA,
        PapelNoEvento.MORTE,
    ]
    assert lista[1].dados["DragonType"] == "Earth"
    assert jogo.pedidos_de_nome == 1  # o nome é pedido uma vez por partida


def test_so_guarda_os_tipos_que_interessam(engine, jogo, coleta):
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [
        evento(0, 0.0, "GameStart"),
        evento(1, 65.0, "MinionsSpawning"),
        evento(2, 300.0, "FirstBrick", KillerName="Riot Tuxedo"),
        evento(3, 310.0, "TurretKilled", KillerName="Riot Tuxedo", TurretKilled="Turret_T2"),
        evento(4, 1800.0, "GameEnd", Result="Win"),
    ]
    avancar(coleta, jogo, 1810)
    assert [e.tipo for e in salvos(engine)] == ["TurretKilled", "GameEnd"]


# ------------------------------------------------------------------ comportamento do coletor


def test_sem_partida_so_espera_sem_encher_o_log(jogo, coleta, caplog):
    with caplog.at_level(logging.DEBUG, logger="clipador.jogo"):
        for _ in range(20):
            assert coleta.passo() == []
    assert caplog.records == []


def test_avisa_uma_vez_quando_a_partida_comeca_e_termina(jogo, coleta, caplog):
    with caplog.at_level(logging.INFO, logger="clipador.jogo"):
        jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
        avancar(coleta, jogo, 60)
        jogo.partida_inicio = None
        for _ in range(5):
            coleta.passo()
    mensagens = [registro.getMessage() for registro in caplog.records]
    assert sum("Partida detectada" in m for m in mensagens) == 1
    assert sum("Partida encerrada" in m for m in mensagens) == 1


def test_tempo_de_jogo_zerado_e_ignorado(engine, jogo, coleta):
    # Terminando de carregar, o relógio do jogo ainda está em 0: o início sairia errado.
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.eventos = [evento(1, 0.0, "TurretKilled")]
    coleta.passo()
    assert not coleta.em_partida and salvos(engine) == []


def test_rodar_registra_um_erro_repetido_uma_vez_e_para_quando_pedido(engine, caplog):
    class JogoQuebrado(JogoFalso):
        def ler(self, com_nome=False):
            raise RuntimeError("API mudou de formato")

    quebrado = JogoQuebrado()
    parar = threading.Event()
    with caplog.at_level(logging.ERROR, logger="clipador.jogo"):
        thread = threading.Thread(
            target=ColetorLol(engine, cliente=quebrado).rodar, args=(parar, 0.01)
        )
        thread.start()
        time.sleep(0.3)
        parar.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(caplog.records) == 1  # e não um a cada consulta
    assert quebrado.fechado


def test_servico_liga_o_coletor_so_se_configurado(tmp_path):
    from clipador.config import ConfigLol, config_sem_arquivo
    from clipador.sistema.servico import Servico

    for ligado in (True, False):
        cfg = config_sem_arquivo(
            pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / f"d{ligado}",
            lol=ConfigLol(coletar=ligado),
        )  # fmt: skip
        parar = threading.Event()
        thread = threading.Thread(
            target=Servico(cfg, executores={}).rodar, args=(parar, timedelta(seconds=1))
        )
        thread.start()
        try:
            limite = time.monotonic() + 10
            while time.monotonic() < limite and not any(
                t.name == "coletor-lol" for t in threading.enumerate()
            ):
                time.sleep(0.05)
            ligou = any(t.name == "coletor-lol" for t in threading.enumerate())
        finally:
            parar.set()
            thread.join(timeout=10)
        assert ligou is ligado
        assert not any(t.name == "coletor-lol" for t in threading.enumerate())  # parou junto


def test_intervalo_de_consulta_e_de_2_segundos():
    assert coletor.INTERVALO_S == 2.0


def test_sem_a_duracao_da_gravacao_vale_tudo_depois_do_inicio(engine, sessao, jogo, coleta):
    # Antes da etapa 5 medir a duração, não há como saber onde a gravação termina.
    registro = gravacao(sessao, datetime(2026, 10, 14, 23, 0, tzinfo=UTC), duracao_s=None)
    jogo.comecar(datetime(2026, 10, 14, 22, 59, tzinfo=UTC))
    jogo.eventos = [evento(1, 30.0), evento(2, 5 * 3600.0)]  # antes do início; 5 h depois
    avancar(coleta, jogo, 5 * 3600 + 10, passo_s=600)
    assert segundos(sessao, registro) == [("ChampionKill", 5 * 3600.0 - 60)]


def test_comando_coletar_lol_mostra_a_partida_e_os_eventos(config_valida, monkeypatch):
    from typer.testing import CliRunner

    import clipador.cli as cli

    jogo = JogoFalso()
    jogo.comecar(datetime(2026, 10, 14, 23, 2, tzinfo=UTC))
    jogo.tempo_jogo_s = 100.0
    jogo.eventos = [evento(1, 95.0, KillerName="Riot Tuxedo", VictimName="X")]
    voltas = 0  # quantas esperas de 2 s já passaram

    class ColetorDeTeste(ColetorLol):
        def passo(self):
            if voltas == 1:
                raise RuntimeError("banco ocupado")  # a 2ª consulta falha
            return super().passo()

    def dormir(segundos):
        nonlocal voltas
        voltas += 1
        if voltas == 2:
            raise KeyboardInterrupt  # o Ctrl+C, depois da consulta com erro

    monkeypatch.setattr(cli, "ColetorLol", lambda engine: ColetorDeTeste(engine, cliente=jogo))
    monkeypatch.setattr(cli.time, "sleep", dormir)

    saida = CliRunner().invoke(cli.app, ["coletar-lol", "--arquivo", str(config_valida)])

    assert saida.exit_code == 0, saida.output
    assert "Partida detectada." in saida.output
    assert "1:35 de jogo" in saida.output and "ChampionKill (seu: abate)" in saida.output
    assert "Erro numa consulta (continuando): banco ocupado" in saida.output
    assert "Coleta encerrada." in saida.output
    assert jogo.fechado
