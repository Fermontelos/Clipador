// A tela de revisão inteira, com uma API falsa na memória (a mesma resposta da real).
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, momentoDeTeste } from "../testes/dados";
import type { Momento } from "../tipos";
import { Revisao } from "./Revisao";

interface Pedido {
  metodo: string;
  caminho: string;
  corpo: unknown;
}

let momentos: Momento[];
let pedidos: Pedido[];

function resposta(dados: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(dados), { status }));
}

function apiFalsa(entrada: RequestInfo | URL, opcoes?: RequestInit) {
  const caminho = String(entrada);
  const metodo = opcoes?.method ?? "GET";
  const corpo = opcoes?.body ? JSON.parse(String(opcoes.body)) : null;
  pedidos.push({ metodo, caminho, corpo });
  if (caminho === "/api/config") return resposta(CONFIG);
  if (caminho === "/api/dias") {
    return resposta([{ dia: "2026-10-23", momentos: 3, decididos: 0, pendentes: 3 }]);
  }
  if (caminho.startsWith("/api/momentos?") || caminho === "/api/momentos") {
    return resposta({
      dia: "2026-10-23",
      limite_diario: 15,
      total: momentos.length,
      pendentes: momentos.filter((m) => !m.decisao).length,
      restantes: 0,
      momentos,
    });
  }
  const [, id, acao] = caminho.match(/^\/api\/momentos\/(\d+)(?:\/(\w+))?$/) ?? [];
  const indice = momentos.findIndex((m) => m.id === Number(id));
  if (indice < 0) return resposta({ detail: "não existe" }, 404);
  const momento = momentos[indice]!;
  let novo: Momento;
  if (acao === undefined) return resposta(momento); // GET /api/momentos/{id}
  if (acao === "decisao" && metodo === "POST") {
    novo = {
      ...momento,
      decisao: {
        aprovado: corpo.aprovado,
        formatos: Object.keys(corpo.destinos ?? {}),
        destinos: corpo.destinos ?? {},
        titulo: corpo.titulo ?? null,
        hashtags: corpo.hashtags ?? [],
        tipo_titulo: corpo.tipo_titulo ?? null,
        ajuste_inicio_s: null,
        ajuste_fim_s: null,
        decidida_em: "2026-10-24T12:00:00Z",
      },
    };
  } else if (acao === "decisao" && metodo === "DELETE") {
    novo = { ...momento, decisao: null };
  } else if (acao === "ajuste") {
    novo = { ...momento, inicio_s: corpo.inicio_s, fim_s: corpo.fim_s, recortando: true };
    momentos[indice] = novo;
    return resposta({ tarefa_id: 99, momento: novo });
  } else {
    return resposta({ detail: "?" }, 405);
  }
  momentos[indice] = novo;
  return resposta(novo);
}

const decisoes = () =>
  pedidos.filter((p) => p.caminho.endsWith("/decisao") && p.metodo !== "GET");

async function abrir() {
  const usuario = userEvent.setup();
  render(<Revisao />);
  await screen.findByTestId("contador");
  return usuario;
}

beforeEach(() => {
  momentos = [momentoDeTeste(1), momentoDeTeste(2), momentoDeTeste(3)];
  pedidos = [];
  vi.stubGlobal("fetch", vi.fn(apiFalsa));
});

describe("autoplay", () => {
  it("o 1º clipe espera uma tecla, e essa tecla não aprova nada", async () => {
    const usuario = await abrir();
    expect(screen.getByText("Aperte qualquer tecla para começar")).toBeInTheDocument();
    await usuario.keyboard("a");
    expect(screen.queryByText("Aperte qualquer tecla para começar")).not.toBeInTheDocument();
    expect(decisoes()).toEqual([]);
  });
});

describe("decidir pelo teclado", () => {
  it("A aprova com o título, as hashtags e os destinos; R recusa; o contador sobe", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" "); // libera o som
    expect(screen.getByTestId("contador")).toHaveTextContent("0 de 3 revisados");

    await usuario.keyboard("a");
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("1 de 3"));
    expect(decisoes()[0]).toEqual({
      metodo: "POST",
      caminho: "/api/momentos/1/decisao",
      corpo: {
        aprovado: true,
        destinos: { horizontal: ["youtube"], vertical_a: ["youtube", "tiktok"] },
        titulo: "NÃO ACREDITO",
        hashtags: ["#leagueoflegends", "#lolbr"],
        tipo_titulo: "frase_sua",
      },
    });

    await usuario.keyboard("r"); // já está no 2º (foi para o próximo pendente)
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("2 de 3"));
    expect(decisoes()[1]).toMatchObject({
      caminho: "/api/momentos/2/decisao",
      corpo: { aprovado: false },
    });
  });

  it("U desfaz a última decisão e volta para aquele momento", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    await usuario.keyboard("a");
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("1 de 3"));
    await usuario.keyboard("r");
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("2 de 3"));

    await usuario.keyboard("u");
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("1 de 3"));
    expect(decisoes().at(-1)).toMatchObject({
      metodo: "DELETE",
      caminho: "/api/momentos/2/decisao",
    });
    await usuario.keyboard("u");
    await waitFor(() => expect(screen.getByTestId("contador")).toHaveTextContent("0 de 3"));
    expect(decisoes().at(-1)).toMatchObject({ caminho: "/api/momentos/1/decisao" });
    await usuario.keyboard("u");
    expect(await screen.findByText("Nada para desfazer.")).toBeInTheDocument();
  });
});

describe("título", () => {
  it("digitando no título, os atalhos ficam desligados; Esc sai do campo", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    await usuario.keyboard("t"); // foca o título
    const campo = screen.getByRole("textbox", { name: /Título/ });
    expect(campo).toHaveFocus();
    await usuario.keyboard("{Control>}a{/Control}{Backspace}rapaz que raiva");
    expect(campo).toHaveValue("rapaz que raiva");
    expect(decisoes()).toEqual([]); // o "a" e o "r" digitados não aprovaram nem recusaram

    await usuario.keyboard("{Escape}");
    expect(campo).not.toHaveFocus();
    await usuario.keyboard("a");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
    expect(decisoes()[0]!.corpo).toMatchObject({ titulo: "rapaz que raiva", tipo_titulo: null });
  });

  it("Tab alterna entre as 3 sugestões", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    const campo = screen.getByRole("textbox", { name: /Título/ });
    await usuario.keyboard("{Tab}");
    expect(campo).toHaveValue("Que jogada");
    await usuario.keyboard("{Tab}{Tab}");
    expect(campo).toHaveValue("NÃO ACREDITO");
  });

  it("aprovar sem título avisa e põe o cursor no título", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" t{Control>}a{/Control}{Backspace}{Escape}a");
    expect(await screen.findByRole("alert")).toHaveTextContent("Escreva ou escolha um título");
    expect(screen.getByRole("textbox", { name: /Título/ })).toHaveFocus();
    expect(decisoes()).toEqual([]);
  });
});

describe("ajuste do corte", () => {
  it("[ e Shift+] mudam o trecho; ao aprovar, o ajuste vai antes da decisão", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    // No user-event, "[[" digita um "["; com Shift, o navegador manda "}" no lugar de "]".
    await usuario.keyboard("[[[[{Shift>}}}}{/Shift}"); // início -2 s, fim +3 s
    expect(screen.getByRole("slider", { name: "Início do corte" })).toHaveAttribute(
      "aria-valuenow",
      "98",
    );
    expect(screen.getByRole("slider", { name: "Fim do corte" })).toHaveAttribute(
      "aria-valuenow",
      "123",
    );
    await usuario.keyboard("a");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
    const ajuste = pedidos.find((p) => p.caminho.endsWith("/ajuste"));
    expect(ajuste?.corpo).toEqual({ inicio_s: 98, fim_s: 123 });
    expect(pedidos.indexOf(ajuste!)).toBeLessThan(pedidos.indexOf(decisoes()[0]!));
  });

  it("o início não passa do mínimo de 15 s, por mais que você aperte ]", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    await usuario.keyboard("]".repeat(12)); // 12 s a mais no início: só 5 cabem
    expect(screen.getByRole("slider", { name: "Início do corte" })).toHaveAttribute(
      "aria-valuenow",
      "105",
    );
  });

  it("teclas seguidas, antes de a tela redesenhar, contam todas (segurar a tecla)", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    const tecla = (key: string, shiftKey = false) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, cancelable: true }));
    act(() => {
      // Tudo dentro de um act: o React só redesenha no fim (bug achado no navegador).
      for (let i = 0; i < 3; i++) tecla("]");
      for (let i = 0; i < 4; i++) tecla("}", true);
    });
    expect(screen.getByRole("slider", { name: "Início do corte" })).toHaveAttribute(
      "aria-valuenow",
      "103",
    );
    expect(screen.getByRole("slider", { name: "Fim do corte" })).toHaveAttribute(
      "aria-valuenow",
      "124",
    );
  });

  it("recusar não manda o ajuste", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" [[r");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
    expect(pedidos.some((p) => p.caminho.endsWith("/ajuste"))).toBe(false);
  });
});

describe("recorte na fila", () => {
  it("bloqueia o ajuste, esconde a posição e troca o vídeo quando o corte fica pronto", async () => {
    momentos[0] = { ...momentoDeTeste(1), recortando: true };
    const usuario = await abrir();
    await usuario.keyboard(" ");
    expect(screen.getByText(/Novo corte na fila/)).toBeInTheDocument();
    expect(screen.queryByTestId("posicao")).not.toBeInTheDocument();
    expect(screen.getByTestId("player").getAttribute("src")).toBe("/v/1h?corte=100-120-antigo");
    await usuario.keyboard("[[");
    expect(await screen.findByText(/espere ele ficar pronto/)).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Início do corte" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );

    // O serviço termina o recorte. O trecho já era o novo durante o recorte (o mesmo
    // 100-120 aqui): mesmo assim o endereço do vídeo muda, para baixar o arquivo novo.
    momentos[0] = { ...momentoDeTeste(1), recortando: false };
    await waitFor(() => expect(screen.getByText("Novo corte pronto.")).toBeInTheDocument(), {
      timeout: 7000,
    });
    expect(screen.queryByText(/Novo corte na fila/)).not.toBeInTheDocument();
    expect(screen.getByTestId("player").getAttribute("src")).toBe("/v/1h?corte=100-120");
  }, 10_000);
});

describe("navegação e formatos", () => {
  it("← → navegam e 1/2 trocam o formato exibido", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    const player = () => screen.getByTestId("player") as HTMLVideoElement;
    expect(player().getAttribute("src")).toBe("/v/1h?corte=100-120");
    await usuario.keyboard("2");
    expect(player().getAttribute("src")).toBe("/v/1v?corte=100-120");
    await usuario.keyboard("{ArrowRight}");
    expect(player().getAttribute("src")).toBe("/v/2v?corte=100-120"); // o formato escolhido continua
    await usuario.keyboard("{ArrowLeft}{ArrowLeft}"); // não passa do 1º
    expect(player().getAttribute("src")).toBe("/v/1v?corte=100-120");
  });

  it("Shift+1 tira o horizontal da aprovação", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" {Shift>}1{/Shift}a");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
    expect(decisoes()[0]!.corpo).toMatchObject({
      destinos: { vertical_a: ["youtube", "tiktok"] },
    });
  });

  it("espaço pausa e continua", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    await usuario.keyboard(" ");
    expect(screen.getByText(/pausado/)).toBeInTheDocument();
    await usuario.keyboard(" ");
    expect(screen.queryByText(/pausado/)).not.toBeInTheDocument();
  });

  it("? mostra a ajuda; com ela aberta, nenhuma tecla age; Esc fecha", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ?");
    expect(screen.getByRole("dialog", { name: "Atalhos do teclado" })).toBeInTheDocument();
    await usuario.keyboard("a");
    expect(decisoes()).toEqual([]); // não aprovou escondido atrás da ajuda
    await usuario.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await usuario.keyboard("a");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
  });

  it("depois de clicar numa caixa de marcar, os atalhos continuam valendo", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    // O TikTok é vertical: só o vertical A tem a caixa dele (etapa 16).
    const caixas = screen.getAllByRole("checkbox", { name: /TikTok/ });
    expect(caixas).toHaveLength(1);
    await usuario.click(caixas[0]!); // tira o vertical A do TikTok
    expect(caixas[0]).toHaveFocus();
    await usuario.keyboard("a");
    await waitFor(() => expect(decisoes()).toHaveLength(1));
    expect(decisoes()[0]!.corpo).toMatchObject({
      destinos: { horizontal: ["youtube"], vertical_a: ["youtube"] },
    });
  });
});

describe("erros", () => {
  it("um erro da API aparece na tela, sem perder o que você escreveu", async () => {
    const usuario = await abrir();
    await usuario.keyboard(" ");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => resposta({ detail: "O momento 1 já foi para a publicação." }, 409)),
    );
    await act(async () => {
      await usuario.keyboard("a");
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("já foi para a publicação");
    expect(screen.getByTestId("contador")).toHaveTextContent("0 de 3");
  });
});
