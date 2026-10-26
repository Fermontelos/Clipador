"""Etapa 14: aprender com as decisões (métricas, pesos, janela e o aceite)."""

import random
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from clipador.aprendizado import janela, metricas, pesos, sugestao
from clipador.aprendizado.metricas import Exemplo
from clipador.banco.modelos import Decisao, Gravacao, Momento, PesosNota, StatusGravacao
from clipador.config import ConfigDeteccao, config_sem_arquivo
from clipador.detectores import nota

ATUAIS = {"fala": 0.25, "lol": 0.25, "volume_voz": 0.5}  # os pesos padrão do config
DIA = date(2026, 10, 20)
QUANDO = datetime(2026, 10, 20, 23, tzinfo=UTC)


def exemplo(id_, forcas, aprovado, dia=DIA, inicio=None, fim=None, quando=QUANDO) -> Exemplo:
    return Exemplo(id_, dia, forcas, aprovado, inicio, fim, quando)


def decisoes_onde_a_fala_decide(n=200, semente=1) -> list[Exemplo]:
    """A fala decide (aprova quando ela é forte); o volume e o LoL são ruído."""
    sorteio = random.Random(semente)
    lista = []
    for i in range(n):
        fala = sorteio.random()
        forcas = {"fala": fala, "volume_voz": sorteio.random(), "lol": sorteio.random()}
        dia = DIA + timedelta(days=i // 20)  # 20 decisões por dia, como na revisão
        lista.append(exemplo(i, forcas, aprovado=fala > 0.5, dia=dia))
    return lista


# ------------------------------------------------------------------ métricas


def test_nota_e_a_mesma_conta_do_detector():
    # 100 × média ponderada só dos detectores disponíveis (as chaves de forcas).
    assert metricas.nota({"volume_voz": 1.0, "fala": 0.0}, ATUAIS) == pytest.approx(
        100 * 0.5 / 0.75
    )
    assert metricas.nota({}, ATUAIS) == 0
    assert metricas.nota({"novo": 1.0}, ATUAIS) == 0  # detector sem peso


def test_precisao_no_topo_olha_a_metade_de_cima_de_cada_dia():
    lista = [
        # Dia 1: as 2 de maior nota (de 4) foram aprovadas: 2 de 2.
        exemplo(1, {"volume_voz": 0.9}, True),
        exemplo(2, {"volume_voz": 0.8}, True),
        exemplo(3, {"volume_voz": 0.2}, False),
        exemplo(4, {"volume_voz": 0.1}, True),
        # Dia 2: 3 decisões, olha as 2 de cima (arredonda para cima): 1 de 2.
        exemplo(5, {"volume_voz": 0.9}, False, dia=DIA + timedelta(days=1)),
        exemplo(6, {"volume_voz": 0.5}, True, dia=DIA + timedelta(days=1)),
        exemplo(7, {"volume_voz": 0.1}, True, dia=DIA + timedelta(days=1)),
    ]
    assert metricas.acertos_no_topo(lista, ATUAIS) == (3, 4)
    assert metricas.precisao_no_topo(lista, ATUAIS) == 0.75
    assert metricas.precisao_no_topo([], ATUAIS) is None


def test_precisao_por_dia():
    lista = [
        exemplo(1, {}, True),
        exemplo(2, {}, False),
        exemplo(3, {}, True, dia=DIA + timedelta(days=2)),
    ]
    assert metricas.precisao_por_dia(lista) == [
        {"dia": "2026-10-20", "decididos": 2, "aprovados": 1, "precisao": 0.5},
        {"dia": "2026-10-22", "decididos": 1, "aprovados": 1, "precisao": 1.0},
    ]


# ------------------------------------------------------------------ pesos


def test_treino_se_recusa_a_rodar_com_menos_de_100_decisoes():
    lista = decisoes_onde_a_fala_decide(n=99)
    with pytest.raises(pesos.PoucasDecisoesError, match="99 decisões.*pelo menos 100"):
        pesos.treinar(lista, ["fala", "lol", "volume_voz"], ATUAIS)


def test_treino_precisa_de_aprovacoes_e_recusas():
    lista = [exemplo(i, {"fala": 0.9}, True) for i in range(150)]
    with pytest.raises(pesos.PoucasDecisoesError, match="aprovações e 5 recusas"):
        pesos.treinar(lista, ["fala"], ATUAIS)


def test_o_sinal_que_leva_a_aprovacao_ganha_mais_peso():
    lista = decisoes_onde_a_fala_decide()
    novos = pesos.treinar(lista, metricas.detectores(lista), ATUAIS)
    assert novos["fala"] > 0.8  # quase todo o peso
    assert novos["fala"] > novos["volume_voz"] and novos["fala"] > novos["lol"]
    assert sum(novos.values()) == pytest.approx(1.0, abs=0.01)  # a mesma soma de antes


def test_sinal_que_atrapalha_fica_com_peso_zero():
    sorteio = random.Random(2)
    lista = []
    for i in range(200):
        fala, lol = sorteio.random(), sorteio.random()
        # Aprova com fala forte e recusa quando o LoL é forte (ex.: só jogo, sem reação).
        lista.append(exemplo(i, {"fala": fala, "lol": lol}, aprovado=fala - lol > 0))
    novos = pesos.treinar(lista, ["fala", "lol"], ATUAIS)
    assert novos["lol"] == 0
    assert novos["fala"] == pytest.approx(0.5)  # fala + lol somavam 0,5
    assert novos["volume_voz"] == 0.5  # fora das decisões: fica como estava


def test_nenhum_sinal_ajuda():
    sorteio = random.Random(3)
    lista = [exemplo(i, {"fala": (f := sorteio.random())}, aprovado=f < 0.5) for i in range(200)]
    with pytest.raises(pesos.SemSinalUtilError):
        pesos.treinar(lista, ["fala"], ATUAIS)


def test_validacao_cruzada_compara_em_decisoes_nao_vistas():
    lista = decisoes_onde_a_fala_decide()
    ruins = {"fala": 0.0, "lol": 0.5, "volume_voz": 0.5}  # pesos que ignoram a fala
    antes, depois = pesos.validacao_cruzada(lista, metricas.detectores(lista), ruins)
    assert antes == pytest.approx(0.5, abs=0.15)  # ordenar por ruído: metade acerta
    # O máximo possível: quem já soubesse que é a fala. Não é 100%, porque num dia em que
    # você aprovou 1 de 4, a metade de cima (2) acerta no máximo 1 (ver metricas).
    sabendo = {"fala": 1.0, "lol": 0.0, "volume_voz": 0.0}
    teto, _ = pesos.validacao_cruzada(lista, metricas.detectores(lista), sabendo)
    assert depois == pytest.approx(teto, abs=0.03)  # aprendendo com 4 partes, chega lá
    assert depois - antes > 0.25
    # Sempre o mesmo resultado (as partes não mudam a cada vez).
    assert pesos.validacao_cruzada(lista, metricas.detectores(lista), ruins) == (antes, depois)


# ------------------------------------------------------------------ janela


def aprovados_com_ajuste(n, inicio, fim, quando=QUANDO):
    return [exemplo(i, {}, True, inicio=inicio, fim=fim, quando=quando) for i in range(n)]


def test_janela_segue_a_media_dos_ajustes():
    # Você sempre adianta o início em 3 s e estica o fim em 2 s: a janela segue.
    sugerida = janela.sugerir(aprovados_com_ajuste(20, -3, 2), 10, 5, desde=None)
    assert (sugerida.segundos_antes, sugerida.segundos_depois) == (13, 7)
    assert (sugerida.media_inicio_s, sugerida.media_fim_s, sugerida.aprovados) == (-3, 2, 20)


def test_aprovado_sem_ajuste_conta_zero_e_recusado_nao_conta():
    lista = aprovados_com_ajuste(10, -4, None) + aprovados_com_ajuste(10, None, None)
    lista += [exemplo(99, {}, False, inicio=-30, fim=30)]  # recusado: ignorado
    sugerida = janela.sugerir(lista, 10, 5, desde=None)
    assert (sugerida.segundos_antes, sugerida.segundos_depois) == (12, 5)


def test_janela_precisa_de_20_aprovacoes_e_de_mudanca():
    assert janela.sugerir(aprovados_com_ajuste(19, -3, 0), 10, 5, desde=None) is None
    assert janela.sugerir(aprovados_com_ajuste(30, 0.1, 0), 10, 5, desde=None) is None


def test_janela_so_conta_depois_da_ultima_mudanca_e_tem_limites():
    antigos = aprovados_com_ajuste(30, -3, 0, quando=QUANDO)
    assert janela.sugerir(antigos, 13, 5, desde=QUANDO + timedelta(days=1)) is None
    extremos = janela.sugerir(aprovados_com_ajuste(20, -500, 500), 10, 5, desde=None)
    assert (extremos.segundos_antes, extremos.segundos_depois) == (60, 60)
    zero = janela.sugerir(aprovados_com_ajuste(20, 30, -30), 10, 5, desde=None)
    assert (zero.segundos_antes, zero.segundos_depois) == (0, 0)


# ------------------------------------------------------------------ no banco


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "d")


def gravar_decisoes(sessao, lista: list[Exemplo]) -> None:
    """Cada exemplo vira uma gravação de um dia, um momento e uma decisão."""
    gravacoes: dict[date, Gravacao] = {}
    for e in lista:
        if e.dia not in gravacoes:
            inicio = datetime.combine(e.dia, datetime.min.time(), UTC) + timedelta(hours=15)
            gravacoes[e.dia] = Gravacao(
                arquivo=f"C:/g/{e.dia}.mkv", inicio=inicio, status=StatusGravacao.CONCLUIDA
            )
            sessao.add(gravacoes[e.dia])
            sessao.flush()
        momento = Momento(
            gravacao_id=gravacoes[e.dia].id, inicio_s=e.momento_id, fim_s=e.momento_id + 20,
            sinais=e.forcas, nota=50,
        )  # fmt: skip
        sessao.add(momento)
        sessao.flush()
        sessao.add(
            Decisao(
                momento_id=momento.id, aprovado=e.aprovado,
                ajuste_inicio_s=e.ajuste_inicio_s, ajuste_fim_s=e.ajuste_fim_s,
            )
        )  # fmt: skip
    sessao.commit()


def test_exemplos_vem_do_banco(sessao):
    gravar_decisoes(sessao, [exemplo(0, {"fala": 0.7}, True, inicio=-2.0)])
    sessao.add(Momento(gravacao_id=1, inicio_s=500, fim_s=520, sinais={"fala": 1}))  # sem decisão
    sessao.commit()
    (unico,) = metricas.exemplos(sessao)
    assert (unico.forcas, unico.aprovado, unico.ajuste_inicio_s, unico.dia) == (
        {"fala": 0.7}, True, -2.0, DIA,
    )  # fmt: skip


def test_sugestao_sem_decisoes_suficientes(sessao, cfg):
    gravar_decisoes(sessao, decisoes_onde_a_fala_decide(n=40))
    s = sugestao.sugerir(sessao, cfg)
    assert s.decisoes == 40 and s.pesos_novos is None and s.janela_nova is None
    assert "40 decisões" in s.sem_pesos_porque
    # 22 aprovados (já passa dos 20), mas nenhum ajustou o corte: a janela fica.
    assert s.sem_janela_porque == "Os seus ajustes não pedem mudança na janela."
    assert not s.tem_sugestao
    with pytest.raises(sugestao.NadaParaAceitarError):
        sugestao.aceitar(sessao, cfg, 40)


def test_aceitar_grava_a_versao_e_a_proxima_deteccao_usa(sessao, cfg):
    lista = [
        exemplo(e.momento_id, e.forcas, e.aprovado, dia=e.dia, inicio=-3.0 if e.aprovado else None)
        for e in decisoes_onde_a_fala_decide()
    ]
    gravar_decisoes(sessao, lista)
    s = sugestao.sugerir(sessao, cfg)
    assert s.pesos_novos["fala"] > 0.8
    assert s.precisao_depois > s.precisao_antes
    assert (s.janela_nova.segundos_antes, s.janela_nova.segundos_depois) == (13, 5)
    assert len(s.precisao_por_dia) == 10

    with pytest.raises(sugestao.SugestaoMudouError, match="Agora são 200 decisões"):
        sugestao.aceitar(sessao, cfg, 199)  # você viu a sugestão com uma decisão a menos
    versao = sugestao.aceitar(sessao, cfg, 200)
    assert versao.versao == 1 and versao.decisoes == 200
    assert versao.valores == s.pesos_novos
    assert (versao.precisao_antes, versao.precisao) == (s.precisao_antes, s.precisao_depois)

    # A detecção (etapa 8) passa a usar os pesos e a janela aceitos.
    assert nota.pesos_atuais(sessao, cfg.deteccao) == s.pesos_novos
    deteccao = nota.deteccao_atual(sessao, cfg.deteccao)
    assert (deteccao.segundos_antes, deteccao.segundos_depois) == (13, 5)
    assert cfg.deteccao.segundos_antes == 10  # o config em si não muda

    # Logo depois de aceitar: os pesos já são esses, e a janela espera ajustes novos.
    depois = sugestao.sugerir(sessao, cfg)
    assert depois.pesos_novos is None and depois.janela_nova is None
    assert depois.versao_atual == 1 and len(depois.versoes) == 1


def test_versao_que_so_muda_pesos_nao_recomeca_a_janela(sessao):
    antes = datetime(2026, 10, 1, tzinfo=UTC)
    sessao.add(PesosNota(versao=1, segundos_antes=13, segundos_depois=5, criada_em=antes))
    sessao.add(
        PesosNota(versao=2, valores={"fala": 1}, segundos_antes=13, segundos_depois=5,
                  criada_em=antes + timedelta(days=5))
    )  # fmt: skip
    sessao.commit()
    assert sugestao._janela_vale_desde(sessao) == antes
    sessao.add(
        PesosNota(versao=3, segundos_antes=12, segundos_depois=5,
                  criada_em=antes + timedelta(days=9))
    )  # fmt: skip
    sessao.commit()
    assert sugestao._janela_vale_desde(sessao) == antes + timedelta(days=9)


def test_deteccao_sem_versao_usa_o_config(sessao):
    cfg = ConfigDeteccao()
    assert nota.deteccao_atual(sessao, cfg) is cfg


# ------------------------------------------------------------------ clipador aprender


def test_clipador_aprender_pergunta_em_portugues(tmp_path, escrever_config):
    from typer.testing import CliRunner

    from clipador.banco.conexao import abrir_banco, nova_sessao
    from clipador.cli import app

    config = escrever_config(
        f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
        f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n'
    )
    engine = abrir_banco(tmp_path / "d" / "clipador.db")
    with nova_sessao(engine) as sessao:
        gravar_decisoes(sessao, decisoes_onde_a_fala_decide())
    runner = CliRunner()

    resultado = runner.invoke(app, ["aprender", "--arquivo", str(config)], input="\n")
    assert resultado.exit_code == 0, resultado.output
    assert "Decisões: 200" in resultado.output and "(s/N)" in resultado.output
    assert "Nada mudou." in resultado.output  # Enter = não

    resultado = runner.invoke(app, ["aprender", "--arquivo", str(config)], input="s\n")
    assert "Versão 1 aceita" in resultado.output
    resultado = runner.invoke(app, ["aprender", "--arquivo", str(config), "--aceitar"])
    assert "Sem sugestão: Os pesos aprendidos são iguais aos atuais." in resultado.output
    with nova_sessao(engine) as sessao:
        assert sessao.exec(select(PesosNota)).one().versao == 1
    engine.dispose()
