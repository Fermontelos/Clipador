"""Cliente da Live Client Data API do League of Legends (etapa 7).

Durante uma partida, o próprio jogo abre uma API só no seu PC, em
``https://127.0.0.1:2999/liveclientdata/`` (documentação oficial:
https://developer.riotgames.com/docs/lol, seção "Game Client API"). Ela não pede chave.
Fora de partida, nada responde nesse endereço.

A cada leitura, o cliente pede:

- ``gamestats``: o tempo de jogo atual (``gameTime``, em segundos);
- ``eventdata``: a lista de eventos da partida até agora (``EventID``, ``EventName``,
  ``EventTime`` e os campos de cada tipo);
- ``activeplayername``: o seu Riot ID (``Nome#TAG``), uma vez por partida.

**Certificado.** O jogo usa um certificado próprio, assinado pela autoridade certificadora
da Riot ("LoL Game Engineering Certificate Authority", em ``certificados/riotgames.pem``,
baixado de https://static.developer.riotgames.com/docs/lol/riotgames.pem; vale até 2043).
A conexão só é aceita se o certificado for assinado por ela e for emitido para
``127.0.0.1`` (verificação completa; conferido numa partida real em 2026-10-19: sem o
``riotgames.pem``, a conexão é recusada).

**Horário da leitura.** Para o alinhamento de tempo (``clipador.jogo.alinhamento``), cada
leitura guarda o horário do PC no meio da consulta ao ``gamestats``: assim o atraso da
própria consulta (alguns milissegundos) se divide igualmente antes e depois.
"""

import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ENDERECO = "https://127.0.0.1:2999/liveclientdata/"
CERTIFICADO = Path(__file__).with_name("certificados") / "riotgames.pem"
# A API é local: se não respondeu em 1 s, não há partida (ou o jogo está carregando).
TEMPO_LIMITE_S = 1.0


@dataclass(frozen=True)
class Leitura:
    """O que o jogo disse numa consulta."""

    # Horário do PC (segundos desde 1970, UTC) no meio da consulta ao gamestats.
    horario_pc: float
    tempo_jogo_s: float
    eventos: list[dict[str, Any]] = field(default_factory=list)
    nome_jogador: str | None = None


def contexto_ssl() -> ssl.SSLContext:
    """Aceita só certificados assinados pela Riot (e só ela: não as autoridades públicas)."""
    return ssl.create_default_context(cafile=str(CERTIFICADO))


class ClienteLol:
    """Lê o estado da partida. Use com ``with`` para fechar a conexão no fim."""

    def __init__(self, endereco: str = ENDERECO, transporte: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=endereco,
            verify=contexto_ssl(),
            timeout=TEMPO_LIMITE_S,
            transport=transporte,  # os testes passam um transporte falso
        )

    def __enter__(self) -> "ClienteLol":
        return self

    def __exit__(self, *erro: object) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._http.close()

    def ler(self, com_nome: bool = False) -> Leitura | None:
        """Lê a partida agora. Devolve ``None`` se não houver partida (o jogo não respondeu).

        ``com_nome``: pede também o seu Riot ID (basta uma vez por partida).
        """
        try:
            antes = time.time()
            estatisticas = self._json("gamestats")
            depois = time.time()
            eventos = self._json("eventdata")
            nome = self._json("activeplayername") if com_nome else None
        except (httpx.HTTPError, ValueError):
            # Sem partida: a conexão é recusada. Na tela de carregamento: erro ou JSON
            # incompleto. Nos dois casos, não há o que ler ainda.
            return None
        if not isinstance(estatisticas, dict) or "gameTime" not in estatisticas:
            return None
        return Leitura(
            horario_pc=(antes + depois) / 2,
            tempo_jogo_s=float(estatisticas["gameTime"]),
            eventos=list((eventos or {}).get("Events", [])),
            nome_jogador=nome if isinstance(nome, str) and nome else None,
        )

    def _json(self, caminho: str) -> Any:
        resposta = self._http.get(caminho)
        resposta.raise_for_status()
        return resposta.json()
