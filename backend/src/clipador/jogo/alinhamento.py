"""Alinhamento de tempo: em que segundo da gravação aconteceu cada evento do LoL.

Três relógios diferentes entram aqui:

1. **o tempo de jogo** (``gameTime`` e ``EventTime``): segundos desde o começo da partida;
2. **o relógio do PC**: o horário real, em UTC;
3. **o tempo da gravação**: segundos desde que o OBS começou a gravar.

A ponte entre 1 e 2: a cada consulta, o coletor guarda o par (horário do PC, tempo de
jogo). Horário do PC menos tempo de jogo = **quando a partida começou** no relógio real.
Uma amostra só já daria a resposta; a mediana das últimas ``AMOSTRAS`` deixa a conta
imune a uma consulta que demorou mais que as outras.

A ponte entre 2 e 3: o início da gravação vem do nome do arquivo do OBS (horário do PC,
etapa 4). Então::

    horário real do evento = início da partida + EventTime
    segundo na gravação    = horário real do evento - início da gravação

Como tudo vira horário real em UTC antes de subtrair, uma gravação que passa da
meia-noite (ou uma partida que começa depois da gravação) não precisa de cuidado especial.

Precisão: o nome do arquivo tem precisão de 1 s, e a consulta à API, de alguns
milissegundos; o roteiro pede margem de 2 s.

Limite conhecido: se a partida for pausada (só em partidas personalizadas), o tempo de
jogo para e o relógio real não; como a conta usa só as amostras mais recentes, os eventos
seguintes continuam certos, mas um evento lido depois da pausa que aconteceu antes dela
ficaria adiantado.
"""

import statistics
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from clipador.banco.modelos import EventoJogo, Gravacao

# Quantas amostras recentes entram na mediana (a cada 2 s: os últimos 10 s).
AMOSTRAS = 5
# Duas estimativas do início a menos disto são a mesma partida (ex.: o app reabriu no
# meio da partida e recalculou o início com uma diferença de milissegundos).
TOLERANCIA_MESMA_PARTIDA = timedelta(seconds=10)
# Sem a duração da gravação (a etapa 5 ainda não a mediu), até onde procurar eventos. Sem
# um limite, eventos das lives dos dias seguintes apareceriam como desta gravação.
LIMITE_SEM_DURACAO = timedelta(hours=12)


def estimar_inicio(amostras: Sequence[tuple[float, float]]) -> datetime:
    """Início da partida no relógio real (UTC), a partir de pares (horário do PC, tempo de jogo).

    ``horário do PC`` em segundos desde 1970 (``time.time()``).
    """
    if not amostras:
        raise ValueError("É preciso pelo menos uma amostra.")
    recentes = amostras[-AMOSTRAS:]
    inicio = statistics.median(horario_pc - tempo_jogo for horario_pc, tempo_jogo in recentes)
    return datetime.fromtimestamp(inicio, UTC)


def horario_do_evento(partida_inicio: datetime, tempo_jogo_s: float) -> datetime:
    return partida_inicio + timedelta(seconds=tempo_jogo_s)


def segundo_na_gravacao(horario_real: datetime, inicio_gravacao: datetime) -> float:
    return (horario_real - inicio_gravacao).total_seconds()


def eventos_da_gravacao(sessao: Session, gravacao: Gravacao) -> list[tuple[EventoJogo, float]]:
    """Os eventos que caem dentro da gravação, com o segundo de cada um no vídeo.

    Eventos de antes de a gravação começar, ou de depois de ela terminar, ficam de fora.
    Sem a duração da gravação (ainda não medida), vale até ``LIMITE_SEM_DURACAO`` depois
    do início.
    """
    if gravacao.duracao_s is not None:
        fim = gravacao.inicio + timedelta(seconds=gravacao.duracao_s)
    else:
        fim = gravacao.inicio + LIMITE_SEM_DURACAO
    consulta = select(EventoJogo).where(
        EventoJogo.horario_real >= gravacao.inicio, EventoJogo.horario_real <= fim
    )
    eventos = sessao.exec(consulta.order_by(col(EventoJogo.horario_real))).all()
    return [
        (evento, segundo_na_gravacao(evento.horario_real, gravacao.inicio)) for evento in eventos
    ]
