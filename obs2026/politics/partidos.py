"""Cadastro de partidos: identidade e cor para o mapa por partido.

Identidade é fato (número, sigla, nome); ideologia é ato editorial e mora em
`classification.py`. Este módulo não sabe nada de esquerda ou direita.

## Resolução: número E sigla

O EA20 manda o número (`par.n`) e a sigla (`par.sg`). O número sozinho não
serve de chave porque é reusado entre eleições — em 2022 o 14 era o PTB, em
2026 é o Missão. A regra é:

- número conhecido e sigla entre as grafias cadastradas: é o partido;
- número conhecido e sigla estranha: **desconhecido**. Não se pinta um
  partido com a cor de outro por coincidência de número;
- sem número (Senado, que só manda a sigla): procura pela sigla;
- nada bate: desconhecido, com cor gerada da própria sigla — estável entre
  recargas, para o mesmo partido não trocar de cor no meio da noite.

O simulado do TSE usa partidos fictícios ("P 9998"); eles caem no último
caso e o mapa por partido funciona com eles do mesmo jeito.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import CONFIG_DIR
from ..tse.parsers.common import fold

#: Cores para partidos fora do cadastro. Tons médios, nenhum igual a um
#: cadastrado; a escolha é por hash da sigla, então é estável.
_CORES_FORA = ("#7E9CB8", "#B89A7E", "#8FB87E", "#B87EA9", "#7EB8B0",
               "#B8B07E", "#9A7EB8", "#B87E7E", "#7EB88A", "#A0A0C8",
               "#C8A0A0", "#A0C8B4")


#: Como o Senado escreve "sem filiação".
SEM_PARTIDO = frozenset({"S/PARTIDO", "S/ PARTIDO", "SEM PARTIDO"})
COR_SEM_PARTIDO = "#6E7681"


@dataclass(frozen=True, slots=True)
class Partido:
    numero: str | None
    sigla: str
    nome: str | None
    cor: str
    cadastrado: bool

    def as_dict(self) -> dict:
        return {"numero": self.numero, "sigla": self.sigla, "nome": self.nome,
                "cor": self.cor, "cadastrado": self.cadastrado}


def _caminho() -> Path:
    alt = os.environ.get("OBS2026_PARTIDOS")
    return Path(alt) if alt else CONFIG_DIR / "partidos_2026.yaml"


@lru_cache(maxsize=4)
def _carregar(caminho: str, mtime: float) -> dict:
    d = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    por_numero: dict[str, dict] = {}
    por_sigla: dict[str, dict] = {}
    for p in d.get("partidos") or []:
        p = dict(p)
        p["numero"] = str(p["numero"])
        p["_siglas"] = {fold(s) for s in (p.get("siglas") or [])} | {
            fold(p["sigla"])}
        por_numero[p["numero"]] = p
        for s in p["_siglas"]:
            por_sigla[s] = p
    return {"versao": str(d.get("versao", "")), "por_numero": por_numero,
            "por_sigla": por_sigla, "lista": list(por_numero.values())}


def cadastro() -> dict:
    p = _caminho()
    if not p.exists():
        return {"versao": "inexistente", "por_numero": {}, "por_sigla": {},
                "lista": []}
    return _carregar(str(p), p.stat().st_mtime)


def _cor_fora(sigla: str) -> str:
    h = int(hashlib.sha256(fold(sigla).encode()).hexdigest()[:8], 16)
    return _CORES_FORA[h % len(_CORES_FORA)]


def resolver(numero: str | None, sigla: str | None) -> Partido:
    """Partido pelo número e sigla do TSE. Nunca levanta: o desconhecido tem
    representação própria.

    Numa eleição passada (seletor de anos), resolve pela linhagem: ver
    `_resolver_passado`."""
    from ..contexto import ano_passado
    ano = ano_passado()
    if ano is not None:
        return _resolver_passado(numero, sigla, ano)
    return _resolver_hoje(numero, sigla)


def _resolver_passado(numero: str | None, sigla: str | None,
                      ano: int) -> Partido:
    """Partido de uma eleição passada: a SIGLA E O NÚMERO DA ÉPOCA, com a cor
    da legenda de hoje a que a linhagem assinada o liga.

    O cadastro é de 2026: o 17 de 2018 (PSL) não está nele, e o 25 de 2018
    (DEM) cairia no PRD se casasse só pelo número. Pela linhagem, PSL e DEM
    de 2018 levam a cor do União Brasil — a mesma série tem a mesma cor em
    todos os anos, que é o que permite comparar mapas lado a lado —, mas o
    rótulo continua sendo o nome que estava na urna."""
    from . import linhagem as LIN
    sg = (sigla or "").strip()
    if fold(sg) in SEM_PARTIDO:
        return _resolver_hoje(numero, sigla)
    num = (str(numero).strip() if numero not in (None, "") else None)
    try:
        chave, _ = LIN.serie(sg, num, ano)
    except LIN.LinhagemError:
        chave = f"{num or ''}|{fold(sg)}"
    n_hoje, _, s_hoje = chave.partition("|")
    hoje = _resolver_hoje(n_hoje or None, s_hoje)
    if not hoje.cadastrado:
        rotulo = sg or (f"nº {num}" if num else "?")
        return Partido(num, rotulo, None, _cor_fora(rotulo), False)
    mesmo = fold(sg).replace(" ", "") == fold(hoje.sigla).replace(" ", "")
    return Partido(num, sg or hoje.sigla,
                   hoje.nome if mesmo else f"{sg} (hoje {hoje.sigla})",
                   hoje.cor, True)


def _resolver_hoje(numero: str | None, sigla: str | None) -> Partido:
    cad = cadastro()
    sg = (sigla or "").strip()
    if fold(sg) in SEM_PARTIDO:
        # Senador sem filiação: não é partido desconhecido, é ausência de
        # partido, e aparece assim.
        return Partido(None, "sem partido", "sem filiação partidária",
                       COR_SEM_PARTIDO, False)
    num = (str(numero).strip() if numero not in (None, "") else None)
    p = None
    if num is not None:
        cand = cad["por_numero"].get(num)
        if cand and (not sg or fold(sg) in cand["_siglas"]):
            p = cand
    elif sg:
        p = cad["por_sigla"].get(fold(sg))
    if p:
        return Partido(p["numero"], p["sigla"], p.get("nome"), p["cor"], True)
    rotulo = sg or (f"nº {num}" if num else "?")
    return Partido(num, rotulo, None, _cor_fora(rotulo), False)


def payload() -> dict:
    """Cadastro para o navegador: cor e nome por número."""
    cad = cadastro()
    return {
        "versao": cad["versao"],
        "partidos": [
            {"numero": p["numero"], "sigla": p["sigla"],
             "nome": p.get("nome"), "cor": p["cor"],
             "siglas": sorted(p.get("siglas") or [p["sigla"]])}
            for p in cad["lista"]
        ],
        "cores_fora_do_cadastro": list(_CORES_FORA),
        "regra": ("Partido identificado por número E sigla: números são "
                  "reusados entre eleições. Sem correspondência, o partido "
                  "aparece como fora do cadastro, com cor própria."),
    }


def gravar_cores(cores: dict[str, str], caminho: Path | None = None) -> int:
    """Troca a cor de partidos do cadastro, linha a linha.

    Edita o texto, não o YAML reescrito: o arquivo é uma tabela alinhada à
    mão, com comentários que explicam a regra de número e sigla. Guarda a
    versão anterior em `config/partidos.anterior/`."""
    import re
    import shutil
    from datetime import datetime

    p = caminho or _caminho()
    texto = p.read_text(encoding="utf-8")
    n = 0
    for numero, cor in cores.items():
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(cor or "")):
            raise ValueError(f"cor inválida para o nº {numero}: {cor!r}")
        padrao = re.compile(
            r'(?m)^(\s*- \{numero: "' + re.escape(str(numero))
            + r'".*?cor: ")#[0-9A-Fa-f]{6}(")')
        texto, k = padrao.subn(lambda m: m.group(1) + cor.upper()
                               + m.group(2), texto, count=1)
        if not k:
            raise ValueError(f"partido nº {numero} não está no cadastro")
        n += k
    pasta = p.parent / "partidos.anterior"
    pasta.mkdir(exist_ok=True)
    shutil.copy2(p, pasta / f"partidos-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, p)
    return n
