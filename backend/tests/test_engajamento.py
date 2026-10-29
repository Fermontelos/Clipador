"""Etapa 17: coleta das métricas e índice de engajamento (APIs simuladas, sem gastar cota)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from clipador.banco import fila
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
    Tarefa,
    TipoTarefa,
)
from clipador.config import ConfigEngajamento, PesosDoIndice, config_sem_arquivo
from clipador.engajamento import coleta, indice
from clipador_integracoes import tiktok, youtube

NO_AR = datetime(2026, 10, 20, 18, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "d")


def nova_publicacao(sessao, plataforma=Plataforma.YOUTUBE, titulo="NÃO ACREDITO", sinais=None,
                    hashtags=("#lol",), tipo="frase_sua", formato=FormatoClipe.VERTICAL_A,
                    **campos) -> Publicacao:  # fmt: skip
    gravacao = Gravacao(arquivo=f"C:/g/{datetime.now().timestamp()}.mkv", inicio=NO_AR,
                        duracao_s=3600, status=StatusGravacao.CONCLUIDA)  # fmt: skip
    sessao.add(gravacao)
    sessao.flush()
    momento = Momento(gravacao_id=gravacao.id, inicio_s=0, fim_s=30, nota=50,
                      sinais=sinais or {"volume_voz": 0.5})  # fmt: skip
    sessao.add(momento)
    sessao.flush()
    clipe = Clipe(momento_id=momento.id, formato=formato, arquivo="x.mp4", duracao_s=30)
    sessao.add(clipe)
    sessao.add(Decisao(momento_id=momento.id, aprovado=True, titulo=titulo,
                       hashtags=list(hashtags), tipo_titulo=tipo))  # fmt: skip
    sessao.flush()
    padrao = {
        Plataforma.YOUTUBE: {"status": StatusPublicacao.AGENDADA, "id_na_plataforma": "yt1",
                             "agendada_para": NO_AR, "enviada_em": NO_AR - timedelta(hours=5)},
        Plataforma.TIKTOK: {"status": StatusPublicacao.ENVIADA, "id_na_plataforma": None,
                            "enviada_em": NO_AR},
    }[plataforma]  # fmt: skip
    pub = Publicacao(clipe_id=clipe.id, plataforma=plataforma, **{**padrao, **campos})
    sessao.add(pub)
    sessao.commit()
    return pub


def tarefa_de(sessao, pub) -> Tarefa:
    return sessao.exec(
        select(Tarefa).where(
            Tarefa.tipo == TipoTarefa.COLETAR_METRICAS, Tarefa.publicacao_id == pub.id
        )  # fmt: skip
    ).one()


def metricas(sessao, pub) -> dict[ColetaMetrica, Metrica]:
    return {
        m.coleta: m
        for m in sessao.exec(select(Metrica).where(Metrica.publicacao_id == pub.id)).all()
    }


def no_relogio(monkeypatch, quando):
    monkeypatch.setattr(coleta, "agora", lambda: quando)


# ------------------------------------------------------------------ agenda


def test_envio_agenda_a_coleta_24h_depois_de_ir_ao_ar(sessao, cfg):
    pub = nova_publicacao(sessao)
    tarefa = coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    assert tarefa.executar_a_partir_de == NO_AR + timedelta(hours=24)
    assert coleta.agendar(sessao, pub, cfg) is tarefa  # não duplica


def test_sem_coletar_no_config_nao_agenda(sessao, cfg):
    cfg = cfg.model_copy(update={"engajamento": ConfigEngajamento(coletar=False)})
    assert coleta.agendar(sessao, nova_publicacao(sessao), cfg) is None


# ------------------------------------------------------------------ YouTube


class YoutubeFalso:
    def __init__(self, visualizacoes=1000, retencao=None, erro=None):
        self.visualizacoes, self.retencao_, self.erro = visualizacoes, retencao, erro
        self.pedidos = []

    def estatisticas(self, ids):
        self.pedidos.append(("estatisticas", ids))
        if self.erro:
            raise self.erro
        if self.visualizacoes is None:
            return {}  # apagado
        return {ids[0]: {"visualizacoes": self.visualizacoes, "curtidas": 50, "comentarios": 5}}

    def retencao(self, video_id, desde, ate):
        self.pedidos.append(("retencao", video_id, desde, ate))
        if isinstance(self.retencao_, Exception):
            raise self.retencao_
        return self.retencao_


@pytest.fixture
def yt(monkeypatch):
    falso = YoutubeFalso()
    monkeypatch.setattr(coleta, "criar_youtube", lambda cfg: falso)
    return falso


def rodar(sessao, pub, cfg):
    coleta.coletar_metricas(sessao, tarefa_de(sessao, pub), cfg)


def test_as_tres_coletas_no_horario(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    yt.retencao_ = {"retencao": 0.6, "duracao_media_s": 15.0, "compartilhamentos": 4}
    no_relogio(monkeypatch, NO_AR + timedelta(hours=24, minutes=1))
    with pytest.raises(fila.TentarDepois, match="72h") as espera:
        rodar(sessao, pub, cfg)
    assert espera.value.quando == NO_AR + timedelta(hours=72)
    m = metricas(sessao, pub)[ColetaMetrica.H24]
    assert (m.visualizacoes, m.curtidas, m.comentarios, m.retencao) == (1000, 50, 5, None)
    assert sessao.get(Publicacao, pub.id).status is StatusPublicacao.PUBLICADA

    no_relogio(monkeypatch, NO_AR + timedelta(hours=72, minutes=1))
    with pytest.raises(fila.TentarDepois, match="7d") as espera:
        rodar(sessao, pub, cfg)
    assert espera.value.quando == NO_AR + timedelta(days=7)
    m = metricas(sessao, pub)[ColetaMetrica.H72]
    assert (m.retencao, m.duracao_media_s, m.compartilhamentos) == (0.6, 15.0, 4)
    # O Analytics conta desde o dia em que foi ao ar.
    assert ("retencao", "yt1", NO_AR.date(), (NO_AR + timedelta(hours=72, minutes=1)).date()) in (
        yt.pedidos
    )

    no_relogio(monkeypatch, NO_AR + timedelta(days=7, minutes=1))
    rodar(sessao, pub, cfg)  # a última: termina sem voltar para a fila
    assert set(metricas(sessao, pub)) == set(ColetaMetrica)


def test_cedo_demais_espera_a_hora_da_coleta(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    no_relogio(monkeypatch, NO_AR + timedelta(hours=2))
    with pytest.raises(fila.TentarDepois) as espera:
        rodar(sessao, pub, cfg)
    assert espera.value.quando == NO_AR + timedelta(hours=24)
    assert yt.pedidos == [] and metricas(sessao, pub) == {}


def test_pc_desligado_nao_inventa_a_coleta_atrasada(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    no_relogio(monkeypatch, NO_AR + timedelta(hours=80))  # perdeu a de 24 h
    with pytest.raises(fila.TentarDepois):
        rodar(sessao, pub, cfg)
    assert set(metricas(sessao, pub)) == {ColetaMetrica.H72}


def test_analytics_sem_dados_ou_sem_permissao_nao_atrapalha(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    yt.retencao_ = youtube.SemPermissaoError("Analytics desligado")
    no_relogio(monkeypatch, NO_AR + timedelta(days=7, hours=1))
    rodar(sessao, pub, cfg)
    m = metricas(sessao, pub)[ColetaMetrica.D7]
    assert m.visualizacoes == 1000 and m.retencao is None


def test_video_apagado_encerra_as_coletas(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    yt.visualizacoes = None
    no_relogio(monkeypatch, NO_AR + timedelta(hours=25))
    rodar(sessao, pub, cfg)  # termina, sem erro e sem voltar para a fila
    assert metricas(sessao, pub) == {}


def test_sem_permissao_de_leitura_explica_e_espera_o_refazer(sessao, cfg, yt, monkeypatch):
    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    yt.erro = youtube.PrecisaEntrarError("falta youtube.readonly")
    no_relogio(monkeypatch, NO_AR + timedelta(hours=25))
    tarefa = tarefa_de(sessao, pub)
    with pytest.raises(fila.FalhaDefinitivaError, match=f"clipador refazer {tarefa.id}"):
        coleta.coletar_metricas(sessao, tarefa, cfg)


def test_coleta_gasta_e_respeita_a_cota_do_youtube(sessao, cfg, yt, monkeypatch):
    from clipador.publicacao import cota

    pub = nova_publicacao(sessao)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    quando = NO_AR + timedelta(hours=25)
    no_relogio(monkeypatch, quando)
    with pytest.raises(fila.TentarDepois):
        rodar(sessao, pub, cfg)
    assert cota.cota_youtube(sessao, quando).unidades == cota.CUSTO_DAS_ESTATISTICAS
    # Cota cheia: espera renovar, sem chamar o YouTube.
    monkeypatch.setattr(cota, "LIMITE_DE_UNIDADES", 1)
    monkeypatch.setattr(cota.CotaYoutube, "cabem_unidades", lambda self, n: self.unidades + n <= 1)
    outra = nova_publicacao(sessao)
    coleta.agendar(sessao, outra, cfg)
    sessao.commit()
    yt.pedidos.clear()
    with pytest.raises(fila.TentarDepois, match="cota"):
        rodar(sessao, outra, cfg)
    assert yt.pedidos == []


# ------------------------------------------------------------------ TikTok


class TiktokFalso:
    def __init__(self, videos=(), erro=None):
        self.videos = list(videos)
        self.erro = erro

    def listar_videos(self, antes_de_ms=None):
        if self.erro:
            raise self.erro
        return sorted(self.videos, key=lambda v: -v["create_time"]), 0, False

    def consultar_videos(self, ids):
        return {v["id"]: v for v in self.videos if v["id"] in ids}


@pytest.fixture
def tt(monkeypatch):
    falso = TiktokFalso()
    monkeypatch.setattr(coleta, "criar_tiktok", lambda cfg: falso)
    return falso


def video_tt(id_, horas_depois_do_envio, legenda, views=500):
    postado = NO_AR + timedelta(hours=horas_depois_do_envio)
    return {
        "id": id_,
        "create_time": int(postado.timestamp()),
        "title": legenda,
        "share_url": f"https://tiktok.com/v/{id_}",
        "view_count": views,
        "like_count": 40,
        "comment_count": 3,
        "share_count": 7,
    }


def test_tiktok_acha_o_video_postado_pelo_titulo_e_pelo_horario(sessao, cfg, tt, monkeypatch):
    pub = nova_publicacao(sessao, Plataforma.TIKTOK, titulo="NÃO ACREDITO nisso")
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    tt.videos = [
        video_tt("antes", -3, "não acredito nisso #lol"),  # postado antes do envio: outro vídeo
        video_tt("outro", 2, "gank no mid"),
        video_tt("certo", 1, "Não acredito nisso!! #leagueoflegends #lol"),
        video_tt("repost", 30, "nao acredito nisso"),  # o mais antigo que bate é o certo
    ]
    no_relogio(monkeypatch, NO_AR + timedelta(hours=26))
    with pytest.raises(fila.TentarDepois):
        rodar(sessao, pub, cfg)  # achou, e coletou a de 24 h (contada de quando foi postado)
    pub = sessao.get(Publicacao, pub.id)
    assert (pub.id_na_plataforma, pub.status) == ("certo", StatusPublicacao.PUBLICADA)
    assert pub.agendada_para == NO_AR + timedelta(hours=1)
    assert pub.link == "https://tiktok.com/v/certo"
    m = metricas(sessao, pub)[ColetaMetrica.H24]
    assert (m.visualizacoes, m.compartilhamentos, m.retencao) == (500, 7, None)


def test_tiktok_ainda_nao_postado_tenta_de_novo_sem_erro(sessao, cfg, tt, monkeypatch):
    pub = nova_publicacao(sessao, Plataforma.TIKTOK)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    no_relogio(monkeypatch, NO_AR + timedelta(hours=24))
    with pytest.raises(fila.TentarDepois, match="ainda não apareceu") as espera:
        rodar(sessao, pub, cfg)
    assert espera.value.quando == NO_AR + timedelta(hours=36)


def test_tiktok_desiste_depois_do_prazo(sessao, cfg, tt, monkeypatch):
    pub = nova_publicacao(sessao, Plataforma.TIKTOK)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    no_relogio(monkeypatch, NO_AR + timedelta(days=15))
    rodar(sessao, pub, cfg)  # termina sem erro
    assert sessao.get(Publicacao, pub.id).status is StatusPublicacao.ENVIADA


def test_tiktok_nao_liga_o_mesmo_video_a_duas_publicacoes(sessao, cfg, tt, monkeypatch):
    primeira = nova_publicacao(sessao, Plataforma.TIKTOK, titulo="gank")
    segunda = nova_publicacao(sessao, Plataforma.TIKTOK, titulo="gank")
    for pub in (primeira, segunda):
        coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    tt.videos = [video_tt("v1", 1, "gank #lol"), video_tt("v2", 3, "gank de novo")]
    no_relogio(monkeypatch, NO_AR + timedelta(hours=30))
    for pub in (primeira, segunda):
        with pytest.raises(fila.TentarDepois):
            rodar(sessao, pub, cfg)
    ligados = {sessao.get(Publicacao, p.id).id_na_plataforma for p in (primeira, segunda)}
    assert ligados == {"v1", "v2"}


def test_tiktok_sem_permissao_de_leitura_explica(sessao, cfg, tt, monkeypatch):
    pub = nova_publicacao(sessao, Plataforma.TIKTOK)
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    tt.erro = tiktok.PrecisaEntrarError("falta video.list")
    no_relogio(monkeypatch, NO_AR + timedelta(hours=25))
    with pytest.raises(fila.FalhaDefinitivaError, match="tiktok-entrar"):
        rodar(sessao, pub, cfg)


def test_rascunho_postado_deixa_de_contar_no_limite_de_5(sessao, cfg):
    from clipador.publicacao import publicar

    agora_ = datetime.now(UTC)
    for status in (StatusPublicacao.ENVIADA, StatusPublicacao.PUBLICADA):
        nova_publicacao(sessao, Plataforma.TIKTOK, status=status, id_na_plataforma="p",
                        enviada_em=agora_ - timedelta(hours=1))  # fmt: skip
    assert len(publicar._rascunhos_recentes(sessao, agora_)) == 1


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("NÃO ACREDITO!! #lol", "nao acredito"), ("  Gank   no MID? ", "gank no mid"), ("#só", "")],
)
def test_simplificar_para_comparar(texto, esperado):
    assert coleta.simplificar(texto) == esperado


# ------------------------------------------------------------------ índice


def com_7d(sessao, plataforma, visualizacoes, curtidas=0, comentarios=0, compartilhamentos=0,
           retencao=None, **kw):  # fmt: skip
    extra = {"status": StatusPublicacao.PUBLICADA, "id_na_plataforma": f"id{visualizacoes}",
             "agendada_para": NO_AR}  # fmt: skip
    pub = nova_publicacao(sessao, plataforma, **extra, **kw)
    sessao.add(Metrica(publicacao_id=pub.id, coleta=ColetaMetrica.D7, visualizacoes=visualizacoes,
                       curtidas=curtidas, comentarios=comentarios,
                       compartilhamentos=compartilhamentos, retencao=retencao))  # fmt: skip
    sessao.commit()
    return pub


def test_indice_com_numeros_de_resultado_conhecido(sessao):
    # YouTube: 3 vídeos. Medianas: visualizações 200; interação 0,1; retenção 0,5.
    a = com_7d(sessao, Plataforma.YOUTUBE, 100, curtidas=10, retencao=0.25)  # 0,5 / 1 / 0,5
    b = com_7d(sessao, Plataforma.YOUTUBE, 200, curtidas=20, retencao=0.5)  # 1 / 1 / 1
    c = com_7d(sessao, Plataforma.YOUTUBE, 400, curtidas=80, retencao=1.0)  # 2 / 2 / 2
    pesos = PesosDoIndice(alcance=0.5, interacao=0.3, retencao=0.2)
    por_pub = {i.linha.publicacao.id: i for i in indice.indices(sessao, pesos)}
    assert por_pub[a.id].valor == pytest.approx(0.5 * 0.5 + 0.3 * 1 + 0.2 * 0.5)  # 0,65
    assert por_pub[b.id].valor == pytest.approx(1.0)
    assert por_pub[c.id].valor == pytest.approx(2.0)
    assert [por_pub[p.id].acima_da_mediana for p in (a, b, c)] == [False, False, True]


def test_indice_sem_retencao_divide_o_peso_entre_as_outras(sessao):
    # TikTok não tem retenção: o índice usa só alcance (0,5) e interação (0,3).
    a = com_7d(sessao, Plataforma.TIKTOK, 1000, curtidas=100)
    b = com_7d(sessao, Plataforma.TIKTOK, 3000, curtidas=150)  # alcance 1,5; interação 0,5
    pesos = PesosDoIndice()
    por_pub = {i.linha.publicacao.id: i for i in indice.indices(sessao, pesos)}
    # Medianas: visualizações 2000; interação 0,075.
    assert por_pub[a.id].valor == pytest.approx((0.5 * 0.5 + 0.3 * (0.1 / 0.075)) / 0.8)
    assert por_pub[b.id].valor == pytest.approx((0.5 * 1.5 + 0.3 * (0.05 / 0.075)) / 0.8)
    assert por_pub[a.id].retencao is None


def test_cada_rede_comparada_com_ela_mesma_e_viral_tem_teto(sessao):
    yt_ = com_7d(sessao, Plataforma.YOUTUBE, 100)
    com_7d(sessao, Plataforma.YOUTUBE, 100)
    tt_ = com_7d(sessao, Plataforma.TIKTOK, 50_000)
    viral = com_7d(sessao, Plataforma.TIKTOK, 50_000_000)
    com_7d(sessao, Plataforma.TIKTOK, 50_000)
    pesos = PesosDoIndice(alcance=1, interacao=0, retencao=0)
    por_pub = {i.linha.publicacao.id: i for i in indice.indices(sessao, pesos)}
    assert por_pub[yt_.id].valor == pytest.approx(1.0)  # 100 é o típico do YouTube
    assert por_pub[tt_.id].valor == pytest.approx(1.0)  # 50 mil é o típico do TikTok
    assert por_pub[viral.id].valor == indice.TETO


def test_so_a_coleta_de_7_dias_entra_no_indice(sessao):
    pub = nova_publicacao(sessao)
    sessao.add(Metrica(publicacao_id=pub.id, coleta=ColetaMetrica.H24, visualizacoes=10))
    sessao.commit()
    assert indice.indices(sessao, PesosDoIndice()) == []


# ------------------------------------------------------------------ ranking e exploração


def _indices_com_hashtags(sessao, grupos):
    """grupos: [(hashtags, tipo, visualizações, quantas)] -> índices (só alcance)."""
    for hashtags, tipo, visualizacoes, quantas in grupos:
        for _ in range(quantas):
            com_7d(sessao, Plataforma.YOUTUBE, visualizacoes, hashtags=hashtags, tipo=tipo)
    return indice.indices(sessao, PesosDoIndice(alcance=1, interacao=0, retencao=0))


def test_ranking_de_hashtags_com_dados_inventados(sessao):
    from clipador.engajamento import ranking

    lista = _indices_com_hashtags(
        sessao,
        [
            (["#lol", "#pentakill"], "exagero", 400, 5),  # pentakill: sempre nos que vão bem
            (["#lol"], "pergunta", 100, 5),
            (["#lol", "#novata"], "pergunta", 400, 2),  # só 2 usos: ainda não conclui
        ],
    )
    por_nome = {d.nome: d for d in ranking.hashtags(lista, minimo_usos=5)}
    assert por_nome["#pentakill"].confiavel and por_nome["#pentakill"].efeito > 0
    assert not por_nome["#novata"].confiavel  # menos de 5 usos não é ranqueada
    assert por_nome["#lol"].efeito is None  # todos os vídeos têm: não dá para comparar
    ordem = [d.nome for d in ranking.hashtags(lista, minimo_usos=5)]
    assert ordem[0] == "#pentakill"
    tipos = {d.nome: d for d in ranking.tipos_de_titulo(lista, minimo_usos=5)}
    assert tipos["exagero"].efeito > 0 > tipos["pergunta"].efeito


def test_exploracao_respeita_a_taxa_configurada(sessao):
    import random

    from clipador.engajamento import ranking

    lista = _indices_com_hashtags(
        sessao, [(["#boa"], "exagero", 400, 5), (["#ruim"], "pergunta", 100, 5)]
    )
    desempenhos = ranking.hashtags(lista, minimo_usos=5)
    candidatas = ["#ruim", "#nova", "#boa"]
    sorteio = random.Random(0)
    testou = sum(
        ranking.ordenar_hashtags(candidatas, desempenhos, 0.2, sorteio)[0] == "#nova"
        for _ in range(2000)
    )
    assert 0.17 < testou / 2000 < 0.23  # ~20%: a taxa de exploração
    # Sem exploração: a melhor conhecida primeiro, a nova no meio, a ruim no fim.
    assert ranking.ordenar_hashtags(candidatas, desempenhos, 0.0) == ["#boa", "#nova", "#ruim"]
    # Nunca entra hashtag que a IA não sugeriu para o clipe.
    assert set(ranking.ordenar_hashtags(candidatas, desempenhos, 1.0)) == set(candidatas)


def test_sem_dado_confiavel_fica_a_ordem_da_ia(sessao):
    from clipador.engajamento import ranking

    assert ranking.ordenar_hashtags(["#b", "#a"], [], 1.0) == ["#b", "#a"]
    sugestoes = [{"titulo": "x", "tipo": "pergunta"}, {"titulo": "y", "tipo": "exagero"}]
    assert ranking.ordenar_titulos(sugestoes, [], 1.0) == sugestoes


def test_tipo_de_titulo_que_engaja_vai_para_a_frente(sessao):
    from clipador.engajamento import ranking

    lista = _indices_com_hashtags(
        sessao, [(["#x"], "exagero", 400, 5), (["#x"], "pergunta", 100, 5)]
    )
    desempenhos = ranking.tipos_de_titulo(lista, minimo_usos=5)
    sugestoes = [{"titulo": "a", "tipo": "pergunta"}, {"titulo": "b", "tipo": "exagero"},
                 {"titulo": "c", "tipo": "frase_sua"}]  # fmt: skip
    ordenados = ranking.ordenar_titulos(sugestoes, desempenhos, 0.0)
    assert [s["tipo"] for s in ordenados] == ["exagero", "frase_sua", "pergunta"]


# ------------------------------------------------------------------ aprendizado


def _cortes_com_gosto_do_publico(sessao, quantos, semente=17):
    """Cortes publicados em que o público engaja pelos eventos do LoL (e não pela voz)."""
    import random

    sorteio = random.Random(semente)
    for _ in range(quantos):
        forcas = {nome: round(sorteio.random(), 3) for nome in ("volume_voz", "fala", "lol")}
        visualizacoes = int(100 + 2000 * forcas["lol"] + 200 * sorteio.random())
        com_7d(sessao, Plataforma.YOUTUBE, visualizacoes, sinais=forcas)


def test_treino_pelo_engajamento_recusa_com_menos_de_50_cortes(sessao, cfg):
    from clipador.aprendizado import engajamento

    _cortes_com_gosto_do_publico(sessao, 49)
    sugestao = engajamento.sugerir(sessao, cfg)
    assert sugestao.cortes == 49 and sugestao.pesos_novos is None
    assert "pelo menos 50" in sugestao.sem_pesos_porque
    with pytest.raises(engajamento.NadaParaAceitarError):
        engajamento.aceitar(sessao, cfg, cortes_vistos=49)


def test_treino_pelo_engajamento_acha_o_gosto_do_publico_e_mistura(sessao, cfg):
    from clipador.aprendizado import engajamento
    from clipador.banco.modelos import PesosNota

    _cortes_com_gosto_do_publico(sessao, 80)
    sugestao = engajamento.sugerir(sessao, cfg)
    assert sugestao.pesos_do_engajamento["lol"] > 0.5 * sum(sugestao.pesos_do_engajamento.values())
    # Metade gosto (0,5/0,25/0,25 do config), metade engajamento.
    esperado = engajamento.misturar(sugestao.pesos_atuais, sugestao.pesos_do_engajamento, 0.5)
    assert sugestao.pesos_novos == esperado
    assert sugestao.pesos_novos["lol"] > sugestao.pesos_atuais["lol"]
    assert sugestao.precisao_depois > sugestao.precisao_antes  # medida em cortes não vistos
    with pytest.raises(engajamento.SugestaoMudouError):
        engajamento.aceitar(sessao, cfg, cortes_vistos=79)
    versao = engajamento.aceitar(sessao, cfg, cortes_vistos=80)
    assert (versao.origem, versao.decisoes, versao.valores) == ("engajamento", 80, esperado)
    assert sessao.exec(select(PesosNota)).one().versao == 1


def test_mistura_zero_e_um():
    from clipador.aprendizado import engajamento

    gosto, publico = {"a": 0.6, "b": 0.4}, {"a": 0.2, "b": 0.8}
    assert engajamento.misturar(gosto, publico, 0.0) == gosto
    assert engajamento.misturar(gosto, publico, 1.0) == publico


def test_titulos_da_etapa_10_saem_na_ordem_do_engajamento(sessao, cfg):
    from clipador.engajamento import ranking
    from clipador.titulos import gerar
    from clipador.titulos.gerador import Sugestoes

    lista = _indices_com_hashtags(
        sessao, [(["#boa"], "exagero", 400, 5), (["#ruim"], "pergunta", 100, 5)]
    )
    cfg = cfg.model_copy(update={"engajamento": ConfigEngajamento(exploracao=0.0)})
    sugestoes = Sugestoes(
        titulos=[{"titulo": "a?", "tipo": "pergunta"}, {"titulo": "b!", "tipo": "exagero"}],
        hashtags=["#leagueoflegends", "#lolbr", "#ruim", "#x", "#y"],  # o corte em 5
        origem="ollama",
        candidatas=["#ruim", "#x", "#y", "#boa"],  # a #boa tinha ficado de fora
    )
    gerar._pelo_engajamento(
        sugestoes, ranking.hashtags(lista, 5), ranking.tipos_de_titulo(lista, 5), cfg
    )
    assert sugestoes.hashtags == ["#leagueoflegends", "#lolbr", "#boa", "#x", "#y"]
    assert sugestoes.titulos[0]["tipo"] == "exagero"


# ------------------------------------------------------------------ demo


def test_demo_tem_engajamento_para_mostrar(sessao, cfg):
    from clipador.aprendizado import sugestao as do_gosto
    from clipador.demo import historico
    from clipador.engajamento import resumo

    historico.popular(sessao)
    r = resumo.resumir(sessao, cfg)
    assert r.com_7_dias >= cfg.engajamento.minimo_cortes  # já dá sugestão
    s = r.sugestao
    # O público gosta do LoL (o que você menos aprova): a mistura sobe o peso dele.
    assert max(s.pesos_do_engajamento, key=s.pesos_do_engajamento.get) == "lol"
    assert s.pesos_novos["lol"] > s.pesos_atuais["lol"]
    assert s.precisao_depois > s.precisao_antes
    assert r.hashtags[0].nome == "#pentakill" and r.hashtags[0].confiavel
    assert {rede.plataforma for rede in r.por_rede} == {"youtube", "tiktok"}
    # E o aprendizado do gosto (etapa 14) continua achando o mesmo de antes.
    gosto = do_gosto.sugerir(sessao, cfg).pesos_novos
    assert gosto == {"volume_voz": 0.286, "fala": 0.623, "lol": 0.091}


def test_aceitar_gosto_e_engajamento_um_nao_apaga_o_outro(sessao, cfg):
    from clipador.aprendizado import engajamento
    from clipador.aprendizado import pesos as aprendizado_pesos
    from clipador.aprendizado import sugestao as do_gosto
    from clipador.demo import historico
    from clipador.detectores import nota

    historico.popular(sessao)  # 120 decisões e 118 cortes com métricas
    publico = engajamento.sugerir(sessao, cfg)
    v1 = engajamento.aceitar(sessao, cfg, publico.cortes)
    assert v1.pesos_do_gosto == nota.pesos_do_config(cfg.deteccao)  # o gosto ainda é o do config
    assert v1.valores == aprendizado_pesos.misturar(v1.pesos_do_gosto, v1.pesos_do_engajamento, 0.5)

    # O gosto (etapa 14) agora: troca só a parte do gosto, e mistura com o engajamento.
    gosto = do_gosto.sugerir(sessao, cfg)
    assert gosto.pesos_do_engajamento == v1.pesos_do_engajamento
    v2 = do_gosto.aceitar(sessao, cfg, gosto.decisoes)
    assert v2.pesos_do_gosto == gosto.pesos_do_gosto_novos
    assert v2.pesos_do_engajamento == v1.pesos_do_engajamento  # não apagou o engajamento
    assert v2.valores == aprendizado_pesos.misturar(v2.pesos_do_gosto, v2.pesos_do_engajamento, 0.5)

    # O engajamento de novo: parte do gosto novo, e não da mistura (não mistura duas vezes).
    de_novo = engajamento.sugerir(sessao, cfg)
    assert de_novo.pesos_do_gosto == v2.pesos_do_gosto
    if de_novo.pesos_novos:
        assert de_novo.pesos_novos == aprendizado_pesos.misturar(
            v2.pesos_do_gosto, de_novo.pesos_do_engajamento, 0.5
        )


def test_tiktok_titulo_bate_so_com_palavras_inteiras(sessao, cfg, tt, monkeypatch):
    pub = nova_publicacao(sessao, Plataforma.TIKTOK, titulo="gank")
    coleta.agendar(sessao, pub, cfg)
    sessao.commit()
    tt.videos = [video_tt("outro", 1, "ganking no mid"), video_tt("certo", 2, "que gank!")]
    no_relogio(monkeypatch, NO_AR + timedelta(hours=30))
    with pytest.raises(fila.TentarDepois):
        rodar(sessao, pub, cfg)
    assert sessao.get(Publicacao, pub.id).id_na_plataforma == "certo"


def test_shorts_e_videos_do_youtube_tem_medianas_separadas(sessao):
    # Shorts têm bem mais visualizações: cada um é comparado com o seu grupo.
    short = com_7d(sessao, Plataforma.YOUTUBE, 5000)
    com_7d(sessao, Plataforma.YOUTUBE, 5000)
    horizontal = com_7d(sessao, Plataforma.YOUTUBE, 200, formato=FormatoClipe.HORIZONTAL)
    com_7d(sessao, Plataforma.YOUTUBE, 200, formato=FormatoClipe.HORIZONTAL)
    pesos = PesosDoIndice(alcance=1, interacao=0, retencao=0)
    por_pub = {i.linha.publicacao.id: i for i in indice.indices(sessao, pesos)}
    assert por_pub[short.id].valor == pytest.approx(1.0)
    assert por_pub[horizontal.id].valor == pytest.approx(1.0)  # típico entre os horizontais
    assert por_pub[short.id].linha.grupo == "youtube_short"
    assert por_pub[horizontal.id].linha.grupo == "youtube_video"
