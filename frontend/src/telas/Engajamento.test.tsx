import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Engajamento as Dados } from "../tipos";
import { Engajamento } from "./Engajamento";

const DADOS: Dados = {
  com_metricas: 70,
  com_7_dias: 60,
  cortes: [
    {
      publicacao_id: 1,
      momento_id: 9,
      titulo: "PENTAKILL NO MID",
      plataforma: "youtube",
      formato: "vertical_a",
      link: "https://youtu.be/x",
      indice: 2.5,
      visualizacoes: 12400,
      curtidas: 800,
      comentarios: 40,
      compartilhamentos: 12,
      retencao: 0.61,
      tipo_de_momento: "evento do LoL",
    },
    {
      publicacao_id: 2,
      momento_id: 10,
      titulo: "gritei",
      plataforma: "tiktok",
      formato: "vertical_a",
      link: null,
      indice: 0.7,
      visualizacoes: 900,
      curtidas: 30,
      comentarios: 1,
      compartilhamentos: 0,
      retencao: null,
      tipo_de_momento: "grito (voz alta)",
    },
  ],
  hashtags: [
    { nome: "#pentakill", usos: 7, indice_medio: 1.9, efeito: 0.8, confiavel: true },
    { nome: "#novata", usos: 2, indice_medio: 1.1, efeito: 0.1, confiavel: false },
  ],
  tipos_de_titulo: [],
  por_tipo_de_momento: [{ nome: "evento do LoL", cortes: 20, indice_medio: 1.6 }],
  por_rede: [
    {
      plataforma: "tiktok",
      cortes: 20,
      mediana_visualizacoes: 3000,
      mediana_interacao: 0.052,
      mediana_retencao: null,
    },
  ],
  sugestao: {
    cortes: 60,
    minimo: 50,
    mistura: 0.5,
    pesos_atuais: { fala: 0.25, lol: 0.25, volume_voz: 0.5 },
    pesos_do_engajamento: { fala: 0.1, lol: 0.7, volume_voz: 0.2 },
    pesos_novos: { fala: 0.175, lol: 0.475, volume_voz: 0.35 },
    precisao_antes: 0.48,
    precisao_depois: 0.6,
    precisao_atual: 0.5,
    sem_pesos_porque: null,
  },
  minimo_usos: 5,
};

let pedidos: { caminho: string; corpo: unknown }[];

beforeEach(() => {
  pedidos = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((entrada: RequestInfo | URL, opcoes?: RequestInit) => {
      const caminho = String(entrada);
      pedidos.push({ caminho, corpo: opcoes?.body ? JSON.parse(String(opcoes.body)) : null });
      const resposta = caminho.endsWith("/aceitar")
        ? {
            ...DADOS,
            sugestao: {
              ...DADOS.sugestao,
              pesos_atuais: DADOS.sugestao.pesos_novos,
              pesos_novos: null,
              sem_pesos_porque: "Os pesos misturados são iguais aos atuais.",
            },
          }
        : DADOS;
      return Promise.resolve(new Response(JSON.stringify(resposta)));
    }),
  );
});

it("mostra os rankings e as redes com números em pt-BR", async () => {
  render(<Engajamento />);
  const cortes = await screen.findByRole("table", { name: "Cortes por engajamento" });
  expect(cortes).toHaveTextContent("PENTAKILL NO MID");
  expect(screen.getByRole("row", { name: /PENTAKILL/ })).toHaveTextContent("2,50");
  expect(screen.getByRole("row", { name: /PENTAKILL/ })).toHaveTextContent("12.400");
  expect(screen.getByRole("link", { name: "PENTAKILL NO MID" })).toHaveAttribute(
    "href",
    "https://youtu.be/x",
  );
  const hashtags = screen.getByRole("table", { name: "Hashtags" });
  expect(hashtags).toHaveTextContent("+0,80");
  expect(hashtags).toHaveTextContent("em teste (2 de 5)"); // menos de 5 usos: sem conclusão
  expect(screen.getByRole("row", { name: /^TikTok 20/ })).toHaveTextContent("5,2%");
});

it("só aceita os pesos depois de confirmar", async () => {
  const usuario = userEvent.setup();
  render(<Engajamento />);
  expect(await screen.findByTestId("comparacao-engajamento")).toHaveTextContent(
    "48% acima da mediana com os pesos atuais e 60% com os sugeridos",
  );
  await usuario.click(screen.getByRole("button", { name: "Aceitar os pesos do engajamento" }));
  expect(pedidos.some((p) => p.caminho.endsWith("/aceitar"))).toBe(false); // ainda não
  await usuario.click(screen.getByRole("button", { name: /Confirmar/ }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Pesos aceitos"));
  expect(pedidos.find((p) => p.caminho.endsWith("/aceitar"))?.corpo).toEqual({ cortes: 60 });
});

it("sem cortes suficientes, explica e não oferece o aceite", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            ...DADOS,
            com_7_dias: 0,
            cortes: [],
            sugestao: {
              ...DADOS.sugestao,
              cortes: 0,
              pesos_novos: null,
              sem_pesos_porque: "São 0 cortes com a métrica de 7 dias; a sugestão precisa de pelo menos 50.",
            },
          }),
        ),
      ),
    ),
  );
  render(<Engajamento />);
  expect(await screen.findByText(/Ainda sem cortes com 7 dias/)).toBeInTheDocument();
  expect(screen.getByText(/precisa de pelo menos 50/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Aceitar/ })).not.toBeInTheDocument();
});
