"""Gera 3 títulos e as hashtags de um momento com a IA local (Ollama), ou sem IA (plano B).

**O pedido ao modelo** leva o que você falou no clipe (a transcrição do trecho), o que
aconteceu (o motivo da nota e os eventos do LoL) e as instruções de estilo, com exemplos
(os do roteiro e os últimos títulos que você escolheu na revisão: assim o modelo aprende
o seu jeito de escrever, "few-shot").

**A resposta** é exigida num formato fixo: o Ollama recebe um JSON Schema (``format``) e é
obrigado a responder nele. Mesmo assim ela é validada de novo (um modelo pequeno às vezes
erra), cada título é cortado em 100 caracteres (o limite do YouTube) e as hashtags são
limpas e juntadas com as fixas do config.

**Escolhas para a GTX 1050 Ti:**

- ``qwen3:4b`` (2,5 GB); se faltar memória na placa, o ``qwen3:1.7b`` (1,4 GB);
- sem o modo "raciocínio" do qwen3 (``think=False``): para textos curtos, ele só atrasa;
- o modelo fica na placa durante a tarefa (``keep_alive`` de 10 min, para não recarregar
  30 vezes) e é **descarregado no fim** (``keep_alive=0``): uma tarefa de GPU por vez.

**Plano B**: se o Ollama não estiver instalado, não responder ou devolver algo inválido, o
título vira a frase mais marcante da transcrição (a dita mais perto do pico), e nada trava.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from clipador.config import ConfigTitulos
from clipador.titulos import texto

log = logging.getLogger(__name__)

MANTER_NA_PLACA = "10m"  # entre um momento e outro da mesma tarefa

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulos": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "tipo": {"type": "string", "enum": list(texto.TIPOS_DE_TITULO)},
                },
                "required": ["titulo", "tipo"],
            },
        },
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["titulos", "hashtags"],
}

INSTRUCOES = """Você escreve títulos para clipes curtos (YouTube Shorts e TikTok) da live de \
um streamer brasileiro de League of Legends. A live é de humor: o que vale é a reação dele.

Regras dos títulos:
- O título é postado pelo próprio streamer: escreva na 1ª pessoa ("eu", "meu time"), como \
ele falaria, em português do Brasil. Nunca escreva "o streamer" no título.
- Frases curtas, com curiosidade ou exagero. Nada de "melhores momentos #12", "clipe da live" \
ou títulos genéricos.
- Pode usar a fala dele no clipe como título, se ela for marcante.
- Não invente o que não aconteceu: o título tem de bater com o clipe.
- No máximo 100 caracteres. Sem hashtags e sem emojis no título.
- Dê exatamente 3 opções, de tipos diferentes quando der. Tipos: "frase_sua" (uma fala dele), \
"pergunta", "exagero", "descricao".

Exemplos do estilo esperado:
- Pentakill de Yasuo com grito: "EU NÃO ACREDITO NO QUE ESSE YASUO FEZ" (frase_sua); \
"O time desistiu. O Yasuo não." (exagero)
- Flash errado com risada: "Eu juro que apertei o flash" (frase_sua); \
"O flash mais triste da história do LoL" (exagero)
Os exemplos mostram o estilo: não copie os títulos deles, crie títulos para este clipe.

Hashtags: até 3, só sobre o que aparece no clipe: o campeão, a jogada (pentakill, flash, \
barão) ou o jogo. Sem espaços, em minúsculas. Não use palavras deste pedido como hashtag \
("evento", "streamer", "clipe"). Hashtag sem relação com o vídeo pode fazer o YouTube removê-lo.

Responda só com o JSON pedido."""


class _Sugestao(BaseModel):
    titulo: str = Field(min_length=1)
    tipo: Literal["frase_sua", "pergunta", "exagero", "descricao"]


class _Resposta(BaseModel):
    titulos: list[_Sugestao] = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list)


@dataclass
class Pedido:
    """O que o gerador sabe de um momento."""

    falas: str  # o que você falou no clipe
    motivo: str  # o motivo da nota ("voz alta (+24 dB) + pentakill seu")
    eventos: list[str] = field(default_factory=list)  # eventos do LoL no trecho
    frase_marcante: str | None = None  # para o plano B
    exemplos: list[dict[str, str]] = field(default_factory=list)  # títulos que você escolheu


@dataclass
class Sugestoes:
    titulos: list[dict[str, str]]  # [{"titulo": ..., "tipo": ...}]
    hashtags: list[str]
    origem: str  # "ollama (qwen3:4b)", "simples" ou "demo"
    # Todas as hashtags que a IA sugeriu para o clipe, antes do corte em max_hashtags: a
    # etapa 17 escolhe entre elas as que entram (as que engajam, ou uma em teste).
    candidatas: list[str] = field(default_factory=list)


def para_o_modelo(motivo: str) -> str:
    """O motivo da nota em linguagem de gente, na voz do streamer (1ª pessoa).

    O motivo é escrito para a revisão ("pentakill seu", "voz alta (+24 dB)"). Mandado assim,
    o modelo copiava o "seu" e os decibéis para o título ("Barão roubado seu"); na 3ª pessoa
    ("o streamer gritou"), copiava "o streamer". Como o título é postado pelo próprio
    streamer, o momento vai descrito na voz dele, e o modelo responde no mesmo tom.
    """
    partes = []
    for parte in (p.strip() for p in motivo.split(" + ") if p.strip()):
        if parte.startswith("voz alta"):
            partes.append("eu gritei")
        elif parte.startswith("fala acelerada"):
            partes.append("eu falei rápido, empolgado")
        elif parte.startswith("“"):
            partes.append(f'eu disse "{parte.strip("“”")}"')
        elif parte == "sua morte":
            partes.append("eu morri")
        elif parte.endswith(" (sua morte)"):
            partes.append(f"{parte.removesuffix(' (sua morte)')}, e eu morri")
        elif parte.endswith(" (sua assistência)"):
            partes.append(f"{parte.removesuffix(' (sua assistência)')}, com a minha ajuda")
        elif parte.endswith(" seu"):
            partes.append(f"{parte.removesuffix(' seu')} (fui eu)")
        else:
            partes.append(parte)
    return "; ".join(partes)


def mensagens(pedido: Pedido) -> list[dict[str, str]]:
    """A conversa enviada ao modelo: as instruções e o momento."""
    partes = [f'O que eu falei no clipe: "{pedido.falas or "(nada)"}"']
    partes.append(f"O que aconteceu: {para_o_modelo(pedido.motivo) or '(sem detalhes)'}")
    if pedido.eventos:
        partes.append(
            "Eventos do jogo no clipe: " + "; ".join(para_o_modelo(e) for e in pedido.eventos)
        )
    if pedido.exemplos:
        exemplos = "\n".join(f'- "{e["titulo"]}" ({e.get("tipo") or "?"})' for e in pedido.exemplos)
        partes.append(f"Títulos que eu escolhi antes (imite este estilo):\n{exemplos}")
    return [
        {"role": "system", "content": INSTRUCOES},
        {"role": "user", "content": "\n".join(partes)},
    ]


def validar(conteudo: str, cfg: ConfigTitulos) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """Confere a resposta do modelo e aplica os limites. Levanta ``ValueError`` se inválida.

    Devolve os títulos, as hashtags (as fixas + as sugeridas, até ``max_hashtags``) e todas as
    sugeridas que passaram no filtro (as candidatas da etapa 17).
    """
    try:
        resposta = _Resposta.model_validate(json.loads(conteudo))
    except (json.JSONDecodeError, ValidationError) as erro:
        raise ValueError(f"Resposta do modelo fora do formato: {erro}") from erro
    titulos: list[dict[str, str]] = []
    for sugestao in resposta.titulos:
        # O modelo às vezes repete o tipo no fim do título: "... (frase_sua)".
        bruto = TIPO_NO_FIM.sub("", sugestao.titulo.strip().strip('"'))
        titulo = texto.cortar_titulo(bruto)
        if titulo and titulo.casefold() not in {t["titulo"].casefold() for t in titulos}:
            titulos.append({"titulo": titulo, "tipo": sugestao.tipo})
    if not titulos:
        raise ValueError("O modelo não sugeriu nenhum título válido.")
    sugeridas = [h for h in resposta.hashtags if not _palavra_do_pedido(h)]
    hashtags = texto.juntar_hashtags(cfg.hashtags_fixas, sugeridas, cfg.max_hashtags)
    return titulos[:3], hashtags, sugeridas


TIPO_NO_FIM = re.compile(r"\s*\((?:" + "|".join(texto.TIPOS_DE_TITULO) + r")\)\s*$")
# Palavras que só aparecem no pedido ao modelo: como hashtag, não dizem nada do clipe.
PALAVRAS_DO_PEDIDO = ("evento", "streamer", "clipe", "titulo", "título")


def _palavra_do_pedido(hashtag: str) -> bool:
    return any(palavra in hashtag.casefold() for palavra in PALAVRAS_DO_PEDIDO)


def simples(pedido: Pedido, cfg: ConfigTitulos) -> Sugestoes:
    """O plano B, sem IA: a frase mais marcante do momento e o motivo dele."""
    titulos = []
    if pedido.frase_marcante:
        titulos.append({"titulo": texto.cortar_titulo(pedido.frase_marcante), "tipo": "frase_sua"})
    descricao = evento_em_primeira_pessoa(pedido.motivo)
    if descricao:
        titulos.append({"titulo": texto.cortar_titulo(descricao), "tipo": "descricao"})
    # O YouTube recomenda de 3 a 5 hashtags: além das fixas, a do evento de jogo, se houver
    # ("Barão roubado" -> #barãoroubado). "Morri" não vira hashtag: não diz o que aconteceu.
    do_evento = [descricao] if descricao and descricao != "Morri" else []
    if not titulos:
        titulos.append({"titulo": "Momento da live", "tipo": "descricao"})
    hashtags = texto.juntar_hashtags(cfg.hashtags_fixas, do_evento, cfg.max_hashtags)
    return Sugestoes(titulos=titulos, hashtags=hashtags, origem="simples")


def evento_em_primeira_pessoa(motivo: str) -> str | None:
    """O evento de jogo mais forte do motivo, como o streamer diria (o título é dele).

    ``"voz alta (+24 dB) + pentakill seu"`` -> ``"Pentakill"``; ``"sua morte"`` ->
    ``"Morri"``. Volume, fala acelerada e expressões não viram título: sem um evento de
    jogo no motivo, ``None``.
    """
    for parte in (p.strip() for p in motivo.split(" + ") if p.strip()):
        if parte.startswith(("voz alta", "fala acelerada", "“")):
            continue
        if parte == "sua morte":
            return "Morri"
        frase = parte.removesuffix(" seu").removesuffix(" (sua assistência)")
        frase = frase.removesuffix(" (sua morte)")
        return frase[:1].upper() + frase[1:]
    return None


def faltou_memoria(erro: BaseException) -> bool:
    mensagem = str(erro).casefold()
    return "out of memory" in mensagem or "more system memory" in mensagem


def criar_cliente(cfg: ConfigTitulos) -> Any:
    """O cliente do Ollama. Os testes trocam esta função por uma falsa."""
    import ollama

    return ollama.Client(host=cfg.endereco, timeout=cfg.tempo_limite_s)


class Gerador:
    """Gera as sugestões de vários momentos seguidos, com o mesmo modelo carregado."""

    def __init__(self, cfg: ConfigTitulos) -> None:
        self.cfg = cfg
        self.modelo = cfg.modelo
        self._cliente: Any = None
        self._ia_indisponivel = cfg.fonte != "ollama"

    def sugerir(self, pedido: Pedido) -> Sugestoes:
        if self._ia_indisponivel:
            return simples(pedido, self.cfg)
        try:
            return self._pela_ia(pedido)
        except Exception as erro:
            # Ollama fechado, sem o modelo, lento demais ou resposta inválida duas vezes:
            # o plano B entra e o resto da live também vai sem IA (avisado uma vez só).
            log.warning(
                "Títulos sem IA a partir de agora (o Ollama falhou: %s: %s).",
                type(erro).__name__,
                erro,
            )
            self._ia_indisponivel = True
            return simples(pedido, self.cfg)

    def _pela_ia(self, pedido: Pedido) -> Sugestoes:
        if self._cliente is None:
            self._cliente = criar_cliente(self.cfg)
        conversa = mensagens(pedido)
        ultimo_erro: Exception | None = None
        for _tentativa in range(2):  # um modelo pequeno às vezes erra o formato: tenta de novo
            try:
                conteudo = self._pedir(conversa)
                titulos, hashtags, candidatas = validar(conteudo, self.cfg)
                return Sugestoes(titulos, hashtags, f"ollama ({self.modelo})", candidatas)
            except ValueError as erro:
                ultimo_erro = erro
        raise ultimo_erro

    def _pedir(self, conversa: list[dict[str, str]]) -> str:
        try:
            resposta = self._chamar(conversa)
        except Exception as erro:
            reserva = self.cfg.modelo_reserva
            if not faltou_memoria(erro) or not reserva or reserva == self.modelo:
                raise
            log.warning(
                "Faltou memória para o modelo %s; trocando para o %s.", self.modelo, reserva
            )
            self.modelo = reserva
            resposta = self._chamar(conversa)
        return resposta.message.content

    def _chamar(self, conversa: list[dict[str, str]]) -> Any:
        return self._cliente.chat(
            model=self.modelo,
            messages=conversa,
            format=ESQUEMA,
            think=False,
            keep_alive=MANTER_NA_PLACA,
            options={"temperature": 0.8},
        )

    def fechar(self) -> None:
        """Tira o modelo da placa de vídeo (``keep_alive=0``)."""
        if self._cliente is None:  # nunca falou com o Ollama: nada carregado
            return
        try:
            self._cliente.generate(model=self.modelo, keep_alive=0)
        except Exception:
            log.debug("Não deu para descarregar o modelo do Ollama.", exc_info=True)
