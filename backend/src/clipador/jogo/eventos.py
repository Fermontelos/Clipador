"""Quais eventos do LoL guardar e qual foi o seu papel em cada um.

Formatos conferidos no exemplo oficial da Riot
(https://static.developer.riotgames.com/docs/lol/liveclientdata_events.json):

- ``ChampionKill``: ``KillerName``, ``VictimName``, ``Assisters`` (lista);
- ``Multikill``: ``KillerName``, ``KillStreak`` (2 = abate duplo ... 5 = pentakill);
- ``Ace``: ``Acer``, ``AcingTeam``;
- ``FirstBlood``: ``Recipient``;
- ``DragonKill``, ``BaronKill``, ``HeraldKill``: ``KillerName``, ``Assisters``,
  ``Stolen`` (texto "True"/"False") e, no dragão, ``DragonType``;
- ``TurretKilled``, ``InhibKilled``: ``KillerName``, ``Assisters``;
- ``GameEnd``: ``Result`` ("Win" ou "Lose").

**Nomes.** O ``activeplayername`` devolve o Riot ID inteiro (``Nome#TAG``), mas os eventos
trazem só o ``Nome``. Por isso a comparação ignora o ``#TAG`` e as maiúsculas. Abates de
torres e tropas aparecem com nomes como ``Turret_T1_L_03_A`` ou ``Minion_T100...``, que
nunca coincidem com o seu.
"""

from typing import Any

from clipador.banco.modelos import PapelNoEvento

# Os que o roteiro pede (abates, objetivos e torres), mais arauto, inibidor e o fim da
# partida (vitória ou derrota também dá clipe). Os outros (GameStart, MinionsSpawning,
# FirstBrick) não dizem nada sobre um momento engraçado.
TIPOS_GUARDADOS = frozenset(
    {
        "ChampionKill",
        "Multikill",
        "Ace",
        "FirstBlood",
        "DragonKill",
        "BaronKill",
        "HeraldKill",
        "TurretKilled",
        "InhibKilled",
        "GameEnd",
    }
)


def sem_tag(nome: str | None) -> str:
    """``"Riot Tuxedo#TXC1"`` -> ``"riot tuxedo"`` (sem o #TAG, sem maiúsculas)."""
    return (nome or "").split("#", 1)[0].strip().casefold()


def mesmo_jogador(nome_no_evento: str | None, meu_nome: str | None) -> bool:
    meu = sem_tag(meu_nome)
    return bool(meu) and sem_tag(nome_no_evento) == meu


def papel(evento: dict[str, Any], meu_nome: str | None) -> PapelNoEvento | None:
    """O que você fez no evento, ou ``None`` se ele não foi seu."""
    autor = evento.get("KillerName") or evento.get("Acer") or evento.get("Recipient")
    if mesmo_jogador(autor, meu_nome):
        return PapelNoEvento.ABATE
    if mesmo_jogador(evento.get("VictimName"), meu_nome):
        return PapelNoEvento.MORTE
    if any(mesmo_jogador(nome, meu_nome) for nome in evento.get("Assisters") or []):
        return PapelNoEvento.ASSISTENCIA
    return None
