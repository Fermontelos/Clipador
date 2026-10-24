"""Etapa 11: desligamento automático (testes do roteiro, com relógio e ociosidade simulados).

Nenhum teste desliga o PC: o ``shutdown`` é sempre um falso que só anota o que pediram.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from clipador.banco import fila
from clipador.banco.modelos import StatusTarefa, TipoTarefa
from clipador.config import ConfigDesligamento, config_sem_arquivo
from clipador.sistema.desligamento import CONTAGEM_S, HORIZONTE, Desligamento

INICIO = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # madrugada


class PCFalso:
    """O relógio, a ociosidade e o ``shutdown``, todos simulados."""

    def __init__(self) -> None:
        self.agora = INICIO
        self.ultimo_input = INICIO - timedelta(hours=1)  # você saiu do PC há 1 h
        self.comandos: list[list[str]] = []

    def ocioso_s(self) -> float:
        return (self.agora - self.ultimo_input).total_seconds()

    def shutdown(self, argumentos: list[str]) -> None:
        self.comandos.append(argumentos)

    def passar(self, segundos: float) -> None:
        self.agora += timedelta(seconds=segundos)

    def mexer_no_mouse(self) -> None:
        self.ultimo_input = self.agora


@pytest.fixture
def pc() -> PCFalso:
    return PCFalso()


def desligamento(pc: PCFalso, **regras) -> Desligamento:
    cfg = ConfigDesligamento(**{"modo_teste": False, **regras})
    regra = Desligamento(cfg, pc.ocioso_s, pc.shutdown)
    regra.houve_trabalho()  # a noite teve processamento
    return regra


def desligou(pc: PCFalso) -> bool:
    return any(comando[0] == "/s" for comando in pc.comandos)


# ------------------------------------------------------------------ a regra


def test_fila_terminou_e_pc_ocioso_desliga_com_contagem_de_60_s(pc):
    regra = desligamento(pc)
    regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos[0][:3] == ["/s", "/t", str(CONTAGEM_S)]
    assert "Mexa no mouse" in pc.comandos[0][-1]  # o aviso que o Windows mostra


def test_fila_pendente_nao_desliga(pc):
    regra = desligamento(pc)
    for _ in range(30):
        regra.verificar(pc.agora, fila_pendente=True)
        pc.passar(60)
    assert pc.comandos == []


def test_usuario_ativo_nao_desliga_e_confere_de_novo_a_cada_minuto(pc, caplog):
    regra = desligamento(pc)
    pc.mexer_no_mouse()  # você está usando o PC
    with caplog.at_level(logging.DEBUG, logger="clipador.sistema.desligamento"):
        for _ in range(9):
            pc.passar(60)
            regra.verificar(pc.agora, fila_pendente=False)
        assert pc.comandos == []  # 9 min parado: ainda não
        regra.verificar(pc.agora + timedelta(seconds=10), fila_pendente=False)
        assert pc.comandos == []  # conferências no meio do minuto não contam
        pc.passar(60)
        regra.verificar(pc.agora, fila_pendente=False)  # 10 min parado
    assert desligou(pc)


def test_mexer_no_mouse_durante_a_contagem_cancela_e_volta_a_esperar(pc):
    regra = desligamento(pc)
    regra.verificar(pc.agora, fila_pendente=False)
    pc.passar(20)
    pc.mexer_no_mouse()
    pc.passar(10)
    regra.verificar(pc.agora, fila_pendente=False)

    assert pc.comandos[-1] == ["/a"]  # shutdown /a: cancelou
    assert regra.contagem_desde is None
    # Volta a esperar 10 min de ociosidade antes de tentar de novo.
    pc.passar(5 * 60)
    regra.verificar(pc.agora, fila_pendente=False)
    assert len(pc.comandos) == 2
    pc.passar(6 * 60)
    regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos[-1][0] == "/s"


def test_contagem_sem_mexer_segue_ate_o_fim(pc):
    regra = desligamento(pc)
    regra.verificar(pc.agora, fila_pendente=False)
    for _ in range(5):
        pc.passar(10)
        regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos == [pc.comandos[0]]  # nenhum /a: o Windows desliga sozinho


def test_trabalho_novo_durante_a_contagem_cancela(pc):
    regra = desligamento(pc)
    regra.verificar(pc.agora, fila_pendente=False)
    pc.passar(10)
    regra.verificar(pc.agora, fila_pendente=True)  # ex.: uma gravação entrou na fila
    assert pc.comandos[-1] == ["/a"]


def test_contagem_que_nao_desligou_volta_a_esperar(pc, caplog):
    regra = desligamento(pc)
    regra.verificar(pc.agora, fila_pendente=False)
    pc.passar(CONTAGEM_S + 200)  # o Windows não desligou (algo bloqueou)
    regra.verificar(pc.agora, fila_pendente=False)
    assert regra.contagem_desde is None
    assert "o PC não desligou" in caplog.text


def test_modo_de_teste_so_escreve_no_log_uma_vez_por_noite(pc, caplog):
    regra = desligamento(pc, modo_teste=True)
    with caplog.at_level(logging.INFO, logger="clipador.sistema.desligamento"):
        for _ in range(60):  # 1 h de conferências
            regra.verificar(pc.agora, fila_pendente=False)
            pc.passar(60)
        assert pc.comandos == []  # não desligou
        assert caplog.text.count("desligaria agora") == 1
        assert "ocioso há 60 min" in caplog.text
        regra.houve_trabalho()  # outra noite
        regra.verificar(pc.agora, fila_pendente=False)
        assert caplog.text.count("desligaria agora") == 2


def test_desiste_se_voce_seguiu_usando_o_pc_por_horas(pc, caplog):
    # A fila acabou às 3h com você no PC; você usa até 8h e sai para almoçar às 13h: o PC
    # não pode desligar de tarde.
    regra = desligamento(pc)
    with caplog.at_level(logging.INFO, logger="clipador.sistema.desligamento"):
        for _ in range(5 * 60):  # 5 h usando o PC, conferências a cada minuto
            pc.mexer_no_mouse()
            regra.verificar(pc.agora, fila_pendente=False)
            pc.passar(60)
        pc.passar(30 * 60)  # 30 min sem mexer
        regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos == []
    assert "desistindo de desligar" in caplog.text
    regra.houve_trabalho()  # a próxima noite
    regra.verificar(pc.agora, fila_pendente=False)
    assert desligou(pc)


def test_sem_processamento_nao_desliga(pc):
    # Uma tarde com o app aberto e nada na fila: deixar o PC 10 min parado não o desliga.
    regra = Desligamento(ConfigDesligamento(modo_teste=False), pc.ocioso_s, pc.shutdown)
    regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos == []


def test_desligado_no_config_nao_desliga(pc):
    regra = desligamento(pc, desligar_ao_terminar=False)
    regra.verificar(pc.agora, fila_pendente=False)
    assert pc.comandos == []


def test_shutdown_que_falha_nao_quebra(pc, caplog):
    def quebra(argumentos):
        raise OSError("acesso negado")

    regra = Desligamento(ConfigDesligamento(modo_teste=False), pc.ocioso_s, quebra)
    regra.houve_trabalho()
    regra.verificar(pc.agora, fila_pendente=False)
    assert regra.contagem_desde is None
    assert "Não foi possível iniciar o desligamento" in caplog.text


# ------------------------------------------------------------------ o que conta como fila


def test_o_que_conta_como_fila(sessao, gravacao):
    tipos = [TipoTarefa.EXTRAIR_AUDIO, TipoTarefa.COLETAR_METRICAS]
    agora = INICIO
    assert not fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)  # fila vazia

    # Só uma tarefa agendada para daqui a 7 dias (métricas): não conta (deve desligar).
    fila.adicionar(
        sessao, TipoTarefa.COLETAR_METRICAS, executar_a_partir_de=agora + timedelta(days=7)
    )
    assert not fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)

    # Uma de um tipo que o serviço ainda não executa (publicar): não conta.
    fila.adicionar(sessao, TipoTarefa.PUBLICAR, executar_a_partir_de=agora)
    assert not fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)

    # Uma nova tentativa daqui a 2 min: conta (ainda há trabalho nesta noite).
    tentativa = fila.adicionar(
        sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id,
        executar_a_partir_de=agora + timedelta(minutes=2),
    )  # fmt: skip
    assert fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)

    # Em andamento: conta, mesmo sendo de outro tipo.
    tentativa.status = StatusTarefa.FEITA
    sessao.add(tentativa)
    sessao.commit()
    assert not fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)
    fila.pegar_proxima(sessao, agora + timedelta(days=1))  # a de publicar, em andamento
    assert fila.ha_trabalho(sessao, agora, tipos, HORIZONTE)


# ------------------------------------------------------------------ no serviço


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "d")


def test_servico_desliga_depois_da_ultima_tarefa(cfg, pc):
    from clipador.sistema.energia import ManterAcordado
    from clipador.sistema.servico import Servico

    regra = Desligamento(ConfigDesligamento(modo_teste=False), pc.ocioso_s, pc.shutdown)
    servico = Servico(
        cfg, executores={TipoTarefa.GERAR_TITULOS: lambda *a: None},
        energia=ManterAcordado(lambda f: 1), relogio=lambda: pc.agora, desligamento=regra,
    )  # fmt: skip
    servico.iniciar()
    try:
        from clipador.banco.conexao import nova_sessao

        with nova_sessao(servico.engine) as sessao:
            fila.adicionar(sessao, TipoTarefa.GERAR_TITULOS, executar_a_partir_de=pc.agora)
        assert servico.passo().tarefa_executada == "gerar_titulos"
        assert pc.comandos == []  # acabou de trabalhar
        servico.passo()  # fila vazia e PC ocioso há 1 h
    finally:
        servico.encerrar()
    assert desligou(pc)


def test_recorte_pedido_na_revisao_nao_arma_o_desligamento(cfg, pc):
    """Um ajuste do corte de manhã (etapa 12) não pode desligar o PC na hora do almoço."""
    from clipador.sistema.energia import ManterAcordado
    from clipador.sistema.servico import Servico

    regra = Desligamento(ConfigDesligamento(modo_teste=False), pc.ocioso_s, pc.shutdown)
    servico = Servico(
        cfg, executores={TipoTarefa.RECORTAR: lambda *a: None},
        energia=ManterAcordado(lambda f: 1), relogio=lambda: pc.agora, desligamento=regra,
    )  # fmt: skip
    servico.iniciar()
    try:
        from clipador.banco.conexao import nova_sessao

        with nova_sessao(servico.engine) as sessao:
            fila.adicionar(sessao, TipoTarefa.RECORTAR, executar_a_partir_de=pc.agora)
        assert servico.passo().tarefa_executada == "recortar"
        for _ in range(3):  # fila vazia e PC ocioso há 1 h, conferindo por 3 min
            servico.passo()
            pc.passar(61)
    finally:
        servico.encerrar()
    assert pc.comandos == []
    assert regra.processou_algo is False


def test_pc_nao_suspende_enquanto_espera_para_desligar(cfg, pc):
    # Se o Windows suspendesse antes dos 10 min de ociosidade, o PC dormiria a noite toda
    # em vez de desligar.
    from clipador.sistema.energia import ManterAcordado
    from clipador.sistema.servico import Servico

    pc.mexer_no_mouse()  # você ainda está no PC: o desligamento espera
    regra = Desligamento(ConfigDesligamento(modo_teste=True), pc.ocioso_s, pc.shutdown)
    energia = ManterAcordado(lambda flags: 1)
    servico = Servico(
        cfg, executores={TipoTarefa.GERAR_TITULOS: lambda *a: None},
        energia=energia, relogio=lambda: pc.agora, desligamento=regra,
    )  # fmt: skip
    servico.iniciar()
    try:
        from clipador.banco.conexao import nova_sessao

        with nova_sessao(servico.engine) as sessao:
            fila.adicionar(sessao, TipoTarefa.GERAR_TITULOS, executar_a_partir_de=pc.agora)
        servico.passo()  # processa
        servico.passo()  # fila vazia, mas esperando a sua ociosidade para desligar
        assert energia.ativo is True
        pc.passar(11 * 60)
        servico.passo()  # modo de teste: "desligaria agora" e acabou a espera
        assert energia.ativo is False  # agora o Windows pode suspender normalmente
    finally:
        servico.encerrar()


def test_erro_repetido_na_ociosidade_entra_no_log_uma_vez(cfg, pc, caplog):
    from clipador.sistema.servico import Servico

    def quebra():
        raise OSError("GetLastInputInfo falhou")

    regra = Desligamento(ConfigDesligamento(), quebra, pc.shutdown)
    regra.houve_trabalho()
    servico = Servico(cfg, executores={}, relogio=lambda: pc.agora, desligamento=regra)
    servico.iniciar()
    try:
        for _ in range(5):
            servico.passo()
            pc.passar(61)
    finally:
        servico.encerrar()
    assert caplog.text.count("Erro ao conferir o desligamento") == 1


def test_processar_a_mao_nunca_desliga(cfg):
    from clipador.sistema.servico import Servico

    assert Servico(cfg).desligamento is None  # só o "clipador iniciar" liga o desligamento


def test_ociosidade_real_responde():
    from clipador.sistema.ociosidade import segundos_ocioso

    assert segundos_ocioso() >= 0
