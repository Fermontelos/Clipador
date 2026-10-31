"""Exporta a demo para a versão da revisão que roda só no navegador (GitHub Pages).

    uv run clipador demo --do-zero
    uv run clipador processar --demo
    cd frontend; npx vite build --mode estatica --outDir dist-estatica; cd ..
    uv run python docs/gerar_demo_estatica.py frontend/dist-estatica

A interface normal conversa com a API local (``clipador web``). Na versão estática não há
servidor: ela lê as respostas da API gravadas aqui, num ``dados.json``, e os vídeos e as
thumbnails dos clipes, copiados ao lado (``demo/``). As decisões ficam só na memória da aba.

As respostas vêm da própria API (o ``TestClient`` do FastAPI, sem abrir porta nenhuma), com
o banco da demo: são exatamente as que a interface receberia do ``clipador web --demo``.
"""

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from clipador.cli import config_do_comando
from clipador_api.app import criar_app

RAIZ = Path(__file__).resolve().parent.parent
VIDEO = re.compile(r"^/api/clipes/(\d+)/video$")
THUMBNAIL = re.compile(r"^/api/momentos/(\d+)/thumbnail$")


class Exportador:
    def __init__(self, cliente: TestClient, pasta: Path) -> None:
        self.cliente = cliente
        self.pasta = pasta
        self.arquivos = 0

    def pedir(self, caminho: str) -> Any:
        resposta = self.cliente.get(caminho)
        resposta.raise_for_status()
        return self.trocar_urls(resposta.json())

    def trocar_urls(self, valor: Any) -> Any:
        """``/api/clipes/7/video`` vira ``demo/videos/7.mp4`` (e copia o vídeo)."""
        if isinstance(valor, dict):
            return {chave: self.trocar_urls(v) for chave, v in valor.items()}
        if isinstance(valor, list):
            return [self.trocar_urls(v) for v in valor]
        if isinstance(valor, str):
            if achado := VIDEO.match(valor):
                return self.copiar(valor, f"videos/{achado.group(1)}.mp4")
            if achado := THUMBNAIL.match(valor):
                return self.copiar(valor, f"thumbs/{achado.group(1)}.jpg")
        return valor

    def copiar(self, url: str, destino: str) -> str:
        arquivo = self.pasta / destino
        if not arquivo.is_file():
            resposta = self.cliente.get(url)
            if resposta.status_code == 404:
                # As lives antigas do histórico só existem no banco (sem vídeo), como no
                # "clipador web --demo": o endereço fica, e o player mostra que não há vídeo.
                return f"demo/{destino}"
            resposta.raise_for_status()
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_bytes(resposta.content)
            self.arquivos += 1
        return f"demo/{destino}"


def exportar(saida: Path) -> Path:
    cfg = config_do_comando(RAIZ / "config.toml", demo=True)
    pasta = saida / "demo"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir(parents=True)
    # O Host precisa ser 127.0.0.1: a API recusa outros nomes (proteção contra DNS rebinding).
    with TestClient(criar_app(cfg), base_url="http://127.0.0.1") as cliente:
        exp = Exportador(cliente, pasta)
        dias = exp.pedir("/api/dias")
        listas = {}
        for dia in ["", *(d["dia"] for d in dias)]:
            for mais in ("", "mais"):
                valores = {"dia": dia, "ver_mais": "true" if mais else ""}
                parametros = urlencode({k: v for k, v in valores.items() if v})
                listas[f"{dia}|{mais}"] = exp.pedir(f"/api/momentos?{parametros}")
        momentos = {
            m["id"]: m for lista in listas.values() for m in lista["momentos"]
        }  # o mesmo que GET /api/momentos/{id}
        dados = {
            "config": exp.pedir("/api/config"),
            "dias": dias,
            "listas": listas,
            "momentos": momentos,
            "status": exp.pedir("/api/status"),
            "estatisticas": exp.pedir("/api/estatisticas"),
            "aprendizado": exp.pedir("/api/aprendizado"),
            "engajamento": exp.pedir("/api/engajamento"),
        }
    (pasta / "dados.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    print(f"{len(momentos)} momentos e {exp.arquivos} vídeos e thumbnails em {pasta}")
    return pasta


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Uso: uv run python docs/gerar_demo_estatica.py <pasta do build estático>")
    exportar(Path(sys.argv[1]))
