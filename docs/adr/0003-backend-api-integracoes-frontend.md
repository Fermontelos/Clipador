# ADR 0003: Backend, API, integrações e frontend em pastas separadas

- **Status:** aceita
- **Data:** 2026-10-15

## Contexto

O app vai ter quatro tipos de código bem diferentes:

- o backend: pipeline de processamento, banco, fila, demo e linha de comando;
- a API local da revisão (FastAPI, etapa 12);
- as integrações com YouTube, TikTok e LoL (etapas 7 e 15 a 17), que precisam de
  chaves e tokens;
- o frontend de revisão (React, etapa 13), que roda no navegador.

Misturar tudo numa pasta só torna fácil um vazamento acidental: por exemplo, a API ou o
frontend passarem a ler as chaves do YouTube sem ninguém perceber.

## Decisão

Quatro pastas na raiz, com uma direção de dependência única:

```
frontend ──(HTTP)──> api ──> backend ──> integracoes ──> YouTube / TikTok / LoL
```

| Pasta | Pacote Python | Pode usar | Não pode usar |
|---|---|---|---|
| `integracoes/` | `clipador_integracoes` | só bibliotecas externas | `clipador`, `clipador_api` |
| `backend/` | `clipador` | `clipador_integracoes` | `clipador_api` |
| `api/` | `clipador_api` | `clipador` | `clipador_integracoes` |
| `frontend/` | (React, sem Python) | a API, por HTTP | banco, arquivos e chaves |

Regras de segurança:

- Só as integrações leem o `.env`, onde ficam as chaves e os tokens. O backend lê
  apenas o `config.toml` e variáveis de ambiente. (Etapa 15: os arquivos do Google, a
  credencial do app e o token do login, ficam em `segredos/`, e a regra é a mesma: o
  backend só passa a pasta.)
- A API nunca chama as integrações direto: publicar vira uma tarefa na fila, que o
  backend executa.
- O frontend nunca guarda chaves: tudo o que vai para o navegador pode ser lido por
  qualquer pessoa.
- A API só ouve em `127.0.0.1`.

As três partes Python formam um workspace do uv: cada uma tem o próprio
`pyproject.toml` (e as próprias dependências), mas todas compartilham um único ambiente e
um único `uv.lock`. O frontend fica fora do workspace, com o próprio `package.json`.

As regras são conferidas automaticamente em `tests/test_arquitetura.py`, que lê o código
de cada parte e falha se, por exemplo, a API importar as integrações ou o backend ler o
`.env` ou os arquivos de `segredos/`.

## Alternativas consideradas

- Tudo num pacote só, separado por subpastas (ex.: `clipador/web`,
  `clipador/publicacao`): mais simples, mas nada impede uma parte de importar a outra, e
  todas teriam acesso às mesmas dependências e às chaves.
- Repositórios separados para cada parte: isolamento máximo, mas exagerado para um
  projeto de uma pessoa: cada mudança exigiria vários commits e versões coordenadas.

## Consequências

- Separar pastas, sozinho, não deixa o projeto seguro. O que protege são as regras acima,
  e a separação torna essas regras fáceis de seguir e de conferir.
- O backend deixou de ler o `.env` (antes lia, para "chaves futuras"). Quando a primeira
  chave aparecer, as integrações ganham a própria configuração que lê o `.env`.
  Atualização (etapa 15): o YouTube não precisou do `.env`. O Google entrega a credencial
  do app como um arquivo JSON, e o login também vira um arquivo, então os dois ficam em
  `segredos/` (fora do git), lidos só por `integracoes/youtube.py`. O `.env` fica para o
  TikTok (etapa 16).
- Os comandos continuam sendo rodados da raiz (`uv run clipador ...`, `uv run pytest`).
