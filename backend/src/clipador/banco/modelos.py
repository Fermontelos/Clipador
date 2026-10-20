"""Tabelas do banco.

Cada etapa do roteiro usa algumas destas tabelas; elas já nascem todas aqui para a
estrutura do app ficar clara desde o começo. Mudanças futuras entram por migração
(Alembic), sem perder dados.

Convenções:
- Datas e horas são sempre **com fuso, em UTC**. O SQLModel recusa datas sem fuso
  (``ValueError``) e devolve tudo em UTC; converta para o horário de Brasília só na hora
  de mostrar. Use ``agora()`` para gerar o horário atual.
- Tempos dentro de uma gravação ficam em segundos (``*_s``), contados do início do vídeo.
- Listas e dicionários (palavras, sinais, hashtags...) ficam em colunas JSON.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def agora() -> datetime:
    """Horário atual em UTC, com fuso (o formato aceito pelo banco)."""
    return datetime.now(UTC)


# ---------------------------------------------------------------- valores fixos


class StatusGravacao(StrEnum):
    NOVA = "nova"
    PROCESSANDO = "processando"
    CONCLUIDA = "concluida"
    ERRO = "erro"


class TipoTarefa(StrEnum):
    # Cadeia principal (uma cria a próxima ao terminar).
    EXTRAIR_AUDIO = "extrair_audio"
    TRANSCREVER = "transcrever"
    DETECTAR = "detectar"
    CORTAR = "cortar"
    GERAR_TITULOS = "gerar_titulos"
    # Criadas por eventos.
    RECORTAR = "recortar"
    PUBLICAR = "publicar"
    COLETAR_METRICAS = "coletar_metricas"


class StatusTarefa(StrEnum):
    PENDENTE = "pendente"
    EM_ANDAMENTO = "em_andamento"
    FEITA = "feita"
    ERRO = "erro"


class FormatoClipe(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL_A = "vertical_a"
    VERTICAL_B = "vertical_b"


class Plataforma(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"


class StatusPublicacao(StrEnum):
    PENDENTE = "pendente"
    ENVIADA = "enviada"
    AGENDADA = "agendada"
    PUBLICADA = "publicada"
    ERRO = "erro"


class ColetaMetrica(StrEnum):
    H24 = "24h"
    H72 = "72h"
    D7 = "7d"


# ---------------------------------------------------------------- tabelas


class Gravacao(SQLModel, table=True):
    """Uma gravação da live (arquivo MKV do OBS)."""

    id: int | None = Field(default=None, primary_key=True)
    arquivo: str = Field(unique=True, index=True)
    inicio: datetime = Field(description="Horário de início da gravação (UTC).")
    duracao_s: float | None = None
    # Dados do vídeo, lidos pelo ffprobe na etapa 5 (os cortes da etapa 9 usam).
    largura: int | None = None
    altura: int | None = None
    fps: float | None = None
    status: StatusGravacao = Field(default=StatusGravacao.NOVA, index=True)
    criada_em: datetime = Field(default_factory=agora)


class Tarefa(SQLModel, table=True):
    """Um trabalho da fila. Ver ``clipador.banco.fila``."""

    id: int | None = Field(default=None, primary_key=True)
    tipo: TipoTarefa = Field(index=True)
    gravacao_id: int | None = Field(default=None, foreign_key="gravacao.id", index=True)
    momento_id: int | None = Field(default=None, foreign_key="momento.id")
    publicacao_id: int | None = Field(default=None, foreign_key="publicacao.id")
    status: StatusTarefa = Field(default=StatusTarefa.PENDENTE, index=True)
    tentativas: int = 0
    ultimo_erro: str | None = None
    executar_a_partir_de: datetime = Field(default_factory=agora, index=True)
    criada_em: datetime = Field(default_factory=agora)
    atualizada_em: datetime = Field(default_factory=agora)


class Transcricao(SQLModel, table=True):
    """Transcrição da faixa de voz de uma gravação (etapa 6).

    É gravada aos poucos, um bloco de alguns minutos por vez: se o PC desligar no meio,
    a transcrição continua de ``transcrito_ate_s``.
    """

    id: int | None = Field(default=None, primary_key=True)
    gravacao_id: int = Field(foreign_key="gravacao.id", unique=True)
    # Lista de {"palavra": str, "inicio_s": float, "fim_s": float}; a transcrição real
    # também guarda "probabilidade" (0 a 1, a confiança do Whisper na palavra).
    palavras: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Lista de {"inicio_s": float, "fim_s": float, "texto": str}: as frases, para a legenda.
    segmentos: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Até onde (em segundos da gravação) a transcrição já está salva.
    transcrito_ate_s: float = 0.0
    concluida: bool = False
    modelo: str | None = None
    criada_em: datetime = Field(default_factory=agora)


class PapelNoEvento(StrEnum):
    """O que você fez num evento do LoL (ver ``clipador.jogo.eventos``)."""

    ABATE = "abate"  # você matou (ou derrubou a torre, o dragão...)
    MORTE = "morte"  # você morreu
    ASSISTENCIA = "assistencia"  # você ajudou


class EventoJogo(SQLModel, table=True):
    """Evento de partida do LoL salvo pelo coletor durante a live (etapa 7).

    Os eventos são salvos durante a live, antes de a gravação entrar no banco: por isso
    guardam o horário real (UTC), e não o segundo na gravação. O segundo na gravação é
    calculado depois (``clipador.jogo.alinhamento.eventos_da_gravacao``).
    """

    # O EventID recomeça do zero a cada partida: o par (partida, EventID) é que é único.
    __table_args__ = (
        UniqueConstraint("partida_inicio", "event_id", name="uq_eventojogo_partida_evento"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tipo: str = Field(index=True)
    # Quando o evento aconteceu no relógio real: partida_inicio + tempo_jogo_s.
    horario_real: datetime = Field(index=True)
    tempo_jogo_s: float
    # Quando a partida começou no relógio real (identifica a partida).
    partida_inicio: datetime = Field(index=True)
    event_id: int
    # O que você fez neste evento; vazio = não foi seu.
    papel: PapelNoEvento | None = None
    dados: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Momento(SQLModel, table=True):
    """Trecho candidato a virar clipe, com a nota calculada (etapas 8 e 10)."""

    id: int | None = Field(default=None, primary_key=True)
    gravacao_id: int = Field(foreign_key="gravacao.id", index=True)
    inicio_s: float
    fim_s: float
    # O instante mais forte do momento (o centro do sinal que mais pesou na nota): é dele a
    # thumbnail (etapa 9).
    pico_s: float | None = None
    sinais: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    nota: float = Field(default=0, index=True)
    motivo: str = ""
    titulos_sugeridos: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    hashtags_sugeridas: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Clipe(SQLModel, table=True):
    """Arquivo de vídeo cortado de um momento, num formato (etapa 9)."""

    __table_args__ = (UniqueConstraint("momento_id", "formato"),)

    id: int | None = Field(default=None, primary_key=True)
    momento_id: int = Field(foreign_key="momento.id", index=True)
    formato: FormatoClipe
    arquivo: str
    duracao_s: float | None = None


class Decisao(SQLModel, table=True):
    """Sua aprovação ou recusa de um momento, na revisão (etapa 12)."""

    id: int | None = Field(default=None, primary_key=True)
    momento_id: int = Field(foreign_key="momento.id", unique=True)
    aprovado: bool
    formatos: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    destinos: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    titulo: str | None = None
    hashtags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tipo_titulo: str | None = None
    ajuste_inicio_s: float | None = None
    ajuste_fim_s: float | None = None
    decidida_em: datetime = Field(default_factory=agora)


class Publicacao(SQLModel, table=True):
    """Envio de um clipe para uma plataforma (etapas 15 e 16)."""

    id: int | None = Field(default=None, primary_key=True)
    clipe_id: int = Field(foreign_key="clipe.id", index=True)
    plataforma: Plataforma
    id_na_plataforma: str | None = Field(default=None, index=True)
    status: StatusPublicacao = Field(default=StatusPublicacao.PENDENTE)
    link: str | None = None
    agendada_para: datetime | None = None
    unidades_cota: int = 0
    criada_em: datetime = Field(default_factory=agora)


class PesosNota(SQLModel, table=True):
    """Versão aceita dos pesos da nota (etapas 14 e 17)."""

    id: int | None = Field(default=None, primary_key=True)
    versao: int = Field(unique=True)
    valores: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    precisao: float | None = None
    criada_em: datetime = Field(default_factory=agora)


class Metrica(SQLModel, table=True):
    """Métricas de engajamento de uma publicação, numa das coletas (etapa 17)."""

    __table_args__ = (UniqueConstraint("publicacao_id", "coleta"),)

    id: int | None = Field(default=None, primary_key=True)
    publicacao_id: int = Field(foreign_key="publicacao.id", index=True)
    coleta: ColetaMetrica
    visualizacoes: int | None = None
    curtidas: int | None = None
    comentarios: int | None = None
    compartilhamentos: int | None = None
    retencao: float | None = None
    coletada_em: datetime = Field(default_factory=agora)
