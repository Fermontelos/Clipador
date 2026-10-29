/** Número no padrão brasileiro (vírgula decimal): 11,5 e 0,62. */
export function br(valor: number, casas = 1, fixas = false): string {
  return valor.toLocaleString("pt-BR", {
    minimumFractionDigits: fixas ? casas : 0,
    maximumFractionDigits: casas,
  });
}

/** 0,62 vira "62%"; sem valor, um traço. */
export function porcento(valor: number | null): string {
  return valor === null ? "—" : `${Math.round(valor * 100)}%`;
}
