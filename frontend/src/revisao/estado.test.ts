import { describe, expect, it } from "vitest";
import { CONFIG, momentoDeTeste } from "../testes/dados";
import type { Decisao, Momento } from "../tipos";
import {
  alternarFormato,
  alternarPlataforma,
  contador,
  desempilhar,
  empilhar,
  pedidoDeAprovacao,
  proximoPendente,
  rascunhoInicial,
  separarHashtags,
  textoDaPublicacao,
  trocarMomento,
  trocarSugestao,
} from "./estado";

const decidido = (id: number, aprovado = true): Momento & { decisao: Decisao } => ({
  ...momentoDeTeste(id),
  decisao: {
    aprovado,
    formatos: [],
    destinos: {},
    titulo: null,
    hashtags: [],
    tipo_titulo: null,
    ajuste_inicio_s: null,
    ajuste_fim_s: null,
    decidida_em: "2026-10-24T12:00:00Z",
  },
});

describe("contador", () => {
  it('"7 de 15 revisados": decididos entre os da tela', () => {
    const lista = [decidido(1), decidido(2, false), momentoDeTeste(3), momentoDeTeste(4)];
    expect(contador(lista)).toEqual({ revisados: 2, total: 4 });
    expect(contador([])).toEqual({ revisados: 0, total: 0 });
  });
});

describe("próximo pendente", () => {
  const lista = [decidido(1), momentoDeTeste(2), decidido(3), momentoDeTeste(4)];

  it("vai para o próximo sem decisão", () => {
    expect(proximoPendente(lista, 1)).toBe(3);
  });

  it("no fim, volta para o primeiro pendente lá de cima", () => {
    expect(proximoPendente(lista, 3)).toBe(1);
  });

  it("tudo decidido: fica onde está", () => {
    expect(proximoPendente([decidido(1), decidido(2)], 1)).toBe(1);
  });

  it("do começo (-1), o primeiro pendente", () => {
    expect(proximoPendente(lista, -1)).toBe(1);
  });
});

describe("desfazer", () => {
  it("desfaz na ordem inversa, um de cada vez", () => {
    let pilha = empilhar(empilhar(empilhar([], 5), 8), 2);
    const ordem: (number | null)[] = [];
    for (let i = 0; i < 4; i++) {
      const passo = desempilhar(pilha);
      ordem.push(passo.id);
      pilha = passo.pilha;
    }
    expect(ordem).toEqual([2, 8, 5, null]);
  });

  it("decidir de novo o mesmo momento não o põe duas vezes na pilha", () => {
    expect(empilhar(empilhar(empilhar([], 5), 8), 5)).toEqual([8, 5]);
  });

  it("trocarMomento põe a versão nova da API no lugar", () => {
    const novos = trocarMomento([momentoDeTeste(1), momentoDeTeste(2)], decidido(2));
    expect(novos.map((m) => m.decisao !== null)).toEqual([false, true]);
  });
});

describe("rascunho", () => {
  it("parte da 1ª sugestão, das hashtags sugeridas e dos destinos padrão", () => {
    const r = rascunhoInicial(momentoDeTeste(1), CONFIG);
    expect(r.titulo).toBe("NÃO ACREDITO");
    expect(r.sugestao).toBe(0);
    expect(r.hashtags).toBe("#leagueoflegends #lolbr");
    expect(r.destinos).toEqual({ horizontal: ["youtube"], vertical_a: ["youtube", "tiktok"] });
    expect([r.inicio_s, r.fim_s]).toEqual([100, 120]);
  });

  it("um momento aprovado volta com o que você escolheu", () => {
    const m = decidido(1);
    m.decisao.titulo = "Meu título";
    m.decisao.destinos = { vertical_a: ["tiktok"] };
    m.decisao.hashtags = ["#x"];
    const r = rascunhoInicial(m, CONFIG);
    expect(r.titulo).toBe("Meu título");
    expect(r.sugestao).toBe(-1);
    expect(r.destinos).toEqual({ horizontal: [], vertical_a: ["tiktok"] });
    expect(r.hashtags).toBe("#x");
  });

  it("Tab dá a volta nas 3 sugestões; Shift+Tab volta", () => {
    const m = momentoDeTeste(1);
    let r = rascunhoInicial(m, CONFIG);
    const titulos = [];
    for (let i = 0; i < 4; i++) {
      r = trocarSugestao(r, m, 1);
      titulos.push(r.titulo);
    }
    expect(titulos).toEqual(["Que jogada", "Eu avisei", "NÃO ACREDITO", "Que jogada"]);
    expect(trocarSugestao(r, m, -1).titulo).toBe("NÃO ACREDITO");
  });

  it("depois de editar o título, Tab volta para a 1ª sugestão", () => {
    const m = momentoDeTeste(1);
    const editado = { ...rascunhoInicial(m, CONFIG), titulo: "meu", sugestao: -1 };
    expect(trocarSugestao(editado, m, 1).titulo).toBe("NÃO ACREDITO");
  });

  it("Shift + número tira o formato e põe de volta com os destinos padrão", () => {
    const r = rascunhoInicial(momentoDeTeste(1), CONFIG);
    const sem = alternarFormato(r, "vertical_a", CONFIG);
    expect(sem.destinos.vertical_a).toEqual([]);
    expect(alternarFormato(sem, "vertical_a", CONFIG).destinos.vertical_a).toEqual([
      "youtube",
      "tiktok",
    ]);
    // Formato não cortado: nada muda.
    expect(alternarFormato(r, "vertical_b", CONFIG)).toBe(r);
  });

  it("liga e desliga uma plataforma", () => {
    const r = rascunhoInicial(momentoDeTeste(1), CONFIG);
    expect(alternarPlataforma(r, "vertical_a", "tiktok").destinos.vertical_a).toEqual(["youtube"]);
    expect(alternarPlataforma(r, "horizontal", "tiktok").destinos.horizontal).toEqual([
      "youtube",
      "tiktok",
    ]);
  });
});

describe("pedido de aprovação", () => {
  it("só os formatos com destino, as hashtags separadas e o tipo da sugestão", () => {
    const m = momentoDeTeste(1);
    const r = { ...rascunhoInicial(m, CONFIG), hashtags: "lol, #pentakill  #lol" };
    const semHorizontal = alternarFormato(r, "horizontal", CONFIG);
    expect(pedidoDeAprovacao(semHorizontal, m)).toEqual({
      aprovado: true,
      destinos: { vertical_a: ["youtube", "tiktok"] },
      titulo: "NÃO ACREDITO",
      hashtags: ["#lol", "#pentakill", "#lol"], // a API tira a repetida
      tipo_titulo: "frase_sua",
    });
  });

  it("título editado vai sem tipo", () => {
    const m = momentoDeTeste(1);
    const r = { ...rascunhoInicial(m, CONFIG), titulo: "NÃO ACREDITO!!", sugestao: -1 };
    expect(pedidoDeAprovacao(r, m).tipo_titulo).toBeNull();
  });

  it("separarHashtags aceita espaço e vírgula", () => {
    expect(separarHashtags(" a,b  #c ,, ")).toEqual(["#a", "#b", "#c"]);
    expect(separarHashtags("")).toEqual([]);
  });
});

describe("publicação", () => {
  const base = {
    id: 1,
    plataforma: "youtube",
    formato: "vertical_a",
    link: null,
    enviada_em: null,
    agendada_para: "2026-10-28T21:00:00Z",
  };
  const agora = new Date("2026-10-28T12:00:00Z");

  it("na fila, ainda dá para cancelar", () => {
    expect(textoDaPublicacao({ ...base, status: "pendente" }, agora)).toMatch(
      /^na fila, vai ao ar .*\(U ainda cancela\)$/,
    );
  });

  it("enviada: vai ao ar no horário; depois dele, publicada", () => {
    const enviada = { ...base, status: "agendada" };
    expect(textoDaPublicacao(enviada, agora)).toMatch(/^enviado, vai ao ar /);
    expect(textoDaPublicacao(enviada, new Date("2026-10-29T00:00:00Z"))).toMatch(/^publicado em /);
  });

  it("erro aponta para a tela da fila", () => {
    expect(textoDaPublicacao({ ...base, status: "erro" }, agora)).toContain("Fila e status");
  });
});
