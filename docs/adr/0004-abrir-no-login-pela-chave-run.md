# ADR 0004: Abrir o serviço no login pela chave "Run" do registro

- **Status:** aceita
- **Data:** 2026-10-15

## Contexto

O serviço precisa abrir sozinho quando o usuário entra no Windows:

- para o coletor do LoL (etapa 7) já estar rodando quando a live começar;
- para pegar as gravações que terminaram enquanto o app estava fechado.

Ele também precisa rodar **na sessão do usuário**, e não como serviço do Windows: a
etapa 11 usa `GetLastInputInfo`, que só enxerga o teclado e o mouse da sessão de quem
está logado.

O roteiro previa o **Agendador de Tarefas** (`schtasks /sc onlogon`). Pesquisando (em
2026-10-15), descobrimos que criar pela linha de comando uma tarefa com gatilho de login
exige **administrador**, mesmo para o próprio usuário: o comando responde
"Access is denied" (várias respostas no Super User confirmam o mesmo comportamento).

## Decisão

Usar a chave **`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`**, com o
valor `Clipador` apontando para:

```
"<projeto>\.venv\Scripts\pythonw.exe" -m clipador iniciar --arquivo "<projeto>\config.toml"
```

- `clipador instalar-servico` cadastra; `clipador remover-servico` desfaz;
- `clipador status` mostra se o serviço está rodando e se abre no login.

## Alternativas consideradas

- **Agendador de Tarefas:** permite reiniciar o serviço se ele cair, mas exige
  administrador para o gatilho de login pela linha de comando (seria preciso abrir o
  terminal como administrador só para instalar).
- **Atalho na pasta Inicializar (`shell:startup`):** também não exige administrador,
  mas criar um atalho `.lnk` pelo Python exige bibliotecas extras do Windows (COM), e um
  `.bat` abriria uma janela de terminal a cada login.
- **Serviço do Windows:** roda sem login, mas em outra sessão, onde o `GetLastInputInfo`
  não funciona, e exige administrador para instalar.

## Consequências

- Não precisa de administrador, só vale para o usuário atual e é o mesmo mecanismo de
  programas como Discord, Steam e OneDrive.
- Com o `pythonw.exe`, o serviço abre sem janela; tudo vai para `dados/logs/servico.log`.
  Se a configuração tiver um problema, o erro é gravado em `clipador-servico-erro.txt`, ao
  lado do `config.toml`, já que não há janela para mostrar a mensagem.
- A chave "Run" não reinicia o serviço se ele cair. Por isso o serviço é feito para não
  cair: erros de uma tarefa viram falha registrada na fila, e erros de uma volta do laço
  são registrados no log e tentados de novo.
- O comando cadastrado tem o caminho do projeto: se a pasta do projeto mudar de lugar, é
  preciso rodar `clipador instalar-servico` de novo.
