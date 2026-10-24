// Teclas da revisão -> ações. Uma função pura (sem React), para testar cada atalho.
//
// Usa ``code`` (a tecla física) nos números, porque com Shift o ``key`` vira "!", "@"...;
// e ``key`` (o caractere) no resto, para funcionar também no teclado ABNT2, onde "[" fica
// em outro lugar. Com Shift, "[" e "]" viram "{" e "}" nos dois teclados.

export type Acao =
  | { tipo: "aprovar" }
  | { tipo: "recusar" }
  | { tipo: "desfazer" }
  | { tipo: "anterior" }
  | { tipo: "proximo" }
  | { tipo: "pausar" }
  | { tipo: "exibirFormato"; indice: number }
  | { tipo: "incluirFormato"; indice: number }
  | { tipo: "editarTitulo" }
  | { tipo: "editarHashtags" }
  | { tipo: "sugestao"; passo: 1 | -1 }
  | { tipo: "inicio"; delta: number }
  | { tipo: "fim"; delta: number }
  | { tipo: "verMais" }
  | { tipo: "ajuda" };

export interface Tecla {
  key: string;
  code: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

/** Lista para a ajuda na tela (a mesma ordem do roteiro). */
export const LISTA_DE_ATALHOS: [string, string][] = [
  ["A", "aprova"],
  ["R", "recusa"],
  ["U", "desfaz a última decisão"],
  ["← →", "momento anterior / próximo"],
  ["Espaço", "pausa / continua"],
  ["1 2 3", "troca o formato exibido"],
  ["Shift + 1 2 3", "tira ou põe o formato na aprovação"],
  ["T", "edita o título (Esc ou Enter sai)"],
  ["Tab", "próxima sugestão de título (Shift + Tab volta)"],
  ["H", "edita as hashtags"],
  ["[ ]", "início do corte −1 s / +1 s"],
  ["Shift + [ ]", "fim do corte −1 s / +1 s"],
  ["M", "ver mais (além do limite do dia)"],
  ["?", "mostra ou esconde esta ajuda"],
];

const DIGITOS: Record<string, number> = {
  Digit1: 0,
  Digit2: 1,
  Digit3: 2,
  Numpad1: 0,
  Numpad2: 1,
  Numpad3: 2,
};

/**
 * A ação da tecla, ou null (a tecla segue o caminho normal do navegador).
 * ``editando``: o cursor está num campo de texto. Aí nenhum atalho vale (senão digitar
 * "a" no título aprovaria o clipe); o campo cuida do Esc e do Enter.
 */
export function acaoDaTecla(tecla: Tecla, editando: boolean): Acao | null {
  if (editando) return null;
  // Ctrl/Alt/Cmd + tecla é do navegador (Ctrl+R recarrega, Alt+← volta...).
  if (tecla.ctrlKey || tecla.metaKey || tecla.altKey) return null;

  const digito = DIGITOS[tecla.code];
  if (digito !== undefined) {
    return tecla.shiftKey
      ? { tipo: "incluirFormato", indice: digito }
      : { tipo: "exibirFormato", indice: digito };
  }
  switch (tecla.key) {
    case "[":
      return { tipo: "inicio", delta: -1 };
    case "]":
      return { tipo: "inicio", delta: 1 };
    case "{":
      return { tipo: "fim", delta: -1 };
    case "}":
      return { tipo: "fim", delta: 1 };
    case "ArrowLeft":
      return { tipo: "anterior" };
    case "ArrowRight":
      return { tipo: "proximo" };
    case " ":
      return { tipo: "pausar" };
    case "Tab":
      return { tipo: "sugestao", passo: tecla.shiftKey ? -1 : 1 };
    case "?":
      return { tipo: "ajuda" };
  }
  if (tecla.shiftKey) return null; // A maiúsculo por engano com Shift: sem surpresa
  switch (tecla.key.toLowerCase()) {
    case "a":
      return { tipo: "aprovar" };
    case "r":
      return { tipo: "recusar" };
    case "u":
      return { tipo: "desfazer" };
    case "t":
      return { tipo: "editarTitulo" };
    case "h":
      return { tipo: "editarHashtags" };
    case "m":
      return { tipo: "verMais" };
  }
  return null;
}
