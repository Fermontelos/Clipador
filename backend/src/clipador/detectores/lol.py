"""Detector de eventos do LoL: pentakill > barão roubado > barão > abate...

Usa os eventos salvos pelo coletor (etapa 7), já no segundo certo da gravação. Cada tipo
de evento tem uma força de 0 a 1 (``FORCA``), multiplicada conforme o seu papel nele:

- seu abate (ou objetivo seu): 100%;
- sua morte: 80% (morrer de um jeito ridículo também dá clipe);
- sua assistência: 70%;
- de outro jogador: 30% (o evento aconteceu, mas a câmera pode nem estar lá).

O fim da partida (vitória ou derrota) não traz nomes, mas é sempre da sua partida: 100%.

Objetivo roubado (``Stolen`` = "True") ganha +0,2. A tabela é um ponto de partida: quem
afina o peso de cada sinal na nota são as suas aprovações (etapa 14) e o engajamento dos
clipes publicados (etapa 17).
"""

from clipador.banco.modelos import EventoJogo, PapelNoEvento
from clipador.config import ConfigLolDetector
from clipador.detectores.base import Entrada, Sinal

# Força por tipo de evento. Multikill depende do KillStreak (2 = duplo ... 5 = penta).
FORCA_MULTIKILL = {2: 0.6, 3: 0.8, 4: 0.9, 5: 1.0}
FORCA = {
    "BaronKill": 0.7,
    "DragonKill": 0.4,
    "HeraldKill": 0.4,
    "Ace": 0.6,
    "FirstBlood": 0.5,
    "ChampionKill": 0.4,
    "InhibKilled": 0.3,
    "TurretKilled": 0.2,
    "GameEnd": 0.5,
}
FORCA_DRAGAO_ANCIAO = 0.7
BONUS_ROUBADO = 0.2
MULTIPLICADOR = {
    PapelNoEvento.ABATE: 1.0,
    PapelNoEvento.MORTE: 0.8,
    PapelNoEvento.ASSISTENCIA: 0.7,
    None: 0.3,
}
NOME_MULTIKILL = {2: "abate duplo", 3: "abate triplo", 4: "quadrakill", 5: "pentakill"}
NOME = {
    "BaronKill": "barão",
    "DragonKill": "dragão",
    "HeraldKill": "arauto",
    "Ace": "ace",
    "FirstBlood": "first blood",
    "ChampionKill": "abate",
    "InhibKilled": "inibidor",
    "TurretKilled": "torre",
}
DE_QUEM = {
    PapelNoEvento.ABATE: " seu",
    PapelNoEvento.MORTE: " (sua morte)",
    PapelNoEvento.ASSISTENCIA: " (sua assistência)",
    None: "",
}


def forca_e_nome(evento: EventoJogo) -> tuple[float, str]:
    """Força do evento (antes do multiplicador do papel) e como chamá-lo no motivo."""
    dados = evento.dados or {}
    if evento.tipo == "Multikill":
        sequencia = min(max(int(dados.get("KillStreak", 2)), 2), 5)
        return FORCA_MULTIKILL[sequencia], NOME_MULTIKILL[sequencia]
    if evento.tipo == "ChampionKill" and evento.papel is PapelNoEvento.MORTE:
        return FORCA["ChampionKill"], "sua morte"
    if evento.tipo == "GameEnd":
        vitoria = dados.get("Result") == "Win"
        return FORCA["GameEnd"], "vitória" if vitoria else "derrota"
    forca = FORCA.get(evento.tipo, 0.0)
    nome = NOME.get(evento.tipo, evento.tipo)
    if evento.tipo == "DragonKill" and dados.get("DragonType") == "Elder":
        forca, nome = FORCA_DRAGAO_ANCIAO, "dragão ancião"
    if str(dados.get("Stolen", "False")).casefold() == "true":
        forca, nome = forca + BONUS_ROUBADO, f"{nome} roubado"
    return min(forca, 1.0), nome


class Lol:
    nome = "lol"

    def __init__(self, cfg: ConfigLolDetector) -> None:
        self.cfg = cfg

    def disponivel(self, entrada: Entrada) -> bool:
        return bool(entrada.eventos)

    def detectar(self, entrada: Entrada) -> list[Sinal]:
        sinais = []
        for evento, segundo in entrada.eventos:
            forca, nome = forca_e_nome(evento)
            sua_partida = evento.tipo == "GameEnd"
            forca *= 1.0 if sua_partida else MULTIPLICADOR[evento.papel]
            if forca <= 0:
                continue
            # "sua morte", "vitória": já dizem de quem são.
            ja_diz = sua_partida or evento.papel is PapelNoEvento.MORTE
            sufixo = "" if ja_diz else DE_QUEM[evento.papel]
            sinais.append(Sinal(self.nome, segundo, segundo, round(forca, 3), f"{nome}{sufixo}"))
        return sinais
