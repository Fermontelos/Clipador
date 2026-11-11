# Integrações

Os clientes dos serviços de fora:

- `lol.py`: a Live Client Data API, que o próprio LoL abre em `https://127.0.0.1:2999` durante
  a partida. Não usa chave, mas só aceita a conexão com o certificado da Riot
  (`certificados/riotgames.pem`, tirado da documentação oficial).
- `youtube.py`: login do Google, envio retomável, thumbnail, estatísticas e retenção.
- `tiktok.py`: login com PKCE, envio de rascunho e a lista dos vídeos publicados.

Este é o único pacote que lê chaves e tokens (do `.env` e da pasta `segredos/`, os dois fora
do git). Ele não conhece o banco, a fila nem a API: recebe dados simples e devolve dados
simples, e nunca escreve chave ou token no log. Deixar tudo isso num lugar só torna fácil
conferir quem tem acesso às credenciais.
