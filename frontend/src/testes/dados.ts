// Dados de exemplo para os testes, no mesmo formato da API.
import type { Config, Momento } from "../tipos";

export const CONFIG: Config = {
  limite_diario: 15,
  duracao_minima_s: 15,
  duracao_maxima_s: 60,
  formatos: ["horizontal", "vertical_a"],
  plataformas: ["youtube", "tiktok"],
  plataformas_por_formato: { horizontal: ["youtube"], vertical_a: ["youtube", "tiktok"] },
  destinos_padrao: {
    horizontal: ["youtube"],
    vertical_a: ["youtube", "tiktok"],
    vertical_b: ["youtube", "tiktok"],
  },
  max_hashtags: 5,
  limite_titulo: 100,
};

export function momentoDeTeste(id: number, nota = 90 - id): Momento {
  return {
    id,
    gravacao_id: 1,
    dia: "2026-10-23",
    posicao: id,
    nota,
    motivo: "voz alta (+24 dB)",
    sinais: { volume_voz: 1 },
    inicio_s: 100,
    fim_s: 120,
    duracao_s: 20,
    pico_s: 110,
    duracao_gravacao_s: 3600,
    inicio_detectado_s: null,
    fim_detectado_s: null,
    titulos_sugeridos: [
      { titulo: "NÃO ACREDITO", tipo: "frase_sua" },
      { titulo: "Que jogada", tipo: "exagero" },
      { titulo: "Eu avisei", tipo: "descricao" },
    ],
    hashtags_sugeridas: ["#leagueoflegends", "#lolbr"],
    clipes: [
      { id: id * 10, formato: "horizontal", duracao_s: 20, video: `/v/${id}h`, disponivel: true },
      { id: id * 10 + 1, formato: "vertical_a", duracao_s: 20, video: `/v/${id}v`, disponivel: true },
    ],
    thumbnail: null,
    destinos_padrao: { horizontal: ["youtube"], vertical_a: ["youtube", "tiktok"] },
    decisao: null,
    recortando: false,
    publicado: false,
    publicacoes: [],
  };
}
