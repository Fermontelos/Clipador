"""O serviço em segundo plano: uma volta do laço (passo) e o laço inteiro (rodar)."""

import os
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from clipador.banco import fila
from clipador.banco.backup import listar_backups
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, StatusGravacao, StatusTarefa, Tarefa, TipoTarefa, agora
from clipador.config import carregar_config
from clipador.sistema.energia import ManterAcordado
from clipador.sistema.servico import Servico
from clipador.sistema.trava import ServicoJaRodandoError


class Relogio:
    """Relógio falso que o teste pode adiantar."""

    def __init__(self) -> None:
        self.momento = agora()

    def __call__(self):
        return self.momento


class EnergiaFalsa(ManterAcordado):
    def __init__(self) -> None:
        super().__init__(lambda flags: 1)
        self.historico: list[bool] = []

    def ativar(self) -> None:
        super().ativar()
        self.historico.append(True)

    def desativar(self) -> None:
        super().desativar()
        self.historico.append(False)


@pytest.fixture
def cfg(escrever_config, tmp_path):
    gravacoes = (tmp_path / "gravacoes").as_posix()
    dados = (tmp_path / "dados").as_posix()
    return carregar_config(
        escrever_config(f'pasta_gravacoes = "{gravacoes}"\npasta_dados = "{dados}"\n')
    )


def gravacao_no_disco(cfg, nome: str, modificada_ha: timedelta) -> Path:
    cfg.pasta_gravacoes.mkdir(parents=True, exist_ok=True)
    arquivo = cfg.pasta_gravacoes / nome
    arquivo.write_bytes(b"video")
    instante = time.time() - modificada_ha.total_seconds()
    os.utime(arquivo, (instante, instante))
    return arquivo


@pytest.fixture
def servico_factory(cfg):
    criados = []

    def criar(executores=None, **extras):
        servico = Servico(cfg, executores=executores or {}, **extras)
        criados.append(servico)
        return servico

    yield criar
    for servico in criados:
        servico.encerrar()


def tarefas(cfg) -> list[Tarefa]:
    engine = abrir_banco(cfg.caminho_banco)
    try:
        with nova_sessao(engine) as sessao:
            return list(sessao.exec(select(Tarefa).order_by(Tarefa.id)).all())
    finally:
        engine.dispose()


# ------------------------------------------------------------------ uma volta (passo)


def test_gravacao_pronta_entra_na_fila_e_e_processada(cfg, servico_factory):
    gravacao_no_disco(cfg, "2026-10-14 20-00-00.mkv", timedelta(hours=1))
    executadas = []
    servico = servico_factory(
        {TipoTarefa.EXTRAIR_AUDIO: lambda sessao, tarefa, cfg: executadas.append(tarefa.id)}
    )
    servico.iniciar()

    resultado = servico.passo()

    assert resultado.gravacoes_novas == ["2026-10-14 20-00-00.mkv"]
    assert resultado.tarefa_executada == "extrair_audio"
    assert executadas == [1]
    # Concluída, ela criou a próxima da cadeia, que espera a etapa 6 (sem executor ainda).
    assert [(t.tipo, t.status) for t in tarefas(cfg)] == [
        (TipoTarefa.EXTRAIR_AUDIO, StatusTarefa.FEITA),
        (TipoTarefa.TRANSCREVER, StatusTarefa.PENDENTE),
    ]


def test_gravacao_em_andamento_pausa_a_fila(cfg, servico_factory):
    gravacao_no_disco(cfg, "2026-10-14 18-00-00.mkv", timedelta(hours=1))
    executadas = []
    servico = servico_factory(
        {TipoTarefa.EXTRAIR_AUDIO: lambda sessao, tarefa, cfg: executadas.append(tarefa.id)}
    )
    servico.iniciar()
    gravacao_no_disco(cfg, "2026-10-15 21-00-00.mkv", timedelta(seconds=5))  # a live está rolando

    resultado = servico.passo()

    assert resultado.pausado_por_gravacao is True
    assert resultado.gravacoes_novas == ["2026-10-14 18-00-00.mkv"]  # a antiga entra na fila...
    assert executadas == []  # ...mas nada é processado durante a live


def test_tempo_de_gravacao_parada_vem_do_config(escrever_config, tmp_path):
    cfg = carregar_config(
        escrever_config(
            f'pasta_gravacoes = "{(tmp_path / "g").as_posix()}"\n'
            f'pasta_dados = "{(tmp_path / "d").as_posix()}"\n'
            "minutos_gravacao_parada = 15\n"
        )
    )
    assert Servico(cfg, executores={}).monitor.estavel_apos == timedelta(minutes=15)


def test_tarefa_sem_executor_fica_esperando_em_vez_de_falhar(cfg, servico_factory):
    gravacao_no_disco(cfg, "2026-10-14 18-00-00.mkv", timedelta(hours=1))
    servico = servico_factory({})  # nenhum executor ainda
    servico.iniciar()

    resultado = servico.passo()

    assert resultado.tarefa_executada is None
    assert [(t.status, t.tentativas) for t in tarefas(cfg)] == [(StatusTarefa.PENDENTE, 0)]


def test_tarefa_que_falha_e_registrada_e_o_servico_continua(cfg, servico_factory):
    def quebra(sessao, tarefa, cfg):
        raise RuntimeError("arquivo travado")

    gravacao_no_disco(cfg, "2026-10-14 18-00-00.mkv", timedelta(hours=1))
    servico = servico_factory({TipoTarefa.EXTRAIR_AUDIO: quebra})
    servico.iniciar()

    resultado = servico.passo()

    assert resultado.tarefa_falhou is True
    tarefa = tarefas(cfg)[0]
    assert tarefa.status is StatusTarefa.PENDENTE  # vai tentar de novo depois
    assert tarefa.tentativas == 1
    assert tarefa.ultimo_erro == "RuntimeError: arquivo travado"
    assert servico.passo().tarefa_executada is None  # espera antes de tentar de novo


def test_tarefa_agendada_atrasada_roda_assim_que_o_servico_abre(cfg, servico_factory):
    # Ex.: coleta de métricas marcada para quando o PC estava desligado.
    engine = abrir_banco(cfg.caminho_banco)
    with nova_sessao(engine) as sessao:
        fila.adicionar(
            sessao, TipoTarefa.COLETAR_METRICAS, executar_a_partir_de=agora() - timedelta(days=2)
        )
    engine.dispose()
    executadas = []
    servico = servico_factory(
        {TipoTarefa.COLETAR_METRICAS: lambda sessao, tarefa, cfg: executadas.append(tarefa.id)}
    )
    servico.iniciar()

    assert servico.passo().tarefa_executada == "coletar_metricas"
    assert executadas == [1]


def test_ao_abrir_recupera_tarefa_interrompida(cfg, servico_factory):
    engine = abrir_banco(cfg.caminho_banco)
    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.PUBLICAR)
        fila.pegar_proxima(sessao)  # ficou "em andamento" e o PC desligou
    engine.dispose()

    servico_factory({}).iniciar()

    assert tarefas(cfg)[0].status is StatusTarefa.PENDENTE


def test_pc_fica_acordado_so_enquanto_processa(cfg, servico_factory):
    gravacao_no_disco(cfg, "2026-10-14 18-00-00.mkv", timedelta(hours=1))
    energia = EnergiaFalsa()
    servico = servico_factory(
        {TipoTarefa.EXTRAIR_AUDIO: lambda sessao, tarefa, cfg: None}, energia=energia
    )
    servico.iniciar()

    servico.passo()  # processa: ativa
    assert energia.ativo is True
    servico.passo()  # sobrou só "transcrever", sem executor: fila parada, desativa
    assert energia.ativo is False
    assert energia.historico[:2] == [True, False]


def test_faz_o_backup_do_dia(cfg, servico_factory):
    servico = servico_factory({})
    servico.iniciar()
    servico.passo()
    servico.passo()
    assert len(listar_backups(cfg.pasta_backups_efetiva)) == 1


def test_dois_servicos_nao_rodam_ao_mesmo_tempo(cfg, servico_factory):
    servico_factory({}).iniciar()
    with pytest.raises(ServicoJaRodandoError):
        servico_factory({}).iniciar()


# ------------------------------------------------------------------ o laço inteiro


def test_laco_pega_a_gravacao_e_para_quando_pedido(cfg):
    gravacao_no_disco(cfg, "2026-10-14 20-00-00.mkv", timedelta(hours=1))
    servico = Servico(cfg, executores={}, energia=EnergiaFalsa())
    parar = threading.Event()
    thread = threading.Thread(target=servico.rodar, args=(parar, timedelta(seconds=1)))
    thread.start()
    try:
        limite = time.monotonic() + 15
        while time.monotonic() < limite:
            engine = abrir_banco(cfg.caminho_banco)
            try:
                with nova_sessao(engine) as sessao:
                    if sessao.exec(select(Gravacao)).first() is not None:
                        break
            finally:
                engine.dispose()
            time.sleep(0.2)
        else:
            pytest.fail("o serviço não registrou a gravação em 15 s")
    finally:
        parar.set()
        thread.join(timeout=10)
    assert not thread.is_alive(), "o serviço não parou quando pedido"
    # Ao parar, liberou a trava: outro serviço pode abrir.
    Servico(cfg, executores={}).iniciar()


# ------------------------------------------------------------------ etapa 5


def test_extrair_audio_ja_vem_registrado():
    from clipador.ingestao import audio
    from clipador.sistema.servico import EXECUTORES

    assert EXECUTORES[TipoTarefa.EXTRAIR_AUDIO] is audio.extrair_audio


def test_falha_definitiva_nao_e_tentada_de_novo(cfg, servico_factory):
    def sem_faixa(sessao, tarefa, cfg):
        raise fila.FalhaDefinitivaError("sem a faixa 2 (voz)")

    gravacao_no_disco(cfg, "2026-10-14 18-00-00.mkv", timedelta(hours=1))
    servico = servico_factory({TipoTarefa.EXTRAIR_AUDIO: sem_faixa})
    servico.iniciar()

    assert servico.passo().tarefa_falhou is True
    tarefa = tarefas(cfg)[0]
    assert (tarefa.status, tarefa.tentativas) == (StatusTarefa.ERRO, 1)
    assert tarefa.ultimo_erro == "FalhaDefinitivaError: sem a faixa 2 (voz)"


def test_temporarios_sao_apagados_quando_a_gravacao_termina(cfg, servico_factory):
    engine = abrir_banco(cfg.caminho_banco)
    with nova_sessao(engine) as sessao:
        gravacao = Gravacao(arquivo="C:/g/live.mkv", inicio=agora())
        sessao.add(gravacao)
        sessao.commit()
        fila.adicionar(sessao, TipoTarefa.GERAR_TITULOS, gravacao_id=gravacao.id)
        id_gravacao = gravacao.id
    engine.dispose()
    temporarios = cfg.pasta_trabalho / str(id_gravacao)
    temporarios.mkdir(parents=True)
    (temporarios / "voz.wav").write_bytes(b"RIFF")
    servico = servico_factory({TipoTarefa.GERAR_TITULOS: lambda sessao, tarefa, cfg: None})
    servico.iniciar()

    servico.passo()  # a última tarefa da cadeia

    assert not temporarios.exists()
    engine = abrir_banco(cfg.caminho_banco)
    with nova_sessao(engine) as sessao:
        assert sessao.get(Gravacao, id_gravacao).status is StatusGravacao.CONCLUIDA
    engine.dispose()
