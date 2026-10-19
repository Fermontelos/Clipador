import pytest
from typer.testing import CliRunner

from clipador import __version__
from clipador.banco import fila
from clipador.banco.conexao import abrir_banco, nova_sessao
from clipador.banco.modelos import Gravacao, TipoTarefa, agora
from clipador.cli import app

runner = CliRunner()


def test_versao_mostra_a_versao_do_app():
    resultado = runner.invoke(app, ["versao"])
    assert resultado.exit_code == 0
    assert __version__ in resultado.output


def test_config_valida_termina_sem_erro(config_valida, tmp_path):
    resultado = runner.invoke(app, ["config", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0
    assert "Configuração válida" in resultado.output
    assert (tmp_path / "dados" / "logs" / "clipador.log").is_file()


def test_config_invalida_explica_o_problema(escrever_config):
    caminho = escrever_config('pasta_gravacoes = "C:/g"\nlimite_diario = -1\n')
    resultado = runner.invoke(app, ["config", "--arquivo", str(caminho)])
    assert resultado.exit_code == 1
    assert "limite_diario" in resultado.output


def test_config_inexistente_explica_como_criar(tmp_path):
    resultado = runner.invoke(app, ["config", "--arquivo", str(tmp_path / "nada.toml")])
    assert resultado.exit_code == 1
    assert "config.example.toml" in resultado.output


def test_status_cria_o_banco_e_mostra_fila_vazia(config_valida, tmp_path):
    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert (tmp_path / "dados" / "clipador.db").is_file()
    assert "pendente: 0" in resultado.output
    assert "Nenhuma tarefa pendente" in resultado.output


def test_status_mostra_uma_fila_de_teste(config_valida, tmp_path):
    # Prepara uma fila com 1 feita, 1 com erro e 1 pendente.
    engine = abrir_banco(tmp_path / "dados" / "clipador.db")
    with nova_sessao(engine) as sessao:
        gravacao = Gravacao(arquivo="live.mkv", inicio=agora())
        sessao.add(gravacao)
        sessao.commit()
        sessao.refresh(gravacao)
        fila.adicionar(sessao, TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id)  # #1
        fila.concluir(sessao, fila.pegar_proxima(sessao))  # #1 feita, cria #2 "transcrever"
        fila.adicionar(sessao, TipoTarefa.PUBLICAR)  # #3
        tarefa = fila.pegar_proxima(sessao)  # a mais antiga das pendentes: #2
        assert tarefa.id == 2
        tarefa.tentativas = 2
        fila.falhar(sessao, tarefa, "sem internet")  # 3ª falha: #2 vira erro
    engine.dispose()

    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert "pendente: 1 | em andamento: 0 | feita: 1 | erro: 1" in resultado.output
    assert "#3 publicar · a partir de" in resultado.output
    assert "#2 transcrever (gravação 1) · 3 tentativas · sem internet" in resultado.output


def test_backup_pelo_comando(config_valida, tmp_path):
    runner.invoke(app, ["status", "--arquivo", str(config_valida)])  # cria o banco
    resultado = runner.invoke(app, ["backup", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert "Backup criado" in resultado.output
    assert len(list((tmp_path / "dados" / "backups").glob("clipador-*.db"))) == 1


def test_backup_sem_banco_explica_o_que_fazer(config_valida):
    resultado = runner.invoke(app, ["backup", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "clipador status" in resultado.output


def test_status_mostra_tarefa_em_andamento(config_valida, tmp_path):
    engine = abrir_banco(tmp_path / "dados" / "clipador.db")
    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.PUBLICAR)
        fila.pegar_proxima(sessao)
    engine.dispose()

    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert "Em andamento:" in resultado.output
    assert "#1 publicar" in resultado.output


def test_status_avisa_quando_a_lista_foi_cortada(config_valida, tmp_path):
    engine = abrir_banco(tmp_path / "dados" / "clipador.db")
    with nova_sessao(engine) as sessao:
        for _ in range(12):
            fila.adicionar(sessao, TipoTarefa.PUBLICAR)
    engine.dispose()

    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert "Próximas (mostrando 10 de 12):" in resultado.output


def test_status_mostra_so_a_primeira_linha_de_um_erro_longo(config_valida, tmp_path):
    engine = abrir_banco(tmp_path / "dados" / "clipador.db")
    with nova_sessao(engine) as sessao:
        fila.adicionar(sessao, TipoTarefa.PUBLICAR)
        tarefa = fila.pegar_proxima(sessao)
        tarefa.tentativas = 2
        fila.falhar(sessao, tarefa, "primeira linha do erro\n" + "detalhe\n" * 50)
    engine.dispose()

    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert "primeira linha do erro" in resultado.output
    assert "detalhe" not in resultado.output


# ------------------------------------------------------------------ serviço (etapa 4)
# O registro do Windows é sempre simulado aqui: nenhum teste mexe no login de verdade.


def test_status_mostra_se_o_servico_roda_e_se_abre_no_login(config_valida, monkeypatch):
    from clipador.sistema import inicializacao

    cadastrado = f'pythonw -m clipador iniciar --arquivo "{config_valida.resolve()}"'
    monkeypatch.setattr(inicializacao, "comando_instalado", lambda: cadastrado)
    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert "Serviço: parado · abre sozinho no login: sim" in resultado.output


def test_status_avisa_se_o_login_aponta_para_outro_config(config_valida, monkeypatch):
    # Ex.: a pasta do projeto foi movida depois de rodar "instalar-servico".
    from clipador.sistema import inicializacao

    antigo = 'pythonw -m clipador iniciar --arquivo "C:/pasta-antiga/config.toml"'
    monkeypatch.setattr(inicializacao, "comando_instalado", lambda: antigo)
    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert "aponta para outro config.toml" in resultado.output


def test_instalar_servico_cadastra_o_comando(config_valida, monkeypatch):
    from clipador.sistema import inicializacao

    cadastrados = []
    monkeypatch.setattr(
        inicializacao, "instalar", lambda arquivo: cadastrados.append(arquivo) or "CMD"
    )
    resultado = runner.invoke(app, ["instalar-servico", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert cadastrados == [config_valida]
    assert "abrir sozinho no próximo login" in resultado.output
    assert "clipador remover-servico" in resultado.output


def test_instalar_servico_recusa_config_invalida(escrever_config, monkeypatch):
    from clipador.sistema import inicializacao

    monkeypatch.setattr(inicializacao, "instalar", lambda arquivo: pytest.fail("não podia"))
    caminho = escrever_config('pasta_gravacoes = "C:/g"\nlimite_diario = -1\n')
    resultado = runner.invoke(app, ["instalar-servico", "--arquivo", str(caminho)])
    assert resultado.exit_code == 1


def test_remover_servico(monkeypatch):
    from clipador.sistema import inicializacao

    monkeypatch.setattr(inicializacao, "remover", lambda: True)
    assert "não abre mais sozinho" in runner.invoke(app, ["remover-servico"]).output
    monkeypatch.setattr(inicializacao, "remover", lambda: False)
    assert "não estava cadastrado" in runner.invoke(app, ["remover-servico"]).output


def test_iniciar_para_com_ctrl_c(config_valida, monkeypatch):
    from clipador.sistema.servico import Servico

    def rodar(self, parar, *args):
        raise KeyboardInterrupt

    monkeypatch.setattr(Servico, "rodar", rodar)
    resultado = runner.invoke(app, ["iniciar", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 0, resultado.output
    assert "Serviço rodando" in resultado.output
    assert "Serviço encerrado" in resultado.output


def test_iniciar_com_outro_servico_rodando_da_erro_claro(config_valida, monkeypatch):
    from clipador.sistema.servico import Servico
    from clipador.sistema.trava import ServicoJaRodandoError

    def rodar(self, parar, *args):
        raise ServicoJaRodandoError("O serviço do Clipador já está rodando")

    monkeypatch.setattr(Servico, "rodar", rodar)
    resultado = runner.invoke(app, ["iniciar", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "já está rodando" in resultado.output


def test_iniciar_sem_janela_grava_o_erro_num_arquivo(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "stdout", None)  # como no pythonw, aberto pelo login
    from clipador.cli import _avisar_sem_janela

    _avisar_sem_janela(tmp_path / "config.toml", "configuração com problema")
    texto = (tmp_path / "clipador-servico-erro.txt").read_text(encoding="utf-8")
    assert "configuração com problema" in texto


def test_status_com_banco_mais_novo_mostra_mensagem_clara(config_valida, tmp_path):
    engine = abrir_banco(tmp_path / "dados" / "clipador.db")
    with engine.begin() as conexao:
        conexao.exec_driver_sql("UPDATE alembic_version SET version_num = '9999'")
    engine.dispose()
    resultado = runner.invoke(app, ["status", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "git pull" in resultado.output
    assert "Traceback" not in resultado.output


def test_coletar_lol_recusa_se_o_servico_ja_coleta(config_valida, monkeypatch):
    import clipador.cli as cli

    monkeypatch.setattr(cli, "servico_esta_rodando", lambda pasta: True)
    resultado = runner.invoke(app, ["coletar-lol", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "já coleta" in resultado.output


def test_eventos_sem_gravacao_explica(config_valida):
    resultado = runner.invoke(app, ["eventos", "--arquivo", str(config_valida)])
    assert resultado.exit_code == 1
    assert "Nenhuma gravação" in resultado.output
