"""Junta os sinais dos detectores em momentos e dá a nota de cada um (0 a 100).

1. **Agrupar**: sinais a menos de ``juntar_ate_s`` (8 s) um do outro são o mesmo momento
   (o grito, o pentakill e o "não acredito" de uma mesma jogada).
2. **Janela do clipe**: começa ``segundos_antes`` (10 s) antes do primeiro sinal, para dar
   contexto, e termina ``segundos_depois`` (5 s) depois do último, com duração entre
   ``duracao_minima_s`` (15 s) e ``duracao_maxima_s`` (60 s):
   - longa demais: fica ancorada no sinal mais forte (com os seus 10 s de contexto antes);
   - curta demais: cresce por igual dos dois lados;
   - nunca passa do começo nem do fim da gravação (é deslocada para dentro).
3. **Sem sobreposição**: duas janelas que se sobrepõem viram um momento só, se a união
   couber na duração máxima; senão, são cortadas no meio do caminho entre os sinais delas.
4. **Nota** = 100 × média ponderada da força de cada detector no momento (a força de um
   detector é o maior sinal dele dentro da janela; sem sinal, 0). Só entram na média os
   detectores **disponíveis** nesta gravação: numa live sem LoL, o peso do LoL sai da conta.
5. Ficam só os ``max_momentos_por_live`` (30) melhores, e só os com nota de pelo menos
   ``nota_minima`` (10): abaixo disso é ruído (ex.: só a torre derrubada por um aliado).

Os pesos vêm do ``[deteccao]`` do config até existir uma versão aprovada na tabela
``PesosNota`` (etapas 14 e 17), que passa a valer no lugar.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from clipador.banco.modelos import PesosNota
from clipador.config import ConfigDeteccao
from clipador.detectores.base import Sinal

# Quantos motivos aparecem no texto do momento ("voz alta + pentakill seu + 'caramba'").
MAX_MOTIVOS = 3


@dataclass
class MomentoCalculado:
    inicio_s: float
    fim_s: float
    sinais: list[Sinal] = field(default_factory=list)
    forcas: dict[str, float] = field(default_factory=dict)  # por detector disponível
    nota: float = 0.0
    motivo: str = ""

    @property
    def duracao_s(self) -> float:
        return self.fim_s - self.inicio_s


def pesos_atuais(sessao: Session, cfg: ConfigDeteccao) -> dict[str, float]:
    """Os pesos aprovados mais recentes (``PesosNota``) ou, sem eles, os do config."""
    aprovados = sessao.exec(select(PesosNota).order_by(col(PesosNota.versao).desc())).first()
    pesos = {
        "volume_voz": cfg.volume_voz.peso,
        "fala": cfg.fala.peso,
        "lol": cfg.lol.peso,
    }
    if aprovados is not None:
        pesos.update(aprovados.valores)
    return pesos


def agrupar(sinais: list[Sinal], juntar_ate_s: float) -> list[list[Sinal]]:
    grupos: list[list[Sinal]] = []
    fim_do_grupo = float("-inf")
    for sinal in sorted(sinais, key=lambda s: (s.inicio_s, s.fim_s)):
        if grupos and sinal.inicio_s - fim_do_grupo <= juntar_ate_s:
            grupos[-1].append(sinal)
            fim_do_grupo = max(fim_do_grupo, sinal.fim_s)
        else:
            grupos.append([sinal])
            fim_do_grupo = sinal.fim_s
    return grupos


def janela(grupo: list[Sinal], cfg: ConfigDeteccao, duracao_total_s: float) -> tuple[float, float]:
    """Início e fim do clipe de um grupo de sinais (ver as regras no topo do arquivo)."""
    inicio = min(s.inicio_s for s in grupo) - cfg.segundos_antes
    fim = max(s.fim_s for s in grupo) + cfg.segundos_depois
    if fim - inicio > cfg.duracao_maxima_s:
        mais_forte = max(grupo, key=lambda s: (s.forca, -s.inicio_s))
        inicio = max(inicio, mais_forte.inicio_s - cfg.segundos_antes)
        fim = min(fim, inicio + cfg.duracao_maxima_s)
    if fim - inicio < cfg.duracao_minima_s:
        falta = cfg.duracao_minima_s - (fim - inicio)
        inicio, fim = inicio - falta / 2, fim + falta / 2
    return _dentro_da_gravacao(inicio, fim, duracao_total_s)


def _dentro_da_gravacao(inicio: float, fim: float, duracao_total_s: float) -> tuple[float, float]:
    """Desloca a janela para dentro da gravação, mantendo o tamanho quando couber."""
    if inicio < 0:
        inicio, fim = 0.0, fim - inicio
    if fim > duracao_total_s:
        inicio, fim = inicio - (fim - duracao_total_s), duracao_total_s
    return max(inicio, 0.0), fim


def sem_sobreposicao(
    momentos: list[MomentoCalculado], cfg: ConfigDeteccao
) -> list[MomentoCalculado]:
    resultado: list[MomentoCalculado] = []
    for momento in sorted(momentos, key=lambda m: m.inicio_s):
        anterior = resultado[-1] if resultado else None
        if anterior is None or momento.inicio_s >= anterior.fim_s:
            resultado.append(momento)
            continue
        uniao_fim = max(anterior.fim_s, momento.fim_s)
        if uniao_fim - anterior.inicio_s <= cfg.duracao_maxima_s:
            anterior.fim_s = uniao_fim
            anterior.sinais.extend(momento.sinais)
            continue
        # Não cabem juntos: corta no meio do caminho entre o fim dos sinais do anterior e o
        # começo dos sinais deste.
        fim_sinais = max(s.fim_s for s in anterior.sinais)
        inicio_sinais = min(s.inicio_s for s in momento.sinais)
        corte = (fim_sinais + inicio_sinais) / 2
        anterior.fim_s = min(anterior.fim_s, corte)
        momento.inicio_s = max(momento.inicio_s, corte)
        resultado.append(momento)
    return resultado


def dar_nota(
    momento: MomentoCalculado, todos: list[Sinal], pesos: dict[str, float], disponiveis: list[str]
) -> None:
    """Calcula forças, nota e motivo do momento, com os sinais que caem dentro da janela."""
    dentro = [s for s in todos if s.fim_s >= momento.inicio_s and s.inicio_s <= momento.fim_s]
    momento.sinais = dentro
    melhores: dict[str, Sinal] = {}
    for sinal in dentro:
        if sinal.detector not in melhores or sinal.forca > melhores[sinal.detector].forca:
            melhores[sinal.detector] = sinal
    momento.forcas = {
        nome: (melhores[nome].forca if nome in melhores else 0.0) for nome in disponiveis
    }
    soma_pesos = sum(pesos.get(nome, 0.0) for nome in disponiveis)
    contribuicao = {nome: pesos.get(nome, 0.0) * momento.forcas[nome] for nome in disponiveis}
    momento.nota = 100 * sum(contribuicao.values()) / soma_pesos if soma_pesos else 0.0
    ordem = sorted(melhores, key=lambda nome: contribuicao.get(nome, 0.0), reverse=True)
    momento.motivo = " + ".join(melhores[nome].motivo for nome in ordem[:MAX_MOTIVOS])


def calcular(
    sinais: list[Sinal],
    disponiveis: list[str],
    pesos: dict[str, float],
    cfg: ConfigDeteccao,
    duracao_total_s: float,
) -> list[MomentoCalculado]:
    """Os melhores momentos da gravação, do de maior nota para o de menor."""
    momentos = [
        MomentoCalculado(*janela(grupo, cfg, duracao_total_s), sinais=list(grupo))
        for grupo in agrupar(sinais, cfg.juntar_ate_s)
    ]
    momentos = sem_sobreposicao(momentos, cfg)
    for momento in momentos:
        dar_nota(momento, sinais, pesos, disponiveis)
    momentos = [m for m in momentos if m.nota >= cfg.nota_minima]
    momentos.sort(key=lambda m: (-m.nota, m.inicio_s))
    return momentos[: cfg.max_momentos_por_live]
