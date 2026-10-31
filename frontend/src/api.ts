// Conversa com a API local (127.0.0.1). Nada de chaves aqui: tudo o que está no navegador
// pode ser lido por quem abrir as ferramentas de desenvolvedor.
import { apiDaDemoEstatica } from "./demo/apiEstatica";
import { ErroDaApi } from "./erros";
import type {
  Aprendizado,
  Config,
  Dia,
  Engajamento,
  Estatisticas,
  ListaMomentos,
  Momento,
  Status,
} from "./tipos";

export { ErroDaApi };

/** A mensagem que a API mandou (``detail``), em texto. */
function explicar(corpo: unknown, status: number): string {
  const detalhe = (corpo as { detail?: unknown } | null)?.detail;
  if (typeof detalhe === "string") return detalhe;
  if (Array.isArray(detalhe)) {
    return detalhe.map((d: { msg?: string }) => d.msg ?? String(d)).join("; ");
  }
  return `Erro ${status} na API.`;
}

async function pedir<T>(caminho: string, opcoes: RequestInit = {}): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(caminho, {
      ...opcoes,
      headers: opcoes.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ErroDaApi(0, "Sem resposta da API: o 'clipador web' está aberto?");
  }
  const corpo = await resposta.json().catch(() => null);
  if (!resposta.ok) throw new ErroDaApi(resposta.status, explicar(corpo, resposta.status));
  return corpo as T;
}

const json = (dados: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(dados) });

const apiLocal = {
  config: () => pedir<Config>("/api/config"),
  dias: () => pedir<Dia[]>("/api/dias"),
  momentos: (dia: string | null, verMais: boolean) => {
    const parametros = new URLSearchParams();
    if (dia) parametros.set("dia", dia);
    if (verMais) parametros.set("ver_mais", "true");
    const texto = parametros.toString();
    return pedir<ListaMomentos>(`/api/momentos${texto ? `?${texto}` : ""}`);
  },
  momento: (id: number) => pedir<Momento>(`/api/momentos/${id}`),
  decidir: (
    id: number,
    decisao: {
      aprovado: boolean;
      destinos?: Record<string, string[]>;
      titulo?: string;
      hashtags?: string[];
      tipo_titulo?: string | null;
    },
  ) => pedir<Momento>(`/api/momentos/${id}/decisao`, json(decisao)),
  desfazer: (id: number) => pedir<Momento>(`/api/momentos/${id}/decisao`, { method: "DELETE" }),
  ajustar: (id: number, inicio_s: number, fim_s: number) =>
    pedir<{ tarefa_id: number | null; momento: Momento }>(
      `/api/momentos/${id}/ajuste`,
      json({ inicio_s, fim_s }),
    ),
  status: () => pedir<Status>("/api/status"),
  estatisticas: () => pedir<Estatisticas>("/api/estatisticas"),
  aprendizado: () => pedir<Aprendizado>("/api/aprendizado"),
  /** ``decisoes``: quantas havia na sugestão que você viu (se mudou, a API recusa). */
  aceitarAprendizado: (decisoes: number) =>
    pedir<Aprendizado>("/api/aprendizado/aceitar", json({ decisoes })),
  engajamento: () => pedir<Engajamento>("/api/engajamento"),
  /** ``cortes``: quantos havia na sugestão que você viu (se mudou, a API recusa). */
  aceitarEngajamento: (cortes: number) =>
    pedir<Engajamento>("/api/engajamento/aceitar", json({ cortes })),
};

export type Api = typeof apiLocal;

/**
 * A versão publicada no GitHub Pages (``vite build --mode estatica``) não tem servidor: lê as
 * respostas gravadas da demo. A condição é decidida no build, e o build normal nem inclui a
 * demo estática.
 */
export const api: Api = import.meta.env.MODE === "estatica" ? apiDaDemoEstatica : apiLocal;
