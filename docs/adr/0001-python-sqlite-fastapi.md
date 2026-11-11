# ADR 0001: Python + SQLite + FastAPI

- **Status:** aceita
- **Data:** 2026-10-14

## Contexto

O app processa gravações de live de ~3h por dia num PC pessoal (Windows, GTX 1050 Ti),
de madrugada, e oferece uma interface web local para revisar os clipes. Precisa:

- de bibliotecas maduras de áudio, vídeo e IA (transcrição, detecção, aprendizado);
- guardar estado (fila, decisões, métricas) de forma que sobreviva a quedas;
- servir uma API local para a interface de revisão;
- ser 100% gratuito e simples de rodar em uma máquina só.

## Decisão

- Python 3.12 como linguagem principal.
- SQLite como banco de dados.
- FastAPI para a API da interface de revisão.

## Alternativas consideradas

- Node.js no backend: bom para a web, mas o ecossistema de áudio, vídeo e IA
  (faster-whisper, numpy, scikit-learn) é muito mais forte em Python.
- PostgreSQL: mais recursos, mas exige um servidor rodando. Para um usuário e uma
  máquina, o SQLite (um arquivo só, sem servidor, backup trivial) é suficiente.
- Flask: mais simples, mas o FastAPI valida dados com tipos e gera documentação
  interativa (`/docs`) sozinho, o que acelera a etapa de revisão.

## Consequências

- Um único processo Python cuida da pipeline e da API, com o banco num arquivo.
- O SQLite precisa do modo WAL para o serviço e a interface usarem o banco ao mesmo tempo
  (etapa 2).
- Se o app um dia virar multiusuário na nuvem, trocar o SQLite por PostgreSQL é viável,
  porque o acesso ao banco passa pelo SQLModel.
