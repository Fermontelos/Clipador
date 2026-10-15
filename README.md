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
| 2 · Banco e fila | Banco SQLite, fila retomável e backup diário | ✅ |
| 3 · Modo demo | Live falsa para testar e demonstrar o app | ✅ |
| 4 · Monitor de pasta | Pega a gravação sozinho quando a live termina | ⏳ próxima |

## Experimente em 1 minuto (modo demo)

Não precisa de placa de vídeo, de contas no YouTube ou no TikTok, nem de configurar nada.
Só do Git, do [uv](https://docs.astral.sh/uv/) e do FFmpeg (`winget install Gyan.FFmpeg`).
No PowerShell:

```powershell
git clone https://github.com/Fermontelos/Clipador.git
cd Clipador
uv run clipador demo           # cria uma live falsa de 3 min em dados/demo/ (~15 s)
uv run clipador status --demo  # mostra a fila da demo
```

A live falsa tem as mesmas 4 faixas de áudio do OBS (mistura, voz, jogo e música), quatro
picos de voz em horários conhecidos (0:25, 1:10, 1:58 e 2:30), eventos de uma partida de LoL,
uma transcrição e títulos prontos. O vídeo mostra o tempo da gravação no centro da tela.
Rodar de novo não duplica nada; `--do-zero` apaga e recria. A demo usa uma pasta e um banco
separados: os seus dados reais não são tocados.

## O que já funciona

- `clipador versao`: mostra a versão.
- `clipador config`: confere se o `config.toml` é válido e mostra os valores carregados.
  Um valor errado (ex.: limite diário negativo, campo com nome digitado errado) gera um
  erro claro.
- `clipador status`: mostra a fila de tarefas (quantas pendentes, feitas e com erro, as
  próximas e o motivo de cada erro). Na primeira vez, cria o banco.
- `clipador demo`: cria a live falsa da demo (veja acima).
- `clipador backup`: faz uma cópia do banco agora. O app guarda as 14 mais recentes.
  Para restaurar uma cópia, veja [como restaurar um backup](#como-restaurar-um-backup).

Por baixo, o app já tem o banco (SQLite, em modo WAL) e uma **fila retomável**: se o PC
desligar no meio do processamento, as tarefas voltam para a fila na próxima abertura, e uma
tarefa que falha é tentada até 3 vezes antes de virar erro.

## Como rodar

Requisitos: Windows 10+, [uv](https://docs.astral.sh/uv/), Git e FFmpeg
(`winget install Gyan.FFmpeg`). O uv instala o Python 3.12 se precisar.

No PowerShell:

```powershell
git clone https://github.com/Fermontelos/Clipador.git
cd Clipador
uv sync
Copy-Item config.example.toml config.toml
uv run clipador config
```

Edite o `config.toml` para apontar `pasta_gravacoes` para a pasta onde o OBS salva as lives.
Caminhos relativos no `config.toml` (como `pasta_dados = "dados"`) são contados a partir da
pasta do próprio `config.toml`.

## Como restaurar um backup

Com o app fechado:

1. Renomeie o `dados/clipador.db` atual (ex.: para `clipador-quebrado.db`).
2. Apague `dados/clipador.db-wal` e `dados/clipador.db-shm`, se existirem. Eles pertencem ao
   banco antigo e podem corromper o banco restaurado.
3. Copie o backup escolhido (em `dados/backups/`, ou na pasta de `pasta_backups`) para
   `dados/clipador.db`.
4. Rode `uv run clipador status` para conferir.

## Desenvolvimento

```bash
uv run pytest -m "not lento"  # testes rápidos (o pre-commit roda estes)
uv run pytest              # todos, inclusive a demo real de 3 min (a CI roda estes)
uv run ruff check          # lint
uv run ruff format         # formatação
uv run pre-commit install  # passa a rodar lint, formatação e testes rápidos a cada commit
```

Depois de mudar uma tabela em `src/clipador/banco/modelos.py`, gere a migração do banco:

```bash
uv run alembic revision --autogenerate -m "o que mudou"
```

A CI (GitHub Actions) roda lint, formatação e testes em Windows a cada push.

## Estrutura

```
src/clipador/   código do app (config, logs, CLI; cada etapa acrescenta módulos)
  banco/        tabelas, fila, backup e migrações (Alembic)
  demo/         roteiro e gerador da live falsa
  ffmpeg.py     chamadas ao FFmpeg e ao ffprobe
tests/          testes automatizados
docs/           roteiro em PDF e decisões técnicas (docs/adr/)
dados/          banco, logs, clipes e a demo (fora do git, criado pelo app)
```

## Decisões técnicas

- [ADR 0001: Python + SQLite + FastAPI](docs/adr/0001-python-sqlite-fastapi.md)
- [ADR 0002: SQLite e não PostgreSQL](docs/adr/0002-sqlite-e-nao-postgres.md)

## Licença

[MIT](LICENSE)
