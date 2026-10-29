import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, ErroDaApi } from "../api";
import { br, porcento } from "../numeros";
import type { Desempenho, Engajamento as Dados } from "../tipos";

const NOMES_DOS_SINAIS: Record<string, string> = {
  volume_voz: "Volume da voz",
  fala: "Fala",
  lol: "Eventos do LoL",
};
const NOMES_DAS_REDES: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok" };
const EIXO = { stroke: "var(--texto-fraco)", fontSize: 12, tickLine: false };
const NO_GRAFICO = 10;

function numero(valor: number | null): string {
  return valor === null ? "—" : valor.toLocaleString("pt-BR");
}

function curto(texto: string | null, limite = 28): string {
  if (!texto) return "(sem título)";
  return texto.length > limite ? `${texto.slice(0, limite - 1)}…` : texto;
}

/**
 * O que o público mostrou (etapa 17): os cortes que mais engajaram, as hashtags e os tipos de
 * título que funcionam, YouTube × TikTok e a sugestão de pesos pelo engajamento.
 */
export function Engajamento() {
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    api
      .engajamento()
      .then(setDados)
      .catch((e: Error) => setErro(e.message));
  }, []);

  async function aceitar() {
    if (!dados) return;
    setOcupado(true);
    try {
      setDados(await api.aceitarEngajamento(dados.sugestao.cortes));
      setAviso(
        "Pesos aceitos: valem a partir da próxima detecção (os momentos já detectados ficam como estão).",
      );
    } catch (e) {
      setErro((e as Error).message);
      if (e instanceof ErroDaApi && e.status === 409) {
        // Chegaram métricas novas: a sugestão na tela é velha. Mostra a de agora.
        api.engajamento().then(setDados).catch(() => {});
      }
    } finally {
      setOcupado(false);
      setConfirmando(false);
    }
  }

  if (!dados) {
    return (
      <main className="p-4">
        <section className="rounded-lg bg-[var(--superficie)] p-4">
          {erro ?? "Carregando o engajamento…"}
        </section>
      </main>
    );
  }

  const s = dados.sugestao;
  // A rede no rótulo: o mesmo momento no YouTube e no TikTok tem o mesmo título, e o eixo
  // juntaria as duas barras numa linha só.
  const topo = dados.cortes.slice(0, NO_GRAFICO).map((c) => ({
    ...c,
    rotulo: `${curto(c.titulo, 24)} · ${NOMES_DAS_REDES[c.plataforma] ?? c.plataforma}`,
  }));
  const sinais = Object.keys(s.pesos_atuais).sort();
  const novos = s.pesos_novos;

  return (
    <main className="flex flex-col gap-4 p-4" aria-label="Engajamento">
      <section className="rounded-lg bg-[var(--superficie)] p-4">
        <h2 className="font-semibold">Engajamento</h2>
        <p className="text-sm text-[var(--texto-fraco)]">
          {dados.com_metricas} cortes com métricas, {dados.com_7_dias} com a coleta de 7 dias (a
          que entra no índice). Índice 1 = o vídeo típico do mesmo grupo no seu canal (a rede e, no YouTube, Short
          ou vídeo normal); 2 = o dobro.
          Engajamento também depende de horário, tendências e do algoritmo: com poucos vídeos, os
          números erram bastante.
        </p>
      </section>

      {dados.cortes.length === 0 ? (
        <section className="rounded-lg bg-[var(--superficie)] p-4 text-sm text-[var(--texto-fraco)]">
          Ainda sem cortes com 7 dias de métricas. Elas são coletadas sozinhas 24 h, 72 h e 7 dias
          depois de cada vídeo ir ao ar.
        </section>
      ) : (
        <>
          <section className="rounded-lg bg-[var(--superficie)] p-4">
            <h3 className="text-sm font-semibold">
              Os {topo.length} cortes que mais engajaram (índice)
            </h3>
            <ResponsiveContainer width="100%" height={Math.max(160, topo.length * 30)}>
              <BarChart data={topo} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid horizontal={false} stroke="var(--grade)" />
                <XAxis type="number" tickFormatter={(v: number) => br(v)} {...EIXO} />
                <YAxis type="category" dataKey="rotulo" width={200} {...EIXO} />
                <Tooltip
                  cursor={{ fill: "var(--grade)" }}
                  contentStyle={{ background: "var(--fundo)", border: "1px solid var(--borda)" }}
                  formatter={(v, _n, item) => [
                    `${br(Number(v), 2)} (${numero(item.payload.visualizacoes)} visualizações)`,
                    `Índice · ${NOMES_DAS_REDES[item.payload.plataforma] ?? item.payload.plataforma}`,
                  ]}
                  labelFormatter={(_r, itens) => itens[0]?.payload.titulo ?? ""}
                />
                <Bar dataKey="indice" fill="var(--serie-1)" radius={[0, 4, 4, 0]} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </section>

          <section className="rounded-lg bg-[var(--superficie)] p-4">
            <h3 className="mb-1 text-sm font-semibold">Todos os cortes, do maior índice ao menor</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm tabular-nums" aria-label="Cortes por engajamento">
                <thead className="text-[var(--texto-fraco)]">
                  <tr>
                    <th className="text-left font-normal">Corte</th>
                    <th className="text-left font-normal">Rede</th>
                    <th className="text-left font-normal">Momento</th>
                    <th className="text-right font-normal">Índice</th>
                    <th className="text-right font-normal">Visualizações</th>
                    <th className="text-right font-normal">Curtidas</th>
                    <th className="text-right font-normal">Comentários</th>
                    <th className="text-right font-normal">Compart.</th>
                    <th className="text-right font-normal">Retenção</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.cortes.map((c) => (
                    <tr key={c.publicacao_id}>
                      <td>
                        {c.link ? (
                          <a href={c.link} target="_blank" rel="noreferrer" className="underline">
                            {curto(c.titulo, 40)}
                          </a>
                        ) : (
                          curto(c.titulo, 40)
                        )}
                      </td>
                      <td>{NOMES_DAS_REDES[c.plataforma] ?? c.plataforma}</td>
                      <td>{c.tipo_de_momento ?? "—"}</td>
                      <td className="text-right font-semibold">{br(c.indice, 2, true)}</td>
                      <td className="text-right">{numero(c.visualizacoes)}</td>
                      <td className="text-right">{numero(c.curtidas)}</td>
                      <td className="text-right">{numero(c.comentarios)}</td>
                      <td className="text-right">{numero(c.compartilhamentos)}</td>
                      <td className="text-right">{porcento(c.retencao)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <TabelaDeDesempenho
              titulo="Hashtags"
              itens={dados.hashtags}
              minimo={dados.minimo_usos}
            />
            <TabelaDeDesempenho
              titulo="Tipos de título"
              itens={dados.tipos_de_titulo}
              minimo={dados.minimo_usos}
            />
            <section className="rounded-lg bg-[var(--superficie)] p-4">
              <h3 className="mb-1 text-sm font-semibold">Por tipo de momento (o sinal mais forte)</h3>
              <table className="w-full text-sm tabular-nums">
                <thead className="text-[var(--texto-fraco)]">
                  <tr>
                    <th className="text-left font-normal">Momento</th>
                    <th className="text-right font-normal">Cortes</th>
                    <th className="text-right font-normal">Índice médio</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.por_tipo_de_momento.map((g) => (
                    <tr key={g.nome}>
                      <td>{g.nome}</td>
                      <td className="text-right">{g.cortes}</td>
                      <td className="text-right">{br(g.indice_medio, 2, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section className="rounded-lg bg-[var(--superficie)] p-4">
              <h3 className="mb-1 text-sm font-semibold">YouTube × TikTok (medianas, 7 dias)</h3>
              <table className="w-full text-sm tabular-nums">
                <thead className="text-[var(--texto-fraco)]">
                  <tr>
                    <th className="text-left font-normal">Rede</th>
                    <th className="text-right font-normal">Cortes</th>
                    <th className="text-right font-normal">Visualizações</th>
                    <th className="text-right font-normal">Interação</th>
                    <th className="text-right font-normal">Retenção</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.por_rede.map((r) => (
                    <tr key={r.plataforma}>
                      <td>{NOMES_DAS_REDES[r.plataforma] ?? r.plataforma}</td>
                      <td className="text-right">{r.cortes}</td>
                      <td className="text-right">{numero(r.mediana_visualizacoes)}</td>
                      <td className="text-right">
                        {r.mediana_interacao === null ? "—" : `${br(r.mediana_interacao * 100)}%`}
                      </td>
                      <td className="text-right">{porcento(r.mediana_retencao)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-[var(--texto-fraco)]">
                Interação = (curtidas + comentários + compartilhamentos) ÷ visualizações. O TikTok
                não dá a retenção pela API.
              </p>
            </section>
          </div>
        </>
      )}

      <section className="flex flex-col gap-2 rounded-lg bg-[var(--superficie)] p-4" aria-label="Pesos pelo engajamento">
        <h3 className="text-sm font-semibold">A nota aprendendo com o público</h3>
        <p className="text-sm text-[var(--texto-fraco)]">
          Com {s.minimo} cortes ou mais com 7 dias, o app sugere pesos que preveem o engajamento. A
          nota final mistura {br(100 - s.mistura * 100, 0)}% do seu gosto e {br(s.mistura * 100, 0)}%
          do engajamento previsto ([engajamento] mistura).
          {s.precisao_atual !== null &&
            ` Hoje, nos de maior nota, ${porcento(s.precisao_atual)} ficaram acima da mediana do canal.`}
        </p>
        {novos ? (
          <>
            <table className="w-full max-w-lg text-sm tabular-nums">
              <thead className="text-[var(--texto-fraco)]">
                <tr>
                  <th className="text-left font-normal">Sinal</th>
                  <th className="text-right font-normal">Atual</th>
                  <th className="text-right font-normal">Do público</th>
                  <th className="text-right font-normal">Sugerido (mistura)</th>
                </tr>
              </thead>
              <tbody>
                {sinais.map((nome) => (
                  <tr key={nome}>
                    <td>{NOMES_DOS_SINAIS[nome] ?? nome}</td>
                    <td className="text-right">{br(s.pesos_atuais[nome] ?? 0, 2, true)}</td>
                    <td className="text-right">{br(s.pesos_do_engajamento?.[nome] ?? 0, 2, true)}</td>
                    <td className="text-right font-semibold">{br(novos[nome] ?? 0, 2, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-sm" data-testid="comparacao-engajamento">
              Nos de maior nota, em cortes que o treino não viu: <b>{porcento(s.precisao_antes)}</b>{" "}
              acima da mediana com os pesos atuais e <b>{porcento(s.precisao_depois)}</b> com os
              sugeridos.
            </p>
            {confirmando ? (
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
                Aceitar os pesos do engajamento
              </button>
            )}
          </>
        ) : (
          <p className="text-sm text-[var(--texto-fraco)]">{s.sem_pesos_porque}</p>
        )}
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
      </section>
    </main>
  );
}

function TabelaDeDesempenho({
  titulo,
  itens,
  minimo,
}: {
  titulo: string;
  itens: Desempenho[];
  minimo: number;
}) {
  return (
    <section className="rounded-lg bg-[var(--superficie)] p-4">
      <h3 className="mb-1 text-sm font-semibold">{titulo}</h3>
      {itens.length === 0 ? (
        <p className="text-sm text-[var(--texto-fraco)]">Ainda sem dados.</p>
      ) : (
        <table className="w-full text-sm tabular-nums" aria-label={titulo}>
          <thead className="text-[var(--texto-fraco)]">
            <tr>
              <th className="text-left font-normal">Nome</th>
              <th className="text-right font-normal">Vídeos</th>
              <th className="text-right font-normal">Efeito no índice</th>
            </tr>
          </thead>
          <tbody>
            {itens.map((d) => (
              <tr key={d.nome}>
                <td>{d.nome}</td>
                <td className="text-right">{d.usos}</td>
                <td className="text-right">
                  {d.confiavel && d.efeito !== null
                    ? `${d.efeito > 0 ? "+" : ""}${br(d.efeito, 2, true)}`
                    : d.efeito === null && d.usos >= minimo
                      ? "em todos"
                      : `em teste (${d.usos} de ${minimo})`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-xs text-[var(--texto-fraco)]">
        Efeito = índice médio dos vídeos com ela − sem ela. Correlação, não causa: só muda a ordem
        das sugestões, sempre entre as que a IA sugeriu para o clipe.
      </p>
    </section>
  );
}
