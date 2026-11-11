# Backend

O grosso do Clipador: a pipeline (áudio, transcrição, detecção, cortes e títulos), o banco
SQLite, a fila que continua de onde parou, o serviço que roda em segundo plano, a demo, o
aprendizado, a publicação e a linha de comando (`clipador ...`).

O código fica em `src/clipador/` e os testes em `tests/`. Para falar com o YouTube, o TikTok e
o LoL ele usa as `integracoes/`, mas nunca lê as chaves diretamente.

Para instalar e rodar, veja o [README principal](../README.md).
