import { beforeEach, expect, it, vi } from "vitest";
import { CONFIG, momentoDeTeste } from "../testes/dados";
import type { Momento } from "../tipos";

const PUBLICADO: Momento = { ...momentoDeTeste(3), publicado: true };
const GRAVADO = {
  config: CONFIG,
  dias: [{ dia: "2026-10-23", momentos: 3, decididos: 0, pendentes: 3 }],
  listas: {
    "2026-10-23|mais": { momentos: [momentoDeTeste(1), momentoDeTeste(2), PUBLICADO] },
    "|": {
      dia: "2026-10-23",
      limite_diario: 15,
      total: 3,
      pendentes: 3,
      restantes: 0,
      momentos: [
        { ...momentoDeTeste(1), thumbnail: "demo/thumbs/1.jpg" },
        momentoDeTeste(2),
        PUBLICADO,
      ],
    },
  },
  momentos: {
    "1": { ...momentoDeTeste(1), thumbnail: "demo/thumbs/1.jpg" },
    "2": momentoDeTeste(2),
    "3": PUBLICADO,
  },
  aprendizado: { pesos_atuais: { fala: 0.25 }, pesos_novos: { fala: 0.6 }, versao_atual: null },
};

// O estado da demo fica no módulo (a memória da aba): cada teste começa com um novo.
async function abrir() {
  vi.resetModules();
  return (await import("./apiEstatica")).apiDaDemoEstatica;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(GRAVADO)))),
  );
});

it("lê as respostas gravadas, com os arquivos na pasta do site", async () => {
  const api = await abrir();
  const lista = await api.momentos(null, false);
  expect(lista.momentos.map((m) => m.id)).toEqual([1, 2, 3]);
  expect(lista.momentos[0]?.thumbnail).toBe("/demo/thumbs/1.jpg"); // BASE_URL + demo/...
  expect(fetch).toHaveBeenCalledTimes(1); // o dados.json é lido uma vez só
  await api.config();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("aprovar guarda a decisão e mostra as publicações na fila; desfazer volta", async () => {
  const api = await abrir();
  const aprovado = await api.decidir(1, {
    aprovado: true,
    destinos: { horizontal: ["youtube"], vertical_a: ["youtube", "tiktok"] },
    titulo: "NÃO ACREDITO",
    hashtags: ["#lolbr"],
  });
  expect(aprovado.decisao).toMatchObject({ aprovado: true, formatos: ["horizontal", "vertical_a"] });
  expect(aprovado.publicacoes.map((p) => `${p.formato}:${p.plataforma}:${p.status}`)).toEqual([
    "horizontal:youtube:pendente",
    "vertical_a:youtube:pendente",
    "vertical_a:tiktok:pendente",
  ]);
  expect((await api.momentos(null, false)).pendentes).toBe(2);
  expect(await api.dias()).toMatchObject([{ decididos: 1, pendentes: 2 }]);

  const desfeito = await api.desfazer(1);
  expect(desfeito.decisao).toBeNull();
  expect(desfeito.publicacoes).toEqual([]);
  expect((await api.momentos(null, false)).pendentes).toBe(3);
  await expect(api.desfazer(1)).rejects.toMatchObject({ status: 404 });
});

it("recusar não leva título nem destinos", async () => {
  const api = await abrir();
  const recusado = await api.decidir(2, { aprovado: false, titulo: "ignorado" });
  expect(recusado.decisao).toMatchObject({ aprovado: false, formatos: [], titulo: null });
  expect(recusado.publicacoes).toEqual([]);
});

it("ajustar muda o trecho e entra na decisão, sem recorte na fila", async () => {
  const api = await abrir();
  const { tarefa_id, momento } = await api.ajustar(2, 95, 125);
  expect(tarefa_id).toBeNull();
  expect(momento).toMatchObject({ inicio_s: 95, fim_s: 125, duracao_s: 30, recortando: false });
  const aprovado = await api.decidir(2, { aprovado: true, destinos: { horizontal: ["youtube"] } });
  expect(aprovado.decisao).toMatchObject({ ajuste_inicio_s: -5, ajuste_fim_s: 5 });
});

it("um momento publicado continua travado, e um que não existe dá 404", async () => {
  const api = await abrir();
  await expect(api.decidir(3, { aprovado: false })).rejects.toMatchObject({ status: 409 });
  await expect(api.momento(99)).rejects.toMatchObject({ status: 404 });
});

it("aceitar a sugestão vale só nesta aba", async () => {
  const api = await abrir();
  const aceito = await api.aceitarAprendizado(120);
  expect(aceito).toMatchObject({ versao_atual: 1, pesos_atuais: { fala: 0.6 }, pesos_novos: null });
  expect(await api.aprendizado()).toBe(aceito);
});
