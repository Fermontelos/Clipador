import { describe, expect, it } from "vitest";
import { type Limites, moverFim, moverInicio, mudou, relogio } from "./corte";

const LIMITES: Limites = { minimo_s: 15, maximo_s: 60, gravacao_s: 3600 };
const TRECHO = { inicio_s: 100, fim_s: 120 }; // 20 s

describe("ajuste do início", () => {
  it("move o início e mantém o fim", () => {
    expect(moverInicio(TRECHO, 98, LIMITES)).toEqual({ inicio_s: 98, fim_s: 120 });
  });

  it("nunca passa do fim nem deixa o clipe com menos de 15 s", () => {
    expect(moverInicio(TRECHO, 110, LIMITES)).toEqual({ inicio_s: 105, fim_s: 120 });
    expect(moverInicio(TRECHO, 500, LIMITES)).toEqual({ inicio_s: 105, fim_s: 120 });
  });

  it("não deixa o clipe passar de 60 s", () => {
    expect(moverInicio(TRECHO, 30, LIMITES)).toEqual({ inicio_s: 60, fim_s: 120 });
  });

  it("não vai para antes do começo da gravação", () => {
    const perto = { inicio_s: 2, fim_s: 20 };
    expect(moverInicio(perto, -5, LIMITES)).toEqual({ inicio_s: 0, fim_s: 20 });
  });
});

describe("ajuste do fim", () => {
  it("move o fim e mantém o início", () => {
    expect(moverFim(TRECHO, 125, LIMITES)).toEqual({ inicio_s: 100, fim_s: 125 });
  });

  it("nunca fica antes do início nem com menos de 15 s", () => {
    expect(moverFim(TRECHO, 90, LIMITES)).toEqual({ inicio_s: 100, fim_s: 115 });
  });

  it("não deixa o clipe passar de 60 s", () => {
    expect(moverFim(TRECHO, 200, LIMITES)).toEqual({ inicio_s: 100, fim_s: 160 });
  });

  it("não passa do fim da gravação", () => {
    const noFim = { inicio_s: 3570, fim_s: 3590 };
    expect(moverFim(noFim, 3700, LIMITES)).toEqual({ inicio_s: 3570, fim_s: 3600 });
  });

  it("sem a duração da gravação, só os limites do clipe valem", () => {
    const semDuracao = { ...LIMITES, gravacao_s: null };
    expect(moverFim(TRECHO, 140, semDuracao)).toEqual({ inicio_s: 100, fim_s: 140 });
  });
});

describe("gravação mais curta que o mínimo", () => {
  const curta: Limites = { minimo_s: 15, maximo_s: 60, gravacao_s: 12 };

  it("vale a gravação inteira", () => {
    expect(moverInicio({ inicio_s: 0, fim_s: 12 }, 3, curta)).toEqual({ inicio_s: 0, fim_s: 12 });
    expect(moverFim({ inicio_s: 0, fim_s: 12 }, 8, curta)).toEqual({ inicio_s: 0, fim_s: 12 });
  });
});

describe("início nunca depois do fim, em qualquer sequência de teclas", () => {
  it("1000 ajustes aleatórios respeitam todas as regras", () => {
    let trecho = { ...TRECHO };
    let semente = 7;
    const aleatorio = () => ((semente = (semente * 16807) % 2147483647) / 2147483647) * 2 - 1;
    for (let i = 0; i < 1000; i++) {
      const delta = Math.round(aleatorio() * 40);
      trecho =
        i % 2
          ? moverInicio(trecho, trecho.inicio_s + delta, LIMITES)
          : moverFim(trecho, trecho.fim_s + delta, LIMITES);
      const duracao = trecho.fim_s - trecho.inicio_s;
      expect(trecho.inicio_s).toBeGreaterThanOrEqual(0);
      expect(trecho.fim_s).toBeLessThanOrEqual(3600);
      expect(duracao).toBeGreaterThanOrEqual(15 - 1e-9);
      expect(duracao).toBeLessThanOrEqual(60 + 1e-9);
    }
  });
});

describe("utilitários", () => {
  it("mudou ignora diferenças de arredondamento", () => {
    expect(mudou(TRECHO, { inicio_s: 100.0001, fim_s: 120 })).toBe(false);
    expect(mudou(TRECHO, { inicio_s: 99, fim_s: 120 })).toBe(true);
  });

  it("relógio", () => {
    expect(relogio(65)).toBe("1:05");
    expect(relogio(3725)).toBe("1:02:05");
    expect(relogio(-3)).toBe("0:00");
  });
});
