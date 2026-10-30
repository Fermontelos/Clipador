# Guia de uso

Tudo o que o Clipador faz, comando por comando. Para começar, veja o
[README](../README.md) (a demo e como instalar); para ligar o app aos seus canais, o guia
de [configurar as contas](configurar-contas.md).

Todos os comandos rodam a partir da pasta do projeto, com `uv run` na frente (ex.:
`uv run clipador status`). Quase todos aceitam `--demo`, para agir sobre a demo em vez dos
seus dados, e `--arquivo` para outro `config.toml`. `uv run clipador --help` lista todos.

## Os comandos e o que cada parte faz

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
  legenda da sua fala queimada ([ADR 0008](adr/0008-vertical-b-camera-e-jogo.md)). O áudio junta só a voz e o jogo,
  **sem a música** (direitos autorais), normalizado para -14 LUFS, o padrão de YouTube e
  TikTok. O horizontal ganha uma thumbnail do pico do momento. Os cortes são feitos no
  processador quando o encoder da placa (NVENC) não funciona ([ADR 0006](adr/0006-cortes-no-processador-sem-nvenc.md)).
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
- `clipador web`: abre a revisão no navegador, em quatro telas:
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
    cada um), a cota do YouTube do dia e quantas pendentes estão só marcadas para depois
    (coletas de métricas, envios esperando a hora ou a cota).
  - **Estatísticas**: a precisão do detector, aprovações por faixa de nota e por dia,
    a força média de cada sinal nos aprovados e nos recusados, e o **aprendizado**
    (veja abaixo), com o botão de aceitar.
  - **Engajamento**: como os cortes publicados foram com o público (veja abaixo), e a
    sugestão de pesos pelo engajamento, com o botão de aceitar.

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
    janela e o aceite (com o número de decisões que você viu: se mudou, é recusado);
  - `GET /api/engajamento` e `POST /api/engajamento/aceitar`: os rankings do engajamento,
    a sugestão de pesos pelo público e o aceite (com o número de cortes que você viu).

  Os destinos padrão de cada formato ficam em `[revisao]` no `config.toml`. A API só aceita
  pedidos do próprio PC ([ADR 0007](adr/0007-api-local-sem-senha.md)). Nada é
  publicado daqui: a publicação (etapas 15 e 16, abaixo) usa só o que você aprovou.
- `clipador momentos`: lista os momentos da gravação mais recente, da maior nota para a
  menor, com o trecho e o motivo ("voz alta (+24 dB) + pentakill seu + “não acredito”").
- `clipador eventos`: mostra os eventos do LoL da gravação mais recente, com o segundo de
  cada um no vídeo (`--gravacao N` escolhe outra; `--demo` mostra os da demo).
- `clipador coletar-lol`: liga só o coletor, sem o serviço, para testar numa partida.
- `clipador refazer <número>`: depois de corrigir a causa de um erro (por exemplo, o
  `[faixas]` errado), devolve a tarefa para a fila. O número é o `#` do `clipador status`.
- `clipador instalar-servico`: faz o serviço abrir sozinho, sem janela, quando você entra
  no Windows (`clipador remover-servico` desfaz). Não precisa de administrador
  ([ADR 0004](adr/0004-abrir-no-login-pela-chave-run.md)).
- Aprendizado com as suas decisões ([ADR 0009](adr/0009-regressao-logistica.md)):
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
- Publicação no YouTube ([ADR 0010](adr/0010-publicacao-agendada-com-tempo-para-desfazer.md)):
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
  [publicar no YouTube](configurar-contas.md#youtube)).
- Publicação no TikTok ([ADR 0011](adr/0011-tiktok-pasta-e-rascunho.md)): só os
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
  métricas (uma vez; veja [publicar no TikTok](configurar-contas.md#tiktok)).
- Engajamento ([ADR 0012](adr/0012-engajamento-ensina-a-nota.md)): o app coleta as
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
- `clipador demo`: cria a live falsa da demo (veja a [demo no README](../README.md#demo-em-3-comandos)).
- `clipador backup`: faz uma cópia do banco agora. O app guarda as 14 mais recentes.
  Para restaurar uma cópia, veja [como restaurar um backup](#como-restaurar-um-backup).

Por baixo, o app já tem o banco (SQLite, em modo WAL) e uma **fila retomável**: se o PC
desligar no meio do processamento, as tarefas voltam para a fila na próxima abertura, e uma
tarefa que falha é tentada até 3 vezes antes de virar erro.

## Como restaurar um backup

Com o app fechado:

1. Renomeie o `dados/clipador.db` atual (ex.: para `clipador-quebrado.db`).
2. Apague `dados/clipador.db-wal` e `dados/clipador.db-shm`, se existirem. Eles pertencem ao
   banco antigo e podem corromper o banco restaurado.
3. Copie o backup escolhido (em `dados/backups/`, ou na pasta de `pasta_backups`) para
   `dados/clipador.db`.
4. Rode `uv run clipador status` para conferir.
