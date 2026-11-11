# ADR 0009: Regressão logística para aprender os pesos da nota

- **Status:** aceita
- **Data:** 2026-10-26

## Contexto

A nota de um momento (etapa 8) é uma média ponderada das forças dos detectores (volume da
voz, fala, LoL), com pesos fixos no config (0,5, 0,25 e 0,25). Cada aprovação e recusa da
revisão (etapas 12 e 13) fica salva. A etapa 14 usa essas decisões para sugerir pesos mais
parecidos com o seu gosto.

Restrições:

- Poucos dados: umas 15 decisões por live. Com 100 decisões (o mínimo do roteiro),
  são algumas semanas de lives.
- Poucas entradas: hoje, 3 sinais, cada um de 0 a 1.
- O resultado precisa virar pesos que a nota usa (números ≥ 0, um por detector) e ser
  explicável: você vê os números e decide se aceita.

## Decisão

Regressão logística (scikit-learn), com as forças dos detectores como entradas e
"aprovou?" como saída.

- Cada coeficiente diz o quanto aquele sinal aumenta a chance de você aprovar. Como as
  entradas estão todas de 0 a 1, os coeficientes são comparáveis entre si e viram os pesos:
  um negativo vira 0 (numa média ponderada, peso negativo não faz sentido), e os aprendidos
  dividem a soma que esses detectores já tinham.
- `class_weight="balanced"`: se você aprova quase tudo, "aprovar tudo" não pode parecer um
  bom modelo.
- Comparação justa: validação cruzada em 5 partes, com a mesma divisão sempre. Os pesos
  são aprendidos com 4 partes e medidos na quinta, que o modelo não viu. A medida é a
  precisão nos momentos de maior nota (a metade de cima de cada dia), com os pesos atuais e
  com os sugeridos, nas mesmas decisões.
- Nada muda sozinho: a sugestão só vale depois de aceita, como uma nova versão na
  tabela `PesosNota`, que a próxima detecção usa.

## Alternativas consideradas

- Média dos sinais nos aprovados contra nos recusados (o que o gráfico de sinais já
  mostra): fácil de entender, mas não separa sinais que andam juntos (um grito costuma vir
  com uma fala acelerada) e não tem uma forma justa de virar peso.
- Árvore de decisão ou gradient boosting: aprendem combinações ("grito E pentakill"),
  mas precisam de muito mais que 100 exemplos para não decorar os dados, e não dão um peso
  por sinal: a nota teria de virar o próprio modelo, e deixaria de ser explicável.
- Rede neural: dados de menos e explicação de menos para o problema.
- Ajustar os pesos à mão: é o que o config já permite; o objetivo é medir, não chutar.

## Consequências

- A nota continua sendo uma média ponderada simples, que dá para explicar numa frase.
- Limitação: só os momentos que você revisou têm decisão, e quase todos vêm dos de
  maior nota de cada dia. O modelo aprende entre os momentos que o detector já achou bons,
  não sobre a live inteira: um tipo de momento que o detector nunca sugere nunca vira
  exemplo.
- Um detector indisponível numa live (ex.: o do LoL numa live de outro jogo) entra como
  força 0 no treino, o que o deixa um pouco mais fraco do que é. Com mais jogos, vale
  treinar só nas lives em que ele existe.
- A precisão no topo tem um máximo menor que 100% num dia com poucas aprovações (ver
  `aprendizado/metricas.py`): ela serve para comparar pesos, não como nota absoluta.
- A etapa 17 reaproveita o mesmo mecanismo com o engajamento do público como resposta.
