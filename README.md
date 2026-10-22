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
| 4 · Monitor de pasta | Pega a gravação sozinho quando a live termina | ✅ |
| 5 · Áudio da voz | Extrai a faixa da voz de cada gravação | ✅ |
| 6 · Transcrição | Transforma a fala em texto, palavra por palavra | ✅ |
| 7 · Coletor do LoL | Registra os eventos da partida durante a live | ✅ |
| 8 · Detectores e nota | Acha os melhores momentos de cada live | ✅ |
| 9 · Cortes | Gera os clipes horizontais e verticais | 🟡 horizontal e vertical A prontos; vertical B a definir |
| 10 · Títulos com IA | Sugere 3 títulos e as hashtags de cada clipe | ✅ |
| 11 · Desligar o PC | Desliga sozinho depois de processar | ⏳ próxima |

## Experimente em 2 minutos (modo demo)

Não precisa de placa de vídeo, de contas no YouTube ou no TikTok, nem de configurar nada.
Só do Git, do [uv](https://docs.astral.sh/uv/) e do FFmpeg (`winget install Gyan.FFmpeg`).
No PowerShell:

```powershell
git clone https://github.com/Fermontelos/Clipador.git
cd Clipador
uv run clipador demo           # cria uma live falsa de 3 min em dados/demo/ (~15 s)
uv run clipador status --demo  # mostra a fila da demo
uv run clipador processar --demo  # áudio, legenda, momentos, 8 clipes e títulos (~1,5 min)
uv run clipador eventos --demo    # os eventos do LoL da demo, no segundo certo do vídeo
uv run clipador momentos --demo   # os 4 melhores momentos, com nota, motivo e título
# os clipes da demo ficam em dados/demo/clipes/ (horizontal, vertical e thumbnail)
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
- `clipador status`: mostra se o serviço está rodando e a fila de tarefas (quantas
  pendentes, feitas e com erro, as próximas e o motivo de cada erro). Na primeira vez,
  cria o banco.
- `clipador iniciar`: liga o serviço. Ele percebe sozinho quando uma live termina (o
  arquivo do OBS fica 2 minutos sem mudar), coloca a gravação na fila e processa. Durante
  a live, a fila fica pausada para não pesar no PC, e enquanto processa o PC não suspende.
  Por segurança, só entram vídeos com o nome no padrão do OBS (data e hora); outros vídeos
  da mesma pasta são ignorados (`so_nomes_do_obs` no `config.toml`), assim como os clipes do
  Buffer de Replay e, se você configurar `ignorar_gravacoes_antes_de`, as lives de antes
  dessa data. Se você usa o "Pausar gravação" do OBS por mais de 2 minutos, aumente
  `minutos_gravacao_parada`, senão a live entra na fila pela metade.
- `clipador processar`: processa a fila agora, sem o serviço, e sai quando acabar. Cada
  gravação passa pela cadeia inteira: áudio da voz, transcrição, momentos, clipes e títulos
  (as etapas estão descritas abaixo). A 1ª tarefa extrai o áudio da voz (faixa 2 do OBS) num
  WAV mono de 16 kHz; a 2ª transcreve a sua fala com o Whisper na placa de vídeo, palavra
  por palavra, salvando uma legenda `.srt` em `dados/transcricoes/` para conferir. Uma live de 3 h leva uns
  15 a 60 min de transcrição; na primeira vez, o modelo (~1,5 GB) é baixado. A transcrição
  é salva a cada 10 min de áudio: se o PC desligar ou uma live começar, ela continua
  depois de onde parou. Nomes e gírias que o Whisper deve acertar ficam em
  `[transcricao] vocabulario` no `config.toml`. Se a gravação não tiver a faixa da voz, ou se ela estiver
  em silêncio (microfone fora da faixa 2 no OBS), a tarefa para com uma mensagem explicando
  o que conferir. As faixas de cada fonte ficam em `[faixas]` no `config.toml`.
  Uma gravação interrompida (o OBS travou ou faltou luz) também é aproveitada.
- Coletor do LoL: junto com o serviço, consulta o jogo a cada 2 s durante as partidas
  (a [API local do LoL](https://developer.riotgames.com/docs/lol), em `127.0.0.1:2999`, sem
  chave) e salva abates, mortes, dragões, arautos, barões, torres, inibidores e o
  resultado, marcando os que foram seus (abate, morte ou assistência). Depois, cada evento
  cai no segundo certo da gravação, mesmo que a partida comece depois da gravação ou a live
  passe da meia-noite. Para desligar: `[lol] coletar = false` no `config.toml`.
- Detecção dos melhores momentos: depois da transcrição, três detectores (plugins) olham
  a live: picos de volume da sua voz (o sinal principal: 10 dB acima do seu volume normal
  por 1 s), expressões e fala acelerada na transcrição ("meu Deus", "não acredito"...) e
  os eventos do LoL (pentakill > barão > abate; os seus valem mais). Sinais próximos viram
  um momento, com uma janela de 15 a 60 s e uma nota de 0 a 100 (média ponderada dos
  sinais; pesos em `[deteccao]` no `config.toml`). Ficam os 30 melhores.
- Cortes: cada momento vira um clipe MP4 em `dados/clipes/`, nos formatos de
  `[cortes] formatos`: horizontal (16:9) e vertical A (9:16, com o vídeo no centro e o
  fundo desfocado, e a legenda da sua fala queimada). O áudio junta só a voz e o jogo,
  **sem a música** (direitos autorais), normalizado para -14 LUFS, o padrão de YouTube e
  TikTok. O horizontal ganha uma thumbnail do pico do momento. Os cortes são feitos no
  processador quando o encoder da placa (NVENC) não funciona ([ADR 0006](docs/adr/0006-cortes-no-processador-sem-nvenc.md)).
- Títulos e hashtags com IA local: para cada momento, o [Ollama](https://ollama.com) com o
  modelo `qwen3:4b` sugere 3 títulos no seu estilo (frase sua, pergunta, exagero ou
  descrição) e as hashtags, a partir do que você falou e do que aconteceu no jogo. Os
  títulos que você escolher na revisão viram exemplos para os próximos. Roda no próprio PC,
  sem internet e sem custo. Sem o Ollama, o título vira a frase mais marcante do momento.
  Para instalar: `winget install Ollama.Ollama` e depois `ollama pull qwen3:4b`.
- `clipador momentos`: lista os momentos da gravação mais recente, da maior nota para a
  menor, com o trecho e o motivo ("voz alta (+24 dB) + pentakill seu + “não acredito”").
- `clipador eventos`: mostra os eventos do LoL da gravação mais recente, com o segundo de
  cada um no vídeo (`--gravacao N` escolhe outra; `--demo` mostra os da demo).
- `clipador coletar-lol`: liga só o coletor, sem o serviço, para testar numa partida.
- `clipador refazer <número>`: depois de corrigir a causa de um erro (por exemplo, o
  `[faixas]` errado), devolve a tarefa para a fila. O número é o `#` do `clipador status`.
- `clipador instalar-servico`: faz o serviço abrir sozinho, sem janela, quando você entra
  no Windows (`clipador remover-servico` desfaz). Não precisa de administrador
  ([ADR 0004](docs/adr/0004-abrir-no-login-pela-chave-run.md)).
- `clipador demo`: cria a live falsa da demo (veja acima).
- `clipador backup`: faz uma cópia do banco agora. O app guarda as 14 mais recentes.
  Para restaurar uma cópia, veja [como restaurar um backup](#como-restaurar-um-backup).

Por baixo, o app já tem o banco (SQLite, em modo WAL) e uma **fila retomável**: se o PC
desligar no meio do processamento, as tarefas voltam para a fila na próxima abertura, e uma
tarefa que falha é tentada até 3 vezes antes de virar erro.

## Como rodar

Requisitos: Windows 10+, [uv](https://docs.astral.sh/uv/), Git e FFmpeg
(`winget install Gyan.FFmpeg`). O uv instala o Python 3.12 se precisar. Para transcrever
na placa de vídeo: uma NVIDIA com driver 570 ou mais novo (as bibliotecas do CUDA 12, ~1 GB,
vêm com o `uv sync`); sem ela, a transcrição usa o processador, bem mais devagar. Para os
títulos com IA (opcional): o [Ollama](https://ollama.com) com o modelo `qwen3:4b`. Num PC
sem placa NVIDIA, `uv sync --no-group gpu` pula esse download.

No PowerShell:

```powershell
git clone https://github.com/Fermontelos/Clipador.git
cd Clipador
uv sync
Copy-Item config.example.toml config.toml
uv run clipador config
```

Edite o `config.toml` para apontar `pasta_gravacoes` para a pasta onde o OBS salva as lives.
Se as faixas de áudio do seu OBS não seguirem o padrão (1 = mistura, 2 = voz, 3 = jogo,
4 = música), ajuste a seção `[faixas]`.
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

Depois de mudar uma tabela em `backend/src/clipador/banco/modelos.py`, gere a migração do banco:

```bash
uv run alembic revision --autogenerate -m "o que mudou"
```

A CI (GitHub Actions) roda lint, formatação e testes em Windows a cada push.
Nenhum teste pode passar de 5 minutos (`pytest-timeout`): um teste travado falha mostrando
onde parou. Os testes nunca mexem na abertura no login de verdade: usam uma chave de teste
do registro, apagada no fim.

## Estrutura

O projeto é dividido em quatro partes, com uma direção de dependência só
([ADR 0003](docs/adr/0003-backend-api-integracoes-frontend.md)):

```
frontend ──(HTTP)──> api ──> backend ──> integracoes ──> YouTube / TikTok / LoL
```

```
backend/        pipeline, banco, fila, serviço, demo e a linha de comando (pacote clipador)
  src/clipador/
    banco/      tabelas, fila, backup e migrações (Alembic)
    ingestao/   monitor da pasta do OBS, horário de início e extração do áudio da voz
    transcricao/ Whisper na placa de vídeo, transcrição em blocos e legenda .srt
    jogo/       coletor do LoL, eventos seus e alinhamento com o tempo da gravação
    detectores/ detectores de momentos (plugins) e a nota de cada momento
    cortes/     clipes horizontal e vertical, legenda do clipe e thumbnail
    titulos/    títulos e hashtags com a IA local (Ollama) e o plano B sem IA
    sistema/    serviço em segundo plano, energia, trava e abertura no login
    demo/       roteiro e gerador da live falsa
    ffmpeg.py   chamadas ao FFmpeg e ao ffprobe
  tests/
api/            API local da revisão, FastAPI (etapa 12)
integracoes/    YouTube, TikTok e LoL (etapas 7, 15-17): único código que lê as chaves
frontend/       interface de revisão, React (etapa 13): nunca guarda chaves
tests/          testes de arquitetura (as regras entre as partes)
docs/           roteiro em PDF e decisões técnicas (docs/adr/)
dados/          banco, backups, logs, clipes, temporários e a demo (fora do git, criado pelo app)
```

As três partes Python formam um workspace do uv: cada uma tem o próprio `pyproject.toml`,
mas todas são instaladas juntas por `uv sync` e testadas juntas por `uv run pytest`, sempre
a partir da raiz. As regras de segurança (por exemplo, a API nunca importar as integrações)
são conferidas automaticamente em `tests/test_arquitetura.py`.

## Decisões técnicas

- [ADR 0001: Python + SQLite + FastAPI](docs/adr/0001-python-sqlite-fastapi.md)
- [ADR 0002: SQLite e não PostgreSQL](docs/adr/0002-sqlite-e-nao-postgres.md)
- [ADR 0003: backend, API, integrações e frontend separados](docs/adr/0003-backend-api-integracoes-frontend.md)
- [ADR 0004: abrir no login pela chave "Run"](docs/adr/0004-abrir-no-login-pela-chave-run.md)
- [ADR 0005: detectores devolvem sinais, e a nota monta os momentos](docs/adr/0005-detectores-devolvem-sinais.md)
- [ADR 0006: cortar no processador quando o NVENC não funcionar](docs/adr/0006-cortes-no-processador-sem-nvenc.md)

## Licença

[MIT](LICENSE)
