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


def test_frontend_nao_guarda_chaves():
    # O que vai para o navegador pode ser lido por qualquer pessoa. Um .env no frontend
    # (fora o .env.example, sem valores) é sinal de chave no lugar errado.
    pasta = RAIZ / "frontend"
    arquivos_env = [p.name for p in pasta.rglob(".env*") if p.name != ".env.example"]
    assert arquivos_env == []


def test_regras_conferem_de_verdade(tmp_path):
    # Garante que o detector de imports funciona (um teste que nunca falha não protege).
    falso = tmp_path / "falso.py"
    falso.write_text(
        "import clipador_integracoes.youtube\nfrom clipador.banco import fila\n",
        encoding="utf-8",
    )
    assert _modulos_importados(falso) == {"clipador_integracoes", "clipador"}
