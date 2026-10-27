"""Em que horário cada clipe vai ao ar (etapa 15).

O vídeo sobe como **privado** com uma data de publicação (``publishAt``), e o YouTube o
publica sozinho no horário. O horário é o próximo dos ``[youtube] horarios`` do config
(no relógio do PC) que:

- fica pelo menos ``antecedencia_min`` depois de agora (o vídeo precisa terminar de
  processar no YouTube antes de ir ao ar);
- fica a pelo menos ``intervalo_minimo_min`` de qualquer outra publicação já marcada,
  para não soltar tudo de uma vez.

Um horário que não cabe passa para o dia seguinte. Com 3 horários por dia e 15 clipes
aprovados, a agenda vai sendo preenchida pelos próximos dias.
"""

from datetime import date, datetime, time, timedelta

from clipador.config import ConfigYoutube

DIAS_A_PROCURAR = 366  # um ano de horários: mais que isso é configuração errada


def horarios_do_dia(dia: date, cfg: ConfigYoutube) -> list[datetime]:
    """Os horários do config naquele dia, no fuso do PC."""
    fuso = datetime.now().astimezone().tzinfo
    resultado = []
    for texto in sorted(set(cfg.horarios)):
        hora, minuto = (int(parte) for parte in texto.split(":"))
        resultado.append(datetime.combine(dia, time(hora, minuto), tzinfo=fuso))
    return resultado


def proximo_horario(agora: datetime, ocupados: list[datetime], cfg: ConfigYoutube) -> datetime:
    """O próximo horário livre (ver as regras no topo). Devolvido no fuso de ``agora``."""
    cedo_demais = agora + timedelta(minutes=cfg.antecedencia_min)
    intervalo = timedelta(minutes=cfg.intervalo_minimo_min)
    dia = agora.astimezone().date()
    for _ in range(DIAS_A_PROCURAR):
        for candidato in horarios_do_dia(dia, cfg):
            if candidato < cedo_demais:
                continue
            if all(abs(candidato - outro) >= intervalo for outro in ocupados):
                return candidato.astimezone(agora.tzinfo)
        dia += timedelta(days=1)
    raise ValueError(  # só com uma agenda absurda (ex.: 1 horário e intervalo de 2 dias)
        "Não há horário livre no próximo ano: confira [youtube] horarios e intervalo_minimo_min."
    )
