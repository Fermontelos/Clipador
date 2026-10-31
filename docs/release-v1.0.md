# Clipador de lives v1.0

Primeira versão completa: da live gravada no OBS ao clipe publicado, com a revisão de manhã
pelo teclado e nada publicado sem aprovação. As 18 etapas do roteiro estão feitas.

## O que o app faz

**Processamento da noite**
- Percebe sozinho quando a live termina (a gravação do OBS fica 2 min sem mudar) e põe na
  fila; enquanto você grava, a fila espera.
- Fila retomável em SQLite: se o PC desligar ou travar, continua de onde parou; uma tarefa
  que falha é tentada até 3 vezes; backup diário do banco (14 cópias).
- Extrai a faixa da sua voz e transcreve com o Whisper na placa de vídeo (CUDA 12, para a
  GTX 1050 Ti), salvando a cada 10 min de áudio.
- Coleta os eventos do LoL durante a partida e os alinha ao segundo do vídeo.
- Três detectores (plugins) acham os melhores momentos: picos da voz, expressões e fala
  acelerada, eventos do LoL; cada momento ganha uma nota de 0 a 100 (até 30 por live).
- Corta cada momento em horizontal, vertical com fundo desfocado e vertical com a câmera em
  cima; legenda da fala, áudio sem a música, -14 LUFS; thumbnail do pico.
- Sugere 3 títulos e as hashtags com uma IA local (Ollama + qwen3:4b), no seu estilo.
- Impede a suspensão do Windows enquanto processa e pode desligar o PC no fim (em modo de
  teste por padrão).

**Revisão**
- Interface web local (React), toda pelo teclado: aprovar, recusar, desfazer, trocar formato
  e título, ajustar o corte na linha do tempo (corta de novo só aquele clipe).
- Limite diário (os 15 de maior nota), fila e erros, cota do YouTube, estatísticas.
- API local (FastAPI) só em 127.0.0.1, com proteção contra pedidos de outros sites.

**Publicação**
- YouTube: Shorts e vídeos sobem privados e agendados nos seus horários, com intervalo
  mínimo, hashtags na descrição e thumbnail; a cota diária é respeitada.
- TikTok: os verticais vão para uma pasta com a legenda num `.txt`, ou como rascunho na caixa
  de entrada do app (até 5 por dia).
- 10 minutos para desfazer antes de qualquer envio; o cancelamento é atômico.

**Aprendizado**
- Com 100 decisões, uma regressão logística sugere os pesos da nota pelo seu gosto; a média
  dos seus ajustes sugere a janela do corte.
- Métricas de cada corte publicado em 24 h, 72 h e 7 dias (YouTube Data API e Analytics,
  TikTok Display API), num índice comparável entre vídeos.
- Com 50 cortes, os pesos passam a misturar o seu gosto com o engajamento previsto; hashtags e
  tipos de título que funcionam vêm primeiro nas sugestões, com exploração (epsilon-greedy).
- Nada muda sem o seu aceite; a comparação "antes e depois" é feita em dados que o treino não
  viu.

**Demo**
- `uv run clipador demo`: uma live falsa de 3 minutos e um histórico inventado de 6 lives,
  sem placa de vídeo e sem contas (a internet só é usada para instalar).

## Qualidade

- 646 testes (Python) e 85 (interface), com 95% de cobertura; detectores e alinhamento acima
  de 80%, conferidos na CI.
- Lint e formatação (ruff), pre-commit e CI no GitHub Actions (Windows e Linux).
- 12 decisões técnicas registradas em `docs/adr/`.

## Ainda não confirmado com dados reais

- O 1º envio real ao YouTube, o rascunho no sandbox do TikTok e as métricas reais.
- O alinhamento do LoL numa live gravada, a detecção com a voz na faixa 2 e uma noite de
  desligamento fora do modo de teste.

Veja as [limitações conhecidas](../README.md#limitações-conhecidas).
