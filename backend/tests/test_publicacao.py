"""Etapa 15: publicação no YouTube, com um YouTube falso (nada vai para a internet)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from clipador.banco import fila
from clipador.banco.modelos import (
    Clipe,
    FormatoClipe,
    Gravacao,
    Momento,
    Plataforma,
    Publicacao,
    StatusGravacao,
    StatusPublicacao,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
)
from clipador.config import ConfigYoutube, config_sem_arquivo
from clipador.cortes.cortar import caminho_da_thumbnail
from clipador.publicacao import agenda, cota, pedido, planejar, publicar
from clipador.revisao import decisoes
from clipador_integracoes import youtube


def local(*partes) -> datetime:
    """Uma hora no relógio do PC (como os horários do config), em UTC."""
    return datetime(*partes).astimezone().astimezone(UTC)


# ------------------------------------------------------------------ agenda


def test_proximo_horario_respeita_a_antecedencia():
    cfg = ConfigYoutube(horarios=["12:00", "18:00", "21:00"], antecedencia_min=60)
    # 11:30: o das 12:00 fica a menos de 1 h; vai para as 18:00.
    assert agenda.proximo_horario(local(2026, 10, 27, 11, 30), [], cfg) == local(2026, 10, 27, 18)
    # 23:00: o próximo é amanhã ao meio-dia.
    assert agenda.proximo_horario(local(2026, 10, 27, 23), [], cfg) == local(2026, 10, 28, 12)


def test_proximo_horario_respeita_o_intervalo_entre_publicacoes():
    cfg = ConfigYoutube(horarios=["12:00", "13:00", "18:00"], intervalo_minimo_min=120)
    agora = local(2026, 10, 27, 8)
    ocupados = [local(2026, 10, 27, 12)]
    # O das 13:00 fica a 1 h do das 12:00 (menos que 2 h): pula para as 18:00.
    assert agenda.proximo_horario(agora, ocupados, cfg) == local(2026, 10, 27, 18)
    ocupados.append(local(2026, 10, 27, 18))
    assert agenda.proximo_horario(agora, ocupados, cfg) == local(2026, 10, 28, 12)


def test_agenda_impossivel_explica():
    cfg = ConfigYoutube(horarios=["12:00"], intervalo_minimo_min=24 * 60)
    ocupados = [local(2026, 10, 27, 12) + timedelta(days=d) for d in range(400)]
    with pytest.raises(ValueError, match="horarios e intervalo_minimo_min"):
        agenda.proximo_horario(local(2026, 10, 27, 8), ocupados, cfg)


# ------------------------------------------------------------------ pedido


def test_pedido_do_video_horizontal():
    cfg = ConfigYoutube(descricao="{titulo} (live de {data})\n\n{hashtags}", tags=["LoL"])
    quando = local(2026, 10, 28, 18)
    feito = pedido.montar(
        titulo="EU NÃO ACREDITO",
        hashtags=["#lol", "#pentakill"],
        dia_da_live=local(2026, 10, 27, 20),
        publicar_em=quando,
        duracao_s=40,
        vertical=False,
        cfg=cfg,
    )
    assert feito.corpo["snippet"] == {
        "title": "EU NÃO ACREDITO",
        "description": "EU NÃO ACREDITO (live de 27/10/2026)\n\n#lol #pentakill",
        "tags": ["LoL", "pentakill"],  # "lol" repetida (sem diferenciar maiúsculas) sai
        "categoryId": "20",
        "defaultLanguage": "pt-BR",
        "defaultAudioLanguage": "pt-BR",
    }
    status = feito.corpo["status"]
    assert status["privacyStatus"] == "private"  # o publishAt só vale com privado
    assert status["publishAt"] == quando.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert status["selfDeclaredMadeForKids"] is False
    assert (feito.short, feito.notificar_inscritos) == (False, True)


def test_vertical_curto_e_short_e_nao_avisa_os_inscritos():
    cfg = ConfigYoutube()
    agora = local(2026, 10, 28, 18)
    short = pedido.montar("t", [], agora, agora, 50, True, cfg)
    assert (short.short, short.notificar_inscritos) == (True, False)
    # Vertical de mais de 3 min não é Short: vai como vídeo normal (e avisa).
    longo = pedido.montar("t", [], agora, agora, 200, True, cfg)
    assert (longo.short, longo.notificar_inscritos) == (False, True)


def test_descricao_sem_os_caracteres_que_o_youtube_recusa():
    cfg = ConfigYoutube(descricao="{titulo} <3 {hashtags}")
    feito = pedido.montar("t", [], datetime.now(UTC), datetime.now(UTC), 30, False, cfg)
    assert feito.corpo["snippet"]["description"] == "t ‹3"


def test_titulo_com_sinal_de_maior_ou_menor_nao_e_aprovado(sessao, momento, cfg):
    with pytest.raises(decisoes.DecisaoInvalidaError, match="'<'"):
        decisoes.decidir(sessao, momento.id, aprovado=True, titulo="eu <3 LoL", cfg=cfg)
    assert publicacoes(sessao) == []


def test_descricao_e_tags_dentro_dos_limites_do_youtube():
    agora = local(2026, 10, 28, 18)
    feito = pedido.montar("t", [], agora, agora, 30, False, ConfigYoutube(descricao="ã" * 3000))
    descricao = feito.corpo["snippet"]["description"]
    assert len(descricao.encode("utf-8")) <= 5000  # "ã" ocupa 2 bytes: 6.000 viraria demais
    assert descricao.endswith("...")
    # Tag com espaço conta as aspas; vírgula entre elas também.
    longas = [f"#tag{i:03d}xxxxxxxxxx" for i in range(60)]
    tags = pedido.tags(["League of Legends"], longas)
    total = sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1
    assert total <= 500 and tags[0] == "League of Legends"


# ------------------------------------------------------------------ cota


def test_cota_conta_pelo_horario_do_envio(sessao, gravacao):
    momento = Momento(gravacao_id=gravacao.id, inicio_s=0, fim_s=20)
    sessao.add(momento)
    sessao.flush()
    clipe = Clipe(momento_id=momento.id, formato=FormatoClipe.HORIZONTAL, arquivo="x.mp4")
    sessao.add(clipe)
    sessao.flush()
    agora = datetime(2026, 10, 27, 15, tzinfo=UTC)  # 8h no Pacífico
    sessao.add(Publicacao(clipe_id=clipe.id, plataforma=Plataforma.YOUTUBE, enviada_em=agora,
                          unidades_cota=50, criada_em=agora - timedelta(days=3)))  # fmt: skip
    # Enviada ontem (no dia da cota): não conta. Pendente (sem envio): não conta.
    sessao.add(Publicacao(clipe_id=clipe.id, plataforma=Plataforma.YOUTUBE,
                          enviada_em=agora - timedelta(hours=10), unidades_cota=50))  # fmt: skip
    sessao.add(Publicacao(clipe_id=clipe.id, plataforma=Plataforma.YOUTUBE))
    sessao.commit()
    uso = cota.cota_youtube(sessao, agora)
    assert (uso.envios, uso.unidades) == (1, 50)
    assert uso.cabe(50) and not cota.CotaYoutube(100, 100, 0, 10_000, agora).cabe()
    assert not cota.CotaYoutube(5, 100, 9_960, 10_000, agora).cabe(50)


# ------------------------------------------------------------------ fixture com clipes


@pytest.fixture
def cfg(tmp_path):
    return config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "d")


@pytest.fixture
def momento(sessao, cfg) -> Momento:
    """Um momento cortado nos 3 formatos (arquivos falsos) e com thumbnail."""
    gravacao = Gravacao(arquivo="C:/g/live.mkv", inicio=datetime(2026, 10, 26, 23, tzinfo=UTC),
                        duracao_s=3600, status=StatusGravacao.CONCLUIDA)  # fmt: skip
    sessao.add(gravacao)
    sessao.flush()
    momento = Momento(
        gravacao_id=gravacao.id,
        inicio_s=100,
        fim_s=130,
        nota=90,
        pico_s=110,
        titulos_sugeridos=[{"titulo": "NÃO ACREDITO", "tipo": "frase_sua"}],
    )
    sessao.add(momento)
    sessao.flush()
    for formato in FormatoClipe:
        arquivo = cfg.pasta_clipes / f"{formato.value}.mp4"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(b"video")
        sessao.add(Clipe(momento_id=momento.id, formato=formato, arquivo=str(arquivo),
                         duracao_s=30))  # fmt: skip
    caminho_da_thumbnail(cfg, gravacao, momento).write_bytes(b"\xff\xd8")
    sessao.commit()
    return momento


def aprovar(sessao, momento, cfg, destinos=None):
    return decisoes.decidir(
        sessao, momento.id, aprovado=True, titulo="NÃO ACREDITO", hashtags=["#lol"], cfg=cfg,
        destinos=destinos or {"horizontal": ["youtube"], "vertical_a": ["youtube", "tiktok"]},
    )  # fmt: skip


def publicacoes(sessao) -> list[Publicacao]:
    return list(sessao.exec(select(Publicacao).order_by(Publicacao.id)).all())


def tarefas_publicar(sessao) -> list[Tarefa]:
    return list(sessao.exec(select(Tarefa).where(Tarefa.tipo == TipoTarefa.PUBLICAR)).all())


# ------------------------------------------------------------------ planejar (pela revisão)


def test_aprovar_poe_as_publicacoes_do_youtube_na_fila(sessao, momento, cfg):
    antes = datetime.now(UTC)
    aprovar(sessao, momento, cfg)
    pubs = publicacoes(sessao)
    assert [p.plataforma for p in pubs] == [
        Plataforma.YOUTUBE,
        Plataforma.YOUTUBE,
    ]  # TikTok: etapa 16
    assert all(p.status is StatusPublicacao.PENDENTE for p in pubs)
    # Horários diferentes, a pelo menos 2 h um do outro (intervalo mínimo padrão).
    assert abs(pubs[0].agendada_para - pubs[1].agendada_para) >= timedelta(hours=2)
    tarefas = tarefas_publicar(sessao)
    assert {t.publicacao_id for t in tarefas} == {p.id for p in pubs}
    # O envio espera 10 min: dá para desfazer antes de qualquer coisa sair do PC.
    for tarefa in tarefas:
        espera = tarefa.executar_a_partir_de - antes
        assert timedelta(minutes=9) < espera < timedelta(minutes=11)


def test_desfazer_ou_recusar_cancela_o_que_nao_saiu(sessao, momento, cfg):
    aprovar(sessao, momento, cfg)
    decisoes.desfazer(sessao, momento.id)
    assert publicacoes(sessao) == [] and tarefas_publicar(sessao) == []
    aprovar(sessao, momento, cfg)
    decisoes.decidir(sessao, momento.id, aprovado=False, cfg=cfg)
    assert publicacoes(sessao) == [] and tarefas_publicar(sessao) == []


def test_mudar_os_destinos_refaz_as_publicacoes(sessao, momento, cfg):
    aprovar(sessao, momento, cfg)
    aprovar(sessao, momento, cfg, destinos={"vertical_a": ["youtube"]})
    (unica,) = publicacoes(sessao)
    assert sessao.get(Clipe, unica.clipe_id).formato is FormatoClipe.VERTICAL_A
    assert len(tarefas_publicar(sessao)) == 1


def test_publicacao_enviada_ou_saindo_trava_a_decisao(sessao, momento, cfg):
    aprovar(sessao, momento, cfg)
    tarefa = tarefas_publicar(sessao)[0]
    tarefa.status = StatusTarefa.EM_ANDAMENTO  # o envio começou
    sessao.add(tarefa)
    sessao.commit()
    with pytest.raises(decisoes.RevisaoBloqueadaError):
        decisoes.desfazer(sessao, momento.id)
    tarefa.status = StatusTarefa.FEITA
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    pub.status = StatusPublicacao.AGENDADA
    sessao.add_all([tarefa, pub])
    sessao.commit()
    with pytest.raises(decisoes.RevisaoBloqueadaError):
        decisoes.decidir(sessao, momento.id, aprovado=False, cfg=cfg)


def test_sem_config_so_grava_a_decisao(sessao, momento):
    decisoes.decidir(sessao, momento.id, aprovado=True, titulo="t",
                     destinos={"horizontal": ["youtube"]})  # fmt: skip
    assert publicacoes(sessao) == []


# ------------------------------------------------------------------ o envio


class YoutubeFalso:
    def __init__(self, erro=None, erro_thumbnail=None):
        self.erro, self.erro_thumbnail = erro, erro_thumbnail
        self.enviados: list[tuple[Path, dict, bool]] = []
        self.thumbnails: list[tuple[str, Path]] = []

    def enviar(self, arquivo, corpo, notificar_inscritos):
        if self.erro:
            raise self.erro
        self.enviados.append((arquivo, corpo, notificar_inscritos))
        return youtube.VideoEnviado(
            f"id{len(self.enviados)}", f"https://youtu.be/id{len(self.enviados)}"
        )

    def definir_thumbnail(self, video_id, imagem):
        if self.erro_thumbnail:
            raise self.erro_thumbnail
        self.thumbnails.append((video_id, imagem))


@pytest.fixture
def falso(monkeypatch):
    yt = YoutubeFalso()
    monkeypatch.setattr(publicar, "criar_enviador", lambda cfg: yt)
    return yt


def tarefa_do(sessao, formato: str) -> Tarefa:
    for tarefa in tarefas_publicar(sessao):
        pub = sessao.get(Publicacao, tarefa.publicacao_id)
        if sessao.get(Clipe, pub.clipe_id).formato.value == formato:
            return tarefa
    raise AssertionError(formato)


def test_envia_o_horizontal_com_thumbnail(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "horizontal")
    publicar.publicar(sessao, tarefa, cfg)
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    assert pub.status is StatusPublicacao.AGENDADA
    assert (pub.id_na_plataforma, pub.link) == ("id1", "https://youtu.be/id1")
    assert pub.enviada_em is not None and pub.unidades_cota == 50  # a thumbnail
    arquivo, corpo, notificar = falso.enviados[0]
    assert arquivo.name == "horizontal.mp4" and notificar is True
    assert corpo["snippet"]["title"] == "NÃO ACREDITO"
    assert corpo["status"]["publishAt"] == pub.agendada_para.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert [v for v, _ in falso.thumbnails] == ["id1"]


def test_envia_o_vertical_como_short(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "vertical_a")
    publicar.publicar(sessao, tarefa, cfg)
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    assert pub.link == "https://youtube.com/shorts/id1"
    assert falso.thumbnails == [] and falso.enviados[0][2] is False  # não avisa os inscritos


def test_nao_envia_duas_vezes(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "horizontal")
    publicar.publicar(sessao, tarefa, cfg)
    publicar.publicar(sessao, tarefa, cfg)  # o PC caiu e a tarefa voltou para a fila
    assert len(falso.enviados) == 1


def test_durante_a_live_espera(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    contexto = fila.Contexto(deve_pausar=lambda: True)
    with pytest.raises(fila.TarefaPausada) as pausa:
        publicar.publicar(sessao, tarefa_do(sessao, "horizontal"), cfg, contexto)
    assert not isinstance(pausa.value, fila.TentarDepois)  # volta quando a live acabar
    assert falso.enviados == []


def test_espera_o_novo_corte(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    sessao.add(Tarefa(tipo=TipoTarefa.RECORTAR, momento_id=momento.id))
    sessao.commit()
    with pytest.raises(fila.TentarDepois, match="cortado de novo") as espera:
        publicar.publicar(sessao, tarefa_do(sessao, "horizontal"), cfg)
    assert espera.value.quando - datetime.now(UTC) == pytest.approx(
        timedelta(minutes=5), abs=timedelta(seconds=5)
    )
    assert falso.enviados == []


def test_cota_esgotada_espera_renovar(sessao, momento, cfg, falso, monkeypatch):
    aprovar(sessao, momento, cfg)
    renova = datetime.now(UTC) + timedelta(hours=6)
    monkeypatch.setattr(
        cota, "cota_youtube", lambda s, agora: cota.CotaYoutube(100, 100, 0, 10_000, renova)
    )
    with pytest.raises(fila.TentarDepois, match="cota") as espera:
        publicar.publicar(sessao, tarefa_do(sessao, "horizontal"), cfg)
    assert espera.value.quando == renova + publicar.FOLGA_DEPOIS_DA_COTA
    assert falso.enviados == []


def test_google_recusa_por_cota_tambem_espera(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    falso.erro = youtube.CotaEsgotadaError("uploadLimitExceeded")
    with pytest.raises(fila.TentarDepois):
        publicar.publicar(sessao, tarefa_do(sessao, "horizontal"), cfg)
    pub = publicacoes(sessao)[0]
    assert pub.status is StatusPublicacao.PENDENTE


def test_sem_login_explica_como_resolver(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    falso.erro = youtube.PrecisaEntrarError("rode 'clipador youtube-entrar'.")
    tarefa = tarefa_do(sessao, "horizontal")
    with pytest.raises(fila.FalhaDefinitivaError, match=f"clipador refazer {tarefa.id}"):
        publicar.publicar(sessao, tarefa, cfg)
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    # A fila marca a tarefa com erro; a revisão mostra a publicação com erro, e o refazer
    # a devolve à fila sem mais nada a mudar.
    tarefa.status = StatusTarefa.ERRO
    sessao.add(tarefa)
    sessao.commit()
    assert planejar.status_visivel(sessao, pub) is StatusPublicacao.ERRO
    fila.refazer(sessao, tarefa.id)
    assert planejar.status_visivel(sessao, pub) is StatusPublicacao.PENDENTE


def test_horario_que_passou_e_remarcado(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "horizontal")
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    pub.agendada_para = datetime.now(UTC) - timedelta(hours=1)  # esperou a cota, por exemplo
    sessao.add(pub)
    sessao.commit()
    publicar.publicar(sessao, tarefa, cfg)
    assert pub.agendada_para > datetime.now(UTC) + timedelta(
        minutes=cfg.youtube.antecedencia_min - 1
    )


def test_thumbnail_recusada_nao_atrapalha(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    falso.erro_thumbnail = youtube.SemPermissaoError("forbidden")
    tarefa = tarefa_do(sessao, "horizontal")
    publicar.publicar(sessao, tarefa, cfg)
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    assert pub.status is StatusPublicacao.AGENDADA and pub.unidades_cota == 0


def test_nunca_publica_sem_aprovacao(sessao, momento, cfg, falso):
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "horizontal")
    # Uma decisão mudada por fora (o desfazer normal já cancelaria a tarefa).
    from clipador.banco.modelos import Decisao

    decisao = sessao.exec(select(Decisao)).one()
    decisao.aprovado = False
    sessao.add(decisao)
    sessao.commit()
    with pytest.raises(fila.FalhaDefinitivaError, match="não está aprovado"):
        publicar.publicar(sessao, tarefa, cfg)
    assert falso.enviados == []


def test_modo_simulado_nao_usa_a_internet(sessao, momento, tmp_path):
    cfg = config_sem_arquivo(pasta_gravacoes=tmp_path / "g", pasta_dados=tmp_path / "d",
                             youtube=ConfigYoutube(modo="simulado"))  # fmt: skip
    assert isinstance(publicar.criar_enviador(cfg), publicar.YoutubeSimulado)
    aprovar(sessao, momento, cfg)
    tarefa = tarefa_do(sessao, "horizontal")
    publicar.publicar(sessao, tarefa, cfg)
    pub = sessao.get(Publicacao, tarefa.publicacao_id)
    assert pub.status is StatusPublicacao.AGENDADA and pub.id_na_plataforma.startswith("simulado")
    assert pub.link is None  # não existe vídeo de verdade


def test_modo_real_usa_o_cliente_da_integracao(cfg):
    enviador = publicar.criar_enviador(cfg)
    assert isinstance(enviador, youtube.ClienteYoutube)
    assert enviador.pasta == cfg.pasta_segredos


# ------------------------------------------------------------------ no serviço


def test_servico_devolve_a_tarefa_para_a_hora_certa_sem_contar_tentativa(cfg, monkeypatch):
    from clipador.banco.conexao import nova_sessao
    from clipador.sistema.servico import Servico

    depois = datetime.now(UTC) + timedelta(hours=5)

    def esperar(sessao, tarefa, cfg, contexto):
        raise fila.TentarDepois("cota", depois)

    servico = Servico(cfg, executores={TipoTarefa.PUBLICAR: esperar})
    servico.iniciar()
    try:
        with nova_sessao(servico.engine) as sessao:
            fila.adicionar(sessao, TipoTarefa.PUBLICAR)
        resultado = servico.passo()
        assert resultado.tarefa_pausada and not resultado.tarefa_falhou
        assert resultado.motivo_da_pausa.endswith(": cota")  # o processar mostra o motivo
        with nova_sessao(servico.engine) as sessao:
            tarefa = sessao.exec(select(Tarefa)).one()
            assert tarefa.status is StatusTarefa.PENDENTE and tarefa.tentativas == 0
            assert tarefa.executar_a_partir_de == depois
    finally:
        servico.encerrar()


def test_servico_tem_o_executor_de_publicar():
    from clipador.sistema.servico import EXECUTORES

    assert EXECUTORES[TipoTarefa.PUBLICAR] is publicar.publicar
