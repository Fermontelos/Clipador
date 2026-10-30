# Roteiro de demonstração (5 minutos)

Para mostrar o Clipador numa entrevista, ao vivo, só com a demo: sem placa de vídeo, sem
internet, sem contas e sem os seus dados. A demo é uma live falsa de 3 minutos com picos de
voz em horários conhecidos, eventos de LoL e um histórico inventado de 6 lives revisadas e
publicadas (para o aprendizado ter o que mostrar).

## Antes da entrevista (uns 5 minutos, uma vez)

```powershell
uv run clipador demo --do-zero      # a live falsa e o histórico, do zero (~15 s)
uv run clipador processar --demo    # áudio, transcrição, momentos, 12 clipes e títulos (~2 min)
cd frontend; npm ci; npm run build; cd ..   # a interface (só na primeira vez)
```

Deixe abertos um terminal com letra grande e o navegador com `uv run clipador web --demo`.
A revisão da live falsa fica para fazer ao vivo; o histórico já vem revisado.

## O roteiro

| Tempo | O que mostrar | O que dizer |
|---|---|---|
| 0:00–0:30 | O README (o GIF da revisão) | "Faço live de LoL 3 h por dia. Cortar os melhores momentos à mão levava horas. O Clipador grava, acha os momentos de madrugada, e de manhã eu só reviso pelo teclado. Nada é publicado sem a minha aprovação." |
| 0:30–1:30 | `uv run clipador momentos --demo` | "Três detectores olham a live, cada um um plugin: picos da minha voz (a faixa do microfone, separada no OBS), expressões na transcrição do Whisper e eventos do LoL, coletados pela API local do jogo e alinhados ao segundo do vídeo. Cada um dá um sinal de 0 a 1, e a nota é uma média ponderada. Na demo, ele acha exatamente os 4 picos que eu plantei." |
| 1:30–3:00 | A revisão no navegador | Aprove um momento com `A`, recuse outro com `R`, desfaça com `U`; troque o formato com `1` `2` `3` (horizontal, vertical com fundo desfocado, vertical com a câmera em cima); `Tab` nos títulos da IA local (Ollama, sem custo); arraste a alça da linha do tempo. "Ao aprovar, o envio espera 10 minutos: dá para desfazer sem nada sair do PC. Depois, o vídeo sobe privado e agendado; aqui o YouTube é simulado." O momento aprovado mostra as publicações "na fila"; rode `uv run clipador processar --demo` num terminal e recarregue: viram "enviado, vai ao ar…" (YouTube) e "na pasta" (TikTok). |
| 3:00–4:00 | Estatísticas (aprendizado) e Engajamento | "Com 100 decisões, uma regressão logística sugere os pesos da nota a partir das minhas aprovações: aqui ela descobriu que eu aprovo pela fala (0,25 → 0,62), e a precisão nos de maior nota sobe de 56% para 65%, medida em decisões que o treino não viu. Mas o objetivo é engajamento: com 50 cortes publicados, o mesmo modelo aprende o que o público assiste. Na demo, o público prefere o LoL, justamente o que eu menos aprovo; a nota final mistura os dois." |
| 4:00–5:00 | O código: `tests/test_arquitetura.py`, um ADR, a CI | "São quatro partes com uma direção de dependência só, e um teste confere que a API nunca importa as integrações, nem o backend lê as chaves. A fila é retomável em SQLite: se a luz cair, continua de onde parou. As decisões estão nos ADRs. Os números da demo são inventados; as limitações estão no README." |

## Perguntas prováveis

- **Por que SQLite e não PostgreSQL?** Um usuário, um PC, nenhum servidor: o SQLite em
  modo WAL dá conta, faz backup com a própria API dele e não precisa de nada instalado
  ([ADR 0002](adr/0002-sqlite-e-nao-postgres.md)).
- **Por que regressão logística e não algo mais forte?** Com 100 a 200 exemplos, um modelo
  maior decoraria os dados. A logística tem um coeficiente por sinal, que vira peso da nota e
  dá para explicar; a comparação é com validação cruzada ([ADR 0009](adr/0009-regressao-logistica.md)).
- **E se o detector errar?** É por isso que a revisão existe: ele só sugere. As recusas viram
  dado para o próximo treino.
- **Como garante que nada é publicado sem querer?** Só uma decisão aprovada cria publicação; o
  envio espera 10 minutos, e o cancelamento é atômico mesmo se o serviço pegar a tarefa no mesmo
  instante ([ADR 0010](adr/0010-publicacao-agendada-com-tempo-para-desfazer.md)).
- **Por que o TikTok não posta sozinho?** O post direto exige uma auditoria que, pelas
  diretrizes do TikTok, não aceita ferramentas para as próprias contas. O app manda o vídeo
  para uma pasta ou como rascunho ([ADR 0011](adr/0011-tiktok-pasta-e-rascunho.md)).
- **O que faria diferente?** Medir antes o que o público quer: comecei pelo meu gosto, e o
  engajamento só entrou no fim. Com dados reais, a mistura entre os dois é a parte que mais
  vale ajustar.

## Se algo der errado

- A interface não abre: `uv run clipador web --demo` sem a interface construída abre a
  documentação interativa da API (`/docs`), que também permite revisar.
- O processamento demora: os momentos e o aprendizado (`clipador momentos --demo`,
  `clipador aprender --demo`, `clipador engajamento --demo`) funcionam no terminal.
- Voltar ao começo: `uv run clipador demo --do-zero` recria tudo em segundos (o processamento
  leva mais ~2 minutos).
