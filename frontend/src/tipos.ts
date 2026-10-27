// O formato dos dados da API (api/src/clipador_api/esquemas.py). Mantenha os dois iguais.

export interface Clipe {
  id: number;
  formato: string;
  duracao_s: number | null;
  video: string;
  disponivel: boolean;
}

export interface Sugestao {
  titulo: string;
  tipo?: string;
}

export interface Publicacao {
  id: number;
  plataforma: string;
  formato: string;
  /** Sobe como Short (vertical de até 3 min). */
  short: boolean;
  /** pendente (na fila: U cancela), enviando (subindo agora), agendada (já no YouTube) ou erro. */
  status: string;
  agendada_para: string | null;
  link: string | null;
  enviada_em: string | null;
}

export interface Decisao {
  aprovado: boolean;
  formatos: string[];
  destinos: Record<string, string[]>;
  titulo: string | null;
  hashtags: string[];
  tipo_titulo: string | null;
  ajuste_inicio_s: number | null;
  ajuste_fim_s: number | null;
  decidida_em: string;
}

export interface Momento {
  id: number;
  gravacao_id: number;
  dia: string;
  posicao: number;
  nota: number;
  motivo: string;
  sinais: Record<string, number>;
  inicio_s: number;
  fim_s: number;
  duracao_s: number;
  pico_s: number | null;
  duracao_gravacao_s: number | null;
  inicio_detectado_s: number | null;
  fim_detectado_s: number | null;
  titulos_sugeridos: Sugestao[];
  hashtags_sugeridas: string[];
  clipes: Clipe[];
  thumbnail: string | null;
  destinos_padrao: Record<string, string[]>;
  decisao: Decisao | null;
  recortando: boolean;
  publicado: boolean;
  publicacoes: Publicacao[];
}

export interface ListaMomentos {
  dia: string | null;
  limite_diario: number;
  total: number;
  pendentes: number;
  restantes: number;
  momentos: Momento[];
}

export interface Dia {
  dia: string;
  momentos: number;
  decididos: number;
  pendentes: number;
}

export interface Config {
  limite_diario: number;
  duracao_minima_s: number;
  duracao_maxima_s: number;
  formatos: string[];
  plataformas: string[];
  destinos_padrao: Record<string, string[]>;
  max_hashtags: number;
  limite_titulo: number;
}

export interface Tarefa {
  id: number;
  tipo: string;
  status: string;
  gravacao_id: number | null;
  momento_id: number | null;
  tentativas: number;
  ultimo_erro: string | null;
  atualizada_em: string;
}

export interface Status {
  fila: Record<string, number>;
  em_andamento: Tarefa[];
  erros: Tarefa[];
  cota_youtube: {
    envios: number;
    limite_envios: number;
    unidades: number;
    limite_unidades: number;
    renova_em: string;
  };
  dias: Dia[];
}

export interface Contagem {
  momentos: number;
  aprovados: number;
  recusados: number;
}

export interface Estatisticas extends Contagem {
  precisao: number | null;
  por_dia: (Contagem & { dia: string })[];
  por_nota: (Contagem & { faixa: string })[];
  sinais: { detector: string; aprovados: number | null; recusados: number | null }[];
  formatos: Record<string, number>;
  plataformas: Record<string, number>;
  ajuste_medio_inicio_s: number | null;
  ajuste_medio_fim_s: number | null;
}

/** O que você está montando para um momento, antes de aprovar. */
export interface Rascunho {
  titulo: string;
  /** Qual das sugestões está no campo (-1 = um título seu). */
  sugestao: number;
  hashtags: string;
  /** Formato -> plataformas escolhidas (lista vazia = formato fora). */
  destinos: Record<string, string[]>;
  inicio_s: number;
  fim_s: number;
}

export interface JanelaSugerida {
  segundos_antes: number;
  segundos_depois: number;
  media_inicio_s: number;
  media_fim_s: number;
  aprovados: number;
}

export interface VersaoAceita {
  versao: number;
  criada_em: string;
  pesos: Record<string, number>;
  precisao_antes: number | null;
  precisao: number | null;
  decisoes: number | null;
  segundos_antes: number | null;
  segundos_depois: number | null;
}

/** O que as suas decisões ensinam (etapa 14). Nada muda até você aceitar. */
export interface Aprendizado {
  decisoes: number;
  aprovadas: number;
  minimo_para_pesos: number;
  versao_atual: number | null;
  pesos_atuais: Record<string, number>;
  pesos_novos: Record<string, number> | null;
  precisao_antes: number | null;
  precisao_depois: number | null;
  sem_pesos_porque: string | null;
  janela_atual: [number, number];
  janela_nova: JanelaSugerida | null;
  sem_janela_porque: string | null;
  precisao_por_dia: { dia: string; decididos: number; aprovados: number; precisao: number }[];
  versoes: VersaoAceita[];
}
