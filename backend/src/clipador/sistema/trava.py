"""Garante que só um serviço rode por vez.

Dois serviços ao mesmo tempo seriam um problema: ao abrir, cada um devolve para a fila as
tarefas "em andamento" (``fila.recuperar_interrompidas``), ou seja, o segundo devolveria
a tarefa que o primeiro está processando naquele momento.

A trava é um arquivo (``servico.lock``) bloqueado pelo Windows enquanto o serviço roda.
Se o processo morrer (queda de energia, erro), o Windows libera o bloqueio sozinho, então
uma trava "esquecida" nunca impede o serviço de abrir de novo.
"""

import msvcrt
from pathlib import Path


class ServicoJaRodandoError(RuntimeError):
    """Outro serviço do Clipador já está rodando."""


class TravaDoServico:
    def __init__(self, pasta_dados: Path) -> None:
        self.caminho = pasta_dados / "servico.lock"
        self._arquivo = None

    def adquirir(self) -> None:
        """Pega a trava ou levanta ``ServicoJaRodandoError`` se outro serviço a tiver."""
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        arquivo = open(self.caminho, "a+b")  # noqa: SIM115 (fica aberto enquanto o serviço roda)
        try:
            arquivo.seek(0)
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as erro:
            arquivo.close()
            raise ServicoJaRodandoError(
                "O serviço do Clipador já está rodando (só pode haver um por vez)."
            ) from erro
        self._arquivo = arquivo

    def liberar(self) -> None:
        if self._arquivo is None:
            return
        try:
            self._arquivo.seek(0)
            msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._arquivo.close()
            self._arquivo = None


def servico_esta_rodando(pasta_dados: Path) -> bool:
    """Testa a trava sem ficar com ela: diz se há um serviço rodando agora."""
    trava = TravaDoServico(pasta_dados)
    try:
        trava.adquirir()
    except ServicoJaRodandoError:
        return True
    trava.liberar()
    return False
