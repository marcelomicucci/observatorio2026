"""Inclinação histórica de cada município: a base do M3.

Para cada município, a fatia do PT nos votos válidos do 2º turno
presidencial de uma eleição (Haddad em 2018, Lula em 2022). É um número por
lugar que resume de que lado ele pende, e serve a qualquer cargo: o M3 usa
a inclinação da eleição ANTERIOR para corrigir o viés de ordem de chegada
(ver docs/M3-DESENHO.md) — se os primeiros municípios a chegar pendem para
um lado, o modelo sabe disso antes de o resto chegar.

O 2º turno é usado porque tem só dois candidatos em todo o país: a mesma
régua em todo município, sem depender de quantos candidatos cada lugar teve.

Uso:
    python scripts/historico_inclinacao.py 2018 2022

Grava `data/historico/inclinacao_<ano>.json`: {"uf:mun": fatia, ...} mais
metadados (eleitorado, votos válidos do 2º turno, fonte).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR                               # noqa: E402
from obs2026.history import tse_odsele as H                       # noqa: E402
from obs2026.tse.parsers.common import fold                       # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
HIST = RAIZ.parent
PASTA = DATA_DIR / "historico"
PT = "13"


def gerar(ano: int) -> dict:
    zv = H.arquivo(f"votacao_candidato_munzona_{ano}.zip", ano)
    votos: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    uf_de: dict[str, str] = {}
    for v in H.ler_votos(zv, "br", cargos=("1",), turno="2"):
        if fold(v.destinacao) != "VALIDO" or v.uf in ("vt", "br"):
            continue
        mun = v.cd_municipio.zfill(5)
        votos[mun][v.nr_partido] += v.votos
        uf_de[mun] = v.uf
    tabela, validos = {}, {}
    for mun, d in votos.items():
        tot = sum(d.values())
        if tot <= 0:
            continue
        chave = f"{uf_de[mun]}:{mun}"
        tabela[chave] = round(d.get(PT, 0) / tot, 6)
        validos[chave] = tot
    total = sum(validos.values())
    nacional = sum(tabela[k] * validos[k] for k in tabela) / total
    return {
        "ano": ano, "turno": 2, "cargo": "presidente",
        "medida": "fatia do PT nos votos válidos do 2º turno presidencial",
        "fonte": zv.name, "gerado_em": datetime.now().isoformat(),
        "municipios": len(tabela), "nacional": round(nacional, 6),
        "inclinacao": tabela, "validos": validos,
    }


def main() -> None:
    anos = [int(a) for a in sys.argv[1:]] or [2018, 2022]
    PASTA.mkdir(parents=True, exist_ok=True)
    for ano in anos:
        d = gerar(ano)
        (PASTA / f"inclinacao_{ano}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"{ano}: {d['municipios']} municípios, PT nacional no 2º turno "
              f"{100 * d['nacional']:.2f}%")


if __name__ == "__main__":
    main()
