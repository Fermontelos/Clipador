# API

Servidor local (FastAPI) que a interface de revisão usa: lista os clipes por nota, envia
o vídeo, grava as decisões (aprovar, recusar, ajustar o corte). Construída na **etapa 12**.

Regras de segurança:

- ouve só em `127.0.0.1`: nenhum outro computador da rede consegue acessar;
- usa o backend para ler e gravar no banco;
- **nunca** importa as integrações direto: publicar vira uma tarefa na fila, que o backend
  executa. Isso é conferido em `tests/test_arquitetura.py`.
