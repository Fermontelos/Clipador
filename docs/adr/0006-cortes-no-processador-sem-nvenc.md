# ADR 0006: Cortar no processador quando o NVENC não funcionar

- **Status:** aceita
- **Data:** 2026-10-20

## Contexto

O plano previa codificar os clipes na placa de vídeo, com o encoder NVENC (`h264_nvenc`)
da GTX 1050 Ti, para deixar o processador livre.

Ao testar na etapa 9, o FFmpeg 9.0.2 (instalado pelo winget) recusou o NVENC:

```
Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0
The minimum required Nvidia driver for nvenc is 610.00 or newer
```

O FFmpeg 9 é compilado com a versão 13.1 da API do NVENC, que só vem no driver 610 ou mais
novo. A GTX 1050 Ti (arquitetura Pascal) parou na série 580 de drivers, que entrega a API
13.0. O mesmo erro foi relatado por outros usuários com o FFmpeg 9 e o driver 580 (fórum
do Arch Linux), e o Fedora decidiu manter as bibliotecas 13.0 justamente para as placas da
série 10 continuarem codificando.

## Decisão

O encoder passa a ser escolhido sozinho (`[cortes] encoder = "auto"`): o app testa o NVENC
uma vez e, se ele não funcionar, corta no processador com o libx264 (preset `medium`,
CRF 21), com um aviso no log. Também dá para forçar `h264_nvenc` ou `libx264`.

## Alternativas consideradas

- Instalar um FFmpeg compilado com a API 13.0 (por exemplo, da linha 8.x) só para os
  cortes: volta a usar a placa, mas adiciona um segundo FFmpeg para manter e conferir a
  cada atualização. Fica como opção futura: o app já usa o NVENC sozinho se ele funcionar.
- Trocar de placa de vídeo: fora do escopo de um projeto só com ferramentas gratuitas.

## Consequências

- Medido no processador (6 núcleos): 20 s de clipe vertical em 1080p levam ~14 s. Uma live
  com 30 momentos em 2 formatos fica entre 30 e 60 min de madrugada, quando a velocidade
  não importa.
- A regra "uma tarefa de GPU por vez" fica mais simples: só o Whisper usa a placa.
- Os testes cortam sempre no processador, o que também funciona na CI (sem placa de vídeo).
