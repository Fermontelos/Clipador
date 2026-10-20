"""Monitor da pasta de gravações do OBS.

Como saber que uma live terminou? O OBS escreve no arquivo o tempo todo enquanto grava.
Então uma gravação é considerada **pronta** quando o tamanho e a data de modificação
ficam parados por 2 minutos. Enquanto isso não acontece, ela está **em andamento** e o
serviço não processa nada, para não disputar CPU e GPU com a live.

O tempo de 2 minutos é configurável (``minutos_gravacao_parada``): quem usa o "Pausar
gravação" do OBS por mais tempo que isso precisa aumentá-lo, senão a live entra na fila
pela metade. Se o OBS dividir a live em vários arquivos ("dividir gravação
automaticamente"), cada arquivo vira uma gravação separada.

Na primeira vez que vê um arquivo, o monitor usa a data de modificação dele como ponto
de partida. Assim:

- uma gravação antiga (de quando o app estava fechado) já sai pronta na varredura que o
  serviço faz ao abrir;
- uma gravação sendo feita agora tem a data de modificação "agora" e fica em andamento.

Por segurança, só entram arquivos com o **nome no padrão do OBS** (data e hora, como
``2026-10-14 20-00-00.mkv``): o OBS grava por padrão na pasta Vídeos, que costuma ter
outros vídeos, e cada um deles viraria horas de processamento à toa. Quem renomeia as
gravações pode desligar isso no config (``so_nomes_do_obs = false``).

O monitor olha só a própria pasta, não as subpastas (o OBS não usa subpastas por padrão),
e não suporta o modo HLS do OBS, que grava a live picotada em vários pedaços ``.ts``.

O watchdog (``AvisoDeMudanca``) só acorda o serviço mais cedo quando algo muda na pasta.
Quem decide é a verificação periódica (``verificar``): no Windows, avisos de mudança em
arquivos às vezes se perdem, e a verificação não depende deles.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from clipador.banco.modelos import Gravacao, Tarefa, TipoTarefa
from clipador.ingestao.nomes import horario_de_inicio, inicio_pelo_nome

log = logging.getLogger(__name__)

ESTAVEL_APOS = timedelta(minutes=2)
# Formatos de arquivo único que o OBS consegue gravar. O recomendado é MKV (não corrompe
# se cair). O .m3u8 (lista de pedaços do modo HLS) não entra: cada pedaço viraria uma live.
EXTENSOES_VIDEO = (".mkv", ".mp4", ".mov", ".flv", ".ts")
# Se o OBS estiver com "remuxar para MP4" ligado, a mesma live aparece como .mkv e .mp4.
# A preferência é pelo MKV, e a cópia nunca é processada duas vezes.
PREFERENCIA = {extensao: posicao for posicao, extensao in enumerate(EXTENSOES_VIDEO)}


@dataclass
class _Observacao:
    tamanho: int
    modificado: float
    sem_mudar_desde: datetime


@dataclass
class ResultadoVerificacao:
    prontas: list[Path]
    em_andamento: list[Path]


def eh_gravacao(arquivo: Path) -> bool:
    """É um vídeo de gravação de live? Ignora outros arquivos, temporários e replays.

    O "Buffer de Replay" do OBS salva clipes curtos na mesma pasta, com nomes como
    "Replay 2026-10-14 20-00-00.mkv": eles já são clipes, não lives inteiras.
    """
    nome = arquivo.name.lower()
    return (
        arquivo.suffix.lower() in EXTENSOES_VIDEO
        and not nome.startswith(".")
        and not nome.startswith("replay")
        and ".parcial." not in nome
    )


class MonitorDeGravacoes:
    """Acompanha os arquivos da pasta e diz quais gravações já terminaram."""

    def __init__(
        self,
        pasta: Path,
        estavel_apos: timedelta = ESTAVEL_APOS,
        so_nomes_do_obs: bool = True,
        ignorar_antes_de: datetime | None = None,
    ) -> None:
        self.pasta = pasta
        self.estavel_apos = estavel_apos
        self.so_nomes_do_obs = so_nomes_do_obs
        # Gravações que começaram antes disto ficam de fora (ex.: lives antigas, gravadas
        # antes de o OBS separar as faixas). None = todas entram.
        self.ignorar_antes_de = ignorar_antes_de
        self._observados: dict[Path, _Observacao] = {}
        self._avisou_pasta_ausente = False
        self._ignorados_avisados: set[Path] = set()

    def candidatos(self) -> list[Path]:
        """Gravações na pasta, com uma por live (o .mkv no lugar de cópias remuxadas)."""
        if not self.pasta.is_dir():
            if not self._avisou_pasta_ausente:
                log.warning("A pasta de gravações %s não existe (ainda).", self.pasta)
                self._avisou_pasta_ausente = True
            return []
        self._avisou_pasta_ausente = False
        por_nome: dict[str, Path] = {}
        for arquivo in self.pasta.iterdir():
            if not (arquivo.is_file() and eh_gravacao(arquivo)):
                continue
            if self.so_nomes_do_obs and inicio_pelo_nome(arquivo) is None:
                if arquivo not in self._ignorados_avisados:
                    log.info(
                        "Ignorando %s: o nome não está no padrão do OBS (data e hora). "
                        "Para aceitar outros nomes, use so_nomes_do_obs = false no config.",
                        arquivo.name,
                    )
                    self._ignorados_avisados.add(arquivo)
                continue
            if self.ignorar_antes_de is not None:
                inicio = inicio_pelo_nome(arquivo) or datetime.fromtimestamp(
                    arquivo.stat().st_ctime, UTC
                )
                if inicio < self.ignorar_antes_de:
                    if arquivo not in self._ignorados_avisados:
                        log.info(
                            "Ignorando %s: começou antes de ignorar_gravacoes_antes_de.",
                            arquivo.name,
                        )
                        self._ignorados_avisados.add(arquivo)
                    continue
            atual = por_nome.get(arquivo.stem)
            if atual is None or (
                PREFERENCIA[arquivo.suffix.lower()] < PREFERENCIA[atual.suffix.lower()]
            ):
                por_nome[arquivo.stem] = arquivo
        return sorted(por_nome.values())

    def verificar(self, agora: datetime) -> ResultadoVerificacao:
        """Separa as gravações em prontas (paradas há 2 min) e em andamento."""
        resultado = ResultadoVerificacao(prontas=[], em_andamento=[])
        vistos = set()
        for arquivo in self.candidatos():
            try:
                info = arquivo.stat()
            except FileNotFoundError:  # apagado entre a listagem e agora
                continue
            vistos.add(arquivo)
            anterior = self._observados.get(arquivo)
            if anterior is None:
                modificado_em = datetime.fromtimestamp(info.st_mtime, UTC)
                self._observados[arquivo] = _Observacao(
                    info.st_size, info.st_mtime, min(modificado_em, agora)
                )
            elif (info.st_size, info.st_mtime) != (anterior.tamanho, anterior.modificado):
                # Ainda está sendo escrita: a contagem recomeça.
                self._observados[arquivo] = _Observacao(info.st_size, info.st_mtime, agora)
            parado_ha = agora - self._observados[arquivo].sem_mudar_desde
            destino = (
                resultado.prontas if parado_ha >= self.estavel_apos else resultado.em_andamento
            )
            destino.append(arquivo)
        # Esquece arquivos que sumiram da pasta.
        for arquivo in set(self._observados) - vistos:
            del self._observados[arquivo]
        return resultado


def registrar_gravacao(sessao: Session, arquivo: Path) -> Gravacao | None:
    """Coloca uma gravação pronta no banco, com a 1ª tarefa da fila. Tudo num commit só.

    Devolve ``None`` se a gravação já estava no banco: ela entra na fila uma vez só.
    """
    caminho = str(arquivo.resolve())
    if sessao.exec(select(Gravacao).where(Gravacao.arquivo == caminho)).first() is not None:
        return None
    gravacao = Gravacao(arquivo=caminho, inicio=horario_de_inicio(arquivo))
    sessao.add(gravacao)
    sessao.flush()  # gera o id, ainda sem gravar de vez
    sessao.add(Tarefa(tipo=TipoTarefa.EXTRAIR_AUDIO, gravacao_id=gravacao.id))
    sessao.commit()
    log.info("Gravação nova na fila: %s", arquivo.name)
    return gravacao


class AvisoDeMudanca(FileSystemEventHandler):
    """Acorda o serviço quando algo muda na pasta (o watchdog chama isto)."""

    def __init__(self, evento: threading.Event) -> None:
        self.evento = evento

    def on_any_event(self, event: FileSystemEvent) -> None:
        self.evento.set()


class SemMonitor:
    """Não vigia pasta nenhuma. Usado pela demo, que já registra a própria gravação: o
    vídeo dela acabou de ser criado e, vigiado, pareceria uma live em andamento por 2 min."""

    def verificar(self, agora: datetime) -> ResultadoVerificacao:
        return ResultadoVerificacao(prontas=[], em_andamento=[])
