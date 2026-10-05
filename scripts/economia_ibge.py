"""Perfil econômico de cada município, do PIB dos Municípios do IBGE.

Pedido de 05/10/2026, para testar e depois usar nas estimativas duas
leituras do voto que chega tarde: onde o **agro** pesa, e onde o município
**depende do Estado** (administração pública como fatia da economia).

Fonte: SIDRA, tabela 5938 (PIB dos Municípios), ano mais recente que já
tenha o valor adicionado por setor.
Variáveis: 37 PIB; 498 valor adicionado bruto (VAB) total; 513 VAB da
agropecuária; 517 da indústria; 6575 dos serviços; 525 da administração,
defesa, educação e saúde públicas e seguridade social.

    python scripts/economia_ibge.py            # usa o arquivo já baixado
    python scripts/economia_ibge.py --baixar   # busca de novo no IBGE

Grava `data/historico/economia_<ano>.json`: por `uf:mun` (código do TSE, a
mesma chave da inclinação), a fatia de cada setor no VAB e o PIB. A rede só
é usada com `--baixar`; o que o motor lê é o arquivo em disco.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR                               # noqa: E402

BRUTO = DATA_DIR / "raw" / "ibge" / "pib_municipios_sidra5938.json"
#: Um ano por pedido: o SIDRA recusa mais de 50 mil valores, e 5.570
#: municípios × 6 variáveis já são 33 mil.
URL = ("https://apisidra.ibge.gov.br/values/t/5938/n6/all/"
       "v/37,498,513,517,6575,525/p/{ano}?formato=json")
VARS = {"37": "pib", "498": "vab", "513": "agro", "517": "ind",
        "6575": "serv", "525": "adm"}


def _tem_setores(linhas: list[dict]) -> bool:
    return sum(1 for l in linhas if l["D2C"] == "513"
               and l["V"].replace(".", "").isdigit()) >= 5000


def baixar() -> int:
    """Busca do ano passado para trás até achar um com o valor adicionado
    por setor: o IBGE publica o PIB total antes do detalhe por atividade
    (em 10/2026, 2022 e 2023 só tinham o total; o detalhe ia até 2021)."""
    BRUTO.parent.mkdir(parents=True, exist_ok=True)
    for ano in range(datetime.now().year - 1, datetime.now().year - 9, -1):
        try:
            with urllib.request.urlopen(URL.format(ano=ano),
                                        timeout=180) as r:
                corpo = r.read()
        except OSError:
            continue
        if _tem_setores(json.loads(corpo.decode("utf-8"))[1:]):
            BRUTO.write_bytes(corpo)
            return ano
    sys.exit("o IBGE não devolveu nenhum ano com o valor por setor")


def codigos_tse(ambiente: str = "oficial2026") -> dict[str, str]:
    """IBGE (7 dígitos) → `uf:mun` do TSE, do cadastro que o EA12 trouxe."""
    con = sqlite3.connect(
        f"file:{DATA_DIR / 'db' / (ambiente + '.sqlite')}?mode=ro", uri=True)
    return {ibge: f"{uf}:{tse}" for ibge, uf, tse in con.execute(
        "select cd_ibge, uf, cd_tse from geography where tipo = 'mu' "
        "and cd_ibge is not null and environment = ?", (ambiente,))}


def gerar() -> dict:
    linhas = json.loads(BRUTO.read_text(encoding="utf-8"))[1:]
    if not _tem_setores(linhas):
        sys.exit(f"{BRUTO} não tem o valor por setor; rode com --baixar")
    ano = max(int(l["D3C"]) for l in linhas)
    por: dict[str, dict] = {}
    for l in linhas:
        if int(l["D3C"]) != ano or l["D2C"] not in VARS:
            continue
        try:
            v = float(l["V"])
        except ValueError:            # '...' e '-' do IBGE: sem informação
            continue
        por.setdefault(l["D1C"], {})[VARS[l["D2C"]]] = v
    tse = codigos_tse()
    out, sem = {}, 0
    for ibge, d in por.items():
        chave = tse.get(ibge)
        vab = d.get("vab") or 0.0
        if chave is None or vab <= 0:
            sem += 1
            continue
        out[chave] = {
            **{k: round(max(0.0, d.get(k, 0.0)) / vab, 5)
               for k in ("agro", "ind", "serv", "adm")},
            "pib_mil": d.get("pib")}
    return {"ano": ano, "fonte": "IBGE, PIB dos Municípios (SIDRA 5938)",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
            "campos": {"agro": "VAB da agropecuária ÷ VAB total",
                       "ind": "VAB da indústria ÷ VAB total",
                       "serv": "VAB dos serviços privados ÷ VAB total",
                       "adm": "VAB da administração pública ÷ VAB total",
                       "pib_mil": "PIB a preços correntes, em mil reais"},
            "municipios_sem_codigo_tse": sem, "municipios": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baixar", action="store_true")
    a = ap.parse_args()
    if a.baixar or not BRUTO.exists():
        baixar()
    d = gerar()
    p = DATA_DIR / "historico" / f"economia_{d['ano']}.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    ms = d["municipios"].values()
    print(f"{p}: {len(d['municipios'])} municípios de {d['ano']} "
          f"({d['municipios_sem_codigo_tse']} sem código do TSE)")
    for k in ("agro", "adm", "ind", "serv"):
        vs = sorted(m[k] for m in ms)
        print(f"  {k:5} mediana {100 * vs[len(vs) // 2]:5.1f}%  "
              f"p10 {100 * vs[len(vs) // 10]:5.1f}%  "
              f"p90 {100 * vs[9 * len(vs) // 10]:5.1f}%")


if __name__ == "__main__":
    main()
