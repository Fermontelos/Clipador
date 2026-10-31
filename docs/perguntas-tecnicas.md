# Perguntas técnicas

As perguntas que uma pessoa técnica costuma fazer sobre o Clipador, com a resposta curta, onde
está no código e o que dizer se ela quiser aprofundar. Complementa o
[roteiro de demonstração](roteiro-de-demonstracao.md) (o que mostrar) e os
[ADRs](adr/) (as decisões, com as alternativas).

Cada resposta segue o mesmo formato: **a resposta em uma ou duas frases**, depois o detalhe.
Numa conversa, comece pela frase curta e só desça se a pessoa pedir.

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Banco e fila](#banco-e-fila)
- [Áudio, transcrição e detecção](#áudio-transcrição-e-detecção)
- [Cortes e IA local](#cortes-e-ia-local)
- [API, interface e segurança](#api-interface-e-segurança)
- [Publicação](#publicação)
- [Aprendizado](#aprendizado)
- [Testes e qualidade](#testes-e-qualidade)
- [Windows](#windows)
- [Perguntas difíceis](#perguntas-difíceis)

## Visão geral

**Explique o projeto em 30 segundos.**
Faço 3 h de live de LoL por dia, e cortar os melhores momentos à mão levava horas. O OBS grava
a live com a voz, o jogo e a música em faixas separadas; de madrugada, o app transcreve,
acha os momentos (picos da voz, expressões na fala, eventos do jogo), corta em horizontal e
vertical e sugere títulos com uma IA local. De manhã eu reviso pelo teclado, e o que aprovo
vai para o YouTube e o TikTok. As aprovações e o engajamento dos vídeos ensinam a nota.

**Qual foi a parte mais difícil?**
Escolha uma e conte como história (problema, investigação, decisão):
- **O NVENC que não funcionava**: o FFmpeg 9 exige a API 13.1 do NVENC (driver 610), e a GTX
  1050 Ti parou no driver 580 (API 13.0). Achei a mensagem no log, confirmei em fóruns e decidi
  testar o encoder uma vez e cair no processador ([ADR 0006](adr/0006-cortes-no-processador-sem-nvenc.md)).
- **A corrida entre desfazer e enviar**: se o serviço pega a tarefa de envio no mesmo instante
  em que eu desfaço a aprovação, um vídeo poderia subir sem decisão. Resolvi com um `DELETE`
  condicional numa instrução só ([Publicação](#publicação)).
- **Os três relógios do LoL**: o tempo do jogo, o relógio do PC e o tempo da gravação
  ([Áudio, transcrição e detecção](#áudio-transcrição-e-detecção)).

**O que você faria diferente?**
Medir antes o que o público quer: comecei pelo meu gosto, e o engajamento só entrou no fim.
Com dados reais, a mistura entre os dois é a parte que mais vale ajustar.

## Arquitetura

**Por que quatro partes (integracoes, backend, api, frontend)?**
Para as chaves ficarem num lugar só e a dependência ir numa direção só:
`frontend -> api -> backend -> integracoes`. Só `integracoes` lê o `.env` e os tokens; a API
nunca chama as redes direto (publicar é uma tarefa na fila); o frontend nunca guarda chaves
([ADR 0003](adr/0003-backend-api-integracoes-frontend.md)).

**Como você garante que essas regras não quebram com o tempo?**
Com um teste: [`tests/test_arquitetura.py`](../tests/test_arquitetura.py) lê o código de cada
pacote com o módulo `ast` do Python (sem executar), lista os `import` e falha se, por exemplo, a
API importar as integrações ou o backend usar `load_dotenv`. Um último teste cria um pacote que
quebra as regras numa pasta temporária e confere que a checagem pega: um teste que nunca falha
não protege nada.
- *Aprofundar*: o teste lê imports, então dá para burlar importando "por texto"
  (`uvicorn.run("clipador_api.app:app")`). Por isso o comando `clipador web` mora na API e
  entra no CLI do backend por um *entry point*, o mesmo mecanismo de plugins do pytest
  ([ADR 0007](adr/0007-api-local-sem-senha.md)).

**Como se acrescenta um jogo novo ou o chat da Twitch?**
Com um plugin: uma classe com `nome`, `disponivel(entrada)` e `detectar(entrada) -> list[Sinal]`,
registrada em `detectores/detectar.py`. O detector só devolve sinais (trecho, força de 0 a 1,
motivo); quem junta sinais próximos em momentos e dá a nota é `detectores/nota.py`
([ADR 0005](adr/0005-detectores-devolvem-sinais.md)).
- *Aprofundar*: no plano original, cada detector devolvia momentos prontos. Mudei porque um
  momento bom junta o grito, o "não acredito" e o pentakill, e três detectores dariam três
  janelas diferentes para a mesma jogada.

## Banco e fila

**Por que SQLite e não PostgreSQL?**
Um usuário, um PC, poucas centenas de linhas por live. O SQLite não precisa de servidor, o
backup é a API dele mesmo, e o modo WAL deixa o serviço da madrugada e a revisão usarem o banco
juntos ([ADR 0002](adr/0002-sqlite-e-nao-postgres.md)). O acesso passa pelo SQLModel, então
trocar por PostgreSQL é viável se o app virar multiusuário.

**Como dois processos não pegam a mesma tarefa?**
`pegar_proxima` em [`banco/fila.py`](../backend/src/clipador/banco/fila.py) faz
`UPDATE tarefa SET status = 'em_andamento' WHERE id = ? AND status = 'pendente'` e confere se
mudou exatamente 1 linha. Se outro processo pegou antes, muda 0 e ele tenta a próxima. É um
*compare-and-set* no banco, sem trava em memória.

**O que acontece se a luz cair no meio do processamento?**
Cada etapa grava o próprio estado, então nada se perde além do trabalho em andamento:
- concluir uma tarefa e criar a próxima da cadeia acontecem **no mesmo commit**: ou as duas
  ficam gravadas, ou nenhuma;
- ao abrir, o serviço devolve à fila as tarefas que estavam "em andamento"
  (`recuperar_interrompidas`);
- a transcrição grava a cada bloco de 10 min de áudio e retoma do último bloco;
- arquivos são gravados como `.parcial` e renomeados no fim: um arquivo pela metade nunca
  parece pronto;
- o arquivo MKV do OBS não se perde se o OBS cair (o MP4 perderia o índice).
- *Aprofundar*: uma tarefa que falha volta para a fila e vira "erro" na 3ª vez; um erro que
  não adianta tentar de novo (gravação sem a faixa da voz) vai direto para "erro"
  (`FalhaDefinitivaError`). Esperas que não são erro (cota acabou, limite do TikTok) voltam
  com horário marcado e sem gastar tentativa (`TentarDepois`).

**Como você muda o esquema do banco sem perder dados?**
Com o Alembic em modo *batch*, porque o SQLite não altera colunas com `ALTER TABLE` (o batch
recria a tabela). A migração roda com as chaves estrangeiras desligadas, confere tudo com
`PRAGMA foreign_key_check` no fim e só depois religa. Antes de migrar um banco existente, o app
faz um backup, porque o driver do SQLite grava algumas mudanças de tabela fora da transação.

**Por que todas as datas em UTC com fuso?**
A live passa da meia-noite, o horário de verão existe, e a cota do YouTube vira à meia-noite do
Pacífico. Guardando tudo em UTC com fuso, subtrair horários é sempre certo; a conversão para o
horário local só acontece na tela (e no "dia" da revisão).

## Áudio, transcrição e detecção

**Por que separar o áudio em faixas no OBS?**
Com a voz isolada, o volume dela é um sinal limpo (um grito não se confunde com uma explosão
do jogo), o Whisper transcreve melhor, e a música nunca entra nos clipes (direitos autorais).
Faixa 1 = mistura (a live), 2 = voz, 3 = jogo, 4 = música.

**Como o detector de volume acha um grito?**
Mede o volume em dBFS em janelas de 0,5 s. O "normal" em cada instante é a **mediana** das
janelas com voz do último minuto; um pico é ficar 10 dB acima do normal por pelo menos 1 s. A
força é 0,5 no limiar e 1 com 20 dB ou mais ([`detectores/volume_voz.py`](../backend/src/clipador/detectores/volume_voz.py)).
- *Por que mediana e não média?* Alguns gritos no último minuto puxariam a média para cima e
  esconderiam o próximo. A mediana ignora esses extremos.
- *E depois de um tempo calado?* Sobram poucas janelas com voz, e uma fala normal pareceria
  grito. Por isso o normal nunca fica mais de 6 dB abaixo do volume típico da live inteira.

**Como funciona a transcrição numa placa de 4 GB?**
faster-whisper (CTranslate2), modelo `medium` em int8: ~1,6 GB de memória da placa, medidos.
Se faltar memória, cai para o `small`. Usa o filtro de silêncio (VAD) e timestamps por palavra,
só na faixa da voz. Roda numa velocidade de ~13x o tempo real na voz pura.
- *Aprofundar*: o vocabulário (nomes de campeões) vai em `hotwords`, e não no
  `initial_prompt`, que só vale na primeira janela de 30 s com `condition_on_previous_text`
  desligado. E é uma tarefa de GPU por vez: o Whisper e a IA dos títulos nunca dividem a placa.

**Como a nota é calculada?**
`nota = 100 × Σ(peso × maior força do detector no momento) ÷ Σ(pesos dos detectores
disponíveis)`. Um detector indisponível (o do LoL numa live de outro jogo) sai da conta, para a
nota máxima continuar sendo 100. Sinais a menos de 8 s viram um momento só; a janela do clipe
vai de 10 s antes a 5 s depois, entre 15 e 60 s.

**Como você sabe em que segundo do vídeo aconteceu um evento do LoL?**
Há três relógios: o tempo do jogo (`EventTime`), o relógio do PC e o tempo da gravação. Durante
a partida, o coletor consulta a API local do jogo a cada 2 s e guarda o par (horário do PC,
tempo de jogo); a diferença é o início da partida no relógio real (a mediana das últimas 5
amostras, para uma consulta lenta não atrapalhar). O início da gravação vem do nome do arquivo
do OBS. Então `segundo no vídeo = início da partida + EventTime − início da gravação`
([`jogo/alinhamento.py`](../backend/src/clipador/jogo/alinhamento.py)).
- *Limitação honesta*: falta conferir numa live gravada (o roteiro pede margem de 2 s), e uma
  pausa de partida personalizada adiantaria os eventos lidos depois dela.

## Cortes e IA local

**Por que os cortes não usam a placa de vídeo?**
O FFmpeg 9 exige o driver 610 para o NVENC, e a 1050 Ti parou no 580. O app testa o NVENC uma
vez e, se falhar, usa o libx264 no processador: ~14 s para 20 s de vertical 1080p, o que de
madrugada não importa ([ADR 0006](adr/0006-cortes-no-processador-sem-nvenc.md)).

**Quais formatos saem?**
Horizontal (com thumbnail do pico), vertical com fundo desfocado e vertical com a câmera em
cima e o jogo embaixo ([ADR 0008](adr/0008-vertical-b-camera-e-jogo.md)). Áudio sem a
música, normalizado a -14 LUFS (o padrão das redes) com o filtro `loudnorm`.

**Como a IA local gera títulos com formato garantido?**
Ollama com o `qwen3:4b`, com a resposta presa a um **JSON Schema** (o modelo só consegue
responder no formato pedido), sem o modo de raciocínio (para textos curtos ele só atrasa), e
descarregado da placa no fim da tarefa (`keep_alive=0`), para o Whisper não ficar sem memória.
Se o Ollama falhar, o plano B é a frase mais marcante da transcrição.
- *Aprofundar*: o pedido é escrito na voz do streamer; quando estava em terceira pessoa,
  palavras como "o streamer" e "dB" vazavam para os títulos.

## API, interface e segurança

**A API não tem senha. Isso não é inseguro?**
Ela só serve o próprio PC, com três barreiras ([ADR 0007](adr/0007-api-local-sem-senha.md)):
1. escuta só em `127.0.0.1`, então a rede não alcança;
2. só aceita `127.0.0.1` e `localhost` no cabeçalho `Host`, o que barra o *DNS rebinding* (um
   site cujo domínio passa a apontar para `127.0.0.1` manda o próprio nome no `Host`);
3. recusa (403) POST e DELETE com `Origin` de outro site, para uma página aberta no navegador
   não aprovar clipes por mim. Sem CORS, outro site também não lê as respostas.

Uma senha protegeria contra outros programas do PC, mas um programa malicioso já rodando no PC
lê o banco direto. Se a revisão for para o celular (etapa 19), a autenticação vira obrigatória.

**Como a interface foi organizada para ser testável?**
A lógica fica em funções puras (`frontend/src/revisao/{atalhos,corte,estado}.ts`), testadas
com o Vitest sem navegador; os componentes React só ligam essas funções à tela.
- *Aprofundar*: os números dos atalhos são lidos por `e.code` (a tecla física) e o resto por
  `e.key`, porque no teclado ABNT2 `Shift+1` vira `!`. E o estado do rascunho é atualizado de
  forma funcional, porque várias teclas podem chegar antes de um render.

**Como o player toca um trecho do vídeo sem baixar o arquivo inteiro?**
A API responde a pedidos com o cabeçalho `Range` (206 Partial Content), que o elemento
`<video>` usa para pular para qualquer ponto.

## Publicação

**Como garante que nada é publicado sem querer?**
Só uma decisão aprovada cria publicação, e o envio espera 10 minutos: dá para desfazer sem
nada sair do PC. No YouTube o vídeo sobe privado, com data de publicação (`publishAt`), e o
YouTube o publica sozinho no horário ([ADR 0010](adr/0010-publicacao-agendada-com-tempo-para-desfazer.md)).

**E se eu desfizer no mesmo instante em que o serviço começa o envio?**
O cancelamento apaga a tarefa com um `DELETE ... WHERE status != 'em_andamento'` numa
instrução só. Se apagou, a publicação é cancelada no mesmo commit da decisão. Se não apagou, o
envio já começou: o desfazer é recusado (409) e nada muda. Antes, o código verificava e depois
apagava, e o serviço podia pegar a tarefa entre as duas coisas.

**O que é PKCE e por que o TikTok exige?**
Num app de computador não dá para guardar um segredo (qualquer um abre o programa). No PKCE, o
app sorteia um `code_verifier`, manda só o hash dele (`code_challenge`, SHA-256) ao pedir o
login e, para trocar o código pelo token, manda o verifier original. Quem interceptar o código
de retorno não tem o verifier e não consegue o token.
- *Detalhe*: o TikTok espera o desafio em hexadecimal, e não em Base64URL como o padrão (e o
  Google). O retorno é um servidor local em `127.0.0.1` numa porta livre, com um `state`
  aleatório contra CSRF ([`integracoes/tiktok.py`](../integracoes/src/clipador_integracoes/tiktok.py)).

**E a cota do YouTube?**
Uploads têm uma cota própria de 100 por dia; as outras chamadas dividem 10.000 unidades (uma
thumbnail custa 50, uma consulta de estatísticas 1). O executor confere antes de enviar e, sem
cota, a tarefa volta para a fila marcada para a meia-noite do Pacífico, sem gastar tentativa.

**Por que o TikTok não posta sozinho?**
O post direto exige uma auditoria que, pelas diretrizes do TikTok, não aceita "uma ferramenta
para subir conteúdo para as contas que você gerencia". O app exporta o vídeo e a legenda para
uma pasta e, opcionalmente, manda como rascunho para o app do celular
([ADR 0011](adr/0011-tiktok-pasta-e-rascunho.md)).

## Aprendizado

**Por que regressão logística e não algo mais forte?**
São 3 entradas e 100 a 200 exemplos. Uma árvore ou um gradient boosting decorariam os dados,
e não dão um número por sinal. A logística dá um coeficiente por detector, que vira o peso da
nota, e a nota continua sendo uma média ponderada que dá para explicar numa frase
([ADR 0009](adr/0009-regressao-logistica.md)).
- *Detalhes*: `class_weight="balanced"` (se eu aprovo quase tudo, "aprovar tudo" não pode
  parecer bom); coeficiente negativo vira 0 (peso negativo numa média ponderada não faz
  sentido); nada muda sem o meu aceite.

**Como você sabe que os pesos novos são melhores?**
Validação cruzada em 5 partes (`StratifiedKFold`, semente fixa): os pesos são aprendidos em 4
partes e medidos na quinta, que o modelo não viu. A medida é a **precisão no topo**: em cada
dia, a metade dos momentos de maior nota, quantos eu aprovei. É o que importa na prática, porque
a revisão mostra os de maior nota primeiro.

**Qual o viés desse treino?**
Seleção: só os momentos revisados têm decisão, e quase todos são os de maior nota do dia. O
modelo aprende entre os momentos que o detector já achou bons; um tipo de momento que o detector
nunca sugere nunca vira exemplo. Está nas limitações do README.

**Como você compara o engajamento de um Short com o de um vídeo do TikTok?**
Com um índice: visualizações, interação ((curtidas + comentários + compartilhamentos) ÷
visualizações) e retenção, cada um dividido pela **mediana do grupo** (a rede e, no YouTube,
Short ou vídeo normal), limitado a 10, numa média com pesos. 1 = o vídeo típico daquele grupo.
Visualizações cruas fariam o TikTok engolir o YouTube, e um canal que cresce faria todo vídeo
novo parecer melhor ([ADR 0012](adr/0012-engajamento-ensina-a-nota.md)).

**Se o público gosta de outra coisa, a nota segue o público?**
Mistura os dois: `(1 − 0,5) × gosto + 0,5 × engajamento`. Só o engajamento faria o app sugerir
o que eu nunca aprovaria, e a revisão viraria recusar tudo.
- *Bug que vale contar*: na primeira versão, aceitar o gosto apagava o engajamento, e aceitar
  o engajamento duas vezes misturava o que já estava misturado (0,5 virava 0,75). A correção
  foi guardar as duas partes separadas em cada versão dos pesos e sempre misturar a partir
  delas.

**O que é o epsilon-greedy nas hashtags?**
Na maior parte das vezes, as hashtags e os tipos de título que funcionaram vêm primeiro; com
chance de 10%, entra um ainda pouco testado. Sem isso, uma hashtag nova nunca teria chance de
mostrar que é boa. É o problema clássico de *exploration vs. exploitation* (multi-armed
bandit).
- *Cuidado*: é correlação, não causa (a `#pentakill` vai bem porque o momento de LoL vai bem).
  Por isso elas só mudam a ordem das sugestões, sempre entre as que a IA sugeriu para o clipe.

**Esses números do README são reais?**
Não, são da demo, e o README diz isso. O histórico da demo esconde um "gosto" conhecido
(fala 0,6, volume 0,3, LoL 0,1), e o treino o reencontra (0,62, 0,29, 0,09): é um teste de que
o aprendizado funciona. Também documentei que, com poucos dados, só mudar a ordem dos exemplos
mudava a comparação de 64% → 74% para 71% → 72%; por isso os exemplos vão em ordem
cronológica, e o número serve para comparar, não como garantia.

## Testes e qualidade

**Como testar um app que usa GPU, Whisper, Ollama, YouTube e TikTok?**
Com substitutos: um `conftest.py` na raiz troca, em todos os testes, o Whisper e o Ollama por
versões falsas, proíbe desligar o PC e aponta a abertura no login para uma chave de teste do
registro. O YouTube e o TikTok têm um modo simulado, e o cliente do TikTok recebe um transporte falso
do httpx (os testes conferem cada pedido sem sair do PC). A demo real (FFmpeg de verdade) roda com o marcador `lento`.

**95% de cobertura quer dizer que não há bugs?**
Não: cobertura diz que a linha rodou, não que o resultado está certo. As revisões acharam bugs
em código coberto (a corrida do desfazer, a mistura dupla dos pesos). O número que a CI cobra
(80%) é nos detectores e no alinhamento, onde um erro silencioso estraga tudo o que vem depois.

**Teve algum problema com a CI?**
Uma execução travou 5 horas. O FFmpeg do Chocolatey é um *shim* (um executável que chama o
FFmpeg de verdade): o tempo limite matava só o shim, e o FFmpeg continuava. A correção foi
matar a árvore de processos (`taskkill /T`), mais um limite de 5 min por teste
(`pytest-timeout`) e de 30 min no job.

## Windows

**Como o app abre sozinho no login?**
Pela chave `Run` do registro do usuário (HKCU), sem administrador, o mesmo mecanismo do
Discord e da Steam. O Agendador de Tarefas exige administrador para o gatilho de login pela
linha de comando, e um serviço do Windows roda em outra sessão, onde não dá para saber se o
usuário está usando o PC ([ADR 0004](adr/0004-abrir-no-login-pela-chave-run.md)).

**Como impede a suspensão e decide desligar o PC?**
Durante o processamento, chama `SetThreadExecutionState` (a mesma API que players de vídeo
usam). No fim, só desliga se a fila acabou **e** o teclado e o mouse estão parados há 10 min
(`GetLastInputInfo`), com 60 s para cancelar; mexer no mouse também cancela. Vem em modo de
teste, que só escreve no log.

## Perguntas difíceis

**Por que o código está em português?**
O projeto é pessoal e o domínio (live, corte, revisão) é pensado em português; manter tudo na
mesma língua deixa o código, os testes e os ADRs coerentes. Num time, eu seguiria a convenção
do time.

**Isso escala?**
Não precisa, e foi uma escolha consciente: um usuário, um PC, nenhum custo. Os pontos de troca
estão mapeados: o SQLite pelo PostgreSQL (o acesso passa pelo SQLModel), a fila por uma fila de
verdade (a interface `pegar_proxima`/`concluir`/`falhar` é pequena), e a API ganharia
autenticação.

**Por que não usar uma API paga (OpenAI, AssemblyAI) em vez de rodar tudo local?**
Regra do projeto: só ferramentas gratuitas. São ~90 h de live por mês; transcrever e gerar
títulos pagos todo dia sairia caro para um projeto pessoal. Rodar local também mantém o áudio
no PC.

**Qual a pior limitação hoje?**
Falta de dados reais: o aprendizado e o engajamento só foram validados na demo. O primeiro
envio real ao YouTube, o rascunho do TikTok e um mês de métricas são o próximo passo.
