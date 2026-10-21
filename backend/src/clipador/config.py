"""Configuração do app.

Os valores vêm de dois lugares, nesta ordem de prioridade:

1. variáveis de ambiente com o prefixo ``CLIPADOR_`` (ex.: ``CLIPADOR_NIVEL_LOG=DEBUG``);
2. o arquivo ``config.toml`` (fora do git).

O backend **não lê o ``.env``**: ele guarda chaves e tokens, e só o pacote
``integracoes`` pode lê-lo (ADR 0003). Tudo é validado pelo pydantic: um valor errado gera
um erro claro logo na abertura, e não no meio do processamento da madrugada.

Caminhos relativos (ex.: ``pasta_dados = "dados"``) são sempre contados a partir da pasta
do ``config.toml``, e não da pasta onde o comando foi executado. Isso importa porque os
programas abertos sozinhos no login (etapa 4, ADR 0004) costumam começar em
``C:\\Windows\\System32``: sem isso, o banco iria parar lá.
"""

import tomllib
from contextvars import ContextVar
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CAMINHO_PADRAO = Path("config.toml")
PREFIXO = "CLIPADOR_"

# Qual TOML ler. Fica numa ContextVar para que carregar_config() possa escolher o arquivo
# sem mudar a classe (útil nos testes). None = não ler arquivo nenhum (ver config_sem_arquivo).
_arquivo_toml: ContextVar[Path | None] = ContextVar("arquivo_toml", default=CAMINHO_PADRAO)


class ConfigNaoEncontradaError(FileNotFoundError):
    """O arquivo de configuração não existe."""


class ConfigMalEscritaError(ValueError):
    """O config.toml tem um erro de sintaxe (ex.: aspas faltando)."""


class _TomlDoApp(TomlConfigSettingsSource):
    """Lê o TOML aceitando o BOM.

    O PowerShell e versões antigas do Bloco de Notas salvam UTF-8 com um BOM (uma marca
    invisível no começo do arquivo), que o leitor de TOML do Python recusa. "utf-8-sig"
    ignora essa marca se ela existir.
    """

    def _read_file(self, file_path) -> dict[str, Any]:
        return tomllib.loads(Path(file_path).read_text(encoding="utf-8-sig"))


class Faixas(BaseModel):
    """Em qual faixa de áudio do OBS está cada fonte (contando a partir de 1, como no OBS).

    A faixa 1 é a mistura completa que vai para a Twitch; o app não a usa.
    """

    model_config = ConfigDict(extra="forbid")

    voz: int = Field(default=2, ge=1, le=6, description="Faixa só com o microfone.")
    jogo: int = Field(default=3, ge=1, le=6, description="Faixa só com o som do jogo.")
    musica: int = Field(default=4, ge=1, le=6, description="Faixa só com a música.")

    @model_validator(mode="after")
    def _faixas_diferentes(self) -> "Faixas":
        if len({self.voz, self.jogo, self.musica}) != 3:
            raise ValueError("voz, jogo e música precisam estar em faixas diferentes")
        return self


# Termos que o Whisper costuma errar numa live de LoL. Vão como "hotwords": uma dica que
# entra em toda janela de 30 s da transcrição (ver transcricao/whisper.py).
VOCABULARIO_PADRAO = [
    "League of Legends", "LoL", "gank", "flash", "ult", "barão", "dragão", "arauto",
    "pentakill", "first blood", "jungle", "mid", "top", "bot", "suporte", "farm",
]  # fmt: skip


class ConfigTranscricao(BaseModel):
    """Como transcrever a voz (etapa 6)."""

    model_config = ConfigDict(extra="forbid")

    modelo: str = Field(
        default="medium", description="Modelo do Whisper (tiny, base, small, medium...)."
    )
    modelo_reserva: str | None = Field(
        default="small",
        description="Modelo menor, usado se faltar memória na placa de vídeo.",
    )
    dispositivo: Literal["auto", "cuda", "cpu"] = Field(
        default="auto",
        description="auto = placa de vídeo (CUDA) se houver; senão, o processador (lento).",
    )
    vocabulario: list[str] = Field(
        default_factory=lambda: list(VOCABULARIO_PADRAO),
        description=(
            "Nomes e gírias que o Whisper deve reconhecer (campeões, termos do jogo, seu nick). "
            "Só os primeiros ~220 tokens são usados: deixe os que mais aparecem."
        ),
    )
    minutos_por_bloco: float = Field(
        default=10,
        ge=1,
        le=60,
        description="A transcrição é salva a cada bloco: é o máximo que se perde se o PC cair.",
    )


class ConfigLol(BaseModel):
    """Coletor de eventos do League of Legends (etapa 7)."""

    model_config = ConfigDict(extra="forbid")

    coletar: bool = Field(
        default=True,
        description="Consultar o LoL a cada 2 s durante as partidas e salvar os eventos.",
    )


EXPRESSOES_PADRAO = [
    "meu deus", "não acredito", "caramba", "nossa", "socorro", "que isso", "como assim",
    "eu juro", "pelo amor de deus", "mano do céu", "não não não", "olha isso",
]  # fmt: skip


class ConfigDetector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ligado: bool = Field(default=True, description="Usar este detector.")
    peso: float = Field(default=1.0, ge=0, le=1, description="Peso do sinal na nota.")


class ConfigVolumeVoz(ConfigDetector):
    """Picos de volume da sua voz (o sinal principal)."""

    peso: float = Field(default=0.5, ge=0, le=1)
    db_acima_do_normal: float = Field(
        default=10, gt=0, le=40, description="Quantos dB acima do seu volume normal é um pico."
    )
    duracao_minima_s: float = Field(
        default=1.0, gt=0, le=10, description="Quanto tempo o volume precisa ficar alto."
    )


class ConfigFala(ConfigDetector):
    """Expressões faladas e fala acelerada, pela transcrição."""

    peso: float = Field(default=0.25, ge=0, le=1)
    expressoes: list[str] = Field(
        default_factory=lambda: list(EXPRESSOES_PADRAO),
        description="Expressões suas que costumam marcar um momento bom.",
    )
    fala_acelerada: float = Field(
        default=1.6,
        gt=1,
        le=5,
        description="Quantas vezes a sua velocidade normal de fala conta como fala acelerada.",
    )


class ConfigLolDetector(ConfigDetector):
    """Eventos da partida de LoL (abates, objetivos)."""

    peso: float = Field(default=0.25, ge=0, le=1)


class ConfigDeteccao(BaseModel):
    """Como achar os momentos e dar a nota (etapa 8)."""

    model_config = ConfigDict(extra="forbid")

    max_momentos_por_live: int = Field(default=30, ge=1, le=200)
    nota_minima: float = Field(
        default=10,
        ge=0,
        le=100,
        description="Momentos com nota abaixo disto são ruído (ex.: só a torre de um aliado).",
    )
    segundos_antes: float = Field(
        default=10, ge=0, le=60, description="O clipe começa este tanto antes do 1º sinal."
    )
    segundos_depois: float = Field(
        default=5, ge=0, le=60, description="O clipe termina este tanto depois do último sinal."
    )
    duracao_minima_s: float = Field(default=15, gt=0, le=600)
    duracao_maxima_s: float = Field(default=60, gt=0, le=600)
    juntar_ate_s: float = Field(
        default=8, ge=0, le=60, description="Sinais a menos disto um do outro viram um momento só."
    )
    volume_voz: ConfigVolumeVoz = Field(default_factory=ConfigVolumeVoz)
    fala: ConfigFala = Field(default_factory=ConfigFala)
    lol: ConfigLolDetector = Field(default_factory=ConfigLolDetector)

    @model_validator(mode="after")
    def _duracoes_coerentes(self) -> "ConfigDeteccao":
        if self.duracao_minima_s > self.duracao_maxima_s:
            raise ValueError("duracao_minima_s não pode ser maior que duracao_maxima_s")
        return self


class ConfigCortes(BaseModel):
    """Como cortar os clipes (etapa 9)."""

    model_config = ConfigDict(extra="forbid")

    formatos: list[Literal["horizontal", "vertical_a"]] = Field(
        default_factory=lambda: ["horizontal", "vertical_a"],
        min_length=1,
        description=(
            "Quais formatos gerar de cada momento: horizontal (16:9) e vertical_a (9:16, com "
            "o fundo desfocado). O vertical_b (zoom na ação) ainda não existe."
        ),
    )
    encoder: Literal["auto", "h264_nvenc", "libx264"] = Field(
        default="auto",
        description=(
            "auto = a placa de vídeo (NVENC) se funcionar; senão, o processador (libx264). "
            "Ver ADR 0006: com o driver 580 e o FFmpeg 9, o NVENC não funciona na GTX 1050 Ti."
        ),
    )
    volume_voz: float = Field(default=1.0, ge=0, le=4, description="Volume da sua voz no clipe.")
    volume_jogo: float = Field(default=0.6, ge=0, le=4, description="Volume do jogo no clipe.")
    lufs: float = Field(
        default=-14,
        ge=-30,
        le=-5,
        description="Volume final (loudness), em LUFS. -14 é o padrão de YouTube e TikTok.",
    )
    legendas: bool = Field(default=True, description="Queimar a legenda nos clipes verticais.")


class ConfigTitulos(BaseModel):
    """Títulos e hashtags sugeridos pela IA local (etapa 10)."""

    model_config = ConfigDict(extra="forbid")

    fonte: Literal["ollama", "simples", "demo"] = Field(
        default="ollama",
        description=(
            "ollama = IA local; simples = a frase mais marcante da transcrição, sem IA "
            "(também é o plano B se o Ollama falhar); demo = os títulos prontos da demo."
        ),
    )
    modelo: str = Field(default="qwen3:4b", description="Modelo do Ollama.")
    modelo_reserva: str | None = Field(
        default="qwen3:1.7b", description="Modelo menor, usado se faltar memória na placa."
    )
    endereco: str = Field(default="http://127.0.0.1:11434", description="Onde o Ollama atende.")
    tempo_limite_s: float = Field(default=180, gt=0, le=1800)
    hashtags_fixas: list[str] = Field(
        default_factory=lambda: ["#leagueoflegends", "#lolbr"],
        description="Hashtags que entram em todo clipe (o resto o modelo sugere).",
    )
    max_hashtags: int = Field(
        default=5, ge=1, le=15, description="O YouTube recomenda de 3 a 5 por vídeo."
    )
    exemplos: int = Field(
        default=10,
        ge=0,
        le=30,
        description="Quantos títulos escolhidos por você entram como exemplo do seu estilo.",
    )


class Config(BaseSettings):
    """Configurações do app. Cada etapa do roteiro acrescenta as suas."""

    # extra="forbid": um campo com nome errado no TOML (ex.: "limte_diario") vira erro,
    # em vez de ser ignorado em silêncio.
    model_config = SettingsConfigDict(env_prefix=PREFIXO, extra="forbid")

    pasta_gravacoes: Path = Field(description="Pasta onde o OBS salva as gravações.")
    pasta_dados: Path = Field(
        default=Path("dados"),
        description="Pasta onde o app guarda banco, logs e clipes.",
    )
    limite_diario: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Quantos momentos a revisão mostra por dia.",
    )
    nivel_log: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Quanto detalhe vai para o log.",
    )
    minutos_gravacao_parada: float = Field(
        default=2,
        gt=0,
        le=120,
        description=(
            "Minutos sem o arquivo mudar para considerar que a live terminou. Se você usa "
            "o 'Pausar gravação' do OBS por mais tempo que isso, aumente este valor."
        ),
    )
    so_nomes_do_obs: bool = Field(
        default=True,
        description=(
            "Só processar vídeos com o nome no padrão do OBS (data e hora). Protege contra "
            "processar outros vídeos que estejam na mesma pasta."
        ),
    )
    faixas: Faixas = Field(
        default_factory=Faixas,
        description="Em qual faixa de áudio do OBS está a voz, o jogo e a música.",
    )
    transcricao: ConfigTranscricao = Field(
        default_factory=ConfigTranscricao,
        description="Como transcrever a voz (modelo do Whisper, vocabulário...).",
    )
    lol: ConfigLol = Field(
        default_factory=ConfigLol,
        description="Coletor de eventos do League of Legends.",
    )
    deteccao: ConfigDeteccao = Field(
        default_factory=ConfigDeteccao,
        description="Como achar os momentos da live e dar a nota.",
    )
    cortes: ConfigCortes = Field(
        default_factory=ConfigCortes,
        description="Formatos dos clipes, encoder, volumes e legendas.",
    )
    titulos: ConfigTitulos = Field(
        default_factory=ConfigTitulos,
        description="Títulos e hashtags com a IA local (Ollama).",
    )
    ignorar_gravacoes_antes_de: date | None = Field(
        default=None,
        description=(
            "Gravações que começaram antes desta data não entram na fila (ex.: lives antigas, "
            "de antes de o OBS gravar as 4 faixas). Vazio = todas entram."
        ),
    )
    pasta_backups: Path | None = Field(
        default=None,
        description=(
            "Pasta das cópias diárias do banco. Se vazia, usa <pasta_dados>/backups. "
            "O ideal é outro disco ou uma pasta sincronizada com o Google Drive."
        ),
    )

    @property
    def caminho_banco(self) -> Path:
        """Arquivo do banco SQLite."""
        return self.pasta_dados / "clipador.db"

    @property
    def pasta_trabalho(self) -> Path:
        """Arquivos temporários do processamento (ex.: o áudio da voz), um por gravação."""
        return self.pasta_dados / "trabalho"

    @property
    def ignorar_antes_de(self) -> datetime | None:
        """``ignorar_gravacoes_antes_de`` como o instante (UTC) da meia-noite no horário do PC."""
        if self.ignorar_gravacoes_antes_de is None:
            return None
        meia_noite = datetime.combine(self.ignorar_gravacoes_antes_de, datetime.min.time())
        return meia_noite.astimezone().astimezone(UTC)

    @property
    def pasta_transcricoes(self) -> Path:
        """Legendas .srt de cada live, para conferir a transcrição."""
        return self.pasta_dados / "transcricoes"

    @property
    def pasta_clipes(self) -> Path:
        """Os clipes prontos para revisar (e as thumbnails)."""
        return self.pasta_dados / "clipes"

    @property
    def pasta_modelos(self) -> Path:
        """Modelos do Whisper, baixados uma vez na primeira transcrição (~1,5 GB o medium)."""
        return self.pasta_dados / "modelos"

    @property
    def pasta_backups_efetiva(self) -> Path:
        """Pasta de backups configurada ou, se não houver, a padrão dentro de pasta_dados."""
        return self.pasta_backups or self.pasta_dados / "backups"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Ordem de prioridade: argumentos diretos > variáveis de ambiente > config.toml.
        # O .env (chaves) fica de fora de propósito: só as integrações o leem.
        arquivo = _arquivo_toml.get()
        if arquivo is None:
            return (init_settings, env_settings)
        return (init_settings, env_settings, _TomlDoApp(settings_cls, toml_file=arquivo))


def carregar_config(caminho: Path = CAMINHO_PADRAO) -> Config:
    """Lê e valida a configuração.

    Levanta ``ConfigNaoEncontradaError`` se o ``config.toml`` não existir,
    ``ConfigMalEscritaError`` se ele tiver erro de sintaxe e ``pydantic.ValidationError``
    se algum valor for inválido.
    """
    if not caminho.is_file():
        raise ConfigNaoEncontradaError(
            f"Arquivo de configuração não encontrado: {caminho}. "
            "Copie o config.example.toml para config.toml e ajuste os valores."
        )
    pasta_do_config = caminho.resolve().parent
    token_toml = _arquivo_toml.set(caminho)
    try:
        cfg = Config()
    except tomllib.TOMLDecodeError as erro:
        raise ConfigMalEscritaError(
            f"O arquivo {caminho} tem um erro de escrita: {erro}. "
            "Confira aspas, sinais de igual e colchetes nessa linha."
        ) from erro
    finally:
        _arquivo_toml.reset(token_toml)
    return _resolver_caminhos(cfg, pasta_do_config)


def config_sem_arquivo(**valores: Any) -> Config:
    """Configuração só com os valores dados e os padrões, sem ler o ``config.toml``.

    Usada pela demo: ela tem as faixas no padrão e pastas próprias, e não pode herdar,
    por exemplo, um ``[faixas]`` diferente do seu ``config.toml``.
    """
    token_toml = _arquivo_toml.set(None)
    try:
        return Config(**valores)
    finally:
        _arquivo_toml.reset(token_toml)


def _resolver_caminhos(cfg: Config, base: Path) -> Config:
    """Troca os caminhos relativos por absolutos, contados a partir de ``base``."""
    absolutos = {}
    for nome in ("pasta_gravacoes", "pasta_dados", "pasta_backups"):
        valor: Path | None = getattr(cfg, nome)
        if valor is not None and not valor.is_absolute():
            absolutos[nome] = base / valor
    return cfg.model_copy(update=absolutos)
