# Clipador de lives

> Nome provisório.

App em Python que transforma gravações de live (OBS) em clipes para YouTube e TikTok.
O processamento roda de madrugada, a revisão acontece no dia seguinte numa interface web
local e **nada é publicado sem aprovação**. Com o tempo, o app aprende com as aprovações e
com o engajamento do público quais momentos viram bons cortes.

Projeto pessoal e de portfólio, feito só com ferramentas e APIs gratuitas.

![A revisão dos clipes, feita só pelo teclado](docs/imagens/revisao.gif)

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
| 9 · Cortes | Gera os clipes horizontais e verticais | ✅ |
| 10 · Títulos com IA | Sugere 3 títulos e as hashtags de cada clipe | ✅ |
| 11 · Desligar o PC | Desliga sozinho depois de processar | ✅ (em modo de teste) |
| 12 · API da revisão | Serve os clipes e grava as suas decisões | ✅ |
| 13 · Interface da revisão | Revisar os clipes pelo teclado, no navegador | ✅ |
| 14 · Aprendizado | Ajustar a nota com as suas aprovações | ⏳ próxima |

## Experimente em 2 minutos (modo demo)

Não precisa de placa de vídeo, de contas no YouTube ou no TikTok, nem de configurar nada.
Só do Git, do [uv](https://docs.astral.sh/uv/) e do FFmpeg (`winget install Gyan.FFmpeg`).
No PowerShell:

```powershell
git clone https://github.com/Fermontelos/Clipador.git
cd Clipador
uv run clipador demo           # cria uma live falsa de 3 min em dados/demo/ (~15 s)
uv run clipador status --demo  # mostra a fila da demo
uv run clipador processar --demo  # áudio, legenda, momentos, 12 clipes e títulos (~2 min)
uv run clipador eventos --demo    # os eventos do LoL da demo, no segundo certo do vídeo
uv run clipador momentos --demo   # os 4 melhores momentos, com nota, motivo e título
uv run clipador web --demo        # revisa a demo no navegador (veja abaixo)
# os clipes da demo ficam em dados/demo/clipes/ (horizontal, vertical e thumbnail)
```

A live falsa tem as mesmas 4 faixas de áudio do OBS (mistura, voz, jogo e música), quatro
picos de voz em horários conhecidos (0:25, 1:10, 1:58 e 2:30), eventos de uma partida de LoL,
uma transcrição e títulos prontos. O vídeo mostra o tempo da gravação no centro da tela
e uma "câmera" falsa no canto de cima, já configurada, para o vertical B mostrar o layout
câmera + jogo (uma demo criada antes dela só a ganha com `clipador demo --do-zero`).
Rodar de novo não duplica nada; `--do-zero` apaga e recria. A demo usa uma pasta e um banco
separados: os seus dados reais não são tocados.

A interface de revisão precisa ser construída uma vez (com o
[Node.js](https://nodejs.org) 22.12 ou mais novo: `winget install OpenJS.NodeJS.LTS`); sem ela, o
`clipador web` abre a documentação interativa da API, que também permite revisar:

```powershell
cd frontend; npm ci; npm run build; cd ..
```

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
  a live, a fila fica pausada para não pesar no PC, e enquanto processa (ou espera para
  desligar, etapa 11) o PC não suspende.
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
  `[cortes] formatos`: horizontal (16:9), vertical A (9:16, com o vídeo no centro e o
  fundo desfocado) e vertical B (9:16, a sua câmera no terço de cima e o centro do jogo
  embaixo; sem câmera configurada, só o centro do jogo, ampliado). Os verticais têm a
  legenda da sua fala queimada ([ADR 0008](docs/adr/0008-vertical-b-camera-e-jogo.md)). O áudio junta só a voz e o jogo,
  **sem a música** (direitos autorais), normalizado para -14 LUFS, o padrão de YouTube e
  TikTok. O horizontal ganha uma thumbnail do pico do momento. Os cortes são feitos no
  processador quando o encoder da placa (NVENC) não funciona ([ADR 0006](docs/adr/0006-cortes-no-processador-sem-nvenc.md)).
- Títulos e hashtags com IA local: para cada momento, o [Ollama](https://ollama.com) com o
  modelo `qwen3:4b` sugere 3 títulos no seu estilo (frase sua, pergunta, exagero ou
  descrição) e as hashtags, a partir do que você falou e do que aconteceu no jogo. Os
  títulos que você escolher na revisão viram exemplos para os próximos. Roda no próprio PC,
  sem internet e sem custo. Sem o Ollama, o título vira a frase mais marcante do momento.
  Para instalar: `winget install Ollama.Ollama` e depois `ollama pull qwen3:4b`.
- Desligamento automático: quando o serviço da noite termina a fila e você está há 10 min
  sem mexer no teclado e no mouse, o PC desliga, depois de uma contagem de 60 s que o
  Windows mostra na tela (mexer no mouse cancela; `shutdown /a` também). Programas abertos
  não impedem; uma gravação em andamento conta como fila; tarefas agendadas para dias
  depois (como as métricas) não seguram o PC ligado. Só o serviço desliga: um
  `clipador processar` rodado à mão, não. E só depois de processar uma gravação: um novo
  corte pedido na revisão de manhã não faz o PC desligar quando você sai de perto. Vem em
  **modo de teste**, que só escreve
  "desligaria agora" no log: confira uma noite inteira e então mude `modo_teste = false`
  em `[desligamento]` no `config.toml`. O `clipador status` mostra há quanto tempo o PC
  está ocioso. Se você estiver usando o PC quando a fila acabar, o app espera você sair de
  perto por até 4 h (`espera_maxima_horas`) e depois desiste até a próxima noite.
- `clipador web`: abre a revisão no navegador, em três telas:
  - **Revisão**: o clipe toca repetindo o trecho do momento, com a nota, o motivo, os 3
    títulos sugeridos, as hashtags e os formatos e destinos. Tudo pelo teclado:

    | Tecla | O que faz |
    |---|---|
    | `A` / `R` | aprova / recusa (e vai para o próximo pendente) |
    | `U` | desfaz a última decisão (de novo: a penúltima...) |
    | `←` `→` / `Espaço` | momento anterior, próximo / pausa |
    | `1` `2` `3` | troca o formato exibido (horizontal, vertical) |
    | `Shift` + `1` `2` `3` | tira ou põe o formato na aprovação |
    | `T` / `Tab` | edita o título / troca entre as 3 sugestões |
    | `H` | edita as hashtags |
    | `[` `]` / `Shift` + `[` `]` | início / fim do corte, 1 s para trás ou para frente |
    | `M` / `?` | ver mais (além do limite do dia) / ajuda |

    Enquanto você digita no título ou nas hashtags, os atalhos ficam desligados (`Esc`
    ou `Enter` sai do campo). O 1º clipe espera uma tecla: o navegador só toca vídeo
    com som depois de você interagir com a página. O corte também se ajusta arrastando
    as alças da linha do tempo, entre 15 e 60 s (`[deteccao] duracao_minima_s` e
    `duracao_maxima_s`); o player mostra na hora o trecho novo, e ao aprovar o app
    corta de novo só esse clipe. Encurtar aparece na hora; a parte que passa do corte
    atual só aparece depois do novo corte (a tela avisa).
  - **Fila e status**: o que está processando, os erros (com o `clipador refazer` de
    cada um) e a cota do YouTube do dia.
  - **Estatísticas**: a precisão do detector, aprovações por faixa de nota e por dia,
    e a força média de cada sinal nos aprovados e nos recusados.

  Por baixo, a interface usa a API (documentada e testável em `/docs`):
  - `GET /api/momentos`: os momentos do dia da live mais recente, da maior nota para a
    menor, até o `limite_diario` (15); `ver_mais=true` traz o resto, e `dia=AAAA-MM-DD`
    escolhe outro dia (`GET /api/dias` lista os dias e quantos faltam revisar);
  - `POST /api/momentos/{id}/decisao`: aprovar (formatos, destinos, título, hashtags) ou
    recusar;
  - `DELETE` na mesma rota: desfazer;
  - `POST /api/momentos/{id}/ajuste`: novo início e fim; o novo corte só desse clipe entra
    na fila e sai quando ela rodar (o serviço, ou `clipador processar`);
  - `GET /api/clipes/{id}/video`: o vídeo, com avanço no player;
  - `GET /api/status`, `GET /api/estatisticas` e `GET /api/config`: fila, erros, cota do
    YouTube, precisão e os limites do corte.

  Os destinos padrão de cada formato ficam em `[revisao]` no `config.toml`. A API só aceita
  pedidos do próprio PC ([ADR 0007](docs/adr/0007-api-local-sem-senha.md)). Nada é
  publicado aqui: a publicação (etapa 15) vai usar só o que você aprovou.
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
- `clipador calibrar`: salva um quadro da live mais recente em `dados/calibracao.png`,
  com uma grade a cada 100 px e as regiões do vertical B desenhadas (verde: o recorte do
  jogo; vermelho: a câmera). É de onde saem os números de `[cortes.vertical_b] camera`
  (`--segundo` escolhe o momento da live; `--demo` usa a demo).
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
sem placa NVIDIA, `uv sync --no-group gpu` pula esse download. Para construir a interface
de revisão (uma vez): o [Node.js](https://nodejs.org) 22.12 ou mais novo
(`winget install OpenJS.NodeJS.LTS`) e `cd frontend; npm ci; npm run build`.

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
uv run pre-commit install  # a cada commit: lint, formatação, testes rápidos e, se o
                           # frontend mudou, os tipos e os testes do Vitest
```

A interface (em `frontend/`, React + Vite + TypeScript + Tailwind):

```bash
npm run dev        # em frontend/, com o "clipador web" aberto: recarrega a cada mudança
npm test           # testes do Vitest (atalhos, desfazer, contador, ajuste do corte)
npm run build      # gera frontend/dist, que o "clipador web" serve
```

No `npm run dev`, o Vite (em `127.0.0.1:5173`) repassa `/api` para o `clipador web`
(porta 8765; outra: `CLIPADOR_API=http://127.0.0.1:PORTA`).

Depois de mudar uma tabela em `backend/src/clipador/banco/modelos.py`, gere a migração do banco:

```bash
uv run alembic revision --autogenerate -m "o que mudou"
```

A CI (GitHub Actions) roda lint, formatação e testes em Windows a cada push, e os tipos,
os testes e o build da interface em Linux.
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
    revisao/    regras da revisão: momentos do dia, decisões, ajuste do corte e cota
    ffmpeg.py   chamadas ao FFmpeg e ao ffprobe
  tests/
api/            API local da revisão, FastAPI (etapa 12) e o comando clipador web
integracoes/    YouTube, TikTok e LoL (etapas 7, 15-17): único código que lê as chaves
frontend/       interface de revisão, React (etapa 13): nunca guarda chaves
  src/revisao/  a tela de revisão: atalhos, ajuste do corte e estado (funções puras)
  src/telas/    fila e status, estatísticas (Recharts)
tests/          testes de arquitetura (as regras entre as partes)
conftest.py     proteções de todos os testes: nenhum abre o Whisper ou o Ollama de verdade,
                mexe na abertura no login ou desliga o PC
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
- [ADR 0007: API da revisão local, sem senha](docs/adr/0007-api-local-sem-senha.md)
- [ADR 0008: vertical B com a câmera em cima e o jogo embaixo](docs/adr/0008-vertical-b-camera-e-jogo.md)

## Licença

[MIT](LICENSE)
