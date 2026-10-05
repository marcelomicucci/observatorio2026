"""Grupo territorial de cada município: capital (com seu arranjo) ou interior.

A capital e as cidades integradas a ela votam parecido entre si e diferente
do interior, e chegam juntas — em geral tarde. O M2 (`models.build_m2`)
estima cada grupo separadamente e dá incerteza larga ao grupo que ainda não
chegou, em vez de supor que ele vota como o que já chegou.

A lista vem de `config/arranjos_capitais.json`, gerado por
`scripts/gerar_arranjos.py` a partir do IBGE (arranjos populacionais, 2ª ed.)
e do cadastro de municípios do TSE. Lida uma vez, do disco: nada aqui depende
de rede na noite da apuração.
"""
from __future__ import annotations

import json
from functools import lru_cache

from ..config import CONFIG_DIR

ARQUIVO = CONFIG_DIR / "arranjos_capitais.json"

CAPITAL = "cap"
INTERIOR = "int"


@lru_cache(maxsize=1)
def _membros() -> frozenset[tuple[str, str]]:
    """(uf, municipio TSE) de todos os municípios do grupo capital."""
    if not ARQUIVO.exists():
        return frozenset()
    d = json.loads(ARQUIVO.read_text(encoding="utf-8"))["ufs"]
    return frozenset((uf, m) for uf, v in d.items() for m in v["membros"])


def disponivel() -> bool:
    return bool(_membros())


def e_capital(uf: str, municipio: str) -> bool:
    """Capital ou cidade do arranjo da capital, na mesma UF."""
    return (uf.lower(), str(municipio).zfill(5)) in _membros()


def grupo(uf: str, municipio: str) -> str:
    return CAPITAL if e_capital(uf, municipio) else INTERIOR
