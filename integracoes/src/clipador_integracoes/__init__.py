"""Integrações com serviços externos: YouTube, TikTok e o cliente do LoL.

Começa a ser construída na etapa 7 (LoL) e cresce nas etapas 15 a 17 (YouTube e TikTok).

Regras:
- é o único código do app que lê chaves e tokens (do ``.env``);
- não conhece o resto do app (banco, fila, API): recebe dados simples, como "envie este
  vídeo com este título", e devolve dados simples, como o ID do vídeo publicado;
- nunca escreve chaves ou tokens no log.
"""
