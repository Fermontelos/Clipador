# Frontend

Interface de revisão (React + Vite + TypeScript + Tailwind), construída na **etapa 13**.
Roda no navegador e conversa só com a `api/`, por HTTP, em `127.0.0.1`.

Regras de segurança:

- **nunca guarda chaves nem tokens**: tudo o que vai para o navegador pode ser visto por
  qualquer pessoa que abra as ferramentas de desenvolvedor;
- não acessa o banco nem os arquivos direto, só pela API.

Esta pasta fica fora do workspace do uv (Python): terá o próprio `package.json`.
