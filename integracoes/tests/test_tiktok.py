"""Cliente do TikTok (etapa 16), com um TikTok falso: nada vai para a internet."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from clipador_integracoes import tiktok

AGORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MB = 1024 * 1024


class TiktokFalso:
    """Responde como a API do TikTok; guarda os pedidos para os testes conferirem."""

    def __init__(self):
        self.pedidos: list[httpx.Request] = []
        self.respostas_dos_pedacos: list[int | Exception] = []
        self.erro_do_inicio: tuple[int, str] | None = None
        self.token_novo = "act.novo"
        self.videos: list[dict] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        url = str(pedido.url)
        if url == tiktok.URL_DO_TOKEN:
            return httpx.Response(
                200,
                json={
                    "access_token": self.token_novo,
                    "refresh_token": "rft.novo",
                    "open_id": "eu",
                    "scope": "video.upload",
                    "expires_in": 86400,
                    "refresh_expires_in": 31536000,
                    "token_type": "Bearer",
                },
            )
        if url == tiktok.URL_DO_RASCUNHO:
            if self.erro_do_inicio:
                status, codigo = self.erro_do_inicio
                return httpx.Response(status, json={"error": {"code": codigo, "message": "x"}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "v_inbox_file~1",
                        "upload_url": "https://up.tiktok/v?x=1",
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if url.startswith("https://up.tiktok/"):
            proxima = self.respostas_dos_pedacos.pop(0)
            if isinstance(proxima, Exception):
                raise proxima
            return httpx.Response(proxima)
        if url.startswith((tiktok.URL_DA_LISTA, tiktok.URL_DA_CONSULTA)):
            assert pedido.url.params["fields"] == tiktok.CAMPOS_DO_VIDEO
            if url.startswith(tiktok.URL_DA_CONSULTA):
                pedidos = json.loads(pedido.content)["filters"]["video_ids"]
                videos = [v for v in self.videos if v["id"] in pedidos]
                return httpx.Response(200, json={"data": {"videos": videos},
                                                 "error": {"code": "ok"}})  # fmt: skip
            return httpx.Response(
                200,
                json={"data": {"videos": self.videos, "cursor": 123, "has_more": True},
                      "error": {"code": "ok"}},
            )  # fmt: skip
        return httpx.Response(404)

    def dos(self, prefixo: str) -> list[httpx.Request]:
        return [p for p in self.pedidos if str(p.url).startswith(prefixo)]


@pytest.fixture
def falso():
    return TiktokFalso()


@pytest.fixture
def env(tmp_path):
    arquivo = tmp_path / ".env"
    arquivo.write_text("TIKTOK_CLIENT_KEY=chave\nTIKTOK_CLIENT_SECRET=segredo\n", encoding="utf-8")
    return arquivo


def cliente(tmp_path, env, falso, agora=AGORA, esperas=None):
    return tiktok.ClienteTiktok(
        tmp_path / "segredos",
        env,
        transporte=httpx.MockTransport(falso),
        esperar=(esperas.append if esperas is not None else lambda s: None),
        agora=lambda: agora,
    )


def salvar_token(
    cli,
    acesso_vence_em,
    renovacao_vence_em=AGORA + timedelta(days=300),
    scope="video.upload,video.list",
):
    cli.pasta.mkdir(parents=True, exist_ok=True)
    cli.arquivo_do_token.write_text(
        json.dumps(
            {
                "access_token": "act.velho",
                "refresh_token": "rft.velho",
                "open_id": "eu",
                "scope": scope,
                "acesso_vence_em": acesso_vence_em.isoformat(),
                "renovacao_vence_em": renovacao_vence_em.isoformat(),
            }
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ regras da documentação


def test_desafio_do_pkce_e_sha256_em_hexadecimal():
    pkce = tiktok.novo_pkce()
    assert 43 <= len(pkce.verificador) <= 128
    assert pkce.desafio == hashlib.sha256(pkce.verificador.encode()).hexdigest()
    assert tiktok.novo_pkce().verificador != pkce.verificador  # um novo a cada login


@pytest.mark.parametrize(
    ("tamanho", "esperado"),
    [
        (4 * MB, (4 * MB, 1)),  # menos de 5 MB: inteiro
        (64 * MB, (64 * MB, 1)),  # até 64 MB pode ir inteiro
        (64 * MB + 1, (10 * MB, 6)),  # acima: pedaços de 10 MB, resto no último
        (50_000_123, (50_000_123, 1)),
    ],
)
def test_pedacos_seguem_as_regras_do_tiktok(tamanho, esperado):
    assert tiktok.dividir_em_pedacos(tamanho) == esperado


def test_resto_da_divisao_vai_no_ultimo_pedaco():
    tamanho = 100 * MB + 123
    faixas = tiktok.faixas_dos_pedacos(tamanho)
    assert len(faixas) == 10
    assert faixas[0] == (0, 10 * MB - 1)
    assert faixas[-1] == (90 * MB, tamanho - 1)  # 10 MB + 123 bytes: entre 5 e 128 MB
    assert all(b + 1 == faixas[i + 1][0] for i, (_, b) in enumerate(faixas[:-1]))


def test_sem_credencial_do_app_explica(tmp_path, falso):
    cli = tiktok.ClienteTiktok(tmp_path, tmp_path / ".env", transporte=httpx.MockTransport(falso))
    with pytest.raises(tiktok.CredencialDoAppAusenteError, match="TIKTOK_CLIENT_KEY"):
        cli.entrar(abrir=lambda url: None)


# ------------------------------------------------------------------ login


def test_endereco_de_login_tem_pkce_state_e_as_permissoes_pedidas(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    pkce = tiktok.novo_pkce()
    pedidas = (tiktok.PERMISSAO_DE_RASCUNHO, tiktok.PERMISSAO_DE_LEITURA)
    url = urlparse(cli.endereco_de_login("http://127.0.0.1:5000/callback/", "abc", pkce, pedidas))
    assert f"{url.scheme}://{url.netloc}{url.path}" == tiktok.URL_DE_LOGIN
    parametros = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert parametros == {
        "client_key": "chave",
        "response_type": "code",
        "scope": "video.upload,video.list",
        "redirect_uri": "http://127.0.0.1:5000/callback/",
        "state": "abc",
        "code_challenge": pkce.desafio,
        "code_challenge_method": "S256",
    }


def test_concluir_login_troca_o_codigo_pelo_token(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    pkce = tiktok.novo_pkce()
    retorno = {"code": "c123", "state": "abc", "scopes": "user.info.basic,video.upload"}
    cli.concluir_login(retorno, "abc", pkce, "http://127.0.0.1:5000/callback/")
    enviado = parse_qs(falso.dos(tiktok.URL_DO_TOKEN)[0].content.decode())
    assert enviado["code_verifier"] == [pkce.verificador]
    assert enviado["grant_type"] == ["authorization_code"]
    assert enviado["redirect_uri"] == ["http://127.0.0.1:5000/callback/"]
    assert cli.login_vale_ate() == AGORA + timedelta(days=365)


@pytest.mark.parametrize(
    ("retorno", "mensagem"),
    [
        ({"code": "c", "state": "outro", "scopes": "video.upload"}, "state"),
        ({"error": "access_denied", "state": "abc"}, "não autorizou"),
        ({"code": "c", "state": "abc", "scopes": "user.info.basic"}, "video.upload"),
    ],
)
def test_login_recusado_ou_forjado_nao_salva_nada(tmp_path, env, falso, retorno, mensagem):
    cli = cliente(tmp_path, env, falso)
    with pytest.raises(tiktok.ErroDoTiktokError, match=mensagem):
        cli.concluir_login(retorno, "abc", tiktok.novo_pkce(), "http://127.0.0.1:1/callback/")
    assert not cli.tem_login()
    assert falso.dos(tiktok.URL_DO_TOKEN) == []


def test_entrar_pelo_navegador_de_ponta_a_ponta(tmp_path, env, falso):
    # O "navegador" deste teste visita o endereço de retorno como o TikTok faria.
    cli = cliente(tmp_path, env, falso)

    def navegador(url):
        parametros = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert urlparse(parametros["redirect_uri"]).hostname == "127.0.0.1"
        httpx.get(
            parametros["redirect_uri"],
            params={"code": "c1", "state": parametros["state"], "scopes": "video.upload"},
        )
        httpx.get(parametros["redirect_uri"])  # recarregou a página: não pode estragar o login

    cli.entrar(abrir=navegador)
    assert cli.tem_login()


def test_acesso_vencendo_e_renovado_e_o_novo_refresh_token_e_guardado(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, acesso_vence_em=AGORA + timedelta(minutes=2))
    assert cli._acesso() == "act.novo"
    enviado = parse_qs(falso.dos(tiktok.URL_DO_TOKEN)[0].content.decode())
    assert enviado["grant_type"] == ["refresh_token"]
    assert enviado["refresh_token"] == ["rft.velho"]
    assert json.loads(cli.arquivo_do_token.read_text())["refresh_token"] == "rft.novo"


def test_acesso_valido_nao_renova(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, acesso_vence_em=AGORA + timedelta(hours=10))
    assert cli._acesso() == "act.velho"
    assert falso.dos(tiktok.URL_DO_TOKEN) == []


def test_login_vencido_pede_para_entrar(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA - timedelta(days=1), renovacao_vence_em=AGORA - timedelta(hours=1))
    with pytest.raises(tiktok.PrecisaEntrarError, match="tiktok-entrar"):
        cli._acesso()


def test_login_acabando_avisa(tmp_path, env, falso, caplog):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10), renovacao_vence_em=AGORA + timedelta(days=10))
    cli._acesso()
    assert "vence em" in caplog.text


def test_sem_login_pede_para_entrar(tmp_path, env, falso):
    with pytest.raises(tiktok.PrecisaEntrarError):
        cliente(tmp_path, env, falso)._acesso()


# ------------------------------------------------------------------ rascunho


def video(tmp_path, tamanho):
    arquivo = tmp_path / "clipe.mp4"
    with arquivo.open("wb") as saida:
        saida.truncate(tamanho)
    return arquivo


def test_rascunho_pequeno_vai_inteiro(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.respostas_dos_pedacos = [201]
    rascunho = cli.enviar_rascunho(video(tmp_path, 3 * MB))
    assert rascunho.publish_id == "v_inbox_file~1"
    inicio = falso.dos(tiktok.URL_DO_RASCUNHO)[0]
    assert inicio.headers["Authorization"] == "Bearer act.velho"
    assert json.loads(inicio.content) == {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": 3 * MB,
            "chunk_size": 3 * MB,
            "total_chunk_count": 1,
        }
    }
    (pedaco,) = falso.dos("https://up.tiktok/")
    assert pedaco.method == "PUT"
    assert pedaco.headers["Content-Range"] == f"bytes 0-{3 * MB - 1}/{3 * MB}"
    assert pedaco.headers["Content-Type"] == "video/mp4"


def test_rascunho_grande_vai_em_pedacos_em_ordem(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    tamanho = 70 * MB
    falso.respostas_dos_pedacos = [206] * 6 + [201]
    cli.enviar_rascunho(video(tmp_path, tamanho))
    faixas = [p.headers["Content-Range"] for p in falso.dos("https://up.tiktok/")]
    assert faixas[0] == f"bytes 0-{10 * MB - 1}/{tamanho}"
    assert faixas[-1] == f"bytes {60 * MB}-{tamanho - 1}/{tamanho}"
    assert len(faixas) == 7


def test_queda_de_internet_reenvia_o_pedaco(tmp_path, env, falso):
    esperas = []
    cli = cliente(tmp_path, env, falso, esperas=esperas)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.respostas_dos_pedacos = [httpx.ConnectError("caiu"), 503, 201]
    cli.enviar_rascunho(video(tmp_path, MB))
    assert len(falso.dos("https://up.tiktok/")) == 3
    assert esperas == [2, 4]


def test_endereco_de_envio_vencido_nao_insiste(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.respostas_dos_pedacos = [403]
    with pytest.raises(tiktok.ErroDoTiktokError, match="403"):
        cli.enviar_rascunho(video(tmp_path, MB))
    assert len(falso.dos("https://up.tiktok/")) == 1


@pytest.mark.parametrize(
    ("status", "codigo", "erro"),
    [
        (403, "spam_risk_too_many_pending_share", tiktok.LimiteDeRascunhosError),
        (403, "spam_risk_user_banned_from_posting", tiktok.ProibidoDePostarError),
        (429, "rate_limit_exceeded", tiktok.MuitosPedidosError),
        (401, "access_token_invalid", tiktok.PrecisaEntrarError),
        (401, "scope_not_authorized", tiktok.PrecisaEntrarError),
        (400, "invalid_param", tiktok.ErroDoTiktokError),
    ],
)
def test_erros_do_tiktok_viram_erros_com_nome(tmp_path, env, falso, status, codigo, erro):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.erro_do_inicio = (status, codigo)
    with pytest.raises(erro):
        cli.enviar_rascunho(video(tmp_path, MB))
    assert falso.dos("https://up.tiktok/") == []


# ------------------------------------------------------------------ métricas (etapa 17)

VIDEO = {"id": "7300", "create_time": 1761700000, "title": "NÃO ACREDITO #lol",
         "view_count": 900, "like_count": 70, "comment_count": 4, "share_count": 9}  # fmt: skip


def test_listar_os_videos_publicos(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.videos = [VIDEO]
    videos, cursor, tem_mais = cli.listar_videos(antes_de_ms=1_761_800_000_000)
    assert (videos, cursor, tem_mais) == ([VIDEO], 123, True)
    (pedido,) = falso.dos(tiktok.URL_DA_LISTA)
    assert json.loads(pedido.content) == {"max_count": 20, "cursor": 1_761_800_000_000}


def test_consultar_videos_pelo_id(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10))
    falso.videos = [VIDEO]
    assert cli.consultar_videos(["7300", "apagado"]) == {"7300": VIDEO}
    with pytest.raises(ValueError):
        cli.consultar_videos([str(i) for i in range(21)])


def test_sem_a_permissao_de_leitura_pede_para_entrar_de_novo(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10), scope="video.upload")
    with pytest.raises(tiktok.PrecisaEntrarError, match="video.list"):
        cli.listar_videos()
    assert falso.dos(tiktok.URL_DA_LISTA) == []


def test_sem_a_permissao_de_rascunho_nao_envia(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    salvar_token(cli, AGORA + timedelta(hours=10), scope="video.list")
    with pytest.raises(tiktok.PrecisaEntrarError, match="video.upload"):
        cli.enviar_rascunho(video(tmp_path, MB))


def test_login_com_parte_das_permissoes_vale(tmp_path, env, falso):
    cli = cliente(tmp_path, env, falso)
    pedidas = (tiktok.PERMISSAO_DE_RASCUNHO, tiktok.PERMISSAO_DE_LEITURA)
    retorno = {"code": "c", "state": "abc", "scopes": "video.list"}
    cli.concluir_login(retorno, "abc", tiktok.novo_pkce(), "http://127.0.0.1:1/callback/", pedidas)
    assert cli.tem_login()  # cada ação confere a permissão dela
