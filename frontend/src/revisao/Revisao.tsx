import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ErroDaApi } from "../api";
import type { Config, Dia, ListaMomentos, Momento, Rascunho } from "../tipos";
import { acaoDaTecla, type Acao, LISTA_DE_ATALHOS } from "./atalhos";
import { type Limites, moverFim, moverInicio, mudou, relogio, type Trecho } from "./corte";
import {
  alternarFormato,
  alternarPlataforma,
  contador,
  desempilhar,
  empilhar,
  pedidoDeAprovacao,
  proximoPendente,
  rascunhoInicial,
  trocarMomento,
  trocarSugestao,
} from "./estado";
import { LinhaDoTempo } from "./LinhaDoTempo";
import { Player } from "./Player";

const NOMES_DOS_FORMATOS: Record<string, string> = {
  horizontal: "Horizontal",
  vertical_a: "Vertical (fundo desfocado)",
  vertical_b: "Vertical (câmera + jogo)",
};
const NOMES_DAS_PLATAFORMAS: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok" };

type Aviso = { texto: string; tipo: "erro" | "info" } | null;

// Campos onde se digita. Uma caixa de marcar também é um <input>, mas nela não se digita:
// depois de clicar em "TikTok", os atalhos continuam valendo.
const SEM_DIGITACAO = new Set(["checkbox", "radio", "button", "submit", "range"]);

export function emCampoDeTexto(ativo: Element | null = document.activeElement): boolean {
  if (ativo instanceof HTMLTextAreaElement) return true;
  return ativo instanceof HTMLInputElement && !SEM_DIGITACAO.has(ativo.type);
}

function dataCurta(dia: string): string {
  const [ano, mes, d] = dia.split("-");
  return `${d}/${mes}/${ano}`;
}

/** ``visivel``: falso quando outra tela está aberta (atalhos desligados, vídeo pausado). */
export function Revisao({ visivel = true }: { visivel?: boolean }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [dias, setDias] = useState<Dia[]>([]);
  const [dia, setDia] = useState<string | null>(null);
  const [verMais, setVerMais] = useState(false);
  const [lista, setLista] = useState<ListaMomentos | null>(null);
  const [indice, setIndice] = useState(0);
  const [rascunhos, setRascunhos] = useState<Record<number, Rascunho>>({});
  const [formato, setFormato] = useState<string | null>(null);
  const [pilha, setPilha] = useState<number[]>([]);
  const [liberado, setLiberado] = useState(false);
  const [pausado, setPausado] = useState(false);
  const [tempo, setTempo] = useState(0);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [ocupado, setOcupado] = useState(false);
  const [ajuda, setAjuda] = useState(false);
  const campoTitulo = useRef<HTMLInputElement>(null);
  const campoHashtags = useRef<HTMLInputElement>(null);

  const mostrarErro = useCallback((erro: unknown) => {
    setAviso({ texto: erro instanceof Error ? erro.message : String(erro), tipo: "erro" });
  }, []);

  // ------------------------------------------------------------------ carregar
  useEffect(() => {
    Promise.all([api.config(), api.dias()])
      .then(([c, d]) => {
        setConfig(c);
        setDias(d);
      })
      .catch(mostrarErro);
  }, [mostrarErro]);

  // O momento que está na tela, para o "ver mais" recarregar a lista sem tirar você dele.
  const idNaTela = useRef<number | null>(null);

  useEffect(() => {
    let cancelado = false;
    api
      .momentos(dia, verMais)
      .then((nova) => {
        if (cancelado) return;
        const mesmo = nova.momentos.findIndex((m) => m.id === idNaTela.current);
        setLista(nova);
        // Senão, começa pelo 1º pendente (o de maior nota que falta revisar).
        setIndice(mesmo >= 0 ? mesmo : proximoPendente(nova.momentos, -1));
        if (!dia && nova.dia) setDia(nova.dia);
      })
      .catch(mostrarErro);
    return () => {
      cancelado = true;
    };
  }, [dia, verMais, mostrarErro]);

  const momentos = lista?.momentos ?? [];
  const momento: Momento | undefined = momentos[indice];
  idNaTela.current = momento?.id ?? null;
  const rascunho: Rascunho | null =
    momento && config ? (rascunhos[momento.id] ?? rascunhoInicial(momento, config)) : null;

  // Com um recorte na fila, confere a cada 5 s se ele ficou pronto (o serviço corta em
  // segundo plano) e troca o momento pela versão nova, com o vídeo novo.
  const idRecortando = momento?.recortando ? momento.id : null;
  useEffect(() => {
    if (idRecortando === null) return;
    const relogio = window.setInterval(() => {
      api
        .momento(idRecortando)
        .then((novo) => {
          if (novo.recortando) return;
          setLista((l) => (l ? { ...l, momentos: trocarMomento(l.momentos, novo) } : l));
          setAviso({ texto: "Novo corte pronto.", tipo: "info" });
        })
        .catch(() => {}); // tenta de novo na próxima volta
    }, 5000);
    return () => window.clearInterval(relogio);
  }, [idRecortando]);

  const formatosCortados = useMemo(() => momento?.clipes.map((c) => c.formato) ?? [], [momento]);
  const clipe =
    momento?.clipes.find((c) => c.formato === formato) ?? momento?.clipes[0] ?? undefined;

  useEffect(() => {
    // Ao trocar de momento, mantém o formato exibido se ele existir nesse momento.
    if (formatosCortados.length && (!formato || !formatosCortados.includes(formato))) {
      setFormato(formatosCortados[0]!);
    }
  }, [formatosCortados, formato]);

  const limites: Limites | null =
    config && momento
      ? {
          minimo_s: config.duracao_minima_s,
          maximo_s: config.duracao_maxima_s,
          gravacao_s: momento.duracao_gravacao_s,
        }
      : null;

  // Toda mudança parte da versão MAIS NOVA do rascunho (e não da desta tela): duas teclas
  // apertadas antes de a tela redesenhar (ex.: segurar "]") contam as duas.
  const mudarRascunho = useCallback(
    (mudar: (atual: Rascunho) => Rascunho) => {
      if (!momento || !config) return;
      setRascunhos((todos) => ({
        ...todos,
        [momento.id]: mudar(todos[momento.id] ?? rascunhoInicial(momento, config)),
      }));
    },
    [momento, config],
  );

  // ------------------------------------------------------------------ ações
  function depoisDeDecidir(novo: Momento) {
    if (!lista) return;
    const novos = trocarMomento(lista.momentos, novo);
    setLista({ ...lista, momentos: novos, pendentes: novos.filter((m) => !m.decisao).length });
    setPilha((p) => empilhar(p, novo.id));
    const proximo = proximoPendente(novos, indice);
    if (proximo === indice && novos.every((m) => m.decisao)) {
      setAviso({ texto: "Tudo revisado neste dia. 🎉", tipo: "info" });
    } else {
      setAviso(null);
    }
    setIndice(proximo);
    setPausado(false);
  }

  async function aprovar() {
    if (!momento || !rascunho || ocupado) return;
    if (momento.publicado) return setAviso({ texto: "Já foi para a publicação.", tipo: "erro" });
    if (!rascunho.titulo.trim()) {
      setAviso({ texto: "Escreva ou escolha um título (T ou Tab).", tipo: "erro" });
      campoTitulo.current?.focus();
      return;
    }
    setOcupado(true);
    try {
      let atual = momento;
      if (mudou(rascunho, momento)) {
        // O novo corte entra na fila; o vídeo novo sai quando a fila rodar.
        atual = (await api.ajustar(momento.id, rascunho.inicio_s, rascunho.fim_s)).momento;
        if (lista) setLista({ ...lista, momentos: trocarMomento(lista.momentos, atual) });
      }
      depoisDeDecidir(await api.decidir(atual.id, pedidoDeAprovacao(rascunho, atual)));
    } catch (erro) {
      mostrarErro(erro);
    } finally {
      setOcupado(false);
    }
  }

  async function recusar() {
    if (!momento || ocupado) return;
    if (momento.publicado) return setAviso({ texto: "Já foi para a publicação.", tipo: "erro" });
    setOcupado(true);
    try {
      depoisDeDecidir(await api.decidir(momento.id, { aprovado: false }));
    } catch (erro) {
      mostrarErro(erro);
    } finally {
      setOcupado(false);
    }
  }

  async function desfazer() {
    if (ocupado || !lista) return;
    const { id, pilha: resto } = desempilhar(pilha);
    if (id === null) return setAviso({ texto: "Nada para desfazer.", tipo: "info" });
    setOcupado(true);
    try {
      const novo = await api.desfazer(id);
      setPilha(resto);
      const novos = trocarMomento(lista.momentos, novo);
      setLista({ ...lista, momentos: novos, pendentes: novos.filter((m) => !m.decisao).length });
      const posicao = novos.findIndex((m) => m.id === id);
      if (posicao >= 0) setIndice(posicao);
      setAviso({ texto: `Decisão do momento #${novo.posicao} desfeita.`, tipo: "info" });
    } catch (erro) {
      if (erro instanceof ErroDaApi && erro.status === 404) setPilha(resto); // já desfeita
      mostrarErro(erro);
    } finally {
      setOcupado(false);
    }
  }

  function ajustar(qual: "inicio" | "fim", delta: number) {
    if (!rascunho || !limites || !momento) return;
    if (momento.recortando) {
      return setAviso({ texto: "Há um novo corte na fila: espere ele ficar pronto.", tipo: "info" });
    }
    if (momento.publicado) return;
    mudarRascunho((r) => ({
      ...r,
      ...(qual === "inicio"
        ? moverInicio(r, r.inicio_s + delta, limites)
        : moverFim(r, r.fim_s + delta, limites)),
    }));
  }

  function executar(acao: Acao) {
    if (!momento || !rascunho || !config) return;
    switch (acao.tipo) {
      case "aprovar":
        return void aprovar();
      case "recusar":
        return void recusar();
      case "desfazer":
        return void desfazer();
      case "anterior":
        return setIndice((i) => Math.max(i - 1, 0));
      case "proximo":
        return setIndice((i) => Math.min(i + 1, momentos.length - 1));
      case "pausar":
        return setPausado((p) => !p);
      case "exibirFormato": {
        const escolhido = config.formatos[acao.indice];
        if (escolhido && formatosCortados.includes(escolhido)) setFormato(escolhido);
        return;
      }
      case "incluirFormato": {
        const escolhido = config.formatos[acao.indice];
        if (escolhido) mudarRascunho((r) => alternarFormato(r, escolhido, config));
        return;
      }
      case "editarTitulo":
        return campoTitulo.current?.focus();
      case "editarHashtags":
        return campoHashtags.current?.focus();
      case "sugestao":
        return mudarRascunho((r) => trocarSugestao(r, momento, acao.passo));
      case "inicio":
        return ajustar("inicio", acao.delta);
      case "fim":
        return ajustar("fim", acao.delta);
      case "verMais":
        if (lista && lista.restantes > 0) setVerMais(true);
        else setAviso({ texto: "Não há mais momentos neste dia.", tipo: "info" });
        return;
      case "ajuda":
        return setAjuda((a) => !a);
    }
  }

  // Uma referência sempre atualizada: o ouvinte do teclado é registrado uma vez só.
  const executarAtual = useRef(executar);
  executarAtual.current = executar;
  const liberadoAtual = useRef(liberado);
  liberadoAtual.current = liberado;
  const ajudaAberta = useRef(ajuda);
  ajudaAberta.current = ajuda;
  const visivelAgora = useRef(visivel);
  visivelAgora.current = visivel;

  useEffect(() => {
    function aoTeclar(evento: KeyboardEvent) {
      if (!visivelAgora.current) return; // outra tela aberta: as teclas são dela
      if (!liberadoAtual.current) {
        // A 1ª tecla só libera o som (regra dos navegadores para autoplay).
        if (evento.ctrlKey || evento.metaKey || evento.altKey) return;
        evento.preventDefault();
        setLiberado(true);
        return;
      }
      if (ajudaAberta.current) {
        // Com a ajuda na tela, as teclas só a fecham (Esc ou ?): nada acontece escondido.
        if (evento.key === "Escape" || evento.key === "?") {
          evento.preventDefault();
          setAjuda(false);
        }
        return;
      }
      const acao = acaoDaTecla(evento, emCampoDeTexto());
      if (!acao) return;
      evento.preventDefault(); // espaço não rola a página, Tab não pula o foco
      executarAtual.current(acao);
    }
    window.addEventListener("keydown", aoTeclar);
    return () => window.removeEventListener("keydown", aoTeclar);
  }, []);

  function teclaNoCampo(evento: React.KeyboardEvent<HTMLInputElement>, deTitulo: boolean) {
    if (evento.key === "Escape" || evento.key === "Enter") {
      evento.preventDefault();
      evento.currentTarget.blur();
    } else if (deTitulo && evento.key === "Tab" && momento && rascunho) {
      evento.preventDefault();
      const passo = evento.shiftKey ? -1 : 1;
      mudarRascunho((r) => trocarSugestao(r, momento, passo));
    }
  }

  // ------------------------------------------------------------------ tela
  if (!config || !lista) {
    return (
      <main className="p-8 text-[var(--texto-2)]">
        {aviso ? <p className="text-[var(--erro)]">{aviso.texto}</p> : "Carregando…"}
      </main>
    );
  }
  if (!momento || !rascunho || !limites) {
    return (
      <main className="p-8 text-[var(--texto-2)]">
        Nenhum momento para revisar ainda. Depois da live, o serviço processa a gravação de
        madrugada e os momentos aparecem aqui (na demo: <code>clipador processar --demo</code>).
      </main>
    );
  }

  const { revisados, total } = contador(momentos);
  const cortado: Trecho = { inicio_s: momento.inicio_s, fim_s: momento.fim_s };
  const vertical = (clipe?.formato ?? "").startsWith("vertical");
  const decisao = momento.decisao;

  return (
    <main className="mx-auto grid max-w-[1400px] gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <section className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <select
            aria-label="Dia da live"
            className="rounded-md border border-[var(--borda)] bg-[var(--superficie)] px-2 py-1"
            value={lista.dia ?? ""}
            onChange={(e) => {
              setVerMais(false);
              setPilha([]);
              setDia(e.target.value);
            }}
          >
            {dias.map((d) => (
              <option key={d.dia} value={d.dia}>
                Live de {dataCurta(d.dia)} ({d.pendentes} pendentes)
              </option>
            ))}
          </select>
          <span className="font-semibold tabular-nums" data-testid="contador">
            {revisados} de {total} revisados
          </span>
          {lista.restantes > 0 && (
            <button
              className="text-[var(--destaque)] underline"
              onClick={() => setVerMais(true)}
              title="Atalho: M"
            >
              ver mais ({lista.restantes})
            </button>
          )}
          <button className="ml-auto text-[var(--texto-fraco)]" onClick={() => setAjuda(true)}>
            atalhos (?)
          </button>
        </div>

        <div className="relative flex justify-center rounded-lg bg-black">
          {clipe && clipe.disponivel ? (
            <Player
              // O endereço muda com o corte: depois de um recorte, o player baixa o arquivo
              // novo em vez de mostrar o antigo, que tem o mesmo nome. Durante o recorte o
              // trecho já é o novo e o arquivo ainda é o velho: "-antigo" separa os dois.
              url={`${clipe.video}?corte=${momento.inicio_s}-${momento.fim_s}${
                momento.recortando ? "-antigo" : ""
              }`}
              deslocamento_s={momento.inicio_s}
              trecho={rascunho}
              liberado={liberado}
              pausado={pausado || !visivel}
              vertical={vertical}
              restringir={!momento.recortando}
              aoAndar={setTempo}
            />
          ) : (
            <p className="p-16 text-[var(--texto-2)]">
              {clipe ? "O arquivo deste clipe não existe mais." : "Este momento não tem clipes."}
            </p>
          )}
          {!liberado && (
            <button
              className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/70 text-lg"
              onClick={() => setLiberado(true)}
            >
              Aperte qualquer tecla para começar
            </button>
          )}
          {pausado && liberado && (
            <span className="absolute left-3 top-3 rounded bg-black/70 px-2 py-1 text-sm">
              ⏸ pausado (espaço)
            </span>
          )}
        </div>

        <div className="flex gap-2" role="tablist" aria-label="Formato exibido">
          {config.formatos.map((f, i) => {
            const existe = formatosCortados.includes(f);
            return (
              <button
                key={f}
                role="tab"
                aria-selected={clipe?.formato === f}
                disabled={!existe}
                onClick={() => setFormato(f)}
                className={`rounded-md px-3 py-1 text-sm ${
                  clipe?.formato === f
                    ? "bg-[var(--destaque)] text-black"
                    : "bg-[var(--superficie)] text-[var(--texto-2)]"
                } disabled:opacity-40`}
              >
                {i + 1} · {NOMES_DOS_FORMATOS[f] ?? f}
              </button>
            );
          })}
        </div>

        <LinhaDoTempo
          trecho={rascunho}
          cortado={momento.recortando ? rascunho : cortado}
          limites={limites}
          tempo_s={momento.recortando ? null : tempo}
          bloqueada={momento.recortando || momento.publicado}
          aoMudar={(t) => mudarRascunho((r) => ({ ...r, ...t }))}
        />
        {momento.recortando && (
          <p className="text-sm text-[var(--aviso)]" role="status">
            ⏳ Novo corte na fila: o vídeo ainda é o antigo. Ele fica pronto quando a fila rodar
            (o serviço, ou <code>clipador processar</code>).
          </p>
        )}

        <ListaDeMomentos momentos={momentos} atual={indice} aoEscolher={setIndice} />
      </section>

      <aside className="flex flex-col gap-4 rounded-lg bg-[var(--superficie)] p-4">
        <div className="flex items-baseline justify-between">
          <span className="text-3xl font-bold tabular-nums">{Math.round(momento.nota)}</span>
          <span className="text-sm text-[var(--texto-fraco)]">
            #{momento.posicao} do dia · {relogio(momento.inicio_s)} da live
          </span>
        </div>
        <p className="text-sm text-[var(--texto-2)]">{momento.motivo}</p>

        <EstadoDaDecisao momento={momento} />

        <label className="flex flex-col gap-1 text-sm">
          <span className="flex justify-between text-[var(--texto-fraco)]">
            Título (T) · Tab troca a sugestão
            <span
              className={
                rascunho.titulo.length > config.limite_titulo ? "text-[var(--erro)]" : undefined
              }
            >
              {rascunho.titulo.length}/{config.limite_titulo}
            </span>
          </span>
          <input
            ref={campoTitulo}
            value={rascunho.titulo}
            onChange={(e) => {
              const titulo = e.target.value;
              mudarRascunho((r) => ({ ...r, titulo, sugestao: -1 }));
            }}
            onKeyDown={(e) => teclaNoCampo(e, true)}
            className="rounded-md border border-[var(--borda)] bg-[var(--fundo)] px-2 py-2 text-base"
          />
        </label>
        <ol className="flex flex-col gap-1 text-sm">
          {momento.titulos_sugeridos.map((s, i) => (
            <li key={i}>
              <button
                className={`w-full rounded px-2 py-1 text-left ${
                  i === rascunho.sugestao
                    ? "bg-[var(--destaque)]/20 text-[var(--texto)]"
                    : "text-[var(--texto-2)]"
                }`}
                onClick={() => mudarRascunho((r) => ({ ...r, sugestao: i, titulo: s.titulo }))}
              >
                {s.titulo} <span className="text-[var(--texto-fraco)]">({s.tipo})</span>
              </button>
            </li>
          ))}
        </ol>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-[var(--texto-fraco)]">
            Hashtags (H) · o YouTube recomenda até {config.max_hashtags}
          </span>
          <input
            ref={campoHashtags}
            value={rascunho.hashtags}
            onChange={(e) => {
              const hashtags = e.target.value;
              mudarRascunho((r) => ({ ...r, hashtags }));
            }}
            onKeyDown={(e) => teclaNoCampo(e, false)}
            className="rounded-md border border-[var(--borda)] bg-[var(--fundo)] px-2 py-2"
          />
        </label>

        <fieldset className="flex flex-col gap-2 text-sm">
          <legend className="mb-1 text-[var(--texto-fraco)]">
            Formatos e destinos (Shift + 1/2/3 tira ou põe)
          </legend>
          {formatosCortados.map((f) => {
            const escolhidas = rascunho.destinos[f] ?? [];
            return (
              <div key={f} className="flex flex-wrap items-center gap-2">
                <span className={escolhidas.length ? "" : "text-[var(--texto-fraco)] line-through"}>
                  {config.formatos.indexOf(f) + 1} · {NOMES_DOS_FORMATOS[f] ?? f}
                </span>
                {config.plataformas.map((p) => (
                  <label key={p} className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={escolhidas.includes(p)}
                      onChange={() => mudarRascunho((r) => alternarPlataforma(r, f, p))}
                    />
                    {NOMES_DAS_PLATAFORMAS[p] ?? p}
                    {p === "youtube" && f.startsWith("vertical") ? " Shorts" : ""}
                  </label>
                ))}
              </div>
            );
          })}
        </fieldset>

        <div className="mt-auto grid grid-cols-3 gap-2">
          <button
            onClick={() => void aprovar()}
            disabled={ocupado || momento.publicado}
            className="rounded-md bg-[var(--bom)] px-3 py-2 font-semibold text-black disabled:opacity-40"
          >
            ✓ Aprovar (A)
          </button>
          <button
            onClick={() => void recusar()}
            disabled={ocupado || momento.publicado}
            className="rounded-md bg-[var(--erro)] px-3 py-2 font-semibold text-white disabled:opacity-40"
          >
            ✗ Recusar (R)
          </button>
          <button
            onClick={() => void desfazer()}
            disabled={ocupado || pilha.length === 0}
            className="rounded-md border border-[var(--borda)] px-3 py-2 disabled:opacity-40"
          >
            ↶ Desfazer (U)
          </button>
        </div>
        {aviso && (
          <p
            role={aviso.tipo === "erro" ? "alert" : "status"}
            className={`text-sm ${aviso.tipo === "erro" ? "text-[var(--erro)]" : "text-[var(--texto-2)]"}`}
          >
            {aviso.texto}
          </p>
        )}
        {decisao === null && mudou(rascunho, cortado) && (
          <p className="text-xs text-[var(--texto-fraco)]">
            Corte ajustado: ao aprovar, o app corta de novo só este clipe.
          </p>
        )}
      </aside>

      {ajuda && <Ajuda aoFechar={() => setAjuda(false)} />}
    </main>
  );
}

function EstadoDaDecisao({ momento }: { momento: Momento }) {
  if (momento.publicado) return <p className="text-sm">📤 Já foi para a publicação.</p>;
  const decisao = momento.decisao;
  if (!decisao) return <p className="text-sm text-[var(--texto-fraco)]">● Pendente</p>;
  return decisao.aprovado ? (
    <p className="text-sm text-[var(--bom)]">✓ Aprovado (aprovar de novo troca a decisão)</p>
  ) : (
    <p className="text-sm text-[var(--erro)]">✗ Recusado</p>
  );
}

function ListaDeMomentos({
  momentos,
  atual,
  aoEscolher,
}: {
  momentos: Momento[];
  atual: number;
  aoEscolher: (i: number) => void;
}) {
  return (
    <ol className="flex gap-2 overflow-x-auto pb-2" aria-label="Momentos do dia">
      {momentos.map((m, i) => {
        const marca = m.decisao ? (m.decisao.aprovado ? "✓" : "✗") : "•";
        return (
          <li key={m.id}>
            <button
              onClick={() => aoEscolher(i)}
              aria-current={i === atual}
              className={`flex min-w-16 flex-col items-center rounded-md px-2 py-1 text-xs tabular-nums ${
                i === atual
                  ? "bg-[var(--destaque)] text-black"
                  : "bg-[var(--superficie)] text-[var(--texto-2)]"
              }`}
            >
              <span className="text-base font-semibold">{Math.round(m.nota)}</span>
              <span>
                {marca} {relogio(m.inicio_s)}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function Ajuda({ aoFechar }: { aoFechar: () => void }) {
  return (
    <div
      role="dialog"
      aria-label="Atalhos do teclado"
      className="fixed inset-0 flex items-center justify-center bg-black/70"
      onClick={aoFechar}
    >
      <div className="rounded-lg bg-[var(--superficie)] p-6">
        <h2 className="mb-3 text-lg font-semibold">Atalhos</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          {LISTA_DE_ATALHOS.map(([tecla, oQueFaz]) => (
            <div key={tecla} className="contents">
              <dt className="font-mono text-[var(--destaque)]">{tecla}</dt>
              <dd className="text-[var(--texto-2)]">{oQueFaz}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-[var(--texto-fraco)]">
          Enquanto você digita no título ou nas hashtags, os atalhos ficam desligados.
          <br />
          Esc ou ? fecha esta ajuda.
        </p>
      </div>
    </div>
  );
}
