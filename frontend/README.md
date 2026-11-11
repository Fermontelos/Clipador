# Frontend

A interface da revisão (React 19, Vite 8, TypeScript 7, Tailwind 4 e Recharts). Roda no
navegador e só conversa com a `api/`, por HTTP, em `127.0.0.1`.

Regras de segurança (conferidas em `tests/test_arquitetura.py`):

- nunca guarda chaves nem tokens: tudo o que vai para o navegador pode ser visto por
  qualquer pessoa que abra as ferramentas de desenvolvedor;
- não acessa o banco nem os arquivos direto, só pela API;
- é servida pela mesma origem da API: a API recusa pedidos que mudam algo vindos de outra
  origem (ADR 0007).

Esta pasta fica fora do workspace do uv (Python): tem o próprio `package.json`, com as
versões exatas travadas no `package-lock.json`. Precisa do Node.js 22.12 ou mais novo.

## Comandos

```bash
npm ci             # instala exatamente as versões do package-lock.json
npm run dev        # Vite em 127.0.0.1:5173, com o "clipador web" aberto (repassa /api)
npm test           # testes do Vitest
npm run test:cobertura  # os mesmos, com cobertura
npm run typecheck  # tipos (TypeScript)
npm run build      # tipos + build em dist/, que o "clipador web" serve
```

No uso normal só o `npm run build` é preciso (uma vez, e de novo quando a interface mudar):
o FastAPI serve os arquivos de `dist/` e o Node não fica rodando.

`npm run build:estatica` gera a versão que roda sem servidor, publicada no GitHub Pages: ela lê
respostas gravadas da demo (`src/demo/apiEstatica.ts`) em vez de chamar a API.

## Organização

```
src/
  api.ts           chamadas à API (e a mensagem de erro dela em texto)
  tipos.ts         o formato dos dados (igual a api/src/clipador_api/esquemas.py)
  App.tsx          as 4 telas, cada uma com o seu endereço (/, /fila, /estatisticas, /engajamento)
  revisao/
    atalhos.ts     tecla -> ação (função pura)
    corte.ts       regras do ajuste do corte, as mesmas da API
    estado.ts      contador, próximo pendente, rascunho, pilha do desfazer
    Revisao.tsx    a tela: junta tudo e cuida do teclado
    Player.tsx     o vídeo, repetindo o trecho escolhido
    LinhaDoTempo.tsx  alças de início e fim para arrastar
  telas/
    Fila.tsx       fila, erros e cota do YouTube (atualiza a cada 5 s)
    Estatisticas.tsx  precisão e gráficos (carrega só ao abrir a tela)
    Aprendizado.tsx   precisão por dia, pesos e janela sugeridos e o aceite em dois
                      cliques, dentro das Estatísticas
    Engajamento.tsx   o que o público assistiu: cortes, hashtags e tipos de título
  demo/
    apiEstatica.ts   a API "de mentira" da demo no navegador
  testes/          preparação do Vitest e dados de exemplo
```

A lógica fica em funções puras (`atalhos.ts`, `corte.ts`, `estado.ts`), testadas sem
navegador; `Revisao.test.tsx` testa a tela inteira com uma API falsa na memória.

## Decisões

- Teclas: os números usam a tecla física (`code`), porque com Shift o caractere vira
  "!"; o resto usa o caractere (`key`), para funcionar também no teclado ABNT2. Com Shift,
  "[" e "]" chegam como "{" e "}". Ctrl, Alt e Cmd ficam com o navegador.
- Mudanças do rascunho partem sempre da versão mais nova (atualização funcional do
  React): segurar "]" conta todas as teclas, mesmo as que chegam antes de a tela redesenhar.
- Ajuste do corte: o player só tem o arquivo do último corte. Encurtar aparece na hora;
  a parte que passa dele só aparece depois do novo corte, que é pedido ao aprovar.
  Enquanto o novo corte está na fila, o ajuste fica travado e a tela confere a cada 5 s se
  ele ficou pronto; o endereço do vídeo muda com o corte, para o navegador baixar o novo.
- A revisão fica sempre montada: ir à tela da fila e voltar não perde o título em
  edição nem a pilha do desfazer. Escondida, ela não ouve o teclado e o vídeo pausa.
- Atalhos e campos: só campos onde se digita (texto, não caixas de marcar) desligam os
  atalhos; com a ajuda aberta, as teclas só a fecham (Esc ou ?).
- Cores: tema escuro, com as cores de série (azul e laranja) e de status validadas para
  daltonismo e contraste com o fundo; status sempre com ícone e texto, nunca só a cor.
