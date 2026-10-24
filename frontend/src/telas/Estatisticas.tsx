import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import type { Estatisticas as Dados } from "../tipos";

// Cores das séries: azul e laranja da paleta de referência no tema escuro (validadas para
// daltonismo e contraste com o fundo #1a1a19). Os textos ficam nas cores de texto.
const SERIES = [
  { chave: "aprovados", nome: "Aprovados", cor: "var(--serie-1)" },
  { chave: "recusados", nome: "Recusados", cor: "var(--serie-2)" },
] as const;
const EIXO = { stroke: "var(--texto-fraco)", fontSize: 12, tickLine: false };
const NOMES_DOS_DETECTORES: Record<string, string> = {
  volume_voz: "Volume da voz",
  fala: "Fala",
  lol: "Eventos do LoL",
};

// A legenda fica na cor do texto: quem diz qual é a série é o quadradinho colorido ao lado.
const textoDaLegenda = (nome: string) => <span style={{ color: "var(--texto-2)" }}>{nome}</span>;

function porcento(valor: number | null): string {
  return valor === null ? "—" : `${Math.round(valor * 100)}%`;
}

function segundos(valor: number | null): string {
  return valor === null ? "—" : `${valor > 0 ? "+" : ""}${valor.toFixed(1)} s`;
}

/** Estatísticas: a precisão do detector e o que cada sinal diz. Engajamento: etapa 17. */
export function Estatisticas() {
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    api
      .estatisticas()
      .then(setDados)
      .catch((e: Error) => setErro(e.message));
  }, []);

  if (!dados) return <main className="p-8">{erro ?? "Carregando…"}</main>;
  const decididos = dados.aprovados + dados.recusados;
  const sinais = dados.sinais.map((s) => ({
    ...s,
    nome: NOMES_DOS_DETECTORES[s.detector] ?? s.detector,
  }));

  return (
    <main className="mx-auto grid max-w-[1100px] gap-6 p-4">
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Resumo">
        <Numero nome="Precisão do detector" valor={porcento(dados.precisao)}>
          aprovados ÷ decididos ({decididos})
        </Numero>
        <Numero nome="Momentos" valor={String(dados.momentos)}>
          {dados.momentos - decididos} sem decisão
        </Numero>
        <Numero nome="Aprovados" valor={String(dados.aprovados)} />
        <Numero nome="Recusados" valor={String(dados.recusados)} />
      </section>

      <Grafico
        titulo="Aprovações por faixa de nota"
        explicacao="Se a nota funciona, as faixas altas têm mais aprovações."
        dados={dados.por_nota}
        eixoX="faixa"
      />
      <Grafico titulo="Por dia de live" dados={dados.por_dia} eixoX="dia" />

      <section className="rounded-lg bg-[var(--superficie)] p-4">
        <h2 className="font-semibold">Força média de cada sinal (0 a 1)</h2>
        <p className="mb-3 text-sm text-[var(--texto-fraco)]">
          Um sinal forte nos aprovados e fraco nos recusados separa bem o bom do ruim. Com 100
          decisões, a etapa 14 sugere novos pesos a partir disto.
        </p>
        {sinais.length === 0 ? (
          <p className="text-sm text-[var(--texto-fraco)]">Ainda sem decisões.</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={sinais} barGap={2} margin={{ left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--grade)" />
              <XAxis dataKey="nome" {...EIXO} />
              <YAxis domain={[0, 1]} {...EIXO} />
              <Tooltip
                contentStyle={{ background: "var(--fundo)", border: "1px solid var(--borda)" }}
                formatter={(v) => (typeof v === "number" ? v.toFixed(2) : "—")}
              />
              <Legend formatter={textoDaLegenda} />
              {SERIES.map((s) => (
                <Bar
                  key={s.chave}
                  dataKey={s.chave}
                  name={s.nome}
                  fill={s.cor}
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <Numero nome="Ajuste médio do início" valor={segundos(dados.ajuste_medio_inicio_s)}>
          negativo = você puxou para mais cedo
        </Numero>
        <Numero nome="Ajuste médio do fim" valor={segundos(dados.ajuste_medio_fim_s)}>
          a etapa 14 usa isto para mudar a janela padrão
        </Numero>
      </section>
      <p className="text-sm text-[var(--texto-fraco)]">
        Engajamento (views, curtidas, retenção) chega na etapa 17, com os cortes publicados.
      </p>
    </main>
  );
}

function Numero({
  nome,
  valor,
  children,
}: {
  nome: string;
  valor: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-[var(--superficie)] p-4">
      <div className="text-sm text-[var(--texto-fraco)]">{nome}</div>
      <div className="text-3xl font-bold">{valor}</div>
      {children && <div className="text-xs text-[var(--texto-fraco)]">{children}</div>}
    </div>
  );
}

function Grafico({
  titulo,
  explicacao,
  dados,
  eixoX,
}: {
  titulo: string;
  explicacao?: string;
  dados: object[];
  eixoX: string;
}) {
  return (
    <section className="rounded-lg bg-[var(--superficie)] p-4">
      <h2 className="font-semibold">{titulo}</h2>
      {explicacao && <p className="mb-3 text-sm text-[var(--texto-fraco)]">{explicacao}</p>}
      {dados.length === 0 ? (
        <p className="text-sm text-[var(--texto-fraco)]">Ainda sem dados.</p>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={dados} margin={{ left: -16 }}>
            <CartesianGrid vertical={false} stroke="var(--grade)" />
            <XAxis dataKey={eixoX} {...EIXO} />
            <YAxis allowDecimals={false} {...EIXO} />
            <Tooltip
              cursor={{ fill: "var(--grade)" }}
              contentStyle={{ background: "var(--fundo)", border: "1px solid var(--borda)" }}
            />
            <Legend formatter={textoDaLegenda} />
            {/* Empilhadas: a altura total é quantos você decidiu naquela faixa. A borda da
                cor do fundo separa os segmentos. */}
            {SERIES.map((s, i) => (
              <Bar
                key={s.chave}
                dataKey={s.chave}
                name={s.nome}
                stackId="decisoes"
                fill={s.cor}
                stroke="var(--superficie)"
                strokeWidth={2}
                maxBarSize={24}
                radius={i === SERIES.length - 1 ? [4, 4, 0, 0] : 0}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}
