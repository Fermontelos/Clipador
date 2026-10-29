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
| 14 · Aprendizado | Ajustar a nota com as suas aprovações | ✅ (falta o gráfico de uma live real) |
| 15 · YouTube | Publicar os clipes aprovados, com agendamento | 🟡 pronto e testado com o YouTube simulado; falta o 1º envio real |
| 16 · TikTok | Publicar no TikTok (pasta, rascunho e post) | 🟡 pasta pronta; rascunho pronto e testado com o TikTok simulado, falta o teste no sandbox; post direto fora (ADR 0011) |
| 17 · Métricas | Engajamento dos cortes publicados ensina a nota | 🟡 pronto e testado com YouTube e TikTok simulados (e na demo); faltam métricas reais |
| 18 · Vitrine | README final, vídeo, cobertura e release v1.0 | ⏳ próxima |

## Experimente em uns 3 minutos (modo demo)

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
# os clipes da demo ficam em dados/demo/clipes/ (horizontal, verticais A e B, thumbnail)
# os aprovados para o TikTok vão para dados/demo/exportar/tiktok/ (vídeo + legenda .txt)
```

A live falsa tem as mesmas 4 faixas de áudio do OBS (mistura, voz, jogo e música), quatro
picos de voz em horários conhecidos (0:25, 1:10, 1:58 e 2:30), eventos de uma partida de LoL,
uma transcrição e títulos prontos. O vídeo mostra o tempo da gravação no centro da tela
e uma "câmera" falsa no canto de cima, já configurada, para o vertical B mostrar o layout
câmera + jogo (uma demo criada antes dela só a ganha com `clipador demo --do-zero`).
A demo também traz 6 lives antigas já revisadas (120 decisões, só no banco, sem vídeo),
para o aprendizado ter o que mostrar: `uv run clipador aprender --demo`.
Cada aprovado dessas lives foi "publicado" (de mentira) no YouTube e no TikTok, com as métricas
de 7 dias, para a tela de Engajamento e `uv run clipador engajamento --demo` mostrarem o
público preferindo os eventos do LoL (uma demo criada antes disso só os ganha com
`--do-zero`).
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
- `clipador config`: confere se o `config.toml` é válido e mostra os valores carregados
  (e avisa quando uma versão aceita no aprendizado vale no lugar dos pesos e da janela).
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
  sinais; pesos em `[deteccao]` no `config.toml`, ou os da versão aceita no aprendizado,
  veja abaixo). Ficam os 30 melhores.
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
    | `1` `2` `3` | troca o formato exibido (horizontal, vertical A, vertical B) |
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
    a força média de cada sinal nos aprovados e nos recusados, e o **aprendizado**
    (veja abaixo), com o botão de aceitar.

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
    YouTube, precisão e os limites do corte;
  - `GET /api/aprendizado` e `POST /api/aprendizado/aceitar`: a sugestão de pesos e de
    janela e o aceite (com o número de decisões que você viu: se mudou, é recusado).

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
- Aprendizado com as suas decisões ([ADR 0009](docs/adr/0009-regressao-logistica.md)):
  `clipador aprender` (ou o painel na tela de Estatísticas) mostra a precisão por dia e, com
  100 decisões ou mais, os pesos que as suas aprovações sugerem para a nota. Eles saem de
  uma regressão logística: as entradas são as forças dos sinais de cada momento, e a saída
  é "aprovou?". Para a comparação ser justa, a precisão "antes e depois" é medida com
  validação cruzada, em decisões que o modelo não viu no treino: dos momentos de maior nota
  de cada dia (a metade de cima), quantos você aprovaria com os pesos atuais e com os
  sugeridos. A média dos seus ajustes de corte também sugere uma nova janela (ex.: você
  sempre adianta o início em 3 s, então os próximos cortes começam 3 s antes), a partir de
  20 aprovações. Nada muda sozinho: você aceita, e a versão aceita vale a partir da próxima
  detecção (os momentos já detectados ficam como estão). **Limitação**: só os momentos que
  você revisou têm decisão, e quase todos são os de maior nota do dia; o modelo aprende
  entre os que o detector já achou bons, não sobre a live inteira.
- Publicação no YouTube ([ADR 0010](docs/adr/0010-publicacao-agendada-com-tempo-para-desfazer.md)):
  aprovar um momento com destino YouTube põe na fila um envio para cada formato (o
  horizontal sobe como vídeo normal; o vertical de até 3 min, como Short). O envio espera 10 min
  (`minutos_para_desfazer`): até lá, desfazer (`U`) cancela sem nada sair do PC. O vídeo
  sobe **privado, com data de publicação**, e o YouTube o publica sozinho no próximo dos
  `[youtube] horarios` (12h, 18h e 21h), com 2 h entre uma publicação e outra. Vai com o
  título que você aprovou, as hashtags na descrição (modelo em `[youtube] descricao`), as
  tags do config, a categoria Jogos e a declaração "não é conteúdo para crianças"; o
  horizontal leva a thumbnail do pico. A cota do dia (100 envios e 10.000 unidades,
  renovada à meia-noite no Pacífico, 4h–5h em Brasília) é conferida antes de cada envio:
  sem cota, a fila espera ela renovar. Durante uma live, o envio espera (para não disputar a
  internet com a transmissão), e um clipe com um novo corte na fila espera o corte. Na
  revisão, cada momento mostra as publicações: na fila, enviando, enviada (com o link) ou
  com erro (`clipador refazer` tenta de novo).
- `clipador youtube-entrar`: liga o app ao seu canal (uma vez; veja
  [publicar no YouTube](#publicar-no-youtube-configuração-uma-vez)).
- Publicação no TikTok ([ADR 0011](docs/adr/0011-tiktok-pasta-e-rascunho.md)): só os
  verticais (o TikTok é vertical). Depois dos mesmos 10 min para desfazer, o vídeo e um
  `.txt` com a legenda (título + hashtags, modelo em `[tiktok] legenda`) vão para
  `dados/exportar/tiktok/` (ou a `[tiktok] pasta`; uma pasta sincronizada com o celular
  evita copiar à mão), e você posta pelo celular. Com `[tiktok] modo = "rascunho"`, o
  vídeo também vai para a **caixa de entrada do app do TikTok**: chega uma notificação, e
  você finaliza lá (o rascunho chega sem legenda: ela está no `.txt`). O TikTok aceita no
  máximo 5 rascunhos pendentes a cada 24 h; o sexto espera na fila (já com o vídeo na
  pasta; desfazer enquanto ele espera também o tira da pasta). O post direto, sem
  passar pelo celular, não foi feito: sem a auditoria do TikTok os posts ficam privados, e
  as diretrizes dele não aceitam na auditoria uma ferramenta para as suas próprias contas.
- `clipador tiktok-entrar`: liga o app à sua conta do TikTok, para os rascunhos e as
  métricas (uma vez; veja [publicar no TikTok](#publicar-no-tiktok-configuração-uma-vez)).
- Engajamento ([ADR 0012](docs/adr/0012-engajamento-ensina-a-nota.md)): o app coleta as
  métricas de cada corte publicado **24 h, 72 h e 7 dias** depois de ele ir ao ar
  (visualizações, curtidas, comentários e compartilhamentos; no YouTube, também a retenção,
  pelo YouTube Analytics). No TikTok, ele acha entre os seus vídeos públicos o que você
  postou, pelo horário e pelo título. Com a coleta de 7 dias, cada corte ganha um **índice
  de engajamento** (1 = o vídeo típico daquela rede no seu canal; no YouTube, Shorts e
  vídeos normais têm medianas separadas), e a tela **Engajamento**
  mostra os cortes que mais engajaram, as hashtags e os tipos de título que funcionam (a
  partir de 5 vídeos), o engajamento por tipo de momento e YouTube × TikTok. Com **50 cortes**,
  o app sugere pesos para a nota que preveem o engajamento, misturados meio a meio com o seu
  gosto (`[engajamento] mistura`), com a comparação antes e depois em cortes que o treino não
  viu; nada muda até você aceitar. As sugestões de título e hashtag (etapa 10) passam a vir
  na ordem do que engaja, testando de vez em quando (10%) uma ainda pouco usada.
  Limitação: engajamento também depende de horário, tendência e algoritmo; com poucos
  vídeos, os números erram bastante.
- `clipador engajamento [--aceitar] [--demo]`: o mesmo no terminal.
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

## Publicar no YouTube (configuração, uma vez)

O app só envia com uma credencial sua no Google Cloud (gratuita). Nada disso vai para o
git: a credencial e o login ficam em `segredos/`, lidos só pelo pacote das integrações.

1. No [Google Cloud Console](https://console.cloud.google.com/): crie um projeto e, na
   Biblioteca de APIs, ative a **YouTube Data API v3**.
2. No **Google Auth Platform** (o antigo "Tela de consentimento OAuth"): preencha o nome do
   app e o seu e-mail; em **Público-alvo**, escolha o tipo **Externo** e **publique o app
   (status "Em produção")**. Em "Teste", o Google cancela o login a cada 7 dias. Em
   **Acesso a dados**, adicione a permissão `youtube.upload`.
3. Em **Clientes**: **Criar cliente**, tipo **App para computador**, e baixe o JSON. Salve
   como `segredos/client_secret.json` (na pasta do `config.toml`). O login volta para o
   próprio PC (`http://127.0.0.1` numa porta livre), e a biblioteca usa PKCE, como o
   Google recomenda para apps de computador.
4. Para as métricas (etapa 17): na Biblioteca de APIs, ative também a **YouTube Analytics
   API**, e em **Acesso a dados** adicione `youtube.readonly` e `yt-analytics.readonly` (só
   leitura; o Analytics exige as duas).
5. Rode `uv run clipador youtube-entrar`: o navegador abre, você entra na conta do canal e
   autoriza (marque todas as permissões). O Google avisa que o app "não foi verificado":
   para uso pessoal, continue em "Avançado". O login fica salvo e se renova sozinho. Um
   login feito antes da etapa 17 continua enviando; para as métricas, entre de novo.
6. Confira num envio de teste: aprove um momento só com o YouTube e veja o vídeo aparecer
   como agendado no YouTube Studio (gasta 1 dos 100 envios do dia).

Quer ver o fluxo sem enviar nada? `[youtube] modo = "simulado"` finge os envios (a demo
já vem assim).

## Publicar no TikTok (configuração, uma vez)

O modo padrão (`[tiktok] modo = "pasta"`) não precisa de nada: os verticais aprovados vão
para a pasta de exportação. Para mandar rascunhos direto para o app do TikTok:

1. Crie uma conta no [TikTok for Developers](https://developers.tiktok.com/) e, em
   **Manage apps**, um app. Adicione os produtos **Login Kit**, **Content Posting API** (com
   a permissão `video.upload`, para o rascunho) e **Display API** (com a `video.list`, para as
   métricas da etapa 17; ela só lê os seus vídeos públicos).
2. No Login Kit, cadastre o endereço de retorno de app de computador
   `http://127.0.0.1:*/callback/` (o `*` deixa o app usar uma porta livre; o TikTok só
   aceita `localhost` ou `127.0.0.1`).
3. Mude o app para **Sandbox**, crie um sandbox e adicione a sua conta do TikTok em
   **Target users**. No sandbox, tudo funciona só para essas contas, sem análise do TikTok.
4. Copie o **Client key** e o **Client secret** para o `.env` (veja o `.env.example`), na
   pasta do `config.toml`. O `.env` fica fora do git e só o pacote das integrações o lê.
5. No `config.toml`: `[tiktok] modo = "rascunho"` (só para os rascunhos; as métricas valem
   também no modo pasta). Rode `uv run clipador tiktok-entrar`: ele pede só as permissões que
   o seu config usa, o navegador abre, você entra na conta e autoriza. O login vale 365 dias (o app avisa no
   log quando faltar um mês) e o acesso se renova sozinho.

Fora do sandbox (para qualquer conta), o TikTok precisa **aprovar o app numa análise**: ele
pede ícone, descrição e páginas de Termos de Uso e Política de Privacidade. Esse pedido não
é feito pelo app.

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
    aprendizado/ precisão, pesos da nota (regressão logística, pelo gosto e pelo
                engajamento) e janela do corte
    publicacao/ fila, agenda, pedido, cota e envio ao YouTube e ao TikTok
    engajamento/ coleta das métricas, índice, rankings de hashtags e títulos
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
- [ADR 0009: regressão logística para aprender os pesos da nota](docs/adr/0009-regressao-logistica.md)
- [ADR 0010: publicação agendada no YouTube, com tempo para desfazer](docs/adr/0010-publicacao-agendada-com-tempo-para-desfazer.md)
- [ADR 0011: TikTok pela pasta e por rascunho, post direto só após a auditoria](docs/adr/0011-tiktok-pasta-e-rascunho.md)
- [ADR 0012: o engajamento ensina a nota, misturado com o seu gosto](docs/adr/0012-engajamento-ensina-a-nota.md)

## Licença

[MIT](LICENSE)
