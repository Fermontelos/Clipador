# Clipador de lives v1.0

Primeira versão completa: da live gravada no OBS até o clipe publicado, passando pela revisão
de manhã. Nada é publicado sem eu aprovar.

## O que tem nesta versão

### De madrugada
- O app percebe quando a live termina (a gravação do OBS para de crescer por 2 minutos) e põe
  ela na fila. Enquanto estou gravando, a fila espera.
- A fila fica no SQLite e continua de onde parou se o PC desligar. Uma tarefa que falha é
  tentada até 3 vezes, e o banco ganha um backup por dia.
- A faixa da minha voz é transcrita com o Whisper na placa de vídeo, salvando a cada 10
  minutos de áudio.
- Os eventos do LoL são coletados durante a partida e alinhados ao segundo do vídeo.
- Três detectores (voz, fala e eventos do jogo) acham até 30 momentos por live, cada um com
  uma nota de 0 a 100.
- Cada momento vira um clipe horizontal e dois verticais, com legenda, sem a música e com o
  volume no padrão das redes. O horizontal ganha uma thumbnail.
- Uma IA local (Ollama com o qwen3:4b) sugere 3 títulos e as hashtags.
- O Windows não suspende no meio, e o PC pode desligar sozinho no fim (por padrão, só em modo
  de teste).

### De manhã, na revisão
- Uma interface web local, toda pelo teclado: aprovar, recusar, desfazer, trocar o formato e
  o título e ajustar o corte na linha do tempo.
- Mostra os 15 momentos de maior nota do dia, a fila, os erros, a cota do YouTube e as
  estatísticas.
- A API só aceita pedidos do próprio PC.

### Publicação
- No YouTube, os vídeos sobem privados e agendados nos horários que eu escolho, respeitando a
  cota diária.
- No TikTok, os verticais vão para uma pasta com a legenda num `.txt`, ou como rascunho no
  app (até 5 por dia).
- Há 10 minutos para desfazer antes de qualquer envio.

### Aprendizado
- Com 100 decisões, uma regressão logística sugere novos pesos para a nota, e a média dos
  meus ajustes sugere uma nova janela de corte.
- As métricas de cada vídeo são coletadas com 24 h, 72 h e 7 dias.
- Com 50 cortes publicados, os pesos passam a misturar o meu gosto com o que o público
  assiste, e as hashtags e os tipos de título que funcionam aparecem primeiro.
- Nada muda sem eu aceitar.

### Para quem quer conhecer
- Uma demo com uma live falsa de 3 minutos e um histórico inventado de 6 lives, que roda sem
  placa de vídeo e sem contas.
- A mesma revisão no navegador, sem instalar nada.
- O README também em inglês.

## Qualidade
- 649 testes em Python (95% de cobertura) e 94 na interface (85%).
- Lint com o ruff, tipos com o mypy, pre-commit e CI no GitHub Actions, no Windows e no Linux.
- 12 decisões técnicas registradas em `docs/adr/`.

## O que ainda falta confirmar com dados reais
- O primeiro envio ao YouTube, o rascunho no TikTok e as métricas de verdade.
- O alinhamento do LoL numa live gravada, a detecção com a voz numa faixa separada e uma
  noite de desligamento fora do modo de teste.

As limitações conhecidas estão no [README](../README.md#limitações-conhecidas).
