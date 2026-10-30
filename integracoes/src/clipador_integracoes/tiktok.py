"""Cliente do TikTok (etapas 16 e 17): login de app de desktop, rascunhos e métricas.

Só este pacote toca nas chaves (ADR 0003): a credencial do app (``TIKTOK_CLIENT_KEY`` e
``TIKTOK_CLIENT_SECRET``, no ``.env``) e o token do seu login (``tiktok_token.json``, na
pasta de segredos). O backend só passa os caminhos e recebe o ``publish_id``.

Conferido na documentação oficial do TikTok for Developers em 2026-10-27:

- **Login de desktop** (Login Kit): o retorno vai para ``http://127.0.0.1:{porta}/callback/``
  (só ``localhost`` ou ``127.0.0.1``, com porta; dá para cadastrar ``*`` no lugar da porta).
  **PKCE obrigatório**, com o desafio em SHA-256 **codificado em hexadecimal** (diferente do
  Google, que usa Base64URL). O ``state`` protege contra pedido forjado (CSRF).
- **Tokens**: o de acesso vale 24 h; o de renovação, 365 dias, e a renovação pode devolver
  um novo (é preciso guardar o novo).
- **Rascunho** (``video.upload``): ``/v2/post/publish/inbox/video/init/`` devolve o
  ``publish_id`` e o ``upload_url`` (vale 1 h); o vídeo vai por ``PUT`` em pedaços. O vídeo
  chega à caixa de entrada do app **sem legenda**: você finaliza (e escreve a legenda) no
  celular, pela notificação. No máximo **5 rascunhos pendentes a cada 24 h**
  (``spam_risk_too_many_pending_share``) e 6 inícios de envio por minuto.
- **Métricas** (etapa 17, Display API, permissão ``video.list``): ``/v2/video/list/`` traz
  só os vídeos **públicos**, do mais novo para o mais antigo, até 20 por página (o
  ``cursor`` é um horário em milissegundos); ``/v2/video/query/`` traz até 20 vídeos pelo ID.
  Os dois dão ``view_count``, ``like_count``, ``comment_count`` e ``share_count``; retenção,
  não. Conferido na documentação oficial em 2026-10-29.
- **Pedaços**: de 5 MB a 64 MB (o último pode chegar a 128 MB); vídeo de até 64 MB pode ir
  inteiro; ``total_chunk_count`` = tamanho ÷ pedaço, arredondado para baixo (o resto vai no
  último pedaço); enviados em ordem; 206 = falta pedaço, 201 = terminou.
"""

import hashlib
import json
import logging
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

URL_DE_LOGIN = "https://www.tiktok.com/v2/auth/authorize/"
URL_DO_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
URL_DO_RASCUNHO = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
URL_DA_LISTA = "https://open.tiktokapis.com/v2/video/list/"
URL_DA_CONSULTA = "https://open.tiktokapis.com/v2/video/query/"
PERMISSAO_DE_RASCUNHO = "video.upload"  # enviar rascunhos (o post direto exige a auditoria)
PERMISSAO_DE_LEITURA = "video.list"  # ler os seus vídeos públicos e as métricas (Display API)
CAMPOS_DO_VIDEO = (
    "id,create_time,title,video_description,share_url,duration,"
    "view_count,like_count,comment_count,share_count"
)
ARQUIVO_DO_TOKEN = "tiktok_token.json"
MENSAGEM_DO_NAVEGADOR = "Pronto! Pode fechar esta aba e voltar ao Clipador."

MB = 1024 * 1024
INTEIRO_ATE = 64 * MB  # até aqui, o vídeo vai num pedaço só
TAMANHO_DO_PEDACO = 10 * MB  # acima disso, pedaços de 10 MB (entre 5 e 64 MB)
TENTATIVAS = 5  # por pedaço, nas quedas de internet e erros 5xx
RENOVAR_ANTES = timedelta(minutes=5)  # renova o acesso um pouco antes de vencer
AVISAR_LOGIN_ANTES = timedelta(days=30)  # avisa quando o login (365 dias) estiver acabando
ESPERA_DO_LOGIN_S = 300  # 5 min para você autorizar no navegador


class ErroDoTiktokError(RuntimeError):
    """O TikTok recusou o pedido (a mensagem diz o motivo)."""


class PrecisaEntrarError(ErroDoTiktokError):
    """Sem login, ou o login venceu/foi revogado: rode ``clipador tiktok-entrar``."""


class CredencialDoAppAusenteError(ErroDoTiktokError):
    """Falta TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET no ``.env`` (veja o README)."""


class LimiteDeRascunhosError(ErroDoTiktokError):
    """Já há 5 rascunhos pendentes nas últimas 24 h: finalize no celular ou espere."""


class MuitosPedidosError(ErroDoTiktokError):
    """Limite de pedidos por minuto: tente de novo daqui a pouco."""


class ProibidoDePostarError(ErroDoTiktokError):
    """O TikTok bloqueou novos posts desta conta (não adianta tentar de novo)."""


class CredencialDoApp(BaseSettings):
    """A credencial do app no TikTok for Developers (``.env`` ou variáveis de ambiente)."""

    model_config = SettingsConfigDict(env_prefix="TIKTOK_", extra="ignore")

    client_key: str = ""
    client_secret: str = ""


def ler_credencial(arquivo_env: Path) -> CredencialDoApp:
    credencial = CredencialDoApp(_env_file=arquivo_env, _env_file_encoding="utf-8")
    if not credencial.client_key or not credencial.client_secret:
        raise CredencialDoAppAusenteError(
            f"Falta TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET em {arquivo_env}: copie do app no "
            "TikTok for Developers (veja o README)."
        )
    return credencial


@dataclass(frozen=True)
class Pkce:
    verificador: str
    desafio: str


def novo_pkce() -> Pkce:
    """Um verificador novo (43 a 128 caracteres permitidos) e o desafio: SHA-256 em hexadecimal."""
    verificador = secrets.token_urlsafe(64)[:96]  # só letras, números, "-" e "_"
    return Pkce(verificador, hashlib.sha256(verificador.encode("ascii")).hexdigest())


def dividir_em_pedacos(tamanho: int) -> tuple[int, int]:
    """``(chunk_size, total_chunk_count)`` seguindo as regras do TikTok (ver o topo)."""
    if tamanho <= 0:
        raise ValueError("O vídeo está vazio.")
    if tamanho <= INTEIRO_ATE:
        return tamanho, 1
    return TAMANHO_DO_PEDACO, tamanho // TAMANHO_DO_PEDACO


def faixas_dos_pedacos(tamanho: int) -> list[tuple[int, int]]:
    """``(primeiro_byte, ultimo_byte)`` de cada pedaço; o resto da divisão vai no último."""
    pedaco, total = dividir_em_pedacos(tamanho)
    faixas = [(i * pedaco, (i + 1) * pedaco - 1) for i in range(total)]
    faixas[-1] = (faixas[-1][0], tamanho - 1)
    return faixas


def _codigo_do_erro(resposta: httpx.Response) -> tuple[str, str]:
    """``(código, mensagem)`` do erro na resposta (o formato muda entre login e posts)."""
    try:
        corpo = resposta.json()
    except ValueError:
        return "", resposta.text[:200]
    erro = corpo.get("error")
    if isinstance(erro, dict):  # API de posts: {"error": {"code": "...", "message": "..."}}
        return str(erro.get("code", "")), str(erro.get("message", ""))
    if erro:  # login: {"error": "...", "error_description": "..."}
        return str(erro), str(corpo.get("error_description", ""))
    return "", ""


def traduzir(resposta: httpx.Response) -> ErroDoTiktokError:
    """Uma resposta de erro do TikTok como um destes erros com nome (ver o topo)."""
    codigo, mensagem = _codigo_do_erro(resposta)
    detalhe = f"{codigo or resposta.status_code}: {mensagem}".strip(": ")
    if codigo == "spam_risk_too_many_pending_share":
        return LimiteDeRascunhosError(
            "O TikTok já tem 5 rascunhos seus pendentes nas últimas 24 h: finalize os que "
            "estão na caixa de entrada do app (ou espere) e o envio continua."
        )
    if codigo == "spam_risk_user_banned_from_posting":
        return ProibidoDePostarError(f"O TikTok bloqueou novos posts desta conta ({detalhe}).")
    if codigo == "rate_limit_exceeded" or resposta.status_code == 429:
        return MuitosPedidosError(f"Muitos pedidos ao TikTok por minuto ({detalhe}).")
    if codigo in {"access_token_invalid", "scope_not_authorized", "invalid_grant"} or (
        resposta.status_code == 401
    ):
        return PrecisaEntrarError(
            f"O TikTok não aceitou o login ({detalhe}): rode 'clipador tiktok-entrar'."
        )
    return ErroDoTiktokError(f"Erro do TikTok ({detalhe}).")


@dataclass(frozen=True)
class Rascunho:
    publish_id: str


class _Retorno(BaseHTTPRequestHandler):
    """Recebe o retorno do login em ``/callback/`` (só em 127.0.0.1)."""

    resultado: dict[str, str]
    pronto: threading.Event

    def do_GET(self) -> None:  # noqa: N802 (nome exigido pelo http.server)
        endereco = urlparse(self.path)
        if endereco.path.rstrip("/") != "/callback":
            self.send_error(404)
            return
        # O primeiro retorno vale: um segundo pedido (você recarregou a página, ou o navegador
        # pediu de novo) não pode trocar o resultado antes de o app lê-lo.
        if not type(self).pronto.is_set():
            type(self).resultado = {k: v[0] for k, v in parse_qs(endereco.query).items()}
        corpo = MENSAGEM_DO_NAVEGADOR.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)
        type(self).pronto.set()

    def log_message(self, formato: str, *args: Any) -> None:
        pass  # sem poluir o terminal (e sem o código de autorização no log)


class ClienteTiktok:
    """Uma conta do TikTok: credencial do app no ``.env`` e login em ``pasta_segredos``."""

    def __init__(
        self,
        pasta_segredos: Path,
        arquivo_env: Path,
        transporte: httpx.BaseTransport | None = None,
        esperar: Callable[[float], None] = time.sleep,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.pasta = Path(pasta_segredos)
        self.arquivo_env = Path(arquivo_env)
        self.arquivo_do_token = self.pasta / ARQUIVO_DO_TOKEN
        self._transporte = transporte  # os testes passam um TikTok falso
        self._esperar = esperar
        self._agora = agora

    def _http(self) -> httpx.Client:
        """Uma conexão por pedido, fechada logo depois (o serviço fica aberto por dias)."""
        return httpx.Client(transport=self._transporte, timeout=httpx.Timeout(60.0))

    # ------------------------------------------------------------------ login

    def endereco_de_login(
        self, redirect_uri: str, estado: str, pkce: Pkce, permissoes: tuple[str, ...]
    ) -> str:
        credencial = ler_credencial(self.arquivo_env)
        parametros = {
            "client_key": credencial.client_key,
            "response_type": "code",
            "scope": ",".join(permissoes),
            "redirect_uri": redirect_uri,
            "state": estado,
            "code_challenge": pkce.desafio,
            "code_challenge_method": "S256",
        }
        return f"{URL_DE_LOGIN}?{urlencode(parametros)}"

    def entrar(
        self,
        abrir: Callable[[str], Any] = webbrowser.open,
        permissoes: tuple[str, ...] = (PERMISSAO_DE_RASCUNHO,),
    ) -> None:
        """Abre o navegador para você autorizar o app e salva o token.

        ``permissoes``: só as que você vai usar (cada uma exige o produto dela no app do
        TikTok for Developers: Content Posting API para o rascunho, Display API para ler).
        """
        ler_credencial(self.arquivo_env)  # falta a credencial: avisa antes de abrir o navegador
        pronto = threading.Event()
        retorno = type("Retorno", (_Retorno,), {"resultado": {}, "pronto": pronto})
        servidor = HTTPServer(("127.0.0.1", 0), retorno)  # porta livre, só neste PC
        redirect_uri = f"http://127.0.0.1:{servidor.server_port}/callback/"
        estado, pkce = secrets.token_urlsafe(24), novo_pkce()
        linha = threading.Thread(target=servidor.serve_forever, daemon=True)
        linha.start()
        try:
            abrir(self.endereco_de_login(redirect_uri, estado, pkce, permissoes))
            if not pronto.wait(ESPERA_DO_LOGIN_S):
                raise ErroDoTiktokError("O login não foi concluído em 5 minutos.")
        finally:
            servidor.shutdown()
            servidor.server_close()
        self.concluir_login(retorno.resultado, estado, pkce, redirect_uri, permissoes)

    def concluir_login(
        self,
        retorno: dict[str, str],
        estado: str,
        pkce: Pkce,
        redirect_uri: str,
        permissoes: tuple[str, ...] = (PERMISSAO_DE_RASCUNHO,),
    ) -> None:
        """Confere o retorno do navegador e troca o código pelo token."""
        if retorno.get("state") != estado:
            raise ErroDoTiktokError("O retorno do login não confere (state): tente de novo.")
        if retorno.get("error"):
            raise ErroDoTiktokError(
                f"O TikTok não autorizou: {retorno.get('error_description') or retorno['error']}"
            )
        concedidas = set(retorno.get("scopes", "").split(","))
        if not concedidas & set(permissoes):
            raise ErroDoTiktokError(
                f"Nenhuma das permissões pedidas ({', '.join(permissoes)}) foi concedida: "
                "autorize de novo marcando-as."
            )
        credencial = ler_credencial(self.arquivo_env)
        self._pedir_token(
            {
                "client_key": credencial.client_key,
                "client_secret": credencial.client_secret,
                "code": retorno.get("code", ""),
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": pkce.verificador,
            }
        )

    def tem_login(self) -> bool:
        return self.arquivo_do_token.is_file()

    def permissoes(self) -> set[str]:
        """As permissões que o seu login deu ao app (vazio sem login)."""
        if not self.tem_login():
            return set()
        return {p for p in self._token().get("scope", "").split(",") if p}

    def _exigir(self, permissao: str, para: str) -> None:
        if permissao not in self.permissoes():
            raise PrecisaEntrarError(
                f"O seu login no TikTok não tem a permissão {permissao} (para {para}): rode "
                "'clipador tiktok-entrar' de novo e marque-a."
            )

    def login_vale_ate(self) -> datetime | None:
        """Até quando o login vale (o token de renovação), se houver login."""
        if not self.tem_login():
            return None
        return datetime.fromisoformat(self._token()["renovacao_vence_em"])

    def _token(self) -> dict[str, Any]:
        return json.loads(self.arquivo_do_token.read_text(encoding="utf-8"))

    def _pedir_token(self, dados: dict[str, str]) -> None:
        with self._http() as http:
            resposta = http.post(
                URL_DO_TOKEN,
                data=dados,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        try:
            corpo = resposta.json()
        except ValueError:
            corpo = {}
        if resposta.status_code != 200 or "access_token" not in corpo:
            raise traduzir(resposta)
        agora = self._agora()
        token = {
            "access_token": corpo["access_token"],
            "refresh_token": corpo["refresh_token"],  # pode ser um novo: guarda sempre
            "open_id": corpo.get("open_id", ""),
            "scope": corpo.get("scope", ""),
            "acesso_vence_em": (agora + timedelta(seconds=corpo["expires_in"])).isoformat(),
            "renovacao_vence_em": (
                agora + timedelta(seconds=corpo["refresh_expires_in"])
            ).isoformat(),
        }
        self.pasta.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo_do_token.with_suffix(".parcial")
        temporario.write_text(json.dumps(token, indent=2), encoding="utf-8")
        temporario.replace(self.arquivo_do_token)  # nunca fica um token pela metade

    def _acesso(self) -> str:
        """O token de acesso válido (renovado sozinho quando está para vencer)."""
        if not self.tem_login():
            raise PrecisaEntrarError("Ainda não há login no TikTok: rode 'clipador tiktok-entrar'.")
        token = self._token()
        agora = self._agora()
        renovacao_vence = datetime.fromisoformat(token["renovacao_vence_em"])
        if renovacao_vence <= agora:
            raise PrecisaEntrarError("O login no TikTok venceu: rode 'clipador tiktok-entrar'.")
        if renovacao_vence - agora < AVISAR_LOGIN_ANTES:
            log.warning(
                "O login no TikTok vence em %s: rode 'clipador tiktok-entrar' antes disso.",
                renovacao_vence.astimezone().strftime("%d/%m/%Y"),
            )
        if datetime.fromisoformat(token["acesso_vence_em"]) - agora > RENOVAR_ANTES:
            return token["access_token"]
        credencial = ler_credencial(self.arquivo_env)
        self._pedir_token(
            {
                "client_key": credencial.client_key,
                "client_secret": credencial.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            }
        )
        return self._token()["access_token"]

    # ------------------------------------------------------------------ rascunho

    def enviar_rascunho(self, arquivo: Path) -> Rascunho:
        """Manda o vídeo para a caixa de entrada do app (você finaliza no celular)."""
        self._exigir(PERMISSAO_DE_RASCUNHO, "enviar rascunhos")
        tamanho = arquivo.stat().st_size
        pedaco, total = dividir_em_pedacos(tamanho)
        with self._http() as http:
            resposta = http.post(
                URL_DO_RASCUNHO,
                headers={
                    "Authorization": f"Bearer {self._acesso()}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": tamanho,
                        "chunk_size": pedaco,
                        "total_chunk_count": total,
                    }
                },
            )
        codigo, _ = _codigo_do_erro(resposta)
        if resposta.status_code != 200 or codigo != "ok":
            raise traduzir(resposta)
        dados = resposta.json()["data"]
        self._mandar_pedacos(dados["upload_url"], arquivo, tamanho)
        log.info("Rascunho enviado ao TikTok: %s (%s).", dados["publish_id"], arquivo.name)
        return Rascunho(publish_id=dados["publish_id"])

    def _mandar_pedacos(self, upload_url: str, arquivo: Path, tamanho: int) -> None:
        faixas = faixas_dos_pedacos(tamanho)
        with arquivo.open("rb") as entrada:
            for numero, (primeiro, ultimo) in enumerate(faixas, start=1):
                entrada.seek(primeiro)
                bytes_ = entrada.read(ultimo - primeiro + 1)
                esperado = 201 if numero == len(faixas) else 206
                self._mandar_pedaco(upload_url, bytes_, primeiro, ultimo, tamanho, esperado)

    def _mandar_pedaco(
        self, url: str, bytes_: bytes, primeiro: int, ultimo: int, tamanho: int, esperado: int
    ) -> None:
        cabecalhos = {
            "Content-Type": "video/mp4",
            "Content-Length": str(len(bytes_)),
            "Content-Range": f"bytes {primeiro}-{ultimo}/{tamanho}",
        }
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                with self._http() as http:
                    resposta = http.put(url, content=bytes_, headers=cabecalhos)
            except httpx.TransportError as erro:  # internet caiu, DNS, tempo esgotado
                problema: str = str(erro)
            else:
                if resposta.status_code == esperado:  # 206 = falta pedaço; 201 = terminou
                    return
                if resposta.status_code < 500:
                    raise ErroDoTiktokError(
                        f"O TikTok recusou o pedaço {primeiro}-{ultimo} "
                        f"(HTTP {resposta.status_code}; 403 = o endereço de envio venceu)."
                    )
                problema = f"HTTP {resposta.status_code}"
            if tentativa == TENTATIVAS:
                raise ErroDoTiktokError(f"O envio ao TikTok não terminou ({problema}).")
            espera = min(2**tentativa, 30)
            log.warning("Envio ao TikTok interrompido (%s); de novo em %d s.", problema, espera)
            self._esperar(espera)

    # ------------------------------------------------------------------ métricas (etapa 17)

    def _pedir_videos(self, url: str, corpo: dict[str, Any]) -> dict[str, Any]:
        self._exigir(PERMISSAO_DE_LEITURA, "ler os seus vídeos e as métricas")
        with self._http() as http:
            resposta = http.post(
                url,
                params={"fields": CAMPOS_DO_VIDEO},
                headers={
                    "Authorization": f"Bearer {self._acesso()}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json=corpo,
            )
        codigo, _ = _codigo_do_erro(resposta)
        if resposta.status_code != 200 or codigo != "ok":
            raise traduzir(resposta)
        return resposta.json().get("data", {})

    def listar_videos(self, antes_de_ms: int | None = None) -> tuple[list[dict], int, bool]:
        """Uma página dos seus vídeos públicos (até 20, do mais novo para o mais antigo).

        Devolve ``(videos, cursor, tem_mais)``: passe o ``cursor`` para a próxima página.
        """
        corpo: dict[str, Any] = {"max_count": 20}
        if antes_de_ms is not None:
            corpo["cursor"] = antes_de_ms
        dados = self._pedir_videos(URL_DA_LISTA, corpo)
        return (
            list(dados.get("videos", [])),
            int(dados.get("cursor") or 0),
            bool(dados.get("has_more")),
        )

    def consultar_videos(self, video_ids: list[str]) -> dict[str, dict]:
        """Os vídeos pedidos (até 20), pelo ID; os que não voltam (apagados) ficam de fora."""
        if not 0 < len(video_ids) <= 20:
            raise ValueError("De 1 a 20 vídeos por consulta.")
        dados = self._pedir_videos(URL_DA_CONSULTA, {"filters": {"video_ids": video_ids}})
        return {str(v["id"]): v for v in dados.get("videos", [])}
