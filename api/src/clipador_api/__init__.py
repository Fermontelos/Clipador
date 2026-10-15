"""API local (FastAPI) da revisão de clipes. Começa a ser construída na etapa 12.

Regras:
- ouve só em 127.0.0.1 (não fica visível na rede);
- usa o backend (``clipador``) para ler e gravar no banco;
- nunca importa ``clipador_integracoes`` direto: publicar vira uma tarefa na fila, e
  quem a executa é o backend.
"""
