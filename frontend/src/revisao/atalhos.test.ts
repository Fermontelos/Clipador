import { describe, expect, it } from "vitest";
import { acaoDaTecla, LISTA_DE_ATALHOS, type Tecla } from "./atalhos";

function tecla(key: string, extra: Partial<Tecla> = {}): Tecla {
  return { key, code: "", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra };
}

describe("cada atalho do roteiro", () => {
  it.each([
    ["a", { tipo: "aprovar" }],
    ["A", { tipo: "aprovar" }], // Caps Lock ligado
    ["r", { tipo: "recusar" }],
    ["u", { tipo: "desfazer" }],
    ["ArrowLeft", { tipo: "anterior" }],
    ["ArrowRight", { tipo: "proximo" }],
    [" ", { tipo: "pausar" }],
    ["t", { tipo: "editarTitulo" }],
    ["Tab", { tipo: "sugestao", passo: 1 }],
    ["h", { tipo: "editarHashtags" }],
    ["[", { tipo: "inicio", delta: -1 }],
    ["]", { tipo: "inicio", delta: 1 }],
    ["m", { tipo: "verMais" }],
    ["?", { tipo: "ajuda" }],
  ])("%s", (key, esperado) => {
    expect(acaoDaTecla(tecla(key), false)).toEqual(esperado);
  });

  it("Shift + [ e Shift + ] mexem no fim (o navegador manda { e })", () => {
    expect(acaoDaTecla(tecla("{", { shiftKey: true }), false)).toEqual({ tipo: "fim", delta: -1 });
    expect(acaoDaTecla(tecla("}", { shiftKey: true }), false)).toEqual({ tipo: "fim", delta: 1 });
  });

  it("Shift + Tab volta a sugestão", () => {
    expect(acaoDaTecla(tecla("Tab", { shiftKey: true }), false)).toEqual({
      tipo: "sugestao",
      passo: -1,
    });
  });

  it("1, 2 e 3 pela tecla física (também no teclado numérico)", () => {
    expect(acaoDaTecla(tecla("1", { code: "Digit1" }), false)).toEqual({
      tipo: "exibirFormato",
      indice: 0,
    });
    expect(acaoDaTecla(tecla("3", { code: "Numpad3" }), false)).toEqual({
      tipo: "exibirFormato",
      indice: 2,
    });
  });

  it("Shift + número tira ou põe o formato (com Shift, o key vira '!')", () => {
    expect(acaoDaTecla(tecla("!", { code: "Digit1", shiftKey: true }), false)).toEqual({
      tipo: "incluirFormato",
      indice: 0,
    });
  });
});

describe("quando nenhum atalho vale", () => {
  it("digitando num campo de texto, nenhuma tecla é atalho", () => {
    for (const key of ["a", "r", "u", "t", " ", "[", "Tab", "ArrowLeft"]) {
      expect(acaoDaTecla(tecla(key, { code: "KeyA" }), true)).toBeNull();
    }
    expect(acaoDaTecla(tecla("1", { code: "Digit1" }), true)).toBeNull();
  });

  it("Ctrl, Alt e Cmd ficam com o navegador (Ctrl+R recarrega, Ctrl+A seleciona)", () => {
    expect(acaoDaTecla(tecla("r", { ctrlKey: true }), false)).toBeNull();
    expect(acaoDaTecla(tecla("a", { metaKey: true }), false)).toBeNull();
    expect(acaoDaTecla(tecla("ArrowLeft", { altKey: true }), false)).toBeNull();
  });

  it("Shift + letra não aprova nem recusa sem querer", () => {
    expect(acaoDaTecla(tecla("A", { shiftKey: true }), false)).toBeNull();
    expect(acaoDaTecla(tecla("R", { shiftKey: true }), false)).toBeNull();
  });

  it("tecla sem atalho", () => {
    expect(acaoDaTecla(tecla("x"), false)).toBeNull();
    expect(acaoDaTecla(tecla("Enter"), false)).toBeNull();
  });
});

it("a ajuda lista todos os atalhos do roteiro", () => {
  const teclas = LISTA_DE_ATALHOS.map(([t]) => t);
  for (const t of ["A", "R", "U", "← →", "Espaço", "1 2 3", "T", "Tab", "[ ]", "Shift + [ ]"]) {
    expect(teclas).toContain(t);
  }
});
