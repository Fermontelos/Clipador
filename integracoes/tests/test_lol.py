"""Cliente da Live Client Data API, com um servidor falso (sem o jogo aberto)."""

import ssl

import httpx

from clipador_integracoes import lol

EVENTOS = {"Events": [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]}


def cliente_com(respostas: dict[str, httpx.Response | Exception]) -> lol.ClienteLol:
    """Um cliente cujas consultas são respondidas por ``respostas`` (por endpoint)."""
    pedidos: list[str] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        endpoint = pedido.url.path.rsplit("/", 1)[-1]
        pedidos.append(endpoint)
        resposta = respostas.get(endpoint, httpx.Response(404))
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    cliente = lol.ClienteLol(transporte=httpx.MockTransport(responder))
    cliente.pedidos = pedidos
    return cliente


def test_le_tempo_de_jogo_eventos_e_nome():
    cliente = cliente_com(
        {
            "gamestats": httpx.Response(200, json={"gameTime": 125.5, "gameMode": "CLASSIC"}),
            "eventdata": httpx.Response(200, json=EVENTOS),
            "activeplayername": httpx.Response(200, json="Riot Tuxedo#TXC1"),
        }
    )
    leitura = cliente.ler(com_nome=True)
    assert leitura.tempo_jogo_s == 125.5
    assert leitura.eventos == EVENTOS["Events"]
    assert leitura.nome_jogador == "Riot Tuxedo#TXC1"
    assert leitura.horario_pc > 1_700_000_000  # horário do PC, em segundos desde 1970


def test_nome_so_e_pedido_quando_preciso():
    cliente = cliente_com(
        {
            "gamestats": httpx.Response(200, json={"gameTime": 10.0}),
            "eventdata": httpx.Response(200, json=EVENTOS),
        }
    )
    assert cliente.ler().nome_jogador is None
    assert "activeplayername" not in cliente.pedidos


def test_sem_partida_devolve_none():
    # Fora de partida, nada responde em 127.0.0.1:2999: a conexão é recusada.
    recusada = httpx.ConnectError("connection refused")
    assert cliente_com({"gamestats": recusada}).ler() is None


def test_tela_de_carregamento_devolve_none():
    # Enquanto o jogo carrega, a API pode responder erro ou um JSON sem o tempo de jogo.
    assert cliente_com({"gamestats": httpx.Response(404)}).ler() is None
    sem_tempo = {"gamestats": httpx.Response(200, json={"errorCode": "RESOURCE_NOT_FOUND"})}
    assert cliente_com(sem_tempo).ler() is None
    assert cliente_com({"gamestats": httpx.Response(200, text="não é json")}).ler() is None


def test_conexao_exige_o_certificado_da_riot():
    contexto = lol.contexto_ssl()
    assert contexto.verify_mode == ssl.CERT_REQUIRED
    assert contexto.check_hostname is True
    autoridades = [dict(campo[0] for campo in ca["subject"]) for ca in contexto.get_ca_certs()]
    assert {"commonName": "LoL Game Engineering Certificate Authority"}.items() <= autoridades[
        0
    ].items()
    assert len(autoridades) == 1  # só a Riot, e não as autoridades públicas do Windows
