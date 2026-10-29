// Trocar de tela não perde a revisão em andamento.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { CONFIG, momentoDeTeste } from "./testes/dados";
import type { Momento } from "./tipos";

let momentos: Momento[];
let decisoes: string[];

const STATUS = {
  fila: { pendente: 0, em_andamento: 0, feita: 5, erro: 0 },
  agendadas: 0,
  em_andamento: [],
  erros: [],
  cota_youtube: {
    envios: 0,
    limite_envios: 100,
    unidades: 0,
    limite_unidades: 10000,
    renova_em: "2026-10-25T07:00:00Z",
  },
  dias: [],
};

function responder(dados: unknown) {
  return Promise.resolve(new Response(JSON.stringify(dados)));
}

beforeEach(() => {
  window.history.pushState(null, "", "/");
  momentos = [momentoDeTeste(1), momentoDeTeste(2)];
  decisoes = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((entrada: RequestInfo | URL, opcoes?: RequestInit) => {
      const caminho = String(entrada);
      const metodo = opcoes?.method ?? "GET";
      if (caminho === "/api/config") return responder(CONFIG);
      if (caminho === "/api/dias") return responder([]);
      if (caminho === "/api/status") return responder(STATUS);
      if (caminho.startsWith("/api/momentos?") || caminho === "/api/momentos") {
        return responder({
          dia: "2026-10-23",
          limite_diario: 15,
          total: momentos.length,
          pendentes: momentos.length,
          restantes: 0,
          momentos,
        });
      }
      const id = Number(caminho.match(/\/api\/momentos\/(\d+)\/decisao/)?.[1]);
      decisoes.push(`${metodo} ${id}`);
      const i = momentos.findIndex((m) => m.id === id);
      const decisao =
        metodo === "DELETE"
          ? null
          : {
              aprovado: false,
              formatos: [],
              destinos: {},
              titulo: null,
              hashtags: [],
              tipo_titulo: null,
              ajuste_inicio_s: null,
              ajuste_fim_s: null,
              decidida_em: "2026-10-24T12:00:00Z",
            };
      momentos[i] = { ...momentos[i]!, decisao };
      return responder(momentos[i]);
    }),
  );
});

it("ir à fila e voltar não perde a pilha do desfazer; na fila, as teclas não revisam", async () => {
  const usuario = userEvent.setup();
  render(<App />);
  await screen.findByTestId("contador");
  await usuario.keyboard(" r"); // libera o som e recusa o 1º
  await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("1 de 2"));

  await usuario.click(screen.getByRole("link", { name: "Fila e status" }));
  expect(await screen.findByText("Cota do YouTube hoje")).toBeInTheDocument();
  await usuario.keyboard("r"); // na tela da fila: não recusa nada escondido
  expect(decisoes).toEqual(["POST 1"]);

  await usuario.click(screen.getByRole("link", { name: "Revisão" }));
  expect(screen.getByTestId("contador")).toHaveTextContent("1 de 2");
  await usuario.keyboard("u"); // a pilha continua lá
  await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("0 de 2"));
  expect(decisoes).toEqual(["POST 1", "DELETE 1"]);
});
