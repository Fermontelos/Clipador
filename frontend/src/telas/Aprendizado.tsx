import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, ErroDaApi } from "../api";
import { br, porcento } from "../numeros";
import type { Aprendizado as Dados } from "../tipos";

const NOMES: Record<string, string> = {
  volume_voz: "Volume da voz",
  fala: "Fala",
  lol: "Eventos do LoL",
};
const EIXO = { stroke: "var(--texto-fraco)", fontSize: 12, tickLine: false };

function dataCurta(dia: string): string {
  const [, mes, d] = dia.split("-");
  return `${d}/${mes}`;
}

/**
 * O que as suas decisões ensinam (etapa 14): a precisão ao longo do tempo e a sugestão de
 * pesos e janela, que só vale depois que você aceita.
 */
export function Aprendizado() {
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    api
      .aprendizado()
      .then(setDados)
      .catch((e: Error) => setErro(e.message));
  }, []);

  async function aceitar() {
    if (!dados) return;
    setOcupado(true);
    try {
      const novos = await api.aceitarAprendizado(dados.decisoes);
      setDados(novos);
      setAviso(
        `Versão ${novos.versao_atual} aceita: vale a partir da próxima detecção (os momentos já ` +
          "detectados ficam como estão).",
      );
    } catch (e) {
      setErro((e as Error).message);
      if (e instanceof ErroDaApi && e.status === 409) {
        // Chegaram decisões novas: a sugestão na tela é velha. Mostra a de agora.
        api.aprendizado().then(setDados).catch(() => {});
      }
    } finally {
      setOcupado(false);
      setConfirmando(false);
    }
  }

  if (!dados) {
    return (
      <section className="rounded-lg bg-[var(--superficie)] p-4">
        {erro ?? "Carregando o aprendizado…"}
      </section>
    );
  }

  const nomes = Object.keys({ ...dados.pesos_atuais, ...(dados.pesos_novos ?? {}) }).sort();
  const [antes, depois] = dados.janela_atual;
  const temSugestao = dados.pesos_novos !== null || dados.janela_nova !== null;

  return (
    <section className="flex flex-col gap-4 rounded-lg bg-[var(--superficie)] p-4" aria-label="Aprendizado">
      <div>
        <h2 className="font-semibold">Aprendizado</h2>
        <p className="text-sm text-[var(--texto-fraco)]">
          {dados.decisoes} decisões ({dados.aprovadas} aprovadas). Com {dados.minimo_para_pesos} ou
          mais, as suas decisões sugerem novos pesos para a nota. Nada muda até você aceitar.
        </p>
      </div>

      <div>
        <h3 className="text-sm font-semibold">Precisão por dia (aprovados ÷ decididos)</h3>
        {dados.precisao_por_dia.length === 0 ? (
          <p className="text-sm text-[var(--texto-fraco)]">Ainda sem decisões.</p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={dados.precisao_por_dia} margin={{ left: -16, right: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grade)" />
              <XAxis dataKey="dia" tickFormatter={dataCurta} {...EIXO} />
              <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${v * 100}%`} {...EIXO} />
              <Tooltip
                contentStyle={{ background: "var(--fundo)", border: "1px solid var(--borda)" }}
                labelFormatter={(dia) => `Live de ${dataCurta(String(dia))}`}
                formatter={(v, _nome, item) => [
                  `${porcento(typeof v === "number" ? v : null)} (${item.payload.aprovados} de ${item.payload.decididos})`,
                  "Precisão",
                ]}
              />
              <Line
                // Retas entre os dias: cada ponto é um dia, e uma curva inventaria valores
                // entre eles.
                type="linear"
                dataKey="precisao"
                stroke="var(--serie-1)"
                strokeWidth={2}
                dot={{ r: 4, fill: "var(--serie-1)", stroke: "var(--superficie)", strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-sm font-semibold">
            Pesos da nota {dados.versao_atual ? `(versão ${dados.versao_atual})` : "(do config)"}
          </h3>
          <table className="w-full text-sm tabular-nums">
            <thead className="text-[var(--texto-fraco)]">
              <tr>
                <th className="text-left font-normal">Sinal</th>
                <th className="text-right font-normal">Atual</th>
                {dados.pesos_novos && <th className="text-right font-normal">Sugerido</th>}
              </tr>
            </thead>
            <tbody>
              {nomes.map((nome) => (
                <tr key={nome}>
                  <td>{NOMES[nome] ?? nome}</td>
                  <td className="text-right">{br(dados.pesos_atuais[nome] ?? 0, 2, true)}</td>
                  {dados.pesos_novos && (
                    <td className="text-right font-semibold">
                      {br(dados.pesos_novos[nome] ?? 0, 2, true)}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {dados.pesos_novos ? (
            <p className="mt-2 text-sm" data-testid="comparacao">
              Nos de maior nota, em decisões que o treino não viu: você aprovaria{" "}
              <b>{porcento(dados.precisao_antes)}</b> com os pesos atuais e{" "}
              <b>{porcento(dados.precisao_depois)}</b> com os sugeridos.
            </p>
          ) : (
            <p className="mt-2 text-sm text-[var(--texto-fraco)]">{dados.sem_pesos_porque}</p>
          )}
        </div>
        <div>
          <h3 className="mb-1 text-sm font-semibold">Janela do corte</h3>
          <p className="text-sm">
            Hoje: {br(antes)} s antes e {br(depois)} s depois do sinal.
          </p>
          {dados.janela_nova ? (
            <p className="mt-2 text-sm" data-testid="janela">
              Sugerida: <b>{br(dados.janela_nova.segundos_antes)} s antes</b> e{" "}
              <b>{br(dados.janela_nova.segundos_depois)} s depois</b>. Nos {dados.janela_nova.aprovados}{" "}
              aprovados, você moveu o início {sinal(dados.janela_nova.media_inicio_s)} e o fim{" "}
              {sinal(dados.janela_nova.media_fim_s)}, em média.
            </p>
          ) : (
            <p className="mt-2 text-sm text-[var(--texto-fraco)]">{dados.sem_janela_porque}</p>
          )}
        </div>
      </div>

      {temSugestao &&
        (confirmando ? (
          <div className="flex flex-wrap items-center gap-2 text-sm" role="group">
            <span>Aceitar? Vale a partir da próxima detecção.</span>
            <button
              onClick={() => void aceitar()}
              disabled={ocupado}
              className="rounded-md bg-[var(--bom)] px-3 py-1 font-semibold text-black disabled:opacity-40"
            >
              ✓ Confirmar
            </button>
            <button
              onClick={() => setConfirmando(false)}
              className="rounded-md border border-[var(--borda)] px-3 py-1"
            >
              Cancelar
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirmando(true)}
            className="self-start rounded-md bg-[var(--destaque)] px-3 py-2 font-semibold text-black"
          >
            Aceitar a sugestão
          </button>
        ))}
      {aviso && (
        <p role="status" className="text-sm text-[var(--bom)]">
          ✓ {aviso}
        </p>
      )}
      {erro && (
        <p role="alert" className="text-sm text-[var(--erro)]">
          {erro}
        </p>
      )}

      {dados.versoes.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-[var(--texto-2)]">
            Versões aceitas ({dados.versoes.length})
          </summary>
          <ul className="mt-2 flex flex-col gap-1 tabular-nums">
            {dados.versoes.map((v) => (
              <li key={v.versao}>
                Versão {v.versao}, {new Date(v.criada_em).toLocaleDateString("pt-BR")}:{" "}
                {v.origem === "engajamento"
                  ? `pelo engajamento, ${v.decisoes} cortes`
                  : `${v.decisoes} decisões`}
                {v.precisao !== null && `, precisão ${porcento(v.precisao_antes)} → ${porcento(v.precisao)}`}
                {v.segundos_antes !== null &&
                  `, janela ${br(v.segundos_antes)} s / ${br(v.segundos_depois ?? 0)} s`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function sinal(segundos: number): string {
  if (segundos === 0) return "0 s";
  return `${br(Math.abs(segundos))} s ${segundos < 0 ? "para mais cedo" : "para mais tarde"}`;
}
