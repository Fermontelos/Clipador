"""Configuração do app.

Os valores vêm de três lugares, nesta ordem de prioridade:

1. variáveis de ambiente com o prefixo ``CLIPADOR_`` (ex.: ``CLIPADOR_NIVEL_LOG=DEBUG``);
2. o arquivo ``.env`` ao lado do ``config.toml``, para chaves e segredos;
3. o arquivo ``config.toml``.

Os dois arquivos ficam fora do git. Tudo é validado pelo pydantic: um valor errado gera
um erro claro logo na abertura, e não no meio do processamento da madrugada.

Caminhos relativos (ex.: ``pasta_dados = "dados"``) são sempre contados a partir da pasta
do ``config.toml``, e não da pasta onde o comando foi executado. Isso importa porque o
Agendador de Tarefas do Windows (etapa 4) costuma iniciar programas em
``C:\\Windows\\System32``: sem isso, o banco iria parar lá.
"""

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CAMINHO_PADRAO = Path("config.toml")
PREFIXO = "CLIPADOR_"

# Quais arquivos ler. Ficam em ContextVars para que carregar_config() possa escolher
# os arquivos sem mudar a classe (útil nos testes).
_arquivo_toml: ContextVar[Path] = ContextVar("arquivo_toml", default=CAMINHO_PADRAO)
_arquivo_env: ContextVar[Path] = ContextVar("arquivo_env", default=Path(".env"))


class ConfigNaoEncontradaError(FileNotFoundError):
    """O arquivo de configuração não existe."""


class _DotEnvDoApp(DotEnvSettingsSource):
    """Lê o ``.env`` ignorando as linhas de outros programas.

    Uma chave sem o prefixo ``CLIPADOR_`` é ignorada. Uma chave com o prefixo que não
    existe na ``Config`` segue adiante e vira erro, porque provavelmente foi digitada
    errado.
    """

    def __call__(self) -> dict[str, Any]:
        dados = super().__call__()
        campos = self.settings_cls.model_fields
        return {
            chave: valor
            for chave, valor in dados.items()
            if chave in campos or chave.startswith(PREFIXO.lower())
        }


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
        # Ordem de prioridade: argumentos diretos > variáveis de ambiente > .env > config.toml.
        return (
            init_settings,
            env_settings,
            _DotEnvDoApp(settings_cls, env_file=_arquivo_env.get(), env_file_encoding="utf-8"),
            TomlConfigSettingsSource(settings_cls, toml_file=_arquivo_toml.get()),
        )


def carregar_config(caminho: Path = CAMINHO_PADRAO, arquivo_env: Path | None = None) -> Config:
    """Lê e valida a configuração.

    Por padrão, o ``.env`` lido é o que fica na mesma pasta do ``config.toml``; se ele
    não existir, é simplesmente ignorado.

    Levanta ``ConfigNaoEncontradaError`` se o ``config.toml`` não existir e
    ``pydantic.ValidationError`` se algum valor for inválido.
    """
    if not caminho.is_file():
        raise ConfigNaoEncontradaError(
            f"Arquivo de configuração não encontrado: {caminho}. "
            "Copie o config.example.toml para config.toml e ajuste os valores."
        )
    pasta_do_config = caminho.resolve().parent
    if arquivo_env is None:
        arquivo_env = pasta_do_config / ".env"
    token_toml = _arquivo_toml.set(caminho)
    token_env = _arquivo_env.set(arquivo_env)
    try:
        cfg = Config()
    finally:
        _arquivo_env.reset(token_env)
        _arquivo_toml.reset(token_toml)
    return _resolver_caminhos(cfg, pasta_do_config)


def _resolver_caminhos(cfg: Config, base: Path) -> Config:
    """Troca os caminhos relativos por absolutos, contados a partir de ``base``."""
    absolutos = {}
    for nome in ("pasta_gravacoes", "pasta_dados", "pasta_backups"):
        valor: Path | None = getattr(cfg, nome)
        if valor is not None and not valor.is_absolute():
            absolutos[nome] = base / valor
    return cfg.model_copy(update=absolutos)
