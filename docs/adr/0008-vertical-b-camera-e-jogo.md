# ADR 0008: Vertical B com a câmera em cima e o jogo embaixo

- **Status:** aceita
- **Data:** 2026-10-24

## Contexto

O vertical A (etapa 9) mostra a tela inteira da live, pequena no meio do celular, com o
fundo desfocado. O vertical B devia **aproximar**, para o clipe ocupar a tela do celular.
Numa gravação 1920×1080, um vertical 9:16 com a altura inteira corresponde a uma faixa de
608×1080.

O conteúdo da live é humor e reação (a voz do streamer é o sinal principal da nota, ADR
0005). Hoje a live não tem webcam, mas a compra de uma está nos planos.

## Decisão

- **Com câmera configurada** (`[cortes.vertical_b] camera`, um retângulo em pixels da
  gravação): a câmera ocupa o terço de cima (1080×640) e o centro do jogo os dois terços de
  baixo (1080×1280). A legenda fica na emenda entre os dois, para não cobrir o meio do jogo,
  onde está a ação.
- **Sem câmera** (hoje): o vertical B é só o centro do jogo, ampliado (1080×1920), com a
  legenda na altura de sempre.
- O recorte do jogo tem a proporção do espaço que vai ocupar e a altura inteira da
  gravação, centrado em `centro_x` (padrão: o meio). Ele é escrito com as medidas do
  próprio vídeo (`iw`, `ih`) e encosta na borda em vez de sair da tela.
- `clipador calibrar` salva um quadro da live com uma grade e as regiões desenhadas, para
  achar os números da câmera sem adivinhar. Uma câmera fora da tela da gravação é erro
  claro (falha definitiva), e não 3 tentativas.
- O vertical B entra nos formatos padrão, e a revisão escolhe qual vertical aprovar.

## Alternativas consideradas

- **Só o recorte fixo no centro**: o mais simples, mas sem a reação, que é o forte do
  conteúdo. Continua existindo como o caso "sem câmera".
- **Centro + minimapa do LoL**: dá contexto para quem joga, mas é específico de um jogo (o
  app não pode depender de um só) e ocupa parte da imagem.
- **Seguir a ação** (visão computacional): o resultado mais profissional, mas complexo, e
  usaria a placa de vídeo, já ocupada com o Whisper. Fica para o futuro (etapa 19).

## Consequências

- Quando a webcam chegar: `clipador calibrar`, pôr o retângulo no `config.toml` e, para as
  lives já cortadas, `clipador refazer` ou um ajuste na revisão.
- Se a câmera mudar de lugar na cena do OBS, o config precisa mudar junto.
- Uma cena sem a câmera (ex.: tela de espera) ainda mostra o canto da cena no terço de
  cima. Detectar a câmera na imagem fica para depois, se isso aparecer nas lives.
- Cada momento ganha mais um clipe (~14 s de processador por 20 s de vídeo), de madrugada.
- A demo tem uma "câmera" falsa no canto, já configurada, para mostrar o layout.
