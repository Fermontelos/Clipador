import { useEffect, useRef } from "react";
import type { Trecho } from "./corte";

interface Props {
  url: string;
  /** Em que segundo da live o arquivo do clipe começa (o início do último corte). */
  deslocamento_s: number;
  /** O trecho escolhido agora (pode ser diferente do cortado, durante um ajuste). */
  trecho: Trecho;
  /** O navegador só toca com som depois de uma tecla: até lá, o vídeo espera. */
  liberado: boolean;
  pausado: boolean;
  vertical: boolean;
  /** Falso enquanto há um recorte na fila: o arquivo ainda é o corte antigo, de início
   * desconhecido, então ele toca inteiro, sem repetir o trecho. */
  restringir: boolean;
  aoAndar: (segundo_da_live: number) => void;
}

/**
 * O vídeo do clipe, repetindo só o trecho escolhido. Mexer no início pula para ele; mexer
 * no fim pula para 3 s antes dele, para ver onde o clipe vai terminar.
 *
 * Um trecho maior que o arquivo (início mais cedo ou fim mais tarde que o corte atual) não
 * tem imagem: essa parte só aparece depois do novo corte (a linha do tempo avisa).
 */
export function Player(props: Props) {
  const { url, deslocamento_s, trecho, liberado, pausado, vertical, restringir, aoAndar } = props;
  const video = useRef<HTMLVideoElement>(null);
  const anterior = useRef<Trecho>(trecho);
  const noArquivo = (segundo: number) => Math.max(0, segundo - deslocamento_s);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    if (liberado && !pausado) v.play().catch(() => {}); // já tocando, ou arquivo sumiu
    else v.pause();
  }, [liberado, pausado, url]);

  useEffect(() => {
    const v = video.current;
    const antes = anterior.current;
    anterior.current = trecho;
    if (!v || !restringir) return;
    if (trecho.inicio_s !== antes.inicio_s) v.currentTime = noArquivo(trecho.inicio_s);
    else if (trecho.fim_s !== antes.fim_s)
      v.currentTime = noArquivo(Math.max(trecho.inicio_s, trecho.fim_s - 3));
  }, [trecho.inicio_s, trecho.fim_s]); // só quando o trecho muda (não a cada quadro)

  function andou() {
    const v = video.current;
    if (!v) return;
    const agora = v.currentTime + deslocamento_s;
    aoAndar(agora);
    if (!restringir) return;
    if (agora >= trecho.fim_s - 0.05 || agora < trecho.inicio_s - 0.3) {
      v.currentTime = noArquivo(trecho.inicio_s); // repete o trecho
    }
  }

  return (
    <video
      key={url}
      ref={video}
      src={url}
      className={`max-h-[62vh] rounded-lg bg-black ${vertical ? "aspect-[9/16]" : "w-full"}`}
      preload="auto"
      playsInline
      onLoadedMetadata={(e) => {
        if (restringir) e.currentTarget.currentTime = noArquivo(trecho.inicio_s);
      }}
      onTimeUpdate={andou}
      onEnded={(e) => {
        // O fim escolhido passa do fim do arquivo: volta para o início do trecho.
        e.currentTarget.currentTime = restringir ? noArquivo(trecho.inicio_s) : 0;
        if (liberado && !pausado) e.currentTarget.play().catch(() => {});
      }}
      data-testid="player"
    />
  );
}
