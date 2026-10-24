import { useEffect, useState } from "react";
import { api } from "../api";
import type { Status, Tarefa } from "../tipos";

const NOMES_DAS_SITUACOES: Record<string, string> = {
  pendente: "Pendentes",
  em_andamento: "Em andamento",
  feita: "Feitas",
  erro: "Com erro",
};
const ATUALIZAR_A_CADA_MS = 5000;

function horaLocal(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function descrever(t: Tarefa): string {
  const alvo = [
    t.gravacao_id !== null ? `gravação ${t.gravacao_id}` : null,
    t.momento_id !== null ? `momento ${t.momento_id}` : null,
  ].filter(Boolean);
  return `#${t.id} ${t.tipo}${alvo.length ? ` (${alvo.join(", ")})` : ""}`;
}

/** Fila e status: o que está processando, o que deu erro e a cota do dia. */
export function Fila() {
  const [status, setStatus] = useState<Status | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    let ativo = true;
    const carregar = () =>
      api
        .status()
        .then((s) => {
          if (!ativo) return;
          setStatus(s);
          setErro(null);
        })
        .catch((e: Error) => ativo && setErro(e.message));
    carregar();
    const relogio = window.setInterval(carregar, ATUALIZAR_A_CADA_MS);
    return () => {
      ativo = false;
      window.clearInterval(relogio);
    };
  }, []);

  if (!status) return <main className="p-8">{erro ?? "Carregando…"}</main>;
  const { cota_youtube: cota } = status;

  return (
    <main className="mx-auto grid max-w-[1100px] gap-6 p-4">
      {erro && <p className="text-[var(--erro)]">{erro} (mostrando os últimos dados)</p>}
      <section aria-label="Fila de tarefas" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Object.entries(NOMES_DAS_SITUACOES).map(([situacao, nome]) => (
          <div key={situacao} className="rounded-lg bg-[var(--superficie)] p-4">
            <div className="text-sm text-[var(--texto-fraco)]">{nome}</div>
            <div
              className={`text-3xl font-bold ${
                situacao === "erro" && status.fila[situacao] ? "text-[var(--erro)]" : ""
              }`}
            >
              {status.fila[situacao] ?? 0}
            </div>
          </div>
        ))}
      </section>

      <section className="rounded-lg bg-[var(--superficie)] p-4">
        <h2 className="mb-2 font-semibold">Processando agora</h2>
        {status.em_andamento.length === 0 ? (
          <p className="text-sm text-[var(--texto-fraco)]">Nada. (A fila roda no serviço.)</p>
        ) : (
          <ul className="text-sm">
            {status.em_andamento.map((t) => (
              <li key={t.id}>
                {descrever(t)} · desde {horaLocal(t.atualizada_em)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg bg-[var(--superficie)] p-4">
        <h2 className="mb-2 font-semibold">Erros</h2>
        {status.erros.length === 0 ? (
          <p className="text-sm text-[var(--texto-fraco)]">Nenhum erro. ✓</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {status.erros.map((t) => (
              <li key={t.id}>
                <span className="text-[var(--erro)]">✗ {descrever(t)}</span> ·{" "}
                {horaLocal(t.atualizada_em)}
                <div className="text-[var(--texto-2)]">{t.ultimo_erro}</div>
                <code className="text-xs text-[var(--texto-fraco)]">
                  depois de corrigir: clipador refazer {t.id}
                </code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg bg-[var(--superficie)] p-4">
        <h2 className="mb-3 font-semibold">Cota do YouTube hoje</h2>
        <Medidor nome="Envios de vídeo" usado={cota.envios} limite={cota.limite_envios} />
        <Medidor
          nome="Outras chamadas (unidades)"
          usado={cota.unidades}
          limite={cota.limite_unidades}
        />
        <p className="mt-2 text-xs text-[var(--texto-fraco)]">
          Renova em {horaLocal(cota.renova_em)} (meia-noite no horário do Pacífico). O que
          passar do limite espera na fila.
        </p>
      </section>
    </main>
  );
}

function Medidor({ nome, usado, limite }: { nome: string; usado: number; limite: number }) {
  const fracao = Math.min(usado / limite, 1);
  return (
    <div className="mb-2">
      <div className="flex justify-between text-sm">
        <span>{nome}</span>
        <span className="tabular-nums">
          {usado.toLocaleString("pt-BR")} de {limite.toLocaleString("pt-BR")}
        </span>
      </div>
      <div
        className="mt-1 h-2 rounded bg-[var(--grade)]"
        role="meter"
        aria-label={nome}
        aria-valuenow={usado}
        aria-valuemin={0}
        aria-valuemax={limite}
      >
        <div
          className={`h-2 rounded ${fracao >= 0.9 ? "bg-[var(--aviso)]" : "bg-[var(--serie-1)]"}`}
          style={{ width: `${fracao * 100}%` }}
        />
      </div>
    </div>
  );
}
