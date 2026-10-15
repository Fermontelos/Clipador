# ADR 0002: SQLite e não PostgreSQL

- **Status:** aceita
- **Data:** 2026-10-15

## Contexto

O app guarda o próprio estado num banco: a fila de tarefas, as gravações, os momentos, as
decisões de revisão, as publicações e as métricas. Esse banco:

- é usado por **uma pessoa, num PC só**;
- recebe pouca escrita (algumas centenas de linhas por live);
- precisa de **dois leitores/escritores ao mesmo tempo**: o serviço da madrugada e a
  interface web de revisão;
- **não pode se perder**: decisões e métricas são o que o app usa para aprender, e não dá
  para recriá-las.

## Decisão

Usar **SQLite**, com:

- **modo WAL** (`PRAGMA journal_mode=WAL`), para leitores e um escritor trabalharem juntos;
- **`busy_timeout` de 5 s**, para uma escrita esperar a outra em vez de falhar;
- **chaves estrangeiras ligadas** (`PRAGMA foreign_keys=ON`), que no SQLite vêm desligadas;
- **Alembic** para as migrações, em modo *batch*, porque o SQLite não altera colunas com
  `ALTER TABLE`;
- **backup diário** pela API de backup do SQLite, guardando as 14 cópias mais recentes.

## Alternativas consideradas

- **PostgreSQL:** mais recursos e concorrência real entre muitos escritores. Mas exige
  instalar e manter um servidor rodando no PC, configurar usuário e senha, e o backup fica
  mais trabalhoso. Nada disso se paga com um usuário só.
- **Arquivos JSON:** simples, mas sem transações. Uma queda de energia no meio de uma
  escrita pode corromper o arquivo, e consultas como "próxima tarefa pendente mais antiga"
  teriam de ser escritas à mão.

## Consequências

- O banco é um arquivo só (`dados/clipador.db`): fácil de copiar, de inspecionar e de
  apagar para recomeçar.
- Só um processo escreve por vez. Para pegar tarefas, a fila usa um
  `UPDATE ... WHERE status = 'pendente'` e confere se mudou exatamente 1 linha, então dois
  processos nunca pegam a mesma tarefa.
- As datas são guardadas sempre em UTC com fuso; o SQLModel recusa datas sem fuso.
- Se o app um dia virar um serviço multiusuário, a troca para PostgreSQL é viável: o acesso
  passa pelo SQLModel/SQLAlchemy e as migrações já existem.
