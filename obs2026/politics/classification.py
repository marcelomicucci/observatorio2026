"""Classificação política e mapa ideológico (§6).

Este módulo entrega a **máquina**; o **conteúdo** mora em
`config/classificacao_2026.yaml` e só vale com assinatura.

Classificar um partido brasileiro como esquerda, centro ou direita é um ato
editorial, não um cálculo. O §6 exige, para cada entrada, fonte,
justificativa, autor, data, validade temporal e grau de controvérsia.
Desde 21/09/2026 o arquivo traz uma proposta redigida para revisão, com
fonte e justificativa por partido e **sem autor**: quem responde pela
classificação revisa e assina (pela linha de comando ou pela aba
Configuração do painel). Assinar em nome de alguém seria inventar autoria
e transformar opinião em dado com aparência de oficial, que é exatamente o
que o §6 proíbe ("não interpretar classificação como atributo oficial do
TSE").

O que está pronto:

- as três dimensões separadas — ideologia, posição em relação ao governo e
  aliança estadual — que o §6 manda não confundir;
- a paleta pedida, com contraste verificado e leitura que não depende só de
  cor;
- o versionamento, a validação e o cálculo de saldo;
- o estado `curadoria_pendente`, que faz o mapa ideológico aparecer
  desabilitado na interface, com o motivo, em vez de aparecer errado.

Para ativar: revisar `config/classificacao_2026.yaml` e assinar com
`obs2026 assinar-classificacao --curador "Nome"` — ver `assinar()`.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..config import CONFIG_DIR
from ..tse.parsers.common import fold

# -- paleta ---------------------------------------------------------------
# Cores pedidas na especificação. Sem cinza: ausência de classificação tem
# padrão visual próprio (hachura), não uma cor que possa ser lida como
# "centro" (§6).
PALETA = {
    "esquerda": "#C62828",
    "centro_esquerda": "#E06C63",
    "centro": "#F2C94C",
    "centro_direita": "#5B8BC9",
    "direita": "#1565C0",
}

#: Ausência de dado NÃO usa o amarelo do centro (§6, camada 6).
SEM_CLASSIFICACAO = {
    "cor": "#FFFFFF",
    "padrao": "hachura-diagonal",
    "rotulo": "sem classificação",
}

#: A leitura não pode depender só de cor (§11.3). Cada categoria tem símbolo
#: e rótulo textual próprios, usados na legenda, no mapa e na exportação.
SIMBOLOS = {
    "esquerda": "◀",
    "centro_esquerda": "◁",
    "centro": "◆",
    "centro_direita": "▷",
    "direita": "▶",
}

CATEGORIAS = tuple(PALETA.keys())

ROTULOS = {
    "esquerda": "Esquerda",
    "centro_esquerda": "Centro-esquerda",
    "centro": "Centro",
    "centro_direita": "Centro-direita",
    "direita": "Direita",
}

#: Agrupamento em três blocos, com a regra explícita (§6).
REGRA_AGRUPAMENTO = {
    "esquerda": ("esquerda", "centro_esquerda"),
    "centro": ("centro",),
    "direita": ("centro_direita", "direita"),
}

POSICOES_GOVERNO = ("governo", "oposicao", "independente")


#: Ano do cadastro de partidos (`config/partidos_2026.yaml`). A conferência
#: de número × sigla só vale para a classificação desse ano.
ANO_CADASTRO = 2026


class ClassificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Entrada:
    ano: int
    entidade_tipo: str          # partido | candidato
    entidade_id: str
    numero: str | None          # número do partido; sigla sozinha é ambígua
    ideologia: str
    posicao_governo: str | None
    fonte: str
    justificativa: str
    autor: str
    decidido_em: date
    valido_de: date | None
    valido_ate: date | None
    grau_controversia: int
    versao: str

    def valida(self) -> list[str]:
        p = []
        if self.ideologia not in CATEGORIAS:
            p.append(f"ideologia fora do vocabulario: {self.ideologia!r}")
        if self.posicao_governo and self.posicao_governo not in POSICOES_GOVERNO:
            p.append(f"posicao_governo invalida: {self.posicao_governo!r}")
        if not self.fonte.strip():
            p.append("fonte vazia")
        if not self.justificativa.strip():
            p.append("justificativa vazia")
        if not self.autor.strip():
            p.append("autor vazio — classificacao exige autoria nominal")
        if not (0 <= self.grau_controversia <= 3):
            p.append("grau_controversia fora de 0..3")
        return p


@dataclass(frozen=True, slots=True)
class Registro:
    versao: str
    ano: int
    curador: str | None
    publicada_em: date | None
    entradas: tuple[Entrada, ...]

    @property
    def curadoria_pendente(self) -> bool:
        return not self.entradas or not self.curador

    def por_partido(self, sigla: str | None,
                    numero: str | None = None) -> Entrada | None:
        """Entrada do partido, identificado pelo cadastro (número + sigla).

        A resolução passa por `partidos.resolver`, que recusa número certo
        com sigla errada: o 14 de 2022 (PTB) não herda a classificação do 14
        de 2026 (Missão)."""
        from .partidos import resolver
        p = resolver(numero, sigla)
        if not p.cadastrado:
            return None
        for e in self.entradas:
            if e.entidade_tipo != "partido":
                continue
            if e.numero and e.numero == p.numero:
                return e
            if not e.numero and fold(e.entidade_id) == fold(p.sigla):
                return e
        return None

    def bloco(self, sigla: str | None, numero: str | None = None) -> str | None:
        """Bloco de três categorias. `None` quando não há classificação.

        `None` NÃO é "centro". Um partido não classificado é desconhecido, e
        a interface o desenha com hachura, não com amarelo.
        """
        e = self.por_partido(sigla, numero)
        if e is None:
            return None
        for bloco, cats in REGRA_AGRUPAMENTO.items():
            if e.ideologia in cats:
                return bloco
        return None


def caminho_padrao() -> Path:
    """`OBS2026_CLASSIFICACAO` troca o arquivo — usado por testes e pela
    demonstração, nunca para ativar uma classificação sem assinatura."""
    alt = os.environ.get("OBS2026_CLASSIFICACAO")
    if alt:
        return Path(alt)
    # Eleição passada (seletor de anos): a classificação DAQUELE ano. Um
    # partido pode mudar de lado sem mudar de nome; herdar a de 2026 seria
    # reescrever 2018 com a régua de hoje.
    from ..contexto import ano_passado
    ano = ano_passado()
    if ano is not None:
        return CONFIG_DIR / f"classificacao_{ano}.yaml"
    return CONFIG_DIR / "classificacao_2026.yaml"


def load(path: Path | None = None) -> Registro:
    p = path or caminho_padrao()
    if not p.exists():
        return Registro(versao="2026.0-inexistente", ano=2026, curador=None,
                        publicada_em=None, entradas=())
    return _load_cache(str(p), p.stat().st_mtime_ns)


@lru_cache(maxsize=16)
def _load_cache(caminho: str, _mtime: int) -> Registro:
    """Uma leitura por versão do arquivo. A composição do Senado classifica
    cadeira por cadeira, e reler o YAML a cada uma levava a rota a 8 s
    (24/09). A data de modificação na chave faz uma assinatura nova valer
    no pedido seguinte. O registro é imutável: pode ser compartilhado."""
    return load_texto(Path(caminho).read_text(encoding="utf-8"))


def load_texto(texto: str) -> Registro:
    d = yaml.safe_load(texto) or {}
    entradas = []
    for e in d.get("entradas") or []:
        entradas.append(Entrada(
            ano=int(e.get("ano", d.get("ano", 2026))),
            entidade_tipo=e["entidade_tipo"], entidade_id=str(e["entidade_id"]),
            numero=(str(e["numero"]) if e.get("numero") not in (None, "")
                    else None),
            ideologia=e["ideologia"], posicao_governo=e.get("posicao_governo"),
            fonte=e.get("fonte", ""), justificativa=e.get("justificativa", ""),
            autor=e.get("autor") or "",
            decidido_em=e.get("decidido_em"),
            valido_de=e.get("valido_de"), valido_ate=e.get("valido_ate"),
            grau_controversia=int(e.get("grau_controversia", 0)),
            versao=str(d.get("versao", "2026.0")),
        ))
    return Registro(
        versao=str(d.get("versao", "2026.0")), ano=int(d.get("ano", 2026)),
        curador=d.get("curador"), publicada_em=d.get("publicada_em"),
        entradas=tuple(entradas),
    )


def validar(reg: Registro) -> list[str]:
    problemas = []
    if reg.curador is None:
        problemas.append(
            "sem curador: a classificacao exige autoria identificada (§6)"
        )
    vistos = set()
    for e in reg.entradas:
        for p in e.valida():
            problemas.append(f"{e.entidade_tipo}:{e.entidade_id}: {p}")
        chave = (e.ano, e.entidade_tipo, e.entidade_id, e.versao)
        if chave in vistos:
            problemas.append(f"entrada duplicada: {chave}")
        vistos.add(chave)
        # O cadastro de partidos é o de 2026. Conferir contra ele só faz
        # sentido na classificação de 2026: em 2018 o 25 era DEM e o 17 era
        # PSL, e cobrar que batam com o cadastro de hoje acusaria erro no
        # arquivo certo (classificação por ano, decisão de 22/09/2026).
        if (e.entidade_tipo == "partido" and e.numero
                and reg.ano == ANO_CADASTRO):
            from .partidos import resolver
            if not resolver(e.numero, e.entidade_id).cadastrado:
                problemas.append(
                    f"partido:{e.entidade_id}: numero {e.numero} e sigla nao "
                    "batem com config/partidos_2026.yaml")
    return problemas


def por_numero(reg: Registro | None = None) -> dict[str, dict]:
    """Classificação de cada partido do cadastro, por número.

    Só devolve partidos com entrada. Partido sem entrada fica de fora — e a
    interface o desenha com hachura, não com o amarelo do centro."""
    from .partidos import cadastro
    reg = reg or load()
    out = {}
    for p in cadastro()["lista"]:
        e = reg.por_partido(p["sigla"], p["numero"])
        if e is None:
            continue
        bloco = next(b for b, cats in REGRA_AGRUPAMENTO.items()
                     if e.ideologia in cats)
        out[p["numero"]] = {
            "sigla": p["sigla"], "ideologia": e.ideologia,
            "rotulo": ROTULOS[e.ideologia], "bloco": bloco,
            "cor": PALETA[e.ideologia], "simbolo": SIMBOLOS[e.ideologia],
            "grau_controversia": e.grau_controversia,
            "justificativa": e.justificativa, "fonte": e.fonte,
        }
    return out


BRT = ZoneInfo("America/Sao_Paulo")


def assinar(curador: str, path: Path | None = None,
            quando: datetime | None = None) -> dict:
    """Assina a classificação: o ato que liga o mapa ideológico.

    Preenche `curador` e `publicada_em`, completa `autor` e `decidido_em`
    das entradas que ainda não têm, sobe a versão e guarda a anterior em
    `config/classificacao.anterior/`. Recusa se, depois de assinada, a
    validação ainda acusar problema — assinar não conserta entrada sem
    fonte ou sem justificativa.

    Trabalha sobre o texto do arquivo, não sobre o YAML reescrito: os
    comentários que documentam o método ficam onde estão.
    """
    import re

    curador = " ".join((curador or "").split())
    if len(curador) < 3:
        raise ClassificationError("curador precisa de nome identificável")
    p = path or caminho_padrao()
    texto = p.read_text(encoding="utf-8")
    agora = quando or datetime.now(BRT)
    hoje = agora.date().isoformat()

    d = yaml.safe_load(texto) or {}
    versao_antiga = str(d.get("versao", "2026.0"))
    m = re.match(r"^(\d{4})\.(\d+)", versao_antiga)
    versao_nova = (f"{m.group(1)}.{int(m.group(2)) + 1}"
                   if m and d.get("curador") else
                   (f"{m.group(1)}.{m.group(2)}" if m else "2026.1"))

    nome_yaml = '"' + curador.replace('"', "'") + '"'
    novo = _marcar_assinatura(texto, curador, versao_nova, agora)
    novo = re.sub(r'(?m)^versao:.*$', f'versao: "{versao_nova}"', novo, 1)
    novo = re.sub(r'(?m)^curador:.*$', f"curador: {nome_yaml}", novo, 1)
    novo = re.sub(r'(?m)^publicada_em:.*$', f"publicada_em: {hoje}", novo, 1)
    novo = re.sub(r"autor: null", f"autor: {nome_yaml}, decidido_em: {hoje}",
                  novo)

    reg_novo = load_texto(novo)
    problemas = validar(reg_novo)
    if problemas:
        raise ClassificationError(
            "assinatura recusada; a classificacao tem problemas:\n  "
            + "\n  ".join(problemas))

    pasta = p.parent / "classificacao.anterior"
    pasta.mkdir(exist_ok=True)
    shutil.copy2(p, pasta / f"classificacao-{agora:%Y%m%d-%H%M%S}.yaml")
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(novo, encoding="utf-8")
    os.replace(tmp, p)
    return {"versao": versao_nova, "curador": curador,
            "entradas": len(reg_novo.entradas), "publicada_em": hoje}


class _SemApelidos(yaml.SafeDumper):
    """Sem `&id001`/`*id001`: o arquivo é lido por gente, e uma data
    repetida em trinta entradas não deve virar referência cruzada."""

    def ignore_aliases(self, data):
        return True


_RE_MARCA = r"(?m)^# (PROPOSTA PARA CURADORIA.*|ASSINADA por .*)$"


def _marcar_assinatura(texto: str, curador: str, versao: str,
                       agora: datetime) -> str:
    """Troca a linha de estado do cabeçalho ("PROPOSTA..." → "ASSINADA
    por..."): um arquivo assinado que ainda se diz proposta confunde quem
    abrir o YAML."""
    import re
    linha = (f"# ASSINADA por {curador} em {agora:%d/%m/%Y} "
             f"(versão {versao}).")
    return re.sub(_RE_MARCA, lambda _: linha, texto, count=1)


def _proxima_versao(d: dict) -> str:
    import re
    m = re.match(r"^(\d{4})\.(\d+)", str(d.get("versao", "2026.0")))
    if not m:
        return "2026.1"
    if d.get("curador"):
        return f"{m.group(1)}.{int(m.group(2)) + 1}"
    return f"{m.group(1)}.{m.group(2)}"


def gravar_versao(curador: str, alteracoes: list[dict],
                  path: Path | None = None,
                  quando: datetime | None = None) -> dict:
    """Nova versão assinada a partir de alterações feitas no painel.

    Cada item de `alteracoes` é `{numero, ideologia, grau_controversia,
    justificativa, fonte}`; `ideologia: null` tira o partido da
    classificação (volta a aparecer com hachura). Entrada alterada recebe o
    nome de quem assina como autor e a data de hoje; entrada intocada
    mantém autor e data que já tinha — e as que ainda estavam sem autor
    (proposta) passam a ter, porque assinar a versão é assinar o conjunto.

    O cabeçalho de comentários (método, fontes) é preservado; o corpo é
    regravado. A versão anterior vai para `classificacao.anterior/`.
    """
    from .partidos import cadastro

    curador = " ".join((curador or "").split())
    if len(curador) < 3:
        raise ClassificationError("curador precisa de nome identificável")
    p = path or caminho_padrao()
    texto = p.read_text(encoding="utf-8")
    d = yaml.safe_load(texto) or {}
    agora = quando or datetime.now(BRT)
    hoje = agora.date()

    brutas = [dict(e) for e in (d.get("entradas") or [])]
    por_num = {str(e.get("numero")): e for e in brutas if e.get("numero")}
    cad = cadastro()["por_numero"]
    mudou = 0
    for alt in alteracoes:
        num = str(alt.get("numero") or "")
        if num not in cad:
            raise ClassificationError(f"partido nº {num!r} fora do cadastro")
        e = por_num.get(num)
        ideo = alt.get("ideologia")
        if not ideo:
            if e is not None:
                brutas.remove(e)
                del por_num[num]
                mudou += 1
            continue
        novo = {
            "ideologia": ideo,
            "grau_controversia": int(alt.get("grau_controversia") or 0),
            "justificativa": str(alt.get("justificativa") or "").strip(),
            "fonte": str(alt.get("fonte") or "").strip(),
        }
        if e is None:
            e = {"entidade_tipo": "partido",
                 "entidade_id": cad[num]["sigla"], "numero": num,
                 "posicao_governo": None}
            brutas.append(e)
            por_num[num] = e
        elif all(e.get(k) == v for k, v in novo.items()):
            continue
        e.update(novo)
        e["autor"] = curador
        e["decidido_em"] = hoje
        mudou += 1
    for e in brutas:
        if not e.get("autor"):
            e["autor"] = curador
            e["decidido_em"] = hoje

    versao = _proxima_versao(d)
    corpo = {"versao": versao, "ano": int(d.get("ano", 2026)),
             "curador": curador, "publicada_em": hoje, "entradas": brutas}
    reg_novo = load_texto(yaml.safe_dump(corpo, allow_unicode=True))
    problemas = validar(reg_novo)
    if problemas:
        raise ClassificationError(
            "versão não gravada; a classificação tem problemas:\n  "
            + "\n  ".join(problemas))

    i = texto.find("\nversao:")
    cabecalho = texto[:i + 1] if i >= 0 else ""
    cabecalho = _marcar_assinatura(cabecalho, curador, versao, agora)
    ordem = {c: k for k, c in enumerate(CATEGORIAS)}
    brutas.sort(key=lambda e: (ordem.get(e.get("ideologia"), 9),
                               str(e.get("entidade_id"))))
    campos = ("entidade_tipo", "entidade_id", "numero", "ideologia",
              "posicao_governo", "grau_controversia", "autor",
              "decidido_em", "fonte", "justificativa")
    corpo["entradas"] = [{k: e.get(k) for k in campos if k in e or
                          k == "posicao_governo"} for e in brutas]
    novo_texto = cabecalho + yaml.dump(
        corpo, Dumper=_SemApelidos, allow_unicode=True, sort_keys=False,
        width=88)

    pasta = p.parent / "classificacao.anterior"
    pasta.mkdir(exist_ok=True)
    shutil.copy2(p, pasta / f"classificacao-{agora:%Y%m%d-%H%M%S}.yaml")
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(novo_texto, encoding="utf-8")
    os.replace(tmp, p)
    return {"versao": versao, "curador": curador, "alteradas": mudou,
            "entradas": len(brutas), "publicada_em": hoje.isoformat()}


def saldo(pct_direita: float | None, pct_esquerda: float | None,
          pct_centro: float | None,
          pct_sem_classificacao: float | None) -> dict:
    """Indicador descritivo do §6: `% direita − % esquerda`, de −100 a +100.

    Vem sempre acompanhado de centro e não classificados, porque saldo zero
    pode significar coisas políticas muito diferentes: um município metade
    esquerda metade direita e um município inteiramente de centro dão o mesmo
    zero.

    O §6 é explícito: isto NÃO é medida psicológica de polarização.
    """
    if pct_direita is None or pct_esquerda is None:
        return {"saldo": None, "motivo": "classificacao indisponivel"}
    return {
        "saldo": pct_direita - pct_esquerda,
        "pct_direita": pct_direita,
        "pct_esquerda": pct_esquerda,
        "pct_centro": pct_centro,
        "pct_sem_classificacao": pct_sem_classificacao,
        "aviso": ("Indicador descritivo da composição do voto. Saldo zero "
                  "pode corresponder a situações políticas muito diferentes. "
                  "Não é medida de polarização afetiva."),
    }


def metodologia() -> dict:
    """Payload de `/api/v1/classifications`."""
    reg = load()
    problemas = validar(reg)
    return {
        "versao": reg.versao,
        "ano": reg.ano,
        "curador": reg.curador,
        "publicada_em": (reg.publicada_em.isoformat()
                         if isinstance(reg.publicada_em, date) else None),
        "estado": ("curadoria_pendente" if reg.curadoria_pendente
                   else "disponivel"),
        "motivo": ("A classificação ideológica é um ato editorial e exige "
                   "fonte, justificativa, autor, data, validade e grau de "
                   "controvérsia por entrada (§6). Enquanto a curadoria não "
                   "for feita e assinada, o mapa ideológico fica desabilitado "
                   "e as camadas de apuração continuam disponíveis."
                   if reg.curadoria_pendente else None),
        "entradas": len(reg.entradas),
        "problemas": problemas,
        # A proposta aparece mesmo antes da assinatura: é o que o curador
        # revisa. O telão só a usa com `estado == disponivel`.
        "por_partido": por_numero(reg),
        "rotulos": ROTULOS,
        "vocabulario": {
            "ideologia": list(CATEGORIAS),
            "posicao_governo": list(POSICOES_GOVERNO),
            "regra_agrupamento": {k: list(v)
                                  for k, v in REGRA_AGRUPAMENTO.items()},
        },
        "paleta": PALETA,
        "simbolos": SIMBOLOS,
        "sem_classificacao": SEM_CLASSIFICACAO,
        "dimensoes_separadas": [
            {"nome": "ideologia",
             "nota": "não determina posição em relação ao governo"},
            {"nome": "posicao_governo",
             "nota": "apoio/oposição; muda sem que a ideologia mude"},
            {"nome": "alianca_estadual",
             "nota": "coligações locais contrariam o alinhamento nacional "
                     "com frequência; não herdar do cabeça de chapa"},
        ],
        "regras": [
            "A cor do cabeça de chapa NÃO é atribuída à coligação inteira.",
            "Classificação não é atributo oficial do TSE.",
            "Alteração de classificação gera nova versão; mapas históricos "
            "não são reescritos em silêncio.",
            "Comparação entre anos usa a classificação de cada época OU uma "
            "classificação harmonizada, sempre com o método identificado.",
            "Ausência de classificação tem padrão visual próprio e nunca usa "
            "o amarelo do centro.",
        ],
        "camadas": [
            "partido/bloco do candidato líder, intensidade pela margem",
            "participação dos votos de cada bloco",
            "governos conquistados (contar governos e eleitorado separado)",
            "Senado: duas marcas por UF, podendo ter cores diferentes",
            "variação entre eleições comparáveis",
            "incerteza e ausência de informação",
        ],
    }
