# ADR 0005: Detectores devolvem sinais, e a nota monta os momentos

- **Status:** aceita
- **Data:** 2026-10-19

## Contexto

A etapa 8 acha os melhores momentos de cada live. O planejamento previa detectores como
plugins com a interface `Detector.detectar(gravacao) -> list[Momento]`: cada detector
devolveria momentos prontos.

Mas um momento bom quase sempre junta **vários aspectos** ao mesmo tempo: o grito (volume
da voz), o "não acredito!" (transcrição) e o pentakill (evento do LoL). Se cada detector
devolvesse momentos, cada um teria a sua própria janela e a sua própria nota para a mesma
jogada, e alguém ainda teria de juntar e desempatar esses momentos.

## Decisão

Cada detector devolve **sinais**, e não momentos:

```python
Detector.detectar(entrada) -> list[Sinal]   # Sinal = trecho + força (0 a 1) + motivo
```

Quem junta os sinais em momentos (sinais próximos viram um só), define a janela do clipe
(15 a 60 s) e dá a nota é um passo separado, `detectores/nota.py`:

```
nota = 100 × Σ(peso × maior força do detector no momento) / Σ(pesos dos detectores disponíveis)
```

Cada detector também diz se está **disponível** na gravação (ex.: o do LoL, numa live de
outro jogo, não está). Um detector indisponível sai da média, para a nota máxima continuar
sendo 100.

## Alternativas consideradas

- **Cada detector devolve momentos** (o plano original): duplicaria a lógica de janela em
  todos os detectores e exigiria uma regra para fundir momentos de detectores diferentes.
- **Um detector só, com tudo dentro**: mais simples no começo, mas acrescentar outro jogo
  ou o chat da Twitch exigiria mexer no código de todos os sinais.

## Consequências

- Acrescentar um detector continua sendo só um plugin: uma classe com `nome`,
  `disponivel` e `detectar`, registrada em `detectores/detectar.py` (`DETECTORES`), e uma
  seção em `[deteccao]` no `config.toml`.
- Cada momento guarda a força de cada detector (`Momento.sinais`). É disso que a etapa 14
  precisa para aprender os pesos com as suas aprovações (regressão logística).
- A janela do clipe e a nota ficam num lugar só, fáceis de testar e de ajustar pela etapa 14.
