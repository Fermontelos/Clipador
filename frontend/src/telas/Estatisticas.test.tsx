import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { Estatisticas as Dados } from "../tipos";
import { Estatisticas } from "./Estatisticas";

const DADOS: Dados = {
  momentos: 30,
  aprovados: 9,
  recusados: 11,
  precisao: 0.45,
  por_dia: [{ dia: "2026-10-20", momentos: 30, aprovados: 9, recusados: 11 }],
  por_nota: [
    { faixa: "0-20", momentos: 10, aprovados: 1, recusados: 6 },
    { faixa: "80-100", momentos: 5, aprovados: 4, recusados: 1 },
  ],
  sinais: [{ detector: "volume_voz", aprovados: 0.71, recusados: 0.42 }],
  formatos: { horizontal: 5, vertical_a: 9 },
  plataformas: { youtube: 12, tiktok: 7 },
  ajuste_medio_inicio_s: -1.5,
  ajuste_medio_fim_s: 0.25,
};

function responder(estatisticas: Dados) {
  vi.stubGlobal(
    "fetch",
    vi.fn((entrada: RequestInfo | URL) =>
      // A tela também abre o painel do aprendizado (testado à parte): aqui ele só falha.
      String(entrada) === "/api/estatisticas"
        ? Promise.resolve(new Response(JSON.stringify(estatisticas)))
        : Promise.resolve(new Response("{}", { status: 500 })),
    ),
  );
}

beforeEach(() => responder(DADOS));

it("mostra a precisão, as contagens e os ajustes médios em pt-BR", async () => {
  render(<Estatisticas />);
  const resumo = await screen.findByRole("region", { name: "Resumo" });
  expect(resumo).toHaveTextContent("Precisão do detector45%");
  expect(resumo).toHaveTextContent("aprovados ÷ decididos (20)");
  expect(resumo).toHaveTextContent("10 sem decisão"); // 30 momentos, 20 decididos
  expect(screen.getByText("-1,5 s")).toBeInTheDocument(); // negativo = começou mais cedo
  expect(screen.getByText("+0,3 s")).toBeInTheDocument(); // uma casa, com o sinal
  expect(screen.queryByText("Ainda sem decisões.")).not.toBeInTheDocument();
});

it("sem decisões, explica em vez de desenhar gráficos vazios", async () => {
  responder({
    ...DADOS,
    aprovados: 0,
    recusados: 0,
    precisao: null,
    por_dia: [],
    por_nota: [],
    sinais: [],
    ajuste_medio_inicio_s: null,
    ajuste_medio_fim_s: null,
  });
  render(<Estatisticas />);
  const resumo = await screen.findByRole("region", { name: "Resumo" });
  expect(resumo).toHaveTextContent("Precisão do detector—");
  expect(screen.getAllByText("Ainda sem dados.")).toHaveLength(2);
  expect(screen.getByText("Ainda sem decisões.")).toBeInTheDocument();
});

it("mostra o erro da API", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ detail: "banco fora" }), { status: 500 }))),
  );
  render(<Estatisticas />);
  expect(await screen.findByText(/banco fora/)).toBeInTheDocument();
});
