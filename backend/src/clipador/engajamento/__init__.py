"""Engajamento dos cortes publicados (etapa 17, ADR 0012).

- ``coleta``: as métricas de cada publicação 24 h, 72 h e 7 dias depois de ela ir ao ar (e,
  no TikTok, achar entre os seus vídeos o que você postou a partir da pasta ou do rascunho);
- ``indice``: as métricas viram um número comparável entre vídeos (1 = o típico do canal);
- ``ranking``: hashtags e tipos de título que funcionam, e a escolha "aproveitar ou testar"
  (epsilon-greedy) nas sugestões da etapa 10.

O que a nota aprende com isso fica em ``aprendizado.engajamento``.
"""
