"""Roteiro da live falsa da demo.

Tudo aqui é inventado, mas coerente entre si: os picos de voz, as falas e os eventos do
LoL acontecem nos mesmos momentos, como numa live de verdade. Assim as etapas seguintes
(detectores, cortes, títulos) têm uma resposta certa conhecida para conferir.

Horários: ``inicio_s`` e ``fim_s`` são segundos contados do começo da gravação.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DURACAO_S = 180
# Horário de Brasília (UTC-3, sem horário de verão desde 2019). Um fuso fixo evita
# depender do banco de fusos horários, que o Windows não tem.
FUSO_BRASILIA = timezone(timedelta(hours=-3))
INICIO = datetime(2026, 1, 10, 20, 0, 0, tzinfo=FUSO_BRASILIA)
# Nome no mesmo padrão do OBS ("ano-mês-dia hora-minuto-segundo"), que a etapa 5 usa
# para descobrir o horário de início.
NOME_ARQUIVO = f"{INICIO:%Y-%m-%d %H-%M-%S}.mkv"
# A partida começa 10 s depois da gravação (o tempo de abrir o jogo).
PARTIDA_COMECA_S = 10.0
SEU_NOME = "Fermontelos"
# Como o jogo devolve o seu nome (Riot ID, com o #TAG); os eventos trazem só SEU_NOME.
SEU_RIOT_ID = f"{SEU_NOME}#BR1"


@dataclass(frozen=True)
class Pico:
    """Um trecho em que a voz fica bem mais alta (grito, risada)."""

    inicio_s: float
    fim_s: float


PICOS_VOZ: tuple[Pico, ...] = (
    Pico(25.0, 28.0),  # pentakill de Yasuo
    Pico(70.0, 72.0),  # flash errado, risada
    Pico(118.0, 122.0),  # barão roubado
    Pico(150.0, 152.5),  # abate duplo
)


# Falas: (início, fim, texto). Viram a transcrição pronta, palavra por palavra.
FALAS: tuple[tuple[float, float, str], ...] = (
    (5.0, 8.0, "bora começar a partida pessoal"),
    (22.0, 24.5, "vai vai vai entra"),
    (25.0, 28.0, "NÃO ACREDITO NO QUE ESSE YASUO FEZ"),
    (45.0, 48.0, "calma que agora vai"),
    (69.0, 70.0, "eu apertei o flash"),
    (70.0, 72.0, "EU JURO QUE APERTEI O FLASH"),
    (100.0, 103.0, "vamos pro barão agora"),
    (118.0, 122.0, "MEU DEUS QUE BARÃO ROUBADO"),
    (150.0, 152.5, "CARAMBA CARAMBA DOIS"),
    (170.0, 174.0, "valeu pessoal até amanhã"),
)


@dataclass(frozen=True)
class EventoDemo:
    """Evento da partida, no formato da Live Client Data API do LoL."""

    gravacao_s: float
    tipo: str
    dados: dict


# Só os tipos que o coletor guarda (ver jogo/eventos.py).
EVENTOS_LOL: tuple[EventoDemo, ...] = (
    EventoDemo(26.0, "Multikill", {"KillerName": SEU_NOME, "KillStreak": 5}),
    EventoDemo(26.5, "Ace", {"Acer": SEU_NOME, "AcingTeam": "ORDER"}),
    EventoDemo(71.0, "ChampionKill", {"KillerName": "Inimigo", "VictimName": SEU_NOME}),
    EventoDemo(95.0, "TurretKilled", {"KillerName": "Aliado", "TurretKilled": "Turret_T2_L_03"}),
    EventoDemo(119.0, "BaronKill", {"KillerName": SEU_NOME, "Stolen": "True"}),
    EventoDemo(151.0, "Multikill", {"KillerName": SEU_NOME, "KillStreak": 2}),
)


# Títulos prontos para cada pico (a etapa 10 usa estes na demo, no lugar da IA).
TITULOS: tuple[tuple[dict, ...], ...] = (
    (
        {"titulo": "EU NÃO ACREDITO NO QUE ESSE YASUO FEZ", "tipo": "frase_sua"},
        {"titulo": "O time desistiu. O Yasuo não.", "tipo": "exagero"},
        {"titulo": "Pentakill que nem eu esperava", "tipo": "descricao"},
    ),
    (
        {"titulo": "Eu juro que apertei o flash", "tipo": "frase_sua"},
        {"titulo": "O flash mais triste da história do LoL", "tipo": "exagero"},
        {"titulo": "Cadê o meu flash?", "tipo": "pergunta"},
    ),
    (
        {"titulo": "MEU DEUS QUE BARÃO ROUBADO", "tipo": "frase_sua"},
        {"titulo": "Roubei o barão na cara deles", "tipo": "descricao"},
        {"titulo": "Isso é legal no LoL?", "tipo": "pergunta"},
    ),
    (
        {"titulo": "Caramba, caramba, DOIS", "tipo": "frase_sua"},
        {"titulo": "O abate duplo que salvou a partida", "tipo": "exagero"},
        {"titulo": "Como eu sobrevivi a isso?", "tipo": "pergunta"},
    ),
)


def frases_da_transcricao() -> list[dict]:
    """As falas como frases, no formato que a etapa 6 salva (usado na legenda)."""
    return [{"inicio_s": inicio, "fim_s": fim, "texto": texto} for inicio, fim, texto in FALAS]


def palavras_da_transcricao() -> list[dict]:
    """As falas quebradas em palavras, com o tempo de cada uma dividido por igual."""
    palavras = []
    for inicio, fim, texto in FALAS:
        partes = texto.split()
        passo = (fim - inicio) / len(partes)
        for indice, palavra in enumerate(partes):
            palavras.append(
                {
                    "palavra": palavra,
                    "inicio_s": round(inicio + indice * passo, 3),
                    "fim_s": round(inicio + (indice + 1) * passo, 3),
                }
            )
    return palavras
