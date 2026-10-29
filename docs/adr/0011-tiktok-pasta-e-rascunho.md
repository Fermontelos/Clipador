# ADR 0011: TikTok pela pasta e por rascunho, post direto só após a auditoria

- **Status:** aceita
- **Data:** 2026-10-27

## Contexto

A etapa 16 leva os verticais aprovados ao TikTok. A Content Posting API tem dois caminhos
(conferidos na documentação oficial em 2026-10-27):

- **Upload (rascunho)**, permissão `video.upload`: o vídeo vai para a caixa de entrada do
  app, e o criador finaliza no celular pela notificação. O vídeo chega **sem legenda**. No
  máximo 5 rascunhos pendentes a cada 24 h. Funciona no **sandbox** sem análise (só para
  as contas cadastradas como *target users*); para qualquer conta, o app precisa ser
  aprovado.
- **Direct Post**, permissão `video.publish`: posta direto, com legenda e privacidade. Sem
  a **auditoria** do TikTok, os posts ficam privados (`SELF_ONLY`) e a conta precisa estar
  privada. Antes de cada post, é preciso consultar o `creator_info`, e as diretrizes de
  conteúdo exigem uma tela própria de confirmação: o apelido da conta, privacidade escolhida
  à mão (sem valor padrão), comentários/dueto/costura desligados por padrão, divulgação de
  conteúdo comercial e a frase de aceite da "Music Usage Confirmation".
- **Uso pretendido** (diretrizes de conteúdo, conferidas em 2026-10-27): a auditoria do
  Direct Post não aceita app só de teste ou de uso privado, e cita como exemplo do que
  **não** é aceito "uma ferramenta para subir conteúdo para as contas que você ou a sua
  equipe gerenciam". É exatamente o Clipador.

## Decisão

- **(a) Pasta, sempre**: todo vertical aprovado com destino TikTok vai para
  `dados/exportar/tiktok/` (ou `[tiktok] pasta`) com um `.txt` da legenda (título +
  hashtags). Funciona sem conta de desenvolvedor e é o plano B de qualquer falha.
- **(b) Rascunho, opcional** (`[tiktok] modo = "rascunho"`): o vídeo também vai para a caixa
  de entrada do app. A pasta continua valendo, porque a legenda só existe no `.txt`.
- **(c) Post direto: não implementado.** Pelas diretrizes, um app pessoal provavelmente
  não passa na auditoria; sem ela, os posts sairiam privados, e o código não teria como
  ser testado de verdade. Se um dia o app virar um produto para outros criadores, a
  decisão volta à mesa (pedir a auditoria é decisão sua, combinada antes).
- **Só verticais**: a revisão e o config recusam o TikTok no horizontal.
- **Sem horário**: o TikTok não tem `publishAt` no rascunho; o envio acontece logo depois
  dos mesmos `minutos_para_desfazer` do YouTube.
- **Limite de 5 em 24 h**: o app conta os rascunhos que ele mesmo enviou e nem pede o
  sexto (a tarefa espera, sem gastar tentativa). Se o TikTok recusar mesmo assim (rascunhos
  enviados por fora), a tarefa espera 6 h.
- **A pasta é o primeiro passo**: mesmo com o rascunho esperando (o limite, o login), o
  vídeo e a legenda já ficam na pasta. Enquanto a publicação está pendente, desfazer ou
  mudar a decisão a cancela **e tira os dois arquivos da pasta** (depois do commit do
  cancelamento), para nada parecer aprovado no celular.
- **Login de app de computador**: retorno em `http://127.0.0.1:{porta}/callback/` e PKCE
  com o desafio em SHA-256 **hexadecimal** (o do Google é Base64URL). Chaves do app no
  `.env` (`arquivo_chaves`), token em `segredos/tiktok_token.json`; os dois lidos só por
  `integracoes/tiktok.py` (ADR 0003).

## Alternativas consideradas

- **Só a pasta**: zero dependência do TikTok, mas sempre exige copiar o vídeo para o
  celular. O rascunho tira esse passo (e a pasta sincronizada também).
- **Pedir a auditoria já**: posts automáticos de verdade, mas as diretrizes recusam
  ferramentas de uso próprio, e a análise ainda pede páginas de termos e privacidade e a
  tela de confirmação.
- **`PULL_FROM_URL`** (o TikTok baixa o vídeo de um endereço): exige um domínio verificado
  na internet; o app roda num PC sem servidor público. Ficou o `FILE_UPLOAD`.

## Consequências

- A revisão mostra cada publicação do TikTok como "na pasta" ou "rascunho no app".
- O rascunho fica no **sandbox**, com a sua conta como *target user* (até 10 contas). A
  documentação não fala em prazo para o sandbox; tirar o app dele (para qualquer conta)
  exige a análise do TikTok, que também pode olhar o uso pretendido.
- O app não sabe se você postou o rascunho (o `status` da API diria, mas só por consulta).
  A etapa 17 liga o vídeo publicado ao rascunho pelo horário e pelo título (ADR 0012):
  um título editado no celular pode impedir a ligação.
- Uma pasta sincronizada com o celular acumula vídeos: apagar os já postados é manual.
