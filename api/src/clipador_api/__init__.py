"""API local (FastAPI) da revisão de clipes (etapa 12).

Regras:
- ouve só em 127.0.0.1 (não fica visível na rede);
- usa o backend (``clipador``) para ler e gravar no banco: a regra de negócio fica lá
  (``clipador.revisao``), e aqui só se traduz HTTP;
- nunca importa ``clipador_integracoes`` direto: publicar vira uma tarefa na fila, e
  quem a executa é o backend.
"""
