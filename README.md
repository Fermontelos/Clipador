# Clipador de lives

> Nome provisório.

App em Python que transforma gravações de live (OBS) em clipes para YouTube e TikTok.
O processamento roda de madrugada, a revisão acontece no dia seguinte numa interface web
local e **nada é publicado sem aprovação**. Com o tempo, o app aprende com as aprovações e
com o engajamento do público quais momentos viram bons cortes.

Projeto pessoal e de portfólio, feito só com ferramentas e APIs gratuitas.

## Status

Em desenvolvimento, uma etapa por vez. O roteiro completo (19 etapas), a stack e os riscos
estão em [`docs/Clipador - Stack e Ferramentas.pdf`](docs/Clipador%20-%20Stack%20e%20Ferramentas.pdf).

| Etapa | O que entrega | Situação |
|---|---|---|
| 1 · Esqueleto | Estrutura, configuração validada, log, CLI, testes e CI | ✅ |
| 2 · Banco e fila | Estado do app e fila retomável | ⏳ próxima |

## O que já funciona

- `clipador versao`: mostra a versão.
- `clipador config`: confere se o `config.toml` é válido e mostra os valores carregados.
  Um valor errado (ex.: limite diário negativo, campo com nome digitado errado) gera um
  erro claro.

## Como rodar

Requisitos: Windows 10+, [uv](https://docs.astral.sh/uv/) e Git. O uv instala o Python 3.12
se precisar.

```bash
git clone <url-do-repositorio>
cd clipador
uv sync
copy config.example.toml config.toml
uv run clipador config
```

Edite o `config.toml` para apontar `pasta_gravacoes` para a pasta onde o OBS salva as lives.

## Desenvolvimento

```bash
uv run pytest            # testes
uv run ruff check        # lint
uv run ruff format       # formatação
uv run pre-commit install  # roda tudo isso antes de cada commit
```

A CI (GitHub Actions) roda lint, formatação e testes em Windows a cada push.

## Estrutura

```
src/clipador/   código do app (config, logs, CLI; cada etapa acrescenta módulos)
tests/          testes automatizados
docs/           roteiro em PDF e decisões técnicas (docs/adr/)
dados/          banco, logs e clipes (fora do git, criado pelo app)
```

## Decisões técnicas

- [ADR 0001: Python + SQLite + FastAPI](docs/adr/0001-python-sqlite-fastapi.md)

## Licença

[MIT](LICENSE)
