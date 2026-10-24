import { useRef } from "react";
import { type Limites, moverFim, moverInicio, relogio, type Trecho } from "./corte";

interface Props {
  trecho: Trecho;
  /** O trecho que existe no arquivo do clipe (o último corte). */
  cortado: Trecho;
  limites: Limites;
  /** Onde o vídeo está (null = desconhecido: o arquivo ainda é o corte antigo). */
  tempo_s: number | null;
  bloqueada: boolean;
  aoMudar: (trecho: Trecho) => void;
}

const FOLGA_S = 10; // quanto da live aparece antes e depois, para dar onde puxar

/** A linha do tempo embaixo do player: alças de início e fim para arrastar. */
export function LinhaDoTempo({ trecho, cortado, limites, tempo_s, bloqueada, aoMudar }: Props) {
  const barra = useRef<HTMLDivElement>(null);
  const de = Math.max(0, Math.min(trecho.inicio_s, cortado.inicio_s) - FOLGA_S);
  const ate = Math.min(
    limites.gravacao_s ?? Number.POSITIVE_INFINITY,
    Math.max(trecho.fim_s, cortado.fim_s) + FOLGA_S,
  );
  const largura = Math.max(ate - de, 1);
  const pct = (s: number) => `${((Math.min(Math.max(s, de), ate) - de) / largura) * 100}%`;

  function segundoNoPonto(x: number): number {
    const caixa = barra.current!.getBoundingClientRect();
    const fracao = Math.min(Math.max((x - caixa.left) / caixa.width, 0), 1);
    return Math.round((de + fracao * largura) * 10) / 10; // de 0,1 em 0,1 s
  }

  function arrastar(alca: "inicio" | "fim") {
    return (evento: React.PointerEvent<HTMLDivElement>) => {
      if (bloqueada) return;
      const alvo = evento.currentTarget;
      alvo.setPointerCapture(evento.pointerId);
      const mover = (e: PointerEvent) => {
        const s = segundoNoPonto(e.clientX);
        aoMudar(alca === "inicio" ? moverInicio(trecho, s, limites) : moverFim(trecho, s, limites));
      };
      const soltar = () => {
        alvo.removeEventListener("pointermove", mover);
        alvo.removeEventListener("pointerup", soltar);
      };
      alvo.addEventListener("pointermove", mover);
      alvo.addEventListener("pointerup", soltar);
    };
  }

  const fora = trecho.inicio_s < cortado.inicio_s - 0.05 || trecho.fim_s > cortado.fim_s + 0.05;
  const duracao = trecho.fim_s - trecho.inicio_s;

  return (
    <div className="select-none" aria-label="Linha do tempo do corte">
      <div ref={barra} className="relative h-10 rounded-md bg-[var(--grade)]">
        {/* O que existe no arquivo do clipe agora. */}
        <div
          className="absolute inset-y-2 rounded bg-[var(--trilho)]"
          style={{ left: pct(cortado.inicio_s), right: `calc(100% - ${pct(cortado.fim_s)})` }}
          title="Trecho do corte atual"
        />
        {/* O trecho escolhido. */}
        <div
          className="absolute inset-y-0 rounded-md border-2 border-[var(--destaque)] bg-[var(--destaque)]/15"
          style={{ left: pct(trecho.inicio_s), right: `calc(100% - ${pct(trecho.fim_s)})` }}
          data-testid="trecho"
        />
        {(["inicio", "fim"] as const).map((alca) => (
          <div
            key={alca}
            role="slider"
            aria-label={alca === "inicio" ? "Início do corte" : "Fim do corte"}
            aria-valuenow={alca === "inicio" ? trecho.inicio_s : trecho.fim_s}
            aria-valuetext={relogio(alca === "inicio" ? trecho.inicio_s : trecho.fim_s)}
            aria-disabled={bloqueada}
            onPointerDown={arrastar(alca)}
            className={`absolute inset-y-[-4px] w-3 -translate-x-1/2 rounded bg-[var(--destaque)] ${
              bloqueada ? "cursor-not-allowed opacity-40" : "cursor-ew-resize"
            }`}
            style={{ left: pct(alca === "inicio" ? trecho.inicio_s : trecho.fim_s) }}
          />
        ))}
        {tempo_s !== null && (
          <div
            className="pointer-events-none absolute inset-y-0 w-0.5 bg-[var(--texto)]"
            style={{ left: pct(tempo_s) }}
            data-testid="posicao"
          />
        )}
      </div>
      <div className="mt-1 flex justify-between text-xs tabular-nums text-[var(--texto-fraco)]">
        <span>{relogio(de)}</span>
        <span className="text-[var(--texto-2)]">
          {relogio(trecho.inicio_s)} – {relogio(trecho.fim_s)} · {duracao.toFixed(0)} s (de{" "}
          {limites.minimo_s} a {limites.maximo_s} s)
        </span>
        <span>{relogio(ate)}</span>
      </div>
      {fora && (
        <p className="mt-1 text-xs text-[var(--aviso)]" role="status">
          ⚠ Parte do trecho fica fora do corte atual: ela aparece no novo corte, depois de aprovar.
        </p>
      )}
    </div>
  );
}
