"""O que vai para o YouTube junto com o vídeo (etapa 15): o pedido do ``videos.insert``.

- **Título**: o que você aprovou (até 100 caracteres e sem ``<`` nem ``>``, que o YouTube
  recusa: conferido na revisão).
- **Descrição**: o modelo ``[youtube] descricao`` (com ``{titulo}``, ``{hashtags}`` e
  ``{data}``); as hashtags aprovadas vão nela. Limite: 5.000 bytes, sem ``<`` nem ``>``
  (trocados por parênteses angulares parecidos, ‹ e ›).
- **Tags**: as do config mais as hashtags sem o ``#``. Limite: 500 caracteres no total,
  contando as vírgulas entre elas e as aspas que o YouTube põe numa tag com espaço.
- **Categoria** 20 (Jogos) e ``selfDeclaredMadeForKids = false`` ("não é conteúdo para
  crianças"), uma declaração que o YouTube exige.
- **Privado com data de publicação** (``publishAt``): o YouTube publica sozinho no horário.
- **Shorts**: o YouTube trata como Short o vídeo vertical de até 3 min; não há campo para
  isso. Por isso o formato só muda o aviso aos inscritos (``notifySubscribers``).

Limites conferidos na documentação do recurso ``videos`` em 2026-10-27.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from clipador.config import ConfigYoutube

LIMITE_DA_DESCRICAO_BYTES = 5000
LIMITE_DAS_TAGS = 500
DURACAO_MAXIMA_DO_SHORT_S = 180


@dataclass(frozen=True)
class PedidoYoutube:
    corpo: dict  # snippet e status, como a API pede
    notificar_inscritos: bool
    short: bool


def _cortar_bytes(texto: str, limite: int) -> str:
    """O texto dentro de ``limite`` bytes em UTF-8, sem partir um caractere no meio."""
    bruto = texto.encode("utf-8")
    if len(bruto) <= limite:
        return texto
    return bruto[: limite - 3].decode("utf-8", "ignore").rstrip() + "..."


def _tamanho_da_tag(tag: str) -> int:
    return len(tag) + (2 if " " in tag else 0)  # tag com espaço conta com as aspas


def tags(fixas: list[str], hashtags: list[str]) -> list[str]:
    """As tags do config mais as hashtags sem o ``#``, sem repetir e dentro do limite."""
    resultado: list[str] = []
    vistas: set[str] = set()
    total = 0
    for tag in [*fixas, *(h.lstrip("#") for h in hashtags)]:
        tag = tag.strip()
        if not tag or tag.casefold() in vistas:
            continue
        custo = _tamanho_da_tag(tag) + (1 if resultado else 0)  # a vírgula entre as tags
        if total + custo > LIMITE_DAS_TAGS:
            break  # as do config vêm primeiro: as últimas hashtags são as que ficam de fora
        resultado.append(tag)
        vistas.add(tag.casefold())
        total += custo
    return resultado


def e_short(vertical: bool, duracao_s: float | None) -> bool:
    """O YouTube trata como Short o vídeo vertical de até 3 min (os clipes vão até 10 min)."""
    return vertical and (duracao_s is None or duracao_s <= DURACAO_MAXIMA_DO_SHORT_S)


def montar(
    titulo: str,
    hashtags: list[str],
    dia_da_live: datetime,
    publicar_em: datetime,
    duracao_s: float | None,
    vertical: bool,
    cfg: ConfigYoutube,
) -> PedidoYoutube:
    descricao = cfg.descricao.format(
        titulo=titulo,
        hashtags=" ".join(hashtags),
        data=dia_da_live.astimezone().strftime("%d/%m/%Y"),
    ).strip()
    descricao = descricao.replace("<", "‹").replace(">", "›")
    short = e_short(vertical, duracao_s)
    corpo = {
        "snippet": {
            "title": titulo,
            "description": _cortar_bytes(descricao, LIMITE_DA_DESCRICAO_BYTES),
            "tags": tags(cfg.tags, hashtags),
            "categoryId": str(cfg.categoria),
            "defaultLanguage": "pt-BR",
            "defaultAudioLanguage": "pt-BR",
        },
        "status": {
            "privacyStatus": "private",  # o publishAt só vale com o vídeo privado
            "publishAt": publicar_em.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }
    notificar = cfg.notificar_inscritos_shorts if short else cfg.notificar_inscritos_videos
    return PedidoYoutube(corpo=corpo, notificar_inscritos=notificar, short=short)
