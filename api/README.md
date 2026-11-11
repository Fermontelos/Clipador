# API

O servidor local que a interface da revisão usa (FastAPI). Ele lista os momentos por nota,
manda os vídeos, grava as decisões e os ajustes de corte e mostra a fila, as estatísticas e o
engajamento. Também traz o comando `clipador web`.

Algumas regras que valem aqui:

- Só escuta em `127.0.0.1`, então nenhum outro computador da rede chega nele.
- Não tem senha, mas recusa nomes estranhos no `Host` e pedidos de outros sites que tentem
  mudar algo ([ADR 0007](../docs/adr/0007-api-local-sem-senha.md)).
- Nunca importa as integrações. Publicar vira uma tarefa na fila, que o backend executa
  (conferido em `tests/test_arquitetura.py`).

A documentação interativa fica em `http://127.0.0.1:8765/docs` com o `clipador web` aberto.
