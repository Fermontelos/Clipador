"""Fila de tarefas: ordem, cadeia, falhas, agendamento e recuperação."""

import contextlib
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from clipador.banco import fila
from clipador.banco.conexao import nova_sessao
from clipador.banco.modelos import StatusTarefa, Tarefa, TipoTarefa, agora


def test_fila_vazia_nao_devolve_nada(sessao):
    assert fila.pegar_proxima(sessao) is None


def test_pegar_marca_como_em_andamento(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    tarefa = fila.pegar_proxima(sessao)
    assert tarefa.status is StatusTarefa.EM_ANDAMENTO
    # Já está em andamento: não pode ser pega de novo.
    assert fila.pegar_proxima(sessao) is None


def test_pega_a_mais_antiga_primeiro(sessao, gravacao):
    primeira = fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    assert fila.pegar_proxima(sessao).id == primeira.id


def test_terminar_cria_a_proxima_da_cadeia(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    tipos = []
    while (tarefa := fila.pegar_proxima(sessao)) is not None:
        tipos.append(tarefa.tipo)
        fila.concluir(sessao, tarefa)
    assert tipos == [
        TipoTarefa.EXTRAIR_AUDIO,
        TipoTarefa.TRANSCREVER,
        TipoTarefa.DETECTAR,
        TipoTarefa.CORTAR,
        TipoTarefa.GERAR_TITULOS,
    ]


def test_proxima_da_cadeia_mantem_a_gravacao(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    proxima = fila.concluir(sessao, fila.pegar_proxima(sessao))
    assert proxima.gravacao_id == gravacao.id


def test_tarefas_fora_da_cadeia_nao_criam_outra(sessao):
    fila.adicionar(sessao, TipoTarefa.PUBLICAR, publicacao_id=None)
    assert fila.concluir(sessao, fila.pegar_proxima(sessao)) is None


def test_falha_volta_para_a_fila_com_espera(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.TRANSCREVER, gravacao_id=gravacao.id)
    tarefa = fila.falhar(sessao, fila.pegar_proxima(sessao), "arquivo travado")

    assert tarefa.status is StatusTarefa.PENDENTE
    assert tarefa.tentativas == 1
    assert tarefa.ultimo_erro == "arquivo travado"
    # Não roda de novo na mesma hora...
    assert fila.pegar_proxima(sessao) is None
    # ...mas roda depois da espera.
    assert fila.pegar_proxima(sessao, agora() + timedelta(minutes=2)) is not None


def test_tres_falhas_viram_erro_e_nao_travam_as_outras(sessao, gravacao):
    problematica = fila.adicionar(sessao, TipoTarefa.TRANSCREVER, gravacao_id=gravacao.id)
    outra = fila.adicionar(sessao, TipoTarefa.PUBLICAR)
    depois = agora() + timedelta(hours=1)

    # Roda a fila até esvaziar: a problemática sempre falha, a outra sempre dá certo.
    ordem = []
    while (tarefa := fila.pegar_proxima(sessao, depois)) is not None:
        ordem.append(tarefa.id)
        if tarefa.id == problematica.id:
            fila.falhar(sessao, tarefa, "erro permanente")
        else:
            fila.concluir(sessao, tarefa)

    problematica = sessao.get(type(problematica), problematica.id, populate_existing=True)
    assert problematica.status is StatusTarefa.ERRO
    assert problematica.tentativas == 3
    assert problematica.ultimo_erro == "erro permanente"
    # A problemática foi tentada 3 vezes e não impediu a outra de rodar: depois da
    # 1ª falha ela espera, e a outra passa na frente.
    assert ordem.count(problematica.id) == 3
    assert ordem.count(outra.id) == 1
    assert ordem.index(outra.id) == 1


def test_tarefa_agendada_nao_roda_antes_da_hora(sessao):
    daqui_a_um_dia = agora() + timedelta(hours=24)
    fila.adicionar(sessao, TipoTarefa.COLETAR_METRICAS, executar_a_partir_de=daqui_a_um_dia)

    assert fila.pegar_proxima(sessao) is None
    assert fila.pegar_proxima(sessao, daqui_a_um_dia - timedelta(seconds=1)) is None
    assert fila.pegar_proxima(sessao, daqui_a_um_dia) is not None


def test_tarefa_interrompida_volta_para_a_fila(engine, gravacao):
    # Simula o PC desligando no meio: a tarefa fica "em andamento" no banco.
    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.TRANSCREVER, gravacao_id=gravacao.id)
        interrompida = fila.pegar_proxima(sessao)

    # O app abre de novo, com outra sessão.
    with nova_sessao(engine) as sessao:
        assert fila.pegar_proxima(sessao) is None
        assert fila.recuperar_interrompidas(sessao) == 1
        retomada = fila.pegar_proxima(sessao)
        assert retomada.id == interrompida.id
        # A interrupção não conta como tentativa.
        assert retomada.tentativas == 0


def test_duas_sessoes_nao_pegam_a_mesma_tarefa(engine, gravacao):
    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    with nova_sessao(engine) as sessao_a, nova_sessao(engine) as sessao_b:
        assert fila.pegar_proxima(sessao_a) is not None
        assert fila.pegar_proxima(sessao_b) is None


def test_resumo_conta_por_status_e_lista_erros(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    fila.adicionar(sessao, TipoTarefa.TRANSCREVER, gravacao_id=gravacao.id)
    tarefa = fila.pegar_proxima(sessao)
    tarefa.tentativas = 2
    fila.falhar(sessao, tarefa, "quebrou")

    resumo = fila.resumo(sessao)
    assert resumo.por_status[StatusTarefa.PENDENTE] == 1
    assert resumo.por_status[StatusTarefa.ERRO] == 1
    assert [t.ultimo_erro for t in resumo.com_erro] == ["quebrou"]
    assert len(resumo.proximas) == 1


def test_concluir_grava_tudo_junto_ou_nada(engine, gravacao, monkeypatch):
    # Cenário do bug: a 1ª gravação no banco funciona e a energia cai antes da 2ª.
    # Se "concluir" gravasse em dois passos, a tarefa ficaria "feita" sem a próxima.
    commit_original = Session.commit
    chamadas = []

    def commit_que_cai_na_segunda(self):
        chamadas.append(1)
        if len(chamadas) >= 2:
            raise RuntimeError("energia caiu")
        commit_original(self)

    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
        tarefa = fila.pegar_proxima(sessao)
        monkeypatch.setattr(Session, "commit", commit_que_cai_na_segunda)
        with contextlib.suppress(RuntimeError):
            fila.concluir(sessao, tarefa)
        monkeypatch.undo()

    # Em nenhum caso pode existir "extrair_audio feita" sem a próxima da cadeia.
    with nova_sessao(engine) as sessao:
        estado = {t.tipo: t.status for t in sessao.exec(select(Tarefa)).all()}
    if estado[TipoTarefa.EXTRAIR_AUDIO] is StatusTarefa.FEITA:
        assert estado.get(TipoTarefa.TRANSCREVER) is StatusTarefa.PENDENTE
    # Com a gravação num passo só, a conclusão inteira entra no 1º commit.
    assert len(chamadas) == 1


def test_resumo_lista_as_em_andamento(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    tarefa = fila.pegar_proxima(sessao)
    assert [t.id for t in fila.resumo(sessao).em_andamento] == [tarefa.id]


def test_concluir_duas_vezes_e_recusado(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    tarefa = fila.pegar_proxima(sessao)
    fila.concluir(sessao, tarefa)
    with pytest.raises(fila.EstadoInvalidoError):
        fila.concluir(sessao, tarefa)
    # A próxima da cadeia continua existindo uma vez só.
    tipos = [t.tipo for t in sessao.exec(select(Tarefa)).all()]
    assert tipos.count(TipoTarefa.TRANSCREVER) == 1


def test_falhar_tarefa_que_nao_esta_em_andamento_e_recusado(sessao):
    tarefa = fila.adicionar(sessao, TipoTarefa.PUBLICAR)
    with pytest.raises(fila.EstadoInvalidoError):
        fila.falhar(sessao, tarefa, "qualquer")


def test_pegar_so_os_tipos_pedidos(sessao, gravacao):
    fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)
    publicar = fila.adicionar(sessao, TipoTarefa.PUBLICAR)
    assert fila.pegar_proxima(sessao, tipos=set()) is None
    assert fila.pegar_proxima(sessao, tipos={TipoTarefa.PUBLICAR}).id == publicar.id
    assert fila.pegar_proxima(sessao, tipos={TipoTarefa.PUBLICAR}) is None
