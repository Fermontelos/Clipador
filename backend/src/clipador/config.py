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
