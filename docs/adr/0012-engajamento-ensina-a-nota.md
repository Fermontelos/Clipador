# ADR 0012: O engajamento ensina a nota, misturado com o seu gosto

- **Status:** aceita
- **Data:** 2026-10-29

## Contexto

A etapa 14 ensina a nota com as suas aprovações: ela aprende o que **você** gosta. O objetivo
do projeto é gerar engajamento, e o público pode gostar de outra coisa. A etapa 17 mede como
cada corte publicado foi (YouTube e TikTok) e usa isso. Perguntas:

1. Quando e como medir, sem depender do PC ligado na hora certa?
2. Como comparar vídeos de redes e de épocas diferentes?
3. Como o engajamento entra na nota sem apagar o seu gosto?
4. Como usar o que funciona (hashtags, tipos de título) sem parar de testar o novo?

## Decisão

- **Uma tarefa por publicação, que se reagenda.** O envio agenda uma tarefa
  `coletar_metricas` para 24 h depois de o vídeo ir ao ar; cada execução grava a coleta que
  venceu e volta para a fila marcada para a próxima (`fila.TentarDepois`), até a de 7 dias.
  Uma coleta atrasada (PC desligado) **não** vira a anterior com números de depois: vale a
  mais recente que venceu, e a outra fica sem dado.
- **TikTok: achar o vídeo.** No rascunho ou na pasta, você posta pelo celular e o app não
  recebe o ID. Ele procura, entre os seus vídeos públicos, um postado depois do envio cuja
  legenda contém o título aprovado (sem acentos, maiúsculas, hashtags nem pontuação), e liga
  o mais antigo que ainda não está ligado a outra publicação. Sem achar, tenta a cada 12 h por
  14 dias. Ligado, um rascunho deixa de contar no limite de 5 pendentes.
- **Índice normalizado pela mediana de cada grupo.** O grupo é a rede, e no YouTube os
  Shorts ficam à parte dos vídeos horizontais (um Short costuma ter muito mais
  visualizações). Alcance (visualizações), interação
  ((curtidas + comentários + compartilhamentos) ÷ visualizações) e retenção, cada um dividido
  pela mediana do grupo no canal e limitado a 10, numa média com pesos do config. Só a
  coleta de 7 dias entra. A resposta do treino é "acima da mediana do índice da rede".
- **Mistura, e não troca.** O treino é a regressão logística da etapa 14 (ADR 0009) com essa
  resposta; os pesos aprendidos entram na nota como
  `(1 − mistura) × gosto + mistura × engajamento` (`mistura` = 0,5). Só com 50 cortes com 7
  dias; aceite manual, com a mesma regra de "a sugestão que você viu" e a versão gravada em
  `PesosNota` com `origem = "engajamento"`.
- **As duas partes guardadas separadas.** Cada versão de `PesosNota` guarda o peso do gosto
  (`pesos_do_gosto`), o do engajamento (`pesos_do_engajamento`) e a mistura (`valores`, o que
  a detecção usa). Aceitar uma sugestão do gosto (etapa 14) troca só o gosto e mistura de novo
  com o engajamento que já valia; aceitar a do engajamento parte do gosto da versão atual, e
  não da mistura. Sem isso, um aceite do gosto apagaria o engajamento, e dois do engajamento
  misturariam o que já estava misturado (0,5 viraria 0,75). As duas validações cruzadas medem
  a mistura, que é o que de fato vale.
- **Epsilon-greedy nas sugestões.** Hashtags e tipos de título ganham um efeito (índice médio
  com − sem), confiável a partir de 5 vídeos. As sugestões da etapa 10 são reordenadas: na
  maior parte das vezes, o que funciona primeiro; com chance de 10%, um ainda pouco testado.
  Sempre entre as hashtags que a IA sugeriu para o clipe (hashtag sem relação com o vídeo pode
  fazer o YouTube removê-lo).
- **Permissões só de leitura**, pedidas num novo login: `youtube.readonly` e
  `yt-analytics.readonly` (o `reports.query` do Analytics exige as duas) e, no TikTok,
  `video.list` (Display API). Um login antigo continua enviando; cada ação confere a
  permissão dela.

## Alternativas consideradas

- **Uma tarefa por coleta (três tarefas).** Mais simples de ler, mas o TikTok só sabe a hora
  de ir ao ar depois de achar o vídeo: as três teriam de ser remarcadas juntas.
- **Visualizações cruas.** Um canal que cresce faria todo vídeo novo parecer melhor, e o
  TikTok (público maior) engoliria o YouTube.
- **Treinar só pelo engajamento.** A nota passaria a sugerir o que viraliza e você nunca
  aprovaria; a revisão viraria recusar tudo.
- **Sempre a melhor hashtag (só aproveitar).** Uma hashtag nova nunca teria chance de mostrar
  que é boa: o ranking congelaria nas primeiras.
- **Webhooks do TikTok** (`post.publish.complete`) em vez de procurar o vídeo: exigem um
  servidor público na internet; o app roda num PC sem um.

## Consequências

- Engajamento também depende de horário, tendência e algoritmo: com poucos vídeos, o modelo
  erra bastante. A precisão "antes e depois" é medida em cortes que o treino não viu, e serve
  para comparar, não como garantia.
- Hashtag e tipo de título são correlação, não causa (a `#pentakill` vai bem porque o momento
  de LoL vai bem). Por isso eles só mudam a ordem das sugestões.
- No TikTok, vídeos privados não aparecem na lista: um corte postado como privado fica sem
  métricas. Um título editado no celular pode impedir que o vídeo seja achado.
- A cota do YouTube passa a contar as coletas (1 unidade cada, pelo `videos.list`); o
  Analytics tem cota própria.
