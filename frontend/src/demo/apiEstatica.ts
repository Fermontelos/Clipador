// A revisão sem servidor, para a demo no GitHub Pages: as respostas da API da demo, gravadas
// por docs/gerar_demo_estatica.py, e as decisões só na memória desta aba (recarregar volta
// ao começo). Nada sai do navegador.
import type { Api } from "../api";
import { ErroDaApi } from "../erros";
import type {
  Aprendizado,
  Config,
  Decisao,
  Dia,
  Engajamento,
  Estatisticas,
  ListaMomentos,
  Momento,
  Publicacao,
  Status,
} from "../tipos";

interface Gravado {
  config: Config;
  dias: Dia[];
  /** "dia|mais": a lista de GET /api/momentos (dia vazio = o padrão; "mais" = ver mais). */
  listas: Record<string, ListaMomentos>;
  momentos: Record<string, Momento>;
  status: Status;
  estatisticas: Estatisticas;
  aprendizado: Aprendizado;
  engajamento: Engajamento;
}

/** O que mudou nesta aba: decisões (null = desfeita) e ajustes do corte. */
const decisoes = new Map<number, Decisao | null>();
const cortes = new Map<number, { inicio_s: number; fim_s: number }>();
let aprendizado: Aprendizado | null = null;
let engajamento: Engajamento | null = null;
let gravado: Promise<Gravado> | null = null;

function carregar(): Promise<Gravado> {
  const base = import.meta.env.BASE_URL;
  gravado ??= fetch(`${base}demo/dados.json`)
    .then((resposta) => {
      if (!resposta.ok) throw new ErroDaApi(resposta.status, "A demo não foi gerada.");
      return resposta.text();
    })
    // Os vídeos e as thumbnails estão ao lado do dados.json ("demo/videos/7.mp4").
    .then((texto) => JSON.parse(texto.replaceAll('"demo/', `"${base}demo/`)) as Gravado);
  return gravado;
}

function publicacoesDe(id: number, decisao: Decisao): Publicacao[] {
  return Object.entries(decisao.destinos).flatMap(([formato, plataformas], i) =>
    plataformas.map((plataforma, j) => ({
      id: id * 100 + i * 10 + j,
      plataforma,
      formato,
      short: formato !== "horizontal",
      status: "pendente",
      agendada_para: null,
      link: null,
      enviada_em: null,
    })),
  );
}

/** O momento como a API o devolveria agora, com o que mudou nesta aba. */
function atual(momento: Momento): Momento {
  const corte = cortes.get(momento.id);
  const comCorte = corte ? { ...momento, ...corte, duracao_s: corte.fim_s - corte.inicio_s } : momento;
  if (!decisoes.has(momento.id)) return comCorte;
  const decisao = decisoes.get(momento.id) ?? null;
  return {
    ...comCorte,
    decisao,
    publicacoes: decisao?.aprovado ? publicacoesDe(momento.id, decisao) : [],
  };
}

/** O momento como foi gravado (antes de qualquer ajuste nesta aba). */
async function gravadoDe(id: number): Promise<Momento> {
  const achado = (await carregar()).momentos[String(id)];
  if (!achado) throw new ErroDaApi(404, `O momento ${id} não existe.`);
  return achado;
}

async function momento(id: number): Promise<Momento> {
  return atual(await gravadoDe(id));
}

function semPublicar(m: Momento): void {
  if (m.publicado) throw new ErroDaApi(409, "Esse momento já foi publicado: a decisão está travada.");
}

export const apiDaDemoEstatica: Api = {
  config: async () => (await carregar()).config,
  dias: async () => {
    const dados = await carregar();
    return dados.dias.map((dia) => {
      const todos = (dados.listas[`${dia.dia}|mais`]?.momentos ?? []).map(atual);
      const decididos = todos.filter((m) => m.decisao).length;
      return { ...dia, decididos, pendentes: todos.length - decididos };
    });
  },
  momentos: async (dia, verMais) => {
    const dados = await carregar();
    const lista = dados.listas[`${dia ?? ""}|${verMais ? "mais" : ""}`] ?? dados.listas["|"];
    if (!lista) throw new ErroDaApi(404, `Sem momentos no dia ${dia}.`);
    const momentos = lista.momentos.map(atual);
    return { ...lista, momentos, pendentes: momentos.filter((m) => !m.decisao).length };
  },
  momento,
  decidir: async (id, pedido) => {
    const m = await gravadoDe(id);
    semPublicar(m);
    const destinos = pedido.aprovado ? (pedido.destinos ?? {}) : {};
    const corte = cortes.get(id);
    decisoes.set(id, {
      aprovado: pedido.aprovado,
      formatos: Object.keys(destinos).filter((f) => (destinos[f] ?? []).length > 0),
      destinos,
      titulo: pedido.aprovado ? (pedido.titulo ?? null) : null,
      hashtags: pedido.aprovado ? (pedido.hashtags ?? []) : [],
      tipo_titulo: pedido.aprovado ? (pedido.tipo_titulo ?? null) : null,
      ajuste_inicio_s: corte ? corte.inicio_s - (m.inicio_detectado_s ?? m.inicio_s) : null,
      ajuste_fim_s: corte ? corte.fim_s - (m.fim_detectado_s ?? m.fim_s) : null,
      decidida_em: new Date().toISOString(),
    });
    return momento(id);
  },
  desfazer: async (id) => {
    const m = await momento(id);
    if (!m.decisao) throw new ErroDaApi(404, "Esse momento não tem decisão para desfazer.");
    semPublicar(m);
    decisoes.set(id, null);
    return momento(id);
  },
  // Sem FFmpeg no navegador: o trecho muda, mas o vídeo continua o do corte original.
  ajustar: async (id, inicio_s, fim_s) => {
    semPublicar(await momento(id));
    cortes.set(id, { inicio_s, fim_s });
    return { tarefa_id: null, momento: await momento(id) };
  },
  status: async () => (await carregar()).status,
  estatisticas: async () => (await carregar()).estatisticas,
  aprendizado: async () => aprendizado ?? (await carregar()).aprendizado,
  aceitarAprendizado: async () => {
    const antes = aprendizado ?? (await carregar()).aprendizado;
    aprendizado = {
      ...antes,
      versao_atual: (antes.versao_atual ?? 0) + 1,
      pesos_atuais: antes.pesos_novos ?? antes.pesos_atuais,
      pesos_novos: null,
      sem_pesos_porque: "Aceito nesta aba (na demo do navegador, nada é gravado).",
    };
    return aprendizado;
  },
  engajamento: async () => engajamento ?? (await carregar()).engajamento,
  aceitarEngajamento: async () => {
    const antes = engajamento ?? (await carregar()).engajamento;
    engajamento = {
      ...antes,
      sugestao: {
        ...antes.sugestao,
        pesos_atuais: antes.sugestao.pesos_novos ?? antes.sugestao.pesos_atuais,
        pesos_novos: null,
        sem_pesos_porque: "Aceito nesta aba (na demo do navegador, nada é gravado).",
      },
    };
    return engajamento;
  },
};
