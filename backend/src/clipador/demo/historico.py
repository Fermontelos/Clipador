"""Lives antigas e já revisadas na demo, para o aprendizado (etapa 14) ter o que mostrar.

São ``DIAS`` (6) lives nos dias antes da live da demo, com ``POR_LIVE`` (20) momentos cada,
todos decididos: 120 decisões, mais que as 100 que o treino pede. Sem vídeo (só o banco):
na revisão, esses dias aparecem já revisados; os aprovados mostram o vertical como
indisponível (o arquivo não existe) e as publicações de mentira da etapa 17.

O padrão é conhecido, para a demo mostrar o aprendizado funcionando: você aprova mais pela
**fala** (as expressões e a fala acelerada) do que o peso padrão dela (0,25) supõe; o LoL
quase não importa. E nos aprovados você costuma adiantar o início do corte em uns 2 s. O
treino deve sugerir mais peso para a fala e uma janela que começa mais cedo.

**Engajamento (etapa 17).** Cada aprovado foi "publicado" no YouTube (Short) e no TikTok, com
as 3 coletas de métricas inventadas. O público tem outro gosto, também escondido: engaja com
os **eventos do LoL** (``PESOS_DO_PUBLICO``), justamente o que você menos aprova. O treino do
engajamento deve sugerir mais peso para o LoL, e a mistura fica entre os dois gostos. A
``#pentakill`` vai nos momentos de LoL forte: é a hashtag que "funciona" no ranking. A
``#clutch`` é sorteada, sem relação com o momento: o efeito dela no ranking é puro acaso (um
bom exemplo de por que o ranking só muda a ordem das sugestões). Nada
disso existe no YouTube nem no TikTok: os IDs são falsos, e nenhuma tarefa é criada.
"""

import math
import random
from datetime import UTC, timedelta

from sqlmodel import Session, col, func, select

from clipador.banco.modelos import (
    Clipe,
    ColetaMetrica,
    Decisao,
    FormatoClipe,
    Gravacao,
    Metrica,
    Momento,
    Plataforma,
    Publicacao,
    StatusGravacao,
    StatusPublicacao,
)
from clipador.demo import roteiro
from clipador.engajamento.coleta import DEPOIS_DE_IR_AO_AR

DIAS = 6
POR_LIVE = 20
SEMENTE = 14  # sempre o mesmo histórico
PREFIXO = "historico-demo"
PESOS_DO_SEU_GOSTO = {"volume_voz": 0.3, "fala": 0.6, "lol": 0.1}
PESOS_DO_PUBLICO = {"volume_voz": 0.3, "fala": 0.1, "lol": 0.6}
TIPOS = ("frase_sua", "pergunta", "exagero", "descricao")
# Visualizações típicas em 7 dias de cada rede (o TikTok, maior) e quanto delas já existe em
# 24 h e 72 h.
TIPICO = {Plataforma.YOUTUBE: 400, Plataforma.TIKTOK: 2500}
FRACAO_ATE = {ColetaMetrica.H24: 0.5, ColetaMetrica.H72: 0.8, ColetaMetrica.D7: 1.0}


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
    # Um sorteio à parte para o engajamento (etapa 17): o da etapa 14 continua o mesmo, e as
    # decisões da demo não mudam.
    do_publico = random.Random(SEMENTE + 3)
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
            decidida_em = inicio + timedelta(hours=14, minutes=i)
            hashtags = ["#leagueoflegends", "#lolbr"]
            if forcas["lol"] > 0.7:
                hashtags.append("#pentakill")
            if do_publico.random() < 0.4:
                hashtags.append("#clutch")  # sorteada: o efeito que aparecer é acaso
            sessao.add(
                Decisao(
                    momento_id=momento.id,
                    aprovado=aprovado,
                    formatos=["vertical_a"] if aprovado else [],
                    destinos={"vertical_a": ["youtube", "tiktok"]} if aprovado else {},
                    titulo=f"Momento {i + 1} da live" if aprovado else None,
                    hashtags=hashtags if aprovado else [],
                    tipo_titulo=do_publico.choice(TIPOS) if aprovado else None,
                    ajuste_inicio_s=round(sorteio.gauss(-2.5, 1.0) * 2) / 2 if ajustou else None,
                    ajuste_fim_s=1.0 if ajustou and sorteio.random() < 0.3 else None,
                    decidida_em=decidida_em,
                )
            )
            if aprovado:
                _publicado(sessao, momento, forcas, decidida_em, do_publico)
            criadas += 1
    sessao.commit()
    return criadas


def _publicado(sessao: Session, momento: Momento, forcas: dict[str, float], decidida_em, sorteio):
    """O vertical do momento "publicado" nas duas redes, com as 3 coletas (só no banco)."""
    clipe = Clipe(
        momento_id=momento.id,
        formato=FormatoClipe.VERTICAL_A,
        arquivo=f"{PREFIXO}/momento_{momento.id}_vertical_a.mp4",  # não existe: só o banco
        duracao_s=25,
    )
    sessao.add(clipe)
    sessao.flush()
    publico = sum(PESOS_DO_PUBLICO[nome] * forca for nome, forca in forcas.items())
    for plataforma, tipico in TIPICO.items():
        no_ar = decidida_em + timedelta(hours=6)
        publicacao = Publicacao(
            clipe_id=clipe.id,
            plataforma=plataforma,
            id_na_plataforma=f"demo-{plataforma.value}-{momento.id}",
            status=StatusPublicacao.PUBLICADA,
            agendada_para=no_ar,
            enviada_em=decidida_em + timedelta(minutes=10),
        )
        sessao.add(publicacao)
        sessao.flush()
        # Quanto mais o momento tem o que o público gosta, mais visualizações (com ruído).
        visualizacoes_7d = tipico * math.exp(3 * (publico - 0.5) + sorteio.gauss(0, 0.35))
        taxa = 0.06 + 0.06 * publico  # curtidas + comentários + compartilhamentos
        for coleta, fracao in FRACAO_ATE.items():
            visualizacoes = int(visualizacoes_7d * fracao)
            youtube = plataforma is Plataforma.YOUTUBE
            sessao.add(
                Metrica(
                    publicacao_id=publicacao.id,
                    coleta=coleta,
                    visualizacoes=visualizacoes,
                    curtidas=int(visualizacoes * taxa * 0.8),
                    comentarios=int(visualizacoes * taxa * 0.1),
                    compartilhamentos=int(visualizacoes * taxa * 0.1),
                    # Retenção só no YouTube (e só nas coletas em que o Analytics já tem dados).
                    retencao=round(0.35 + 0.35 * publico, 3)
                    if youtube and coleta is not ColetaMetrica.H24
                    else None,
                    coletada_em=no_ar + DEPOIS_DE_IR_AO_AR[coleta],
                )
            )
