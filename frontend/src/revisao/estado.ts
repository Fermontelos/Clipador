// O estado da revisão em funções puras (sem React): o contador, o próximo momento, o
// rascunho de cada momento e a pilha do desfazer.
import type { Config, Momento, Publicacao, Rascunho } from "../tipos";

/** "7 de 15 revisados": os decididos entre os que estão na tela. */
export function contador(momentos: Momento[]): { revisados: number; total: number } {
  return { revisados: momentos.filter((m) => m.decisao !== null).length, total: momentos.length };
}

/**
 * Para onde ir depois de decidir: o próximo sem decisão depois de ``atual``; se não houver,
 * o primeiro sem decisão antes dele; se todos estiverem decididos, fica onde está.
 */
export function proximoPendente(momentos: Momento[], atual: number): number {
  const pendente = (i: number) => momentos[i]?.decisao === null;
  for (let i = atual + 1; i < momentos.length; i++) if (pendente(i)) return i;
  for (let i = 0; i < atual; i++) if (pendente(i)) return i;
  return atual;
}

/** Troca o momento da lista pela versão nova que a API devolveu. */
export function trocarMomento(momentos: Momento[], novo: Momento): Momento[] {
  return momentos.map((m) => (m.id === novo.id ? novo : m));
}

/** O ponto de partida do rascunho: a decisão salva, se houver; senão, as sugestões. */
export function rascunhoInicial(momento: Momento, config: Config): Rascunho {
  const decisao = momento.decisao?.aprovado ? momento.decisao : null;
  const cortados = momento.clipes.map((c) => c.formato);
  const destinos: Record<string, string[]> = {};
  for (const formato of cortados) {
    destinos[formato] = decisao
      ? (decisao.destinos[formato] ?? [])
      : (momento.destinos_padrao[formato] ?? config.destinos_padrao[formato] ?? []);
  }
  const titulo = decisao?.titulo ?? momento.titulos_sugeridos[0]?.titulo ?? "";
  return {
    titulo,
    sugestao: momento.titulos_sugeridos.findIndex((s) => s.titulo === titulo),
    hashtags: (decisao?.hashtags ?? momento.hashtags_sugeridas).join(" "),
    destinos,
    inicio_s: momento.inicio_s,
    fim_s: momento.fim_s,
  };
}

/** Tab: a próxima (ou anterior) das sugestões, dando a volta. */
export function trocarSugestao(rascunho: Rascunho, momento: Momento, passo: 1 | -1): Rascunho {
  const total = momento.titulos_sugeridos.length;
  if (total === 0) return rascunho;
  const atual = rascunho.sugestao < 0 ? (passo === 1 ? -1 : 0) : rascunho.sugestao;
  const indice = (((atual + passo) % total) + total) % total;
  return { ...rascunho, sugestao: indice, titulo: momento.titulos_sugeridos[indice]!.titulo };
}

/** Shift + número: tira o formato da aprovação, ou põe de volta com os destinos padrão. */
export function alternarFormato(rascunho: Rascunho, formato: string, config: Config): Rascunho {
  if (!(formato in rascunho.destinos)) return rascunho; // não foi cortado
  const incluido = (rascunho.destinos[formato] ?? []).length > 0;
  return {
    ...rascunho,
    destinos: {
      ...rascunho.destinos,
      [formato]: incluido ? [] : [...(config.destinos_padrao[formato] ?? config.plataformas)],
    },
  };
}

/** Liga ou desliga uma plataforma de um formato (clique). */
export function alternarPlataforma(
  rascunho: Rascunho,
  formato: string,
  plataforma: string,
): Rascunho {
  const atuais = rascunho.destinos[formato] ?? [];
  const novas = atuais.includes(plataforma)
    ? atuais.filter((p) => p !== plataforma)
    : [...atuais, plataforma];
  return { ...rascunho, destinos: { ...rascunho.destinos, [formato]: novas } };
}

/** "#lol lolbr, #pentakill" -> ["#lol", "#lolbr", "#pentakill"] (a API confere o resto). */
export function separarHashtags(texto: string): string[] {
  return texto
    .split(/[\s,]+/)
    .filter(Boolean)
    .map((h) => (h.startsWith("#") ? h : `#${h}`));
}

/** O que vai para a API ao aprovar (só os formatos com algum destino). */
export function pedidoDeAprovacao(rascunho: Rascunho, momento: Momento) {
  const destinos = Object.fromEntries(
    Object.entries(rascunho.destinos).filter(([, plataformas]) => plataformas.length > 0),
  );
  const sugestao = momento.titulos_sugeridos[rascunho.sugestao];
  const titulo = rascunho.titulo.trim();
  return {
    aprovado: true,
    destinos,
    titulo,
    hashtags: separarHashtags(rascunho.hashtags),
    // Só manda o tipo se o título é exatamente o da sugestão (editado = sem tipo).
    tipo_titulo: sugestao && sugestao.titulo === titulo ? (sugestao.tipo ?? null) : null,
  };
}

/**
 * Pilha do desfazer: os ids decididos nesta sessão, do mais antigo ao mais novo. ``U``
 * desfaz o último, depois o penúltimo...
 */
export function empilhar(pilha: number[], id: number): number[] {
  return [...pilha.filter((x) => x !== id), id];
}

export function desempilhar(pilha: number[]): { id: number | null; pilha: number[] } {
  if (pilha.length === 0) return { id: null, pilha };
  return { id: pilha[pilha.length - 1]!, pilha: pilha.slice(0, -1) };
}

/** O YouTube trata como Short o vídeo vertical de até 3 min (os clipes podem ser maiores). */
export const DURACAO_MAXIMA_DO_SHORT_S = 180;

export function ehShort(formato: string, duracaoS: number | null): boolean {
  return formato.startsWith("vertical") && (duracaoS === null || duracaoS <= DURACAO_MAXIMA_DO_SHORT_S);
}

function quando(iso: string | null): string {
  if (!iso) return "?";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

/** O TikTok não tem horário: o vídeo vai para a pasta (e, no modo rascunho, para o app). */
function textoDoTiktok(p: Publicacao): string {
  switch (p.status) {
    case "pendente":
      return "na fila (U ainda cancela)";
    case "enviando":
      return "enviando agora";
    case "na_pasta":
      return "na pasta de exportação, com a legenda no .txt: poste pelo celular";
    case "rascunho":
      return "rascunho no app do TikTok: finalize pela notificação (a legenda está no .txt)";
    case "erro":
      return "erro no envio (veja a tela Fila e status)";
    default:
      return p.status;
  }
}

/**
 * Ex.: "na fila, vai ao ar 28/10/2026, 18:00 (U ainda cancela)", "enviando agora...",
 * "enviado, vai ao ar 28/10/2026, 18:00", "publicado em ..." ou o aviso de erro.
 */
export function textoDaPublicacao(p: Publicacao, agora: Date = new Date()): string {
  if (p.plataforma === "tiktok") return textoDoTiktok(p);
  if (p.status === "pendente") {
    return `na fila, vai ao ar ${quando(p.agendada_para)} (U ainda cancela)`;
  }
  if (p.status === "enviando") return `enviando agora, vai ao ar ${quando(p.agendada_para)}`;
  if (p.status === "erro") return "erro no envio (veja a tela Fila e status)";
  if (p.agendada_para && new Date(p.agendada_para) <= agora) {
    return `publicado em ${quando(p.agendada_para)}`;
  }
  return `enviado, vai ao ar ${quando(p.agendada_para)}`;
}
