import { lazy, Suspense, useEffect, useState } from "react";
import { Revisao } from "./revisao/Revisao";
import { Fila } from "./telas/Fila";

// Os gráficos (Recharts) são a maior parte do código: só carregam ao abrir a tela deles,
// e a revisão abre mais rápido.
const Estatisticas = lazy(() =>
  import("./telas/Estatisticas").then((m) => ({ default: m.Estatisticas })),
);
const Engajamento = lazy(() =>
  import("./telas/Engajamento").then((m) => ({ default: m.Engajamento })),
);

// As 4 telas, cada uma com o próprio endereço (o FastAPI devolve o index para qualquer um).
// BASE: a pasta onde a interface está publicada ("" no clipador web; "/Clipador" na demo do
// GitHub Pages, que serve o site numa subpasta).
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
const DEMO_ESTATICA = import.meta.env.MODE === "estatica";
const telaDoEndereco = () => window.location.pathname.slice(BASE.length) || "/";

const TELAS = [
  { caminho: "/", nome: "Revisão" },
  { caminho: "/fila", nome: "Fila e status" },
  { caminho: "/estatisticas", nome: "Estatísticas" },
  { caminho: "/engajamento", nome: "Engajamento" },
] as const;

export function App() {
  const [caminho, setCaminho] = useState(telaDoEndereco);

  useEffect(() => {
    const voltar = () => setCaminho(telaDoEndereco());
    window.addEventListener("popstate", voltar);
    return () => window.removeEventListener("popstate", voltar);
  }, []);

  const atual = TELAS.find((t) => t.caminho === caminho) ?? TELAS[0];

  return (
    <div className="min-h-screen">
      {DEMO_ESTATICA && (
        <p className="bg-[var(--superficie)] px-4 py-2 text-center text-sm text-[var(--texto-2)]">
          Esta é a demo: os dados são inventados e o que você decidir fica só nesta aba. Aqui
          ajustar o corte não gera um vídeo novo, porque o navegador não tem FFmpeg. Aperte{" "}
          <kbd>?</kbd> para ver os atalhos.
        </p>
      )}
      <header className="flex items-center gap-6 border-b border-[var(--borda)] px-4 py-2">
        <span className="font-bold">Clipador</span>
        <nav className="flex gap-1" aria-label="Telas">
          {TELAS.map((t) => (
            <a
              key={t.caminho}
              href={BASE + t.caminho}
              aria-current={t === atual ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                window.history.pushState(null, "", BASE + t.caminho);
                setCaminho(t.caminho);
              }}
              className={`rounded-md px-3 py-1 text-sm ${
                t === atual ? "bg-[var(--superficie)] text-[var(--texto)]" : "text-[var(--texto-2)]"
              }`}
            >
              {t.nome}
            </a>
          ))}
        </nav>
        <span className="ml-auto text-xs text-[var(--texto-fraco)]">
          Nada é publicado sem a sua aprovação.
        </span>
      </header>
      {/* A revisão fica sempre montada (só escondida): ir ver a fila e voltar não perde o
          título que você estava editando nem a pilha do desfazer. */}
      <div hidden={atual.caminho !== "/"}>
        <Revisao visivel={atual.caminho === "/"} />
      </div>
      {atual.caminho === "/fila" && <Fila />}
      {atual.caminho === "/estatisticas" && (
        <Suspense fallback={<main className="p-8">Carregando…</main>}>
          <Estatisticas />
        </Suspense>
      )}
      {atual.caminho === "/engajamento" && (
        <Suspense fallback={<main className="p-8">Carregando…</main>}>
          <Engajamento />
        </Suspense>
      )}
    </div>
  );
}
