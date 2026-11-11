# ADR 0007: API da revisão local, sem senha, e o comando `clipador web` vindo da API

- **Status:** aceita
- **Data:** 2026-10-24

## Contexto

A etapa 12 cria a API que a interface de revisão (etapa 13) usa para aprovar, recusar e
ajustar os clipes. Ela roda no mesmo PC, só para uma pessoa. Duas perguntas:

1. Precisa de login? Uma senha protegeria contra outros programas e sites, mas exige
   guardar e digitar a senha, e o frontend não pode guardar segredos (ADR 0003).
2. Onde fica o comando `clipador web`? O CLI é do backend, e o backend não pode
   importar a API (ADR 0003).

## Decisão

Sem senha, mas só o próprio PC alcança a API, com três barreiras:

- o servidor escuta só em `127.0.0.1` (fixo no código; nunca `0.0.0.0`): a rede não alcança;
- só aceita `127.0.0.1` e `localhost` no cabeçalho `Host`. Isso barra o *DNS rebinding*: um
  site cujo domínio passa a apontar para `127.0.0.1` manda o próprio nome no `Host`;
- recusa (403) pedidos que mudam algo (POST, DELETE) quando o cabeçalho `Origin` é de
  outro site. Sem isso, uma página aberta no navegador poderia aprovar clipes por você.
  A API não libera CORS, então outro site também não consegue ler as respostas, e um
  formulário disfarçado (`text/plain`) não é aceito como JSON.

O comando `clipador web` mora na API e se cadastra no CLI do backend por um *entry
point* (grupo `clipador.comandos` no `api/pyproject.toml`). O backend só procura comandos
desse grupo ao abrir, sem saber que a API existe. É o mesmo mecanismo que o pytest e outras
ferramentas usam para plugins.

## Alternativas consideradas

- Senha ou token: protege também contra outros programas do PC, mas um programa
  malicioso já rodando no PC tem acesso direto ao banco e aos arquivos. Não compensa o
  incômodo para um app pessoal. Para o celular (etapa 19, via Tailscale) isto muda: a
  autenticação vira obrigatória.
- Comando separado (`clipador-web`): funciona, mas o roteiro e o hábito pedem
  `clipador web`, junto dos outros comandos.
- O backend importar a API "por texto" (ex.: `uvicorn.run("clipador_api.app:app")`):
  passaria no teste de arquitetura, que lê os `import`, mas quebraria a regra por baixo.

## Consequências

- Testes conferem as barreiras: `Host` de outro nome dá 400, `Origin` de outro site dá
  403, formulário `text/plain` dá 422 (`api/tests/test_seguranca_e_comando.py`).
- A interface da etapa 13 é servida pela mesma origem da API: a própria API serve os
  arquivos prontos (`frontend/dist`). Durante o desenvolvimento, o Vite repassa `/api` com
  *proxy* e troca só a origem dele (`127.0.0.1:5173`) pela da API; a de qualquer outro
  site passa adiante e continua recusada (conferido na etapa 13, em `frontend/vite.config.ts`).
- Sem a API instalada, o CLI do backend funciona normalmente, só sem o `web`.
