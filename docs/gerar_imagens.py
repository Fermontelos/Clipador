"""Gera as imagens do README (etapa 18): os gráficos da demo e o selo de cobertura.

    uv run python docs/gerar_imagens.py               # os gráficos (dados da demo)
    uv run python docs/gerar_imagens.py --cobertura   # e o selo (depois de "pytest --cov")

Os gráficos usam **os dados inventados da demo** (``clipador.demo.historico``: 6 lives,
120 decisões e 118 cortes "publicados"), num banco temporário: não precisam de vídeo, de
internet nem do seu banco. Eles mostram o app funcionando, não resultados de lives reais.

São SVGs com o tema claro e o escuro (``prefers-color-scheme``), nas cores da paleta já
validada da interface (contraste e daltonismo): o GitHub mostra o README nos dois temas.
"""

import json
import sys
import tempfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent / "imagens"

ESTILO = """<style>
  svg { --fundo:#fcfcfb; --texto:#0b0b0b; --texto-2:#52514e; --fraco:#898781;
        --grade:#e1e0d9; --base:#c3c2b7; --s1:#2a78d6; --s2:#eb6834; }
  @media (prefers-color-scheme: dark) {
    svg { --fundo:#1a1a19; --texto:#f0efec; --texto-2:#c3c2b7; --fraco:#898781;
          --grade:#2c2c2a; --base:#383835; --s1:#3987e5; --s2:#d95926; }
  }
  text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--texto-2); }
  .titulo { font-size: 15px; font-weight: 600; fill: var(--texto); }
  .nota { font-size: 11px; fill: var(--fraco); }
  .rotulo { font-size: 13px; }
  .valor { font-size: 13px; font-weight: 600; fill: var(--texto);
           font-variant-numeric: tabular-nums; }
</style>"""


def br(valor: float, casas: int = 2) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _barra(x: float, y: float, largura: float, altura: float, cor: str) -> str:
    """Barra horizontal com o fim arredondado (4 px) e o começo reto, colado na base."""
    r = min(4.0, largura / 2, altura / 2)
    if largura <= 0:
        return ""
    return (
        f'<path d="M{x},{y} h{largura - r} a{r},{r} 0 0 1 {r},{r} v{altura - 2 * r} '
        f'a{r},{r} 0 0 1 {-r},{r} h{-(largura - r)} z" fill="var({cor})"/>'
    )


def grafico_por_tipo(grupos: list[tuple[str, int, float]]) -> str:
    """Índice médio de engajamento por tipo de momento (uma série: sem legenda)."""
    largura, esquerda, direita, topo = 640, 170, 70, 62
    altura_barra, passo = 26, 40
    altura = topo + passo * len(grupos) + 40
    maximo = max(2.0, max(v for _, _, v in grupos) * 1.1)
    escala = (largura - esquerda - direita) / maximo
    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" height="{altura}" '
        f'viewBox="0 0 {largura} {altura}" role="img" aria-label="Engajamento por tipo de '
        f'momento, dados da demo">',
        ESTILO,
        f'<rect width="{largura}" height="{altura}" rx="8" fill="var(--fundo)"/>',
        '<text class="titulo" x="20" y="28">Engajamento por tipo de momento</text>',
        '<text class="nota" x="20" y="46">Índice médio (1 = o vídeo típico da rede). '
        "Dados inventados da demo.</text>",
    ]
    # O "típico" (1,0): a referência de leitura, discreta.
    x1 = esquerda + escala
    fim_y = topo + passo * len(grupos) - 8
    partes.append(
        f'<line x1="{x1}" y1="{topo - 6}" x2="{x1}" y2="{fim_y}" stroke="var(--base)" '
        'stroke-dasharray="3 3"/>'
    )
    partes.append(f'<text class="nota" x="{x1 + 4}" y="{fim_y + 14}">típico = 1</text>')
    for i, (nome, cortes, valor) in enumerate(grupos):
        y = topo + i * passo
        partes.append(f'<line x1="{esquerda}" y1="{y - 3}" x2="{esquerda}" '
                      f'y2="{y + altura_barra + 3}" stroke="var(--base)"/>')  # fmt: skip
        partes.append(
            f'<text class="rotulo" x="{esquerda - 10}" y="{y + 17}" text-anchor="end">{nome}</text>'
        )
        partes.append(_barra(esquerda, y, valor * escala, altura_barra, "--s1"))
        partes.append(
            f'<text class="valor" x="{esquerda + valor * escala + 8}" y="{y + 17}">'
            f"{br(valor)}</text>"
        )
        partes.append(
            f'<text class="nota" x="{esquerda + valor * escala + 46}" y="{y + 17}">'
            f"{cortes} cortes</text>"
        )
    partes.append("</svg>")
    return "\n".join(partes)


def grafico_antes_e_depois(linhas: list[tuple[str, float, float]]) -> str:
    """Precisão no topo com os pesos de antes e os aprendidos (duas séries, com legenda)."""
    largura, esquerda, direita, topo = 640, 170, 70, 86
    altura_barra, passo = 18, 62
    altura = topo + passo * len(linhas) + 18
    escala = (largura - esquerda - direita) / 1.0
    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" height="{altura}" '
        f'viewBox="0 0 {largura} {altura}" role="img" aria-label="Precisão no topo antes e '
        f'depois de aprender, dados da demo">',
        ESTILO,
        f'<rect width="{largura}" height="{altura}" rx="8" fill="var(--fundo)"/>',
        '<text class="titulo" x="20" y="28">A nota aprendendo: precisão nos de maior nota</text>',
        '<text class="nota" x="20" y="46">Medida em decisões e cortes que o treino não viu '
        "(validação cruzada). Dados inventados da demo.</text>",
        # Legenda: sempre presente com duas séries; a cor nunca sozinha (o texto diz qual é).
        '<rect x="20" y="58" width="12" height="12" rx="2" fill="var(--s1)"/>',
        '<text class="rotulo" x="38" y="69">pesos de antes</text>',
        '<rect x="150" y="58" width="12" height="12" rx="2" fill="var(--s2)"/>',
        '<text class="rotulo" x="168" y="69">pesos aprendidos</text>',
    ]
    for i, (nome, antes, depois) in enumerate(linhas):
        y = topo + i * passo
        partes.append(
            f'<text class="rotulo" x="{esquerda - 10}" y="{y + 24}" text-anchor="end">{nome}</text>'
        )
        partes.append(f'<line x1="{esquerda}" y1="{y - 3}" x2="{esquerda}" '
                      f'y2="{y + 2 * altura_barra + 5}" stroke="var(--base)"/>')  # fmt: skip
        for j, (valor, cor) in enumerate(((antes, "--s1"), (depois, "--s2"))):
            yb = y + j * (altura_barra + 2)  # 2 px de folga entre as barras vizinhas
            partes.append(_barra(esquerda, yb, valor * escala, altura_barra, cor))
            partes.append(
                f'<text class="valor" x="{esquerda + valor * escala + 8}" y="{yb + 14}">'
                f"{round(valor * 100)}%</text>"
            )
    partes.append("</svg>")
    return "\n".join(partes)


def selo(rotulo: str, valor: str, cor: str) -> str:
    """Um selo no estilo dos do shields.io, sem depender de serviço de fora."""
    l1, l2 = 7 * len(rotulo) + 12, 7 * len(valor) + 12
    total = l1 + l2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{rotulo}: {valor}"><rect width="{total}" height="20" rx="3" '
        f'fill="#555"/><rect x="{l1}" width="{l2}" height="20" rx="3" fill="{cor}"/>'
        f'<rect x="{l1}" width="4" height="20" fill="{cor}"/>'
        '<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" '
        f'text-anchor="middle"><text x="{l1 / 2}" y="14">{rotulo}</text>'
        f'<text x="{l1 + l2 / 2}" y="14">{valor}</text></g></svg>'
    )


def dados_da_demo() -> dict:
    from clipador.aprendizado import sugestao
    from clipador.banco.conexao import abrir_banco, nova_sessao
    from clipador.config import config_sem_arquivo
    from clipador.demo import historico
    from clipador.engajamento import resumo

    with tempfile.TemporaryDirectory() as pasta:
        cfg = config_sem_arquivo(pasta_gravacoes=Path(pasta), pasta_dados=Path(pasta))
        engine = abrir_banco(cfg.caminho_banco)
        try:
            with nova_sessao(engine) as sessao:
                historico.popular(sessao)
                gosto = sugestao.sugerir(sessao, cfg)
                publico = resumo.resumir(sessao, cfg)
                return {
                    "por_tipo": [
                        (g.nome, g.cortes, g.indice_medio) for g in publico.por_tipo_de_momento
                    ],
                    "gosto": (gosto.precisao_antes, gosto.precisao_depois),
                    "publico": (
                        publico.sugestao.precisao_antes,
                        publico.sugestao.precisao_depois,
                    ),
                }
        finally:
            engine.dispose()


def main() -> None:
    PASTA.mkdir(exist_ok=True)
    dados = dados_da_demo()
    (PASTA / "engajamento-por-tipo.svg").write_text(
        grafico_por_tipo(dados["por_tipo"]), encoding="utf-8"
    )
    (PASTA / "aprendizado-antes-e-depois.svg").write_text(
        grafico_antes_e_depois(
            [
                ("Suas aprovações", *dados["gosto"]),
                ("Engajamento", *dados["publico"]),
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(dados, ensure_ascii=False, indent=1))
    if "--cobertura" in sys.argv:
        relatorio = json.loads(Path("dados/cobertura.json").read_text(encoding="utf-8"))
        total = relatorio["totals"]["percent_covered"]
        cor = "#2e7d32" if total >= 80 else "#b26a00"  # verde a partir de 80%
        (PASTA / "cobertura.svg").write_text(
            selo("cobertura", f"{round(total)}%", cor), encoding="utf-8"
        )
        print(f"cobertura: {total:.1f}%")


if __name__ == "__main__":
    main()
