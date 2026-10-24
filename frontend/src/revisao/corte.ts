// Regras do ajuste do corte, iguais às da API (clipador/revisao/decisoes.py: ajustar):
// - o início nunca passa do fim, e o clipe fica entre a duração mínima e a máxima do
//   detector ([deteccao] duracao_minima_s / duracao_maxima_s, padrão 15 a 60 s);
// - nunca antes do começo nem depois do fim da gravação;
// - numa gravação mais curta que o mínimo, vale a gravação inteira.
// Aqui o trecho é sempre "consertado" em vez de recusado: segurar a tecla "[" para no
// limite, em vez de dar erro.

export interface Trecho {
  inicio_s: number;
  fim_s: number;
}

export interface Limites {
  minimo_s: number;
  maximo_s: number;
  /** Duração da gravação inteira (null = desconhecida: sem limite no fim). */
  gravacao_s: number | null;
}

const arredondar = (s: number) => Math.round(s * 1000) / 1000;

function minimoEfetivo(limites: Limites): number {
  return limites.gravacao_s === null
    ? limites.minimo_s
    : Math.min(limites.minimo_s, limites.gravacao_s);
}

/** Muda o início, mantendo o fim no lugar. */
export function moverInicio(trecho: Trecho, novoInicio: number, limites: Limites): Trecho {
  const minimo = minimoEfetivo(limites);
  let inicio = Math.min(novoInicio, trecho.fim_s - minimo); // curto demais
  inicio = Math.max(inicio, trecho.fim_s - limites.maximo_s); // longo demais
  inicio = Math.max(inicio, 0);
  // O fim só se mexe se nem com o início em 0 der o mínimo (ex.: fim aos 5 s).
  const fim = Math.max(trecho.fim_s, inicio + minimo);
  return { inicio_s: arredondar(inicio), fim_s: arredondar(fim) };
}

/** Muda o fim, mantendo o início no lugar. */
export function moverFim(trecho: Trecho, novoFim: number, limites: Limites): Trecho {
  const minimo = minimoEfetivo(limites);
  let fim = Math.max(novoFim, trecho.inicio_s + minimo);
  fim = Math.min(fim, trecho.inicio_s + limites.maximo_s);
  if (limites.gravacao_s !== null) fim = Math.min(fim, limites.gravacao_s);
  const inicio = Math.min(trecho.inicio_s, fim - minimo);
  return { inicio_s: arredondar(Math.max(inicio, 0)), fim_s: arredondar(fim) };
}

export function mudou(a: Trecho, b: Trecho): boolean {
  return Math.abs(a.inicio_s - b.inicio_s) > 0.0005 || Math.abs(a.fim_s - b.fim_s) > 0.0005;
}

/** "1:05" (ou "1:02:05" numa live de horas). */
export function relogio(segundos: number): string {
  const total = Math.max(0, Math.round(segundos));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}
