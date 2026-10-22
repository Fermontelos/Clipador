"""Há quanto tempo o teclado e o mouse estão parados (GetLastInputInfo, do Windows).

``GetLastInputInfo`` devolve o instante (em milissegundos desde que o Windows ligou, o
"tick count") da última vez que você mexeu no teclado ou no mouse. Ociosidade = agora -
esse instante.

Dois cuidados:

- só enxerga a **sua sessão** do Windows: por isso o app abre pela chave "Run" no login
  (ADR 0004), e não como serviço do sistema, que roda noutra sessão e veria sempre zero;
- o instante é um número de 32 bits, que volta a zero a cada ~49,7 dias de PC ligado; a
  subtração é feita com o ``GetTickCount`` (também de 32 bits) e "dá a volta" junto, então
  a conta continua certa.

Assistir a um vídeo sem tocar no PC conta como ocioso: o aviso de 60 s do desligamento
aparece, e basta mexer o mouse para cancelar.
"""

import ctypes
from ctypes import wintypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def segundos_ocioso() -> float:
    """Há quantos segundos o teclado e o mouse estão parados."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo falhou (o app está fora da sua sessão do Windows?)")
    agora = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((agora - info.dwTime) & 0xFFFFFFFF) / 1000
