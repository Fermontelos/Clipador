"""O formato dos dados que entram e saem da API (aparecem no /docs)."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class ClipeSaida(BaseModel):
    id: int
    formato: str
    duracao_s: float | None
    video: str = Field(description="Endereço do vídeo (aceita Range: dá para avançar no player).")
    disponivel: bool = Field(description="O arquivo existe (falso se foi apagado ou movido).")


class PublicacaoSaida(BaseModel):
    id: int
    plataforma: str
    formato: str
    short: bool = Field(description="Sobe como Short no YouTube (vertical de até 3 min).")
    status: str = Field(
        description=(
            "pendente (na fila: desfazer cancela), enviando (subindo agora), agendada (já no "
            "YouTube, vai ao ar em agendada_para), na_pasta (TikTok: na pasta de exportação), "
            "rascunho (TikTok: na caixa de entrada do app) ou erro (veja a fila)."
        )
    )
    agendada_para: datetime | None = Field(description="Quando vai ao ar (a plataforma publica).")
    link: str | None
    enviada_em: datetime | None


class DecisaoSaida(BaseModel):
    aprovado: bool
    formatos: list[str]
    destinos: dict[str, list[str]]
    titulo: str | None
    hashtags: list[str]
    tipo_titulo: str | None
    ajuste_inicio_s: float | None
    ajuste_fim_s: float | None
    decidida_em: datetime


class MomentoSaida(BaseModel):
    id: int
    gravacao_id: int
    dia: date
    posicao: int = Field(
        description="Posição no dia pela nota (1 = a maior; 0 = gravação ainda em processamento)."
    )
    nota: float
    motivo: str
    sinais: dict[str, float]
    inicio_s: float
    fim_s: float
    duracao_s: float
    pico_s: float | None
    duracao_gravacao_s: float | None = Field(
        description="Duração da live inteira (o ajuste do corte não passa dela)."
    )
    inicio_detectado_s: float | None = Field(description="Vazio se o corte nunca foi ajustado.")
    fim_detectado_s: float | None
    titulos_sugeridos: list[dict]
    hashtags_sugeridas: list[str]
    clipes: list[ClipeSaida]
    thumbnail: str | None
    destinos_padrao: dict[str, list[str]] = Field(
        description="Destinos do config para cada formato cortado (o ponto de partida ao aprovar)."
    )
    decisao: DecisaoSaida | None
    recortando: bool = Field(description="Há um novo corte na fila (depois de um ajuste).")
    publicado: bool = Field(
        description="Algum clipe já foi enviado (ou está sendo): a decisão não muda mais."
    )
    publicacoes: list[PublicacaoSaida] = Field(
        description="As publicações do momento (as pendentes somem se você desfizer)."
    )


class ListaMomentos(BaseModel):
    dia: date | None = Field(description="Vazio se ainda não há nenhum momento para revisar.")
    limite_diario: int
    total: int = Field(description="Momentos do dia.")
    pendentes: int = Field(description="Momentos do dia sem decisão.")
    restantes: int = Field(description="Momentos que ficaram fora da lista (o 'ver mais').")
    momentos: list[MomentoSaida]


class DiaSaida(BaseModel):
    dia: date
    momentos: int
    decididos: int
    pendentes: int


class PedidoDecisao(BaseModel):
    aprovado: bool
    destinos: dict[str, list[str]] = Field(
        default_factory=dict,
        description='Formato -> plataformas. Ex.: {"vertical_a": ["youtube", "tiktok"]}.',
        examples=[{"vertical_a": ["youtube", "tiktok"]}],
    )
    titulo: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    tipo_titulo: str | None = Field(
        default=None,
        description=(
            "frase_sua, pergunta, exagero ou descricao (os da etapa 10). Se vazio, vem da "
            "sugestão com o mesmo título (um título editado fica sem tipo)."
        ),
    )


class PedidoAjuste(BaseModel):
    # allow_inf_nan=False: "NaN" e "Infinity" passam no JSON do Python, mas não são um tempo.
    inicio_s: float = Field(
        allow_inf_nan=False, description="Novo início, em segundos desde o começo da gravação."
    )
    fim_s: float = Field(allow_inf_nan=False)


class RespostaAjuste(BaseModel):
    tarefa_id: int | None = Field(description="A tarefa do novo corte (vazio: nada mudou).")
    momento: MomentoSaida


class TarefaSaida(BaseModel):
    id: int
    tipo: str
    status: str
    gravacao_id: int | None
    momento_id: int | None
    tentativas: int
    ultimo_erro: str | None
    atualizada_em: datetime


class CotaSaida(BaseModel):
    envios: int
    limite_envios: int
    unidades: int
    limite_unidades: int
    renova_em: datetime


class StatusSaida(BaseModel):
    fila: dict[str, int] = Field(description="Quantas tarefas há em cada situação.")
    em_andamento: list[TarefaSaida]
    erros: list[TarefaSaida]
    cota_youtube: CotaSaida
    dias: list[DiaSaida] = Field(description="Dias com momentos, do mais recente.")


class EstatisticasSaida(BaseModel):
    momentos: int
    aprovados: int
    recusados: int
    precisao: float | None = Field(description="Aprovados / decididos (a precisão do detector).")
    por_dia: list[dict]
    por_nota: list[dict]
    sinais: list[dict] = Field(
        description="Força média de cada detector (0 a 1) nos aprovados e nos recusados."
    )
    formatos: dict[str, int]
    plataformas: dict[str, int]
    ajuste_medio_inicio_s: float | None
    ajuste_medio_fim_s: float | None


class ConfigSaida(BaseModel):
    """O que a interface precisa saber do config (nada de caminhos nem chaves)."""

    limite_diario: int
    duracao_minima_s: float = Field(description="Menor duração aceita num ajuste do corte.")
    duracao_maxima_s: float
    formatos: list[str] = Field(description="Formatos cortados, na ordem das teclas 1, 2 e 3.")
    plataformas: list[str]
    plataformas_por_formato: dict[str, list[str]] = Field(
        description="Para onde cada formato pode ir (o TikTok só recebe os verticais)."
    )
    destinos_padrao: dict[str, list[str]]
    max_hashtags: int = Field(description="Quantas hashtags o YouTube recomenda (só aviso).")
    limite_titulo: int


class JanelaSugerida(BaseModel):
    segundos_antes: float
    segundos_depois: float
    media_inicio_s: float = Field(description="Quanto você moveu o início, em média.")
    media_fim_s: float
    aprovados: int


class AprendizadoSaida(BaseModel):
    """O que as suas decisões ensinam (etapa 14). Nada muda até você aceitar."""

    decisoes: int
    aprovadas: int
    minimo_para_pesos: int
    versao_atual: int | None
    pesos_atuais: dict[str, float]
    pesos_novos: dict[str, float] | None
    precisao_antes: float | None = Field(
        description="Precisão nos de maior nota com os pesos atuais, em decisões não vistas."
    )
    precisao_depois: float | None = Field(description="O mesmo, com os pesos sugeridos.")
    sem_pesos_porque: str | None
    janela_atual: tuple[float, float] = Field(description="(segundos antes, segundos depois).")
    janela_nova: JanelaSugerida | None
    sem_janela_porque: str | None
    precisao_por_dia: list[dict]
    versoes: list[dict] = Field(description="As versões aceitas, da primeira à atual.")


class CorteSaida(BaseModel):
    publicacao_id: int
    momento_id: int
    titulo: str | None
    plataforma: str
    formato: str
    link: str | None
    indice: float = Field(description="1 = o vídeo típico daquela rede no seu canal.")
    visualizacoes: int | None
    curtidas: int | None
    comentarios: int | None
    compartilhamentos: int | None
    retencao: float | None = Field(description="Fração média assistida (0 a 1; só YouTube).")
    tipo_de_momento: str | None = Field(description="O sinal mais forte do momento.")


class DesempenhoSaida(BaseModel):
    nome: str
    usos: int
    indice_medio: float
    efeito: float | None = Field(description="Índice médio com ela − sem ela.")
    confiavel: bool = Field(description="Usada em vídeos suficientes para concluir algo.")


class GrupoSaida(BaseModel):
    nome: str
    cortes: int
    indice_medio: float


class RedeSaida(BaseModel):
    plataforma: str
    cortes: int
    mediana_visualizacoes: float | None
    mediana_interacao: float | None
    mediana_retencao: float | None


class SugestaoEngajamentoSaida(BaseModel):
    cortes: int = Field(description="Cortes com a métrica de 7 dias (os exemplos do treino).")
    minimo: int
    mistura: float = Field(description="Quanto o engajamento vale na nota final (0 a 1).")
    pesos_atuais: dict[str, float]
    pesos_do_engajamento: dict[str, float] | None
    pesos_novos: dict[str, float] | None = Field(description="A mistura: o que vale se aceitar.")
    precisao_antes: float | None
    precisao_depois: float | None
    precisao_atual: float | None
    sem_pesos_porque: str | None


class EngajamentoSaida(BaseModel):
    """O que o público mostrou (etapa 17). Os pesos só mudam se você aceitar."""

    com_metricas: int
    com_7_dias: int
    cortes: list[CorteSaida]
    hashtags: list[DesempenhoSaida]
    tipos_de_titulo: list[DesempenhoSaida]
    por_tipo_de_momento: list[GrupoSaida]
    por_rede: list[RedeSaida]
    sugestao: SugestaoEngajamentoSaida
    minimo_usos: int


class PedidoAceiteEngajamento(BaseModel):
    cortes: int = Field(
        description="Quantos cortes havia na sugestão que você viu (se mudou, é recusado)."
    )


class PedidoAceite(BaseModel):
    decisoes: int = Field(
        description="Quantas decisões havia na sugestão que você viu (se mudou, é recusado)."
    )
