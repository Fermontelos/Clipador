"""Configuração do app.

Os valores vêm do arquivo ``config.toml`` (fora do git) e podem ser sobrescritos por
variáveis de ambiente com o prefixo ``CLIPADOR_`` (ex.: ``CLIPADOR_NIVEL_LOG=DEBUG``).
Tudo é validado pelo pydantic: um valor errado gera um erro claro logo na abertura,
e não no meio do processamento da madrugada.
"""

from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CAMINHO_PADRAO = Path("config.toml")

# Qual arquivo TOML ler. Fica numa ContextVar para que carregar_config() possa
# escolher o arquivo sem mudar a classe (útil nos testes).
_arquivo_toml: ContextVar[Path] = ContextVar("arquivo_toml", default=CAMINHO_PADRAO)


class ConfigNaoEncontradaError(FileNotFoundError):
    """O arquivo de configuração não existe."""


class Config(BaseSettings):
    """Configurações do app. Cada etapa do roteiro acrescenta as suas."""

    # extra="forbid": um campo com nome errado no TOML (ex.: "limte_diario") vira erro,
    # em vez de ser ignorado em silêncio.
    model_config = SettingsConfigDict(env_prefix="CLIPADOR_", extra="forbid")

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
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=_arquivo_toml.get()),
        )


def carregar_config(caminho: Path = CAMINHO_PADRAO) -> Config:
    """Lê e valida a configuração.

    Levanta ``ConfigNaoEncontradaError`` se o arquivo não existir e
    ``pydantic.ValidationError`` se algum valor for inválido.
    """
    if not caminho.is_file():
        raise ConfigNaoEncontradaError(
            f"Arquivo de configuração não encontrado: {caminho}. "
            "Copie o config.example.toml para config.toml e ajuste os valores."
        )
    token = _arquivo_toml.set(caminho)
    try:
        return Config()
    finally:
        _arquivo_toml.reset(token)
