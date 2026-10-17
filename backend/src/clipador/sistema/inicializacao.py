"""Abrir o serviço sozinho quando você entra no Windows.

O roteiro previa o Agendador de Tarefas, mas criar pela linha de comando uma tarefa que
roda no login exige **administrador**, mesmo para o próprio usuário ("Access is denied";
pesquisado em 2026-10-15). Por isso o app usa a chave ``Run`` do registro do **seu
usuário** (``HKEY_CURRENT_USER``), o mesmo mecanismo de Discord, Steam e OneDrive:

- não precisa de administrador e só vale para o seu usuário;
- o serviço abre com o ``pythonw.exe``, que não mostra janela; tudo vai para o log;
- desfazer é só rodar ``clipador remover-servico``.

O serviço roda na sua sessão do Windows, o que também é necessário para a etapa 11
(``GetLastInputInfo`` só enxerga o teclado e o mouse da sessão do usuário).
"""

import sys
import winreg
from pathlib import Path

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME = "Clipador"

# A chave "Run" pode não existir (Windows recém-instalado, a máquina da CI do GitHub):
# ler e remover tratam isso como "não está cadastrado", e instalar cria a chave.


def comando_do_servico(arquivo_config: Path) -> str:
    """Linha de comando que o Windows roda no login."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise FileNotFoundError(f"pythonw.exe não encontrado ao lado de {sys.executable}")
    return f'"{pythonw}" -m clipador iniciar --arquivo "{arquivo_config.resolve()}"'


def instalar(arquivo_config: Path) -> str:
    """Cadastra o serviço para abrir no login. Devolve o comando cadastrado."""
    comando = comando_do_servico(arquivo_config)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as chave:
        winreg.SetValueEx(chave, NOME, 0, winreg.REG_SZ, comando)
    return comando


def remover() -> bool:
    """Tira o serviço do login. Devolve ``False`` se ele não estava cadastrado."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as chave:
            winreg.DeleteValue(chave, NOME)
    except FileNotFoundError:  # sem a chave ou sem o valor
        return False
    return True


def comando_instalado() -> str | None:
    """O comando cadastrado hoje, ou ``None`` se o serviço não abre no login."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as chave:
            valor, _tipo = winreg.QueryValueEx(chave, NOME)
    except FileNotFoundError:  # sem a chave ou sem o valor
        return None
    return valor
