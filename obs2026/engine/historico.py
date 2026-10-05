"""Histórico por município, para o M3.

A inclinação de um município é a fatia do PT nos votos válidos do 2º turno
presidencial de uma eleição passada (`scripts/historico_inclinacao.py`).
Resume de que lado o lugar pende, com a mesma régua em todo o país.

Por que isso importa: a ordem de chegada dos municípios não é aleatória.
No 1º turno de 2022 o interior bolsonarista chegou antes, e M1/M2 — que só
olham o que já chegou nesta noite — demoraram a virar para Lula. Com a
inclinação, o modelo sabe que os que faltam pendem para o outro lado antes
de eles chegarem. Medido: a inclinação de 2018 e o voto de Lula em 2022 têm
correlação de 0,97 por município; prever cada município pela média nacional
erra 12,5 pp em média, pela inclinação 2,5 pp.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR

PASTA = DATA_DIR / "historico"
#: Extremos aparados antes do logit: um município com 0% ou 100% não pode
#: virar infinito.
PISO = 0.005


class HistoricoAusente(RuntimeError):
    pass


@lru_cache(maxsize=8)
def inclinacao(ano: int) -> dict[str, float]:
    """{"uf:mun": fatia do PT no 2º turno presidencial de `ano`}."""
    p = PASTA / f"inclinacao_{ano}.json"
    if not p.exists():
        raise HistoricoAusente(
            f"{p} não existe; rode `python scripts/historico_inclinacao.py "
            f"{ano}`")
    return json.loads(p.read_text(encoding="utf-8"))["inclinacao"]


def logit(p: float) -> float:
    p = min(max(p, PISO), 1 - PISO)
    return math.log(p / (1 - p))


def chave(unit: str) -> str:
    """`uf:mun` a partir da chave da unidade (`uf:mun` ou `uf:mun...`)."""
    uf, _, mun = unit.partition(":")
    return f"{uf.lower()}:{mun[:5]}"


def ano_de_referencia(ano_eleicao: int) -> int:
    """A eleição geral anterior: 2026 usa 2022; o teste de 2022 usa 2018."""
    return ano_eleicao - 4
