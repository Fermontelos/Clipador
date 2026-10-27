"""Cliente do YouTube (etapa 15), com um YouTube falso: nada vai para a internet."""

import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response, ServerNotFoundError

from clipador_integracoes import youtube


def erro_http(status: int, motivo: str = "") -> HttpError:
    corpo = {"error": {"errors": [{"reason": motivo}]}} if motivo else {}
    return HttpError(Response({"status": status}), json.dumps(corpo).encode())


class Progresso:
    def __init__(self, fracao):
        self.fracao = fracao

    def progress(self):
        return self.fracao


class PedidoFalso:
    """Um envio em pedaços: cada item de ``roteiro`` é um pedaço (ou o erro dele)."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)

    def next_chunk(self):
        passo = self.roteiro.pop(0)
        if isinstance(passo, Exception):
            raise passo
        return passo


class YoutubeFalso:
    def __init__(self, roteiro=(), erro_thumbnail=None):
        self.roteiro = roteiro
        self.erro_thumbnail = erro_thumbnail
        self.inseridos = []
        self.thumbnails_pedidos = []

    def videos(self):
        return self

    def thumbnails(self):
        return _Thumbnails(self)

    def insert(self, **pedido):
        self.inseridos.append(pedido)
        return PedidoFalso(self.roteiro)


class _Thumbnails:
    def __init__(self, youtube_falso):
        self.yt = youtube_falso

    def set(self, **pedido):
        self.yt.thumbnails_pedidos.append(pedido)
        return self

    def execute(self):
        if self.yt.erro_thumbnail:
            raise self.yt.erro_thumbnail
        return {}


@pytest.fixture
def video(tmp_path):
    arquivo = tmp_path / "clipe.mp4"
    arquivo.write_bytes(b"\x00" * 1024)
    return arquivo


def cliente(tmp_path, falso, esperas=None):
    c = youtube.ClienteYoutube(
        tmp_path / "segredos",
        construir=lambda *a, **k: falso,
        esperar=(esperas.append if esperas is not None else lambda s: None),
    )
    c._credenciais = lambda: object()  # login simulado
    return c


CORPO = {"snippet": {"title": "Título"}, "status": {"privacyStatus": "private"}}


def test_envio_em_pedacos_devolve_o_id_e_o_link(tmp_path, video):
    falso = YoutubeFalso([(Progresso(0.5), None), (None, {"id": "abc123"})])
    progresso = []
    enviado = cliente(tmp_path, falso).enviar(video, CORPO, False, progresso.append)
    assert enviado == youtube.VideoEnviado("abc123", "https://youtu.be/abc123")
    assert progresso == [0.5]
    (pedido,) = falso.inseridos
    assert pedido["part"] == "snippet,status"
    assert pedido["body"] == CORPO
    assert pedido["notifySubscribers"] is False
    assert pedido["media_body"].resumable()  # envio retomável


def test_queda_de_internet_reenvia_o_pedaco(tmp_path, video):
    falso = YoutubeFalso([ServerNotFoundError("sem internet"), erro_http(503), (None, {"id": "x"})])
    esperas = []
    assert cliente(tmp_path, falso, esperas).enviar(video, CORPO, True).id == "x"
    assert len(esperas) == 2 and esperas[0] < esperas[1]  # espera crescente


def test_desiste_depois_de_muitas_quedas(tmp_path, video):
    falso = YoutubeFalso([erro_http(500)] * (youtube.TENTATIVAS + 1))
    with pytest.raises(youtube.ErroDoYoutubeError, match="500"):
        cliente(tmp_path, falso).enviar(video, CORPO, True)


@pytest.mark.parametrize(
    ("erro", "tipo"),
    [
        (erro_http(403, "uploadLimitExceeded"), youtube.CotaEsgotadaError),
        (erro_http(403, "quotaExceeded"), youtube.CotaEsgotadaError),
        (erro_http(401, "authError"), youtube.PrecisaEntrarError),
        (erro_http(403, "forbidden"), youtube.SemPermissaoError),
        (erro_http(400, "invalidPublishAt"), youtube.ErroDoYoutubeError),
    ],
)
def test_erros_do_google_viram_erros_com_nome(tmp_path, video, erro, tipo):
    falso = YoutubeFalso([erro])
    with pytest.raises(tipo) as capturado:
        cliente(tmp_path, falso).enviar(video, CORPO, True)
    assert type(capturado.value) is tipo


def test_thumbnail(tmp_path):
    imagem = tmp_path / "thumb.jpg"
    imagem.write_bytes(b"\xff\xd8")
    falso = YoutubeFalso()
    cliente(tmp_path, falso).definir_thumbnail("abc", imagem)
    assert falso.thumbnails_pedidos[0]["videoId"] == "abc"
    falso = YoutubeFalso(erro_thumbnail=erro_http(403, "forbidden"))
    with pytest.raises(youtube.SemPermissaoError):
        cliente(tmp_path, falso).definir_thumbnail("abc", imagem)


def test_sem_login_pede_para_entrar(tmp_path, video):
    c = youtube.ClienteYoutube(tmp_path / "segredos", construir=lambda *a, **k: YoutubeFalso())
    assert not c.tem_login()
    with pytest.raises(youtube.PrecisaEntrarError, match="clipador youtube-entrar"):
        c.enviar(video, CORPO, True)


def test_entrar_sem_a_credencial_do_app_explica(tmp_path):
    with pytest.raises(youtube.CredencialDoAppAusenteError, match="App para computador"):
        youtube.ClienteYoutube(tmp_path / "segredos").entrar()


def test_login_vencido_e_renovado_e_salvo(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    pasta = tmp_path / "segredos"
    pasta.mkdir()
    (pasta / youtube.ARQUIVO_DO_TOKEN).write_text(
        json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s",
                    "token": "velho", "expiry": "2020-01-01T00:00:00Z"}),
        encoding="utf-8",
    )  # fmt: skip
    renovados = []

    def renovar(self, pedido):
        self.token = "novo"
        renovados.append(True)

    monkeypatch.setattr(Credentials, "refresh", renovar)
    monkeypatch.setattr(Credentials, "valid", property(lambda self: self.token == "novo"))
    credenciais = youtube.ClienteYoutube(pasta)._credenciais()
    assert renovados and credenciais.token == "novo"
    salvo = json.loads((pasta / youtube.ARQUIVO_DO_TOKEN).read_text(encoding="utf-8"))
    assert salvo["token"] == "novo"  # o token renovado fica salvo
