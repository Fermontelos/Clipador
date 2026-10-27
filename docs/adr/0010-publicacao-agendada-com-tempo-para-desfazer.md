# ADR 0010: Publicação agendada no YouTube, com tempo para desfazer

- **Status:** aceita
- **Data:** 2026-10-27

## Contexto

A etapa 15 sobe os clipes aprovados para o YouTube. Regras do projeto: nada é publicado
sem aprovação, a cota é limitada (100 envios e 10.000 unidades por dia) e a revisão acontece
de manhã, num lote de ~15 clipes. Perguntas:

1. **Quando** o envio acontece e quando o vídeo vai ao ar?
2. Como **desfazer** uma aprovação por engano (a tecla `U` da revisão)?
3. O que fazer quando a cota acaba ou o login vence?

## Decisão

- **Aprovar cria a publicação na hora**, pendente, junto com a decisão (mesmo commit), e
  uma tarefa `publicar` que só roda depois de `minutos_para_desfazer` (10 min). Até lá,
  desfazer ou mudar a decisão **cancela** a publicação sem nada ter saído do PC. Depois que
  o envio começa, a decisão trava (mudar aqui não mudaria o que já está no YouTube). O
  cancelamento só apaga a tarefa se ela não estiver em andamento, numa instrução só (como
  a fila faz ao pegá-la): se o serviço começar o envio no mesmo instante, o desfazer recusa
  em vez de apagar a decisão de um vídeo que está subindo.
- **O vídeo sobe privado, com data de publicação** (`publishAt`), e o YouTube o publica
  sozinho. O horário é o próximo dos `[youtube] horarios` (ex.: 12h, 18h e 21h) com uma
  antecedência mínima e um intervalo mínimo entre publicações: as aprovações viram uma
  agenda pelos próximos dias, e não dezenas de vídeos de uma vez. Cada formato aprovado com
  destino YouTube é um vídeo: com 3 horários por dia, 1 aprovação nos 3 formatos ocupa um
  dia inteiro da agenda (ver "Consequências").
- **Cota e espera**: o executor confere a cota antes de enviar (contando pelo horário do
  envio). Sem cota, a tarefa volta para a fila marcada para quando ela renovar, sem gastar
  tentativa (`fila.TentarDepois`). O mesmo para um clipe com um novo corte na fila.
- **Login**: só a permissão `youtube.upload`; o token fica em `segredos/` (fora do git) e é
  lido só pelo pacote `integracoes` (ADR 0003). Sem login, a tarefa falha com a instrução
  (`clipador youtube-entrar` e `clipador refazer`), em vez de tentar 3 vezes.
- **Modo simulado**: um YouTube falso, sem internet, para a demo e para testar o fluxo.

## Alternativas consideradas

- **Enviar só na hora de publicar** (o app espera até 18h e sobe): exige o PC ligado no
  horário, e um envio que falha perde o horário. Com `publishAt`, o vídeo já está no
  YouTube horas antes.
- **Publicar na hora, público**: 15 vídeos de uma vez no canal e nos inscritos.
- **Criar as publicações só no envio** (sem a pendente): o desfazer ficaria simples, mas a
  revisão não mostraria o horário planejado, e a fila não teria o que mostrar.
- **Confirmação extra antes de cada envio**: a aprovação já é a confirmação; os 10 min
  cobrem o engano.

## Consequências

- A revisão mostra cada publicação: na fila (com o horário planejado), enviando, enviada
  (com o link) ou com erro. "Enviando" e "erro" vêm da tarefa, e não ficam gravados na
  publicação: `clipador refazer` devolve a tarefa e a publicação volta para a fila.
- Um vídeo enviado e depois desaprovado precisa ser apagado à mão no YouTube Studio: o app
  só tem a permissão de enviar.
- Se o PC cair entre o fim do envio e o commit que grava o ID (milissegundos), o vídeo
  pode subir duas vezes: o ID é gravado logo depois da resposta, num commit só para isso.
- O TikTok (etapa 16) entra no mesmo mecanismo, com o executor dele.
- **A agenda acompanha o ritmo das aprovações só se os números baterem**: vídeos aprovados
  para o YouTube por dia (aprovações × formatos com YouTube) contra os horários por dia.
  Passando disso, a fila de agendados cresce todo dia. Os destinos de cada formato
  (`[revisao] destinos_padrao`) e os `[youtube] horarios` são o ajuste.
