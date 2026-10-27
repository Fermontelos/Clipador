"""Publicação dos clipes aprovados (etapas 15 e 16).

- ``planejar``: aprovar cria as publicações pendentes (e as tarefas ``publicar``);
  desfazer ou mudar a decisão cancela as que ainda não foram enviadas;
- ``agenda``: o horário de cada publicação (os horários do config, com intervalo mínimo);
- ``pedido``: o que vai para o YouTube (título, descrição, tags, categoria, privacidade);
- ``cota``: a cota diária do YouTube;
- ``publicar``: o executor da tarefa ``publicar`` (o envio, pela integração do YouTube).

Nada é publicado sem a sua aprovação: só uma decisão aprovada cria publicação.
"""
