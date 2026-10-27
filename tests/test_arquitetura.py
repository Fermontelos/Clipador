"""Regras de arquitetura e de segurança entre as partes do projeto (ADR 0003).

    frontend ──(HTTP)──> api ──> backend ──> integracoes ──> YouTube / TikTok / LoL

Estes testes leem o código-fonte (sem executá-lo) e falham se alguém quebrar as regras,
por exemplo, se a API passar a importar as integrações direto.
"""

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PACOTES = {
    "clipador_integracoes": RAIZ / "integracoes" / "src" / "clipador_integracoes",
    "clipador": RAIZ / "backend" / "src" / "clipador",
    "clipador_api": RAIZ / "api" / "src" / "clipador_api",
}
# Quem NÃO pode importar quem.
PROIBIDO = {
    # As integrações não conhecem o resto do app.
    "clipador_integracoes": {"clipador", "clipador_api"},
    # O backend não depende da API (a API é que depende dele).
    "clipador": {"clipador_api"},
    # A API nunca fala com as integrações direto: publicar vira tarefa na fila.
    "clipador_api": {"clipador_integracoes"},
}
# Sinais de que um código está lendo o .env (onde ficam as chaves).
LEITURA_DO_ENV = ("DotEnvSettingsSource", "env_file", "load_dotenv", "dotenv_values")
# Sinais de que um código está mexendo nos segredos do Google (etapa 15): a credencial do app,
# o token do login e as bibliotecas que os leem. Só as integrações podem.
LEITURA_DOS_SEGREDOS = (
    "client_secret",
    "youtube_token",
    "google.oauth2",
    "google_auth_oauthlib",
    "googleapiclient",
)


def _arquivos(pacote: str) -> list[Path]:
    return sorted(PACOTES[pacote].rglob("*.py"))


def _modulos_importados(arquivo: Path) -> set[str]:
    """Nome do pacote de topo de cada import do arquivo (ex.: "clipador" em clipador.banco)."""
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(alias.name.split(".")[0] for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            nomes.add(no.module.split(".")[0])
    return nomes


def test_todos_os_pacotes_existem():
    for pacote, pasta in PACOTES.items():
        assert (pasta / "__init__.py").is_file(), f"{pacote} não encontrado em {pasta}"


@pytest.mark.parametrize("pacote", list(PROIBIDO))
def test_dependencias_seguem_a_direcao_permitida(pacote):
    violacoes = []
    for arquivo in _arquivos(pacote):
        proibidos = _modulos_importados(arquivo) & PROIBIDO[pacote]
        if proibidos:
            violacoes.append(f"{arquivo.relative_to(RAIZ)} importa {sorted(proibidos)}")
    assert violacoes == [], f"{pacote} quebrou a regra de dependência:\n" + "\n".join(violacoes)


@pytest.mark.parametrize("pacote", ["clipador", "clipador_api"])
def test_so_as_integracoes_leem_o_env(pacote):
    violacoes = [
        f"{arquivo.relative_to(RAIZ)} usa {sinal}"
        for arquivo in _arquivos(pacote)
        for sinal in LEITURA_DO_ENV
        if sinal in arquivo.read_text(encoding="utf-8")
    ]
    assert violacoes == [], "Só as integrações podem ler o .env (chaves):\n" + "\n".join(violacoes)


@pytest.mark.parametrize("pacote", ["clipador", "clipador_api"])
def test_so_as_integracoes_leem_os_segredos_do_google(pacote):
    # O backend só passa a pasta (pasta_segredos); o que tem dentro é das integrações.
    violacoes = [
        f"{arquivo.relative_to(RAIZ)} usa {sinal}"
        for arquivo in _arquivos(pacote)
        for sinal in LEITURA_DOS_SEGREDOS
        if sinal in arquivo.read_text(encoding="utf-8")
    ]
    assert violacoes == [], "Só as integrações podem ler segredos/:\n" + "\n".join(violacoes)


def test_frontend_nao_guarda_chaves():
    # O que vai para o navegador pode ser lido por qualquer pessoa. Um .env no frontend
    # (fora o .env.example, sem valores) é sinal de chave no lugar errado.
    pasta = RAIZ / "frontend"
    arquivos_env = [
        p.relative_to(pasta)
        for p in pasta.rglob(".env*")
        if p.name != ".env.example" and not _de_terceiros(p, pasta)
    ]
    assert arquivos_env == []


def _de_terceiros(arquivo: Path, pasta: Path) -> bool:
    """Dependências do npm e o build não são código do projeto."""
    return arquivo.relative_to(pasta).parts[0] in {"node_modules", "dist"}


def test_codigo_do_frontend_nao_fala_de_chaves():
    # O frontend conversa só com a API local; tokens do YouTube e do TikTok ficam nas
    # integrações (lidas do .env pelo backend). Estes nomes no frontend são sinal de vazamento.
    proibidos = ("client_secret", "refresh_token", "access_token", "api_key", "apikey")
    violacoes = [
        f"{arquivo.relative_to(RAIZ)} menciona {nome}"
        for arquivo in (RAIZ / "frontend" / "src").rglob("*")
        if arquivo.is_file()
        for nome in proibidos
        if nome in arquivo.read_text(encoding="utf-8").lower()
    ]
    assert violacoes == []


def test_regras_conferem_de_verdade(tmp_path):
    # Garante que o detector de imports funciona (um teste que nunca falha não protege).
    falso = tmp_path / "falso.py"
    falso.write_text(
        "import clipador_integracoes.youtube\nfrom clipador.banco import fila\n",
        encoding="utf-8",
    )
    assert _modulos_importados(falso) == {"clipador_integracoes", "clipador"}
