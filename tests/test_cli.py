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
