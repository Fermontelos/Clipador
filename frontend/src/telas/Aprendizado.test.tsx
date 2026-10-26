import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Aprendizado as Dados } from "../tipos";
import { Aprendizado } from "./Aprendizado";

const SUGESTAO: Dados = {
  decisoes: 120,
  aprovadas: 59,
  minimo_para_pesos: 100,
  versao_atual: null,
  pesos_atuais: { fala: 0.25, lol: 0.25, volume_voz: 0.5 },
  pesos_novos: { fala: 0.62, lol: 0.09, volume_voz: 0.29 },
  precisao_antes: 0.56,
  precisao_depois: 0.65,
  sem_pesos_porque: null,
  janela_atual: [10, 5],
  janela_nova: {
    segundos_antes: 11.5,
    segundos_depois: 5,
    media_inicio_s: -1.57,
    media_fim_s: 0.19,
    aprovados: 59,
  },
  sem_janela_porque: null,
  precisao_por_dia: [{ dia: "2026-01-04", decididos: 20, aprovados: 9, precisao: 0.45 }],
  versoes: [],
};

let pedidos: { caminho: string; corpo: unknown }[];

beforeEach(() => {
  pedidos = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((entrada: RequestInfo | URL, opcoes?: RequestInit) => {
      const caminho = String(entrada);
      pedidos.push({ caminho, corpo: opcoes?.body ? JSON.parse(String(opcoes.body)) : null });
      const resposta =
        caminho === "/api/aprendizado/aceitar"
          ? {
              ...SUGESTAO,
              versao_atual: 1,
              pesos_atuais: SUGESTAO.pesos_novos,
              pesos_novos: null,
              janela_atual: [11.5, 5],
              janela_nova: null,
              sem_pesos_porque: "Os pesos aprendidos são iguais aos atuais.",
              sem_janela_porque: "São 0 aprovações desde a última mudança da janela; precisa de 20.",
            }
          : SUGESTAO;
      return Promise.resolve(new Response(JSON.stringify(resposta)));
    }),
  );
});

it("mostra a sugestão e só aceita depois de confirmar", async () => {
  const usuario = userEvent.setup();
  render(<Aprendizado />);
  expect(await screen.findByTestId("comparacao")).toHaveTextContent(
    "você aprovaria 56% com os pesos atuais e 65% com os sugeridos",
  );
  expect(screen.getByTestId("janela")).toHaveTextContent("11,5 s antes");
  expect(screen.getByTestId("janela")).toHaveTextContent("1,6 s para mais cedo");
  expect(screen.getByRole("row", { name: /Fala/ })).toHaveTextContent("0,250,62");

  await usuario.click(screen.getByRole("button", { name: "Aceitar a sugestão" }));
  expect(pedidos.some((p) => p.caminho.endsWith("/aceitar"))).toBe(false); // ainda não
  await usuario.click(screen.getByRole("button", { name: /Confirmar/ }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Versão 1 aceita"));
  // Manda quantas decisões havia na sugestão que você viu.
  expect(pedidos.find((p) => p.caminho.endsWith("/aceitar"))?.corpo).toEqual({ decisoes: 120 });
  expect(screen.queryByRole("button", { name: "Aceitar a sugestão" })).not.toBeInTheDocument();
});

it("cancelar não aceita nada", async () => {
  const usuario = userEvent.setup();
  render(<Aprendizado />);
  await usuario.click(await screen.findByRole("button", { name: "Aceitar a sugestão" }));
  await usuario.click(screen.getByRole("button", { name: "Cancelar" }));
  expect(pedidos.some((p) => p.caminho.endsWith("/aceitar"))).toBe(false);
});

it("sem decisões suficientes, explica e não oferece o aceite", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            ...SUGESTAO,
            decisoes: 12,
            pesos_novos: null,
            janela_nova: null,
            sem_pesos_porque: "São 12 decisões; o treino precisa de pelo menos 100.",
            sem_janela_porque: "São 6 aprovações desde a última mudança da janela; precisa de 20.",
          }),
        ),
      ),
    ),
  );
  render(<Aprendizado />);
  expect(await screen.findByText(/o treino precisa de pelo menos 100/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Aceitar a sugestão" })).not.toBeInTheDocument();
});

it("se chegaram decisões novas, o aceite é recusado e a tela mostra a sugestão de agora", async () => {
  let chamadas = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((entrada: RequestInfo | URL) => {
      const caminho = String(entrada);
      if (caminho.endsWith("/aceitar")) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "Agora são 121 decisões" }), { status: 409 }),
        );
      }
      chamadas += 1; // a 2ª leitura já vem com a decisão nova
      return Promise.resolve(
        new Response(JSON.stringify({ ...SUGESTAO, decisoes: chamadas === 1 ? 120 : 121 })),
      );
    }),
  );
  const usuario = userEvent.setup();
  render(<Aprendizado />);
  await usuario.click(await screen.findByRole("button", { name: "Aceitar a sugestão" }));
  await usuario.click(screen.getByRole("button", { name: /Confirmar/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Agora são 121 decisões");
  expect(await screen.findByText(/121 decisões \(59 aprovadas\)/)).toBeInTheDocument();
});
