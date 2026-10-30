# Configurar as contas do YouTube e do TikTok

Este guia é para quem quer usar o Clipador com **os próprios canais**. Para só ver o app
funcionando, a [demo](../README.md#demo-em-3-comandos) não precisa de conta nenhuma: o YouTube é
simulado e o TikTok vai para uma pasta.

Tudo aqui é gratuito e é feito **uma vez**. Nada vai para o git: as chaves e os logins ficam
no `.env` e em `segredos/` (os dois no `.gitignore`), e só o pacote `integracoes/` os lê
([ADR 0003](adr/0003-backend-api-integracoes-frontend.md)).

| Para quê | O que precisa | Seção |
|---|---|---|
| Publicar no YouTube (Shorts e vídeos) | Projeto no Google Cloud + `clipador youtube-entrar` | [YouTube](#youtube) |
| Métricas do YouTube (etapa 17) | O mesmo projeto, com as permissões de leitura | [YouTube](#youtube), passo 4 |
| TikTok pela pasta (padrão) | Nada | [TikTok](#tiktok) |
| Rascunho no app do TikTok e métricas | App no TikTok for Developers (sandbox) + `clipador tiktok-entrar` | [TikTok](#tiktok) |

Antes, o app precisa estar instalado e com o `config.toml` criado (veja
[Como usar com as suas lives](../README.md#como-usar-com-as-suas-lives)).

## YouTube

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

## TikTok

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

## Conferir

- `uv run clipador status`: os erros de login aparecem nas tarefas com erro, cada um com o
  comando que resolve e o `clipador refazer <número>` para tentar de novo.
- Na revisão (`uv run clipador web`), cada momento aprovado mostra as publicações: na fila,
  enviando, enviada (com o link), na pasta, rascunho no app ou com erro.
- Os logins vencidos ou sem uma permissão não travam o resto: o envio ao YouTube continua
  funcionando com um login antigo, só de envio, e cada ação avisa qual permissão falta.

## Revogar o acesso

- Google: na sua Conta do Google, em "apps de terceiros com acesso à sua conta"
  ([myaccount.google.com/permissions](https://myaccount.google.com/permissions)), remova o
  app, e apague `segredos/youtube_token.json`.
- TikTok: remova o app da lista de apps com acesso à sua conta, nas configurações do TikTok,
  e apague `segredos/tiktok_token.json`.
