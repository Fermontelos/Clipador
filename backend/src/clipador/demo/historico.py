"""Lives antigas e já revisadas na demo, para o aprendizado (etapa 14) ter o que mostrar.

São ``DIAS`` (6) lives nos dias antes da live da demo, com ``POR_LIVE`` (20) momentos cada,
todos decididos: 120 decisões, mais que as 100 que o treino pede. Sem vídeo (só o banco):
na revisão, esses dias aparecem já revisados e sem clipes.

O padrão é conhecido, para a demo mostrar o aprendizado funcionando: você aprova mais pela
**fala** (as expressões e a fala acelerada) do que o peso padrão dela (0,25) supõe; o LoL
quase não importa. E nos aprovados você costuma adiantar o início do corte em uns 2 s. O
treino deve sugerir mais peso para a fala e uma janela que começa mais cedo.
"""

import math
import random
from datetime import UTC, timedelta

from sqlmodel import Session, col, func, select

from clipador.banco.modelos import Decisao, Gravacao, Momento, StatusGravacao
from clipador.demo import roteiro

DIAS = 6
POR_LIVE = 20
SEMENTE = 14  # sempre o mesmo histórico
PREFIXO = "historico-demo"
PESOS_DO_SEU_GOSTO = {"volume_voz": 0.3, "fala": 0.6, "lol": 0.1}


def _chance_de_aprovar(forcas: dict[str, float]) -> float:
    gosto = sum(PESOS_DO_SEU_GOSTO[nome] * forca for nome, forca in forcas.items())
    return 1 / (1 + math.exp(-10 * (gosto - 0.5)))


def popular(sessao: Session) -> int:
    """Grava o histórico (uma vez só). Devolve quantas decisões criou (0 se já existia)."""
    ja_tem = sessao.exec(
        select(func.count()).select_from(Gravacao).where(col(Gravacao.arquivo).contains(PREFIXO))
    ).one()
    if ja_tem:
        return 0
    sorteio = random.Random(SEMENTE)
    criadas = 0
    for dias_antes in range(DIAS, 0, -1):
        inicio = (roteiro.INICIO - timedelta(days=dias_antes)).astimezone(UTC)
        gravacao = Gravacao(
            arquivo=f"{PREFIXO}/{inicio:%Y-%m-%d} 20-00-00.mkv",  # não existe: só o banco
            inicio=inicio,
            duracao_s=3 * 3600,
            status=StatusGravacao.CONCLUIDA,
            criada_em=inicio + timedelta(hours=3),
        )
        sessao.add(gravacao)
        sessao.flush()
        for i in range(POR_LIVE):
            forcas = {nome: round(sorteio.random(), 3) for nome in PESOS_DO_SEU_GOSTO}
            nota_padrao = 100 * (0.5 * forcas["volume_voz"] + 0.25 * forcas["fala"]
                                 + 0.25 * forcas["lol"])  # fmt: skip
            inicio_s = 300.0 + i * 500
            momento = Momento(
                gravacao_id=gravacao.id,
                inicio_s=inicio_s,
                fim_s=inicio_s + 25,
                pico_s=inicio_s + 12,
                sinais=forcas,
                nota=round(nota_padrao, 1),
                motivo="(histórico da demo)",
            )
            sessao.add(momento)
            sessao.flush()
            aprovado = sorteio.random() < _chance_de_aprovar(forcas)
            ajustou = aprovado and sorteio.random() < 0.7
            sessao.add(
                Decisao(
                    momento_id=momento.id,
                    aprovado=aprovado,
                    formatos=["vertical_a"] if aprovado else [],
                    destinos={"vertical_a": ["youtube", "tiktok"]} if aprovado else {},
                    titulo=f"Momento {i + 1} da live" if aprovado else None,
                    ajuste_inicio_s=round(sorteio.gauss(-2.5, 1.0) * 2) / 2 if ajustou else None,
                    ajuste_fim_s=1.0 if ajustou and sorteio.random() < 0.3 else None,
                    decidida_em=inicio + timedelta(hours=14, minutes=i),
                )
            )
            criadas += 1
    sessao.commit()
    return criadas
