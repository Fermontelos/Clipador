"""Cliente do YouTube Data API v3 (etapa 15): login, envio retomável e thumbnail.

Só este pacote toca nos arquivos secretos do Google (ADR 0003): o ``client_secret.json``
(a credencial do app, criada por você no Google Cloud) e o token do seu login, os dois na
pasta de segredos (``segredos/``, fora do git). O backend só passa a pasta e recebe os IDs.

- **Login uma vez**: abre o navegador, você autoriza, e o token fica salvo. Ele vence em
  ~1 h, mas traz um *refresh token* que o renova sozinho (com o app "Em produção" no Google
  Cloud; em "Teste", o Google cancela o refresh token em 7 dias).
- **Permissão mínima**: só ``youtube.upload`` (enviar vídeos e trocar a thumbnail). A etapa
  17 vai pedir a de leitura das métricas, com um novo login.
- **Envio retomável**: o vídeo sobe em pedaços de 8 MB; se a internet cair no meio, o
  pedaço é reenviado (até ``TENTATIVAS``, com espera crescente) sem recomeçar do zero.
- Os erros do Google viram erros com nome: precisa entrar de novo, cota esgotada, sem
  permissão (ex.: thumbnail num canal sem o recurso) ou outro.

Cotas, conferidas na documentação oficial em 2026-10-27: ``videos.insert`` gasta 1 de 100
envios por dia (cota própria); ``thumbnails.set``, 50 das 10.000 unidades diárias. As duas
renovam à meia-noite no horário do Pacífico. A conta fica com o backend (ele sabe o que já
foi enviado); aqui só se avisa quando o Google recusa por cota.
"""

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PERMISSOES = ["https://www.googleapis.com/auth/youtube.upload"]
ARQUIVO_DO_APP = "client_secret.json"
ARQUIVO_DO_TOKEN = "youtube_token.json"
TAMANHO_DO_PEDACO = 8 * 1024 * 1024  # 8 MB: múltiplo de 256 KB, como o Google exige
TENTATIVAS = 8
ERROS_PASSAGEIROS = {500, 502, 503, 504}
MOTIVOS_DE_COTA = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}
MENSAGEM_DO_NAVEGADOR = "Pronto! Pode fechar esta aba e voltar ao Clipador."


class ErroDoYoutubeError(RuntimeError):
    """O YouTube recusou o pedido (a mensagem diz o motivo)."""


class PrecisaEntrarError(ErroDoYoutubeError):
    """Sem login, ou o login venceu/foi revogado: rode ``clipador youtube-entrar``."""


class CredencialDoAppAusenteError(ErroDoYoutubeError):
    """Falta o ``client_secret.json`` (criado por você no Google Cloud)."""


class CotaEsgotadaError(ErroDoYoutubeError):
    """O Google recusou por cota: espere renovar (meia-noite no Pacífico)."""


class SemPermissaoError(ErroDoYoutubeError):
    """O canal não pode fazer isto (ex.: thumbnail personalizada sem o recurso liberado)."""


@dataclass(frozen=True)
class VideoEnviado:
    id: str
    link: str


def _motivo(erro: Exception) -> str:
    """O ``reason`` de um ``HttpError`` do Google (ex.: "quotaExceeded"), se houver."""
    conteudo = getattr(erro, "content", b"") or b""
    try:
        detalhes = json.loads(conteudo.decode("utf-8", "replace"))["error"]["errors"]
        return detalhes[0].get("reason", "")
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _status(erro: Exception) -> int | None:
    resposta = getattr(erro, "resp", None)
    return getattr(resposta, "status", None)


def traduzir(erro: Exception) -> ErroDoYoutubeError:
    """O erro do Google como um destes erros com nome (ver o topo)."""
    from google.auth.exceptions import RefreshError

    if isinstance(erro, ErroDoYoutubeError):
        return erro
    if isinstance(erro, RefreshError):
        return PrecisaEntrarError(
            "O login no YouTube venceu ou foi revogado: rode 'clipador youtube-entrar'."
        )
    status, motivo = _status(erro), _motivo(erro)
    if motivo in MOTIVOS_DE_COTA:
        return CotaEsgotadaError(f"O YouTube recusou por cota ({motivo}).")
    if status == 401:
        return PrecisaEntrarError("O YouTube não aceitou o login: rode 'clipador youtube-entrar'.")
    if status == 403:
        return SemPermissaoError(f"O YouTube não deixou ({motivo or 'forbidden'}): {erro}")
    return ErroDoYoutubeError(f"Erro do YouTube ({status or '?'} {motivo}): {erro}")


class ClienteYoutube:
    """Um canal do YouTube, com os arquivos do Google em ``pasta_segredos``."""

    def __init__(
        self,
        pasta_segredos: Path,
        construir: Callable[..., Any] | None = None,
        esperar: Callable[[float], None] = time.sleep,
    ) -> None:
        self.pasta = Path(pasta_segredos)
        self.arquivo_do_app = self.pasta / ARQUIVO_DO_APP
        self.arquivo_do_token = self.pasta / ARQUIVO_DO_TOKEN
        self._construir = construir  # os testes passam um YouTube falso
        self._esperar = esperar
        self._servico: Any = None

    # ------------------------------------------------------------------ login

    def entrar(self) -> None:
        """Abre o navegador para você autorizar o app no seu canal e salva o token."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.arquivo_do_app.is_file():
            raise CredencialDoAppAusenteError(
                f"Falta {self.arquivo_do_app}: crie a credencial 'App para computador' no "
                "Google Cloud e salve o JSON com esse nome (veja o README)."
            )
        fluxo = InstalledAppFlow.from_client_secrets_file(str(self.arquivo_do_app), PERMISSOES)
        # Porta livre em 127.0.0.1 (nunca exposta na rede); o Google volta para ela.
        credenciais = fluxo.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, success_message=MENSAGEM_DO_NAVEGADOR
        )
        self._salvar(credenciais)

    def tem_login(self) -> bool:
        return self.arquivo_do_token.is_file()

    def _salvar(self, credenciais: Any) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo_do_token.with_suffix(".parcial")
        temporario.write_text(credenciais.to_json(), encoding="utf-8")
        temporario.replace(self.arquivo_do_token)  # nunca fica um token pela metade

    def _credenciais(self) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self.tem_login():
            raise PrecisaEntrarError(
                "Ainda não há login no YouTube: rode 'clipador youtube-entrar'."
            )
        credenciais = Credentials.from_authorized_user_file(str(self.arquivo_do_token), PERMISSOES)
        if not credenciais.valid:
            if not credenciais.refresh_token:
                raise PrecisaEntrarError("O login venceu: rode 'clipador youtube-entrar'.")
            try:
                credenciais.refresh(Request())
            except Exception as erro:
                raise traduzir(erro) from erro
            self._salvar(credenciais)
        return credenciais

    def _youtube(self) -> Any:
        if self._servico is None:
            construir = self._construir
            if construir is None:
                from googleapiclient.discovery import build as construir
            # static_discovery: a descrição da API vem dentro da biblioteca (sem baixar).
            self._servico = construir(
                "youtube", "v3", credentials=self._credenciais(), static_discovery=True
            )
        return self._servico

    # ------------------------------------------------------------------ envio

    def enviar(
        self,
        arquivo: Path,
        corpo: dict,
        notificar_inscritos: bool,
        ao_progredir: Callable[[float], None] | None = None,
    ) -> VideoEnviado:
        """Envia o vídeo com os metadados de ``corpo`` (``snippet`` e ``status``)."""
        from googleapiclient.http import MediaFileUpload

        midia = MediaFileUpload(
            str(arquivo), mimetype="video/mp4", chunksize=TAMANHO_DO_PEDACO, resumable=True
        )
        try:
            pedido = (
                self._youtube()
                .videos()
                .insert(
                    part=",".join(corpo),
                    body=corpo,
                    media_body=midia,
                    notifySubscribers=notificar_inscritos,
                )
            )
        except Exception as erro:
            raise traduzir(erro) from erro
        resposta = self._ate_o_fim(pedido, ao_progredir)
        video_id = resposta["id"]
        log.info("Vídeo enviado ao YouTube: %s (%s).", video_id, arquivo.name)
        return VideoEnviado(id=video_id, link=f"https://youtu.be/{video_id}")

    def _ate_o_fim(self, pedido: Any, ao_progredir: Callable[[float], None] | None) -> dict:
        """Manda os pedaços até o fim, repetindo os que falharem por erro passageiro."""
        from httplib2 import HttpLib2Error

        resposta = None
        falhas = 0
        while resposta is None:
            try:
                progresso, resposta = pedido.next_chunk()
                falhas = 0
                if progresso is not None and ao_progredir is not None:
                    ao_progredir(progresso.progress())
            except Exception as erro:
                passageiro = _status(erro) in ERROS_PASSAGEIROS or isinstance(
                    erro,
                    (OSError, HttpLib2Error),  # internet caiu, DNS, tempo esgotado
                )
                if not passageiro or falhas >= TENTATIVAS:
                    raise traduzir(erro) from erro
                falhas += 1
                espera = min(2**falhas, 64) + random.random()  # noqa: S311 (não é segurança)
                log.warning("Envio interrompido (%s); tentando de novo em %.0f s.", erro, espera)
                self._esperar(espera)
        return resposta

    def definir_thumbnail(self, video_id: str, imagem: Path) -> None:
        """Troca a thumbnail (50 unidades de cota). ``SemPermissaoError`` se o canal não pode."""
        from googleapiclient.http import MediaFileUpload

        try:
            self._youtube().thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(imagem), mimetype="image/jpeg")
            ).execute()
        except Exception as erro:
            raise traduzir(erro) from erro
