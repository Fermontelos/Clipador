# Integrações

Clientes dos serviços externos: o LoL (Live Client Data API, **etapa 7**), o YouTube e o
TikTok (**etapas 15 a 17**).

Regras de segurança:

- **único código do app que lê chaves e tokens** (do `.env`, que fica fora do git);
- não conhece banco, fila nem API: recebe dados simples e devolve dados simples;
- nunca escreve chaves ou tokens no log.

Concentrar as chaves aqui deixa fácil conferir quem tem acesso a elas: só este pacote.
