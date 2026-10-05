"""M3.1 — resposta à inclinação por bloco ideológico, medida em 2018.

Para cada candidato de governador e senador de 2018 (apuração completa), a
resposta (slope) do log da fatia dele à inclinação histórica de 2014 — a
mesma regressão que o M3 faz na noite, aqui com todos os municípios — e o
bloco do partido pela classificação assinada. Dá a régua do M3.1: a média
e a dispersão do slope em cada bloco (o "prior" que o M3.1 usa enquanto a
noite ainda não mediu o slope do candidato).

Só 2018: 2022 é a eleição de teste.

    python scripts/medir_blocos.py
Grava `data/exports/ensaio2022/medicao_blocos_2018.json`.
"""

from __future__ import annotations

import json
import math
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ensaio2022 as E                                    # noqa: E402
from obs2026.engine import historico as HI                # noqa: E402
from obs2026.engine.registro_continuo import UFS          # noqa: E402
from obs2026.politics import classification as CL         # noqa: E402

FATIA_MIN = 0.03


def uma(cargo_nome: str, uf: str) -> list[dict]:
    cargo = E.CARGOS[cargo_nome]
    muns, vagas, eleicao = E.carregar_historico(2018, cargo, uf)
    finais = [E.observar(m, set(m.zonas), cargo, vagas, eleicao)
              for m in muns]
    inc = HI.inclinacao(2014)
    reg = CL.load()
    pts = []
    for o in finais:
        v = inc.get(HI.chave(o.unit))
        b = o.obs
        if v is None or b.validos <= 0:
            continue
        pts.append((HI.logit(v), b.validos, b.votos_por_candidato))
    W = sum(w for _, w, _ in pts)
    if len(pts) < 3 or W <= 0:
        return []
    xbar = sum(w * x for x, w, _ in pts) / W
    sxx = sum(w * (x - xbar) ** 2 for x, w, _ in pts)
    cands = {c.sqcand: c for o in finais for c in o.result.cargo.candidatos
             if c.conta_como_valido}
    tot = {c: sum(v.get(c, 0) for _, _, v in pts) for c in cands}
    T = sum(tot.values())
    out = []
    for sq, c in cands.items():
        if not T or tot[sq] / T < FATIA_MIN:
            continue
        ys = [(x, w, math.log((v.get(sq, 0) + 0.5) / (w + 0.5)))
              for x, w, v in pts]
        ybar = sum(w * y for _, w, y in ys) / W
        slope = sum(w * (x - xbar) * (y - ybar) for x, w, y in ys) / sxx
        out.append({"disputa": f"2018-{cargo_nome}-{uf}",
                    "candidato": c.nome_urna, "partido": c.partido_sigla,
                    "numero": c.partido_numero,
                    "bloco": reg.bloco(c.partido_sigla, c.partido_numero),
                    "fatia": round(tot[sq] / T, 4),
                    "slope": round(slope, 4), "municipios": len(pts)})
    return out


def main() -> None:
    tarefas = [(c, uf) for c in ("governador", "senador") for uf in UFS
               if uf not in ("br", "zz")]
    with ProcessPoolExecutor(4) as ex:
        linhas = [x for r in ex.map(uma, *zip(*tarefas)) for x in r]
    resumo = {}
    for b in ("esquerda", "centro", "direita", None):
        v = [x["slope"] for x in linhas if x["bloco"] == b]
        if len(v) >= 2:
            resumo[str(b)] = {"n": len(v), "media": round(st.mean(v), 3),
                              "mediana": round(st.median(v), 3),
                              "dp": round(st.stdev(v), 3)}
    todos = [x["slope"] for x in linhas]
    resumo["todos"] = {"n": len(todos), "media": round(st.mean(todos), 3),
                       "dp": round(st.stdev(todos), 3)}
    (E.SAIDA / "medicao_blocos_2018.json").write_text(json.dumps(
        {"resumo": resumo, "candidatos": linhas}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    sem = sorted({(x["partido"], x["numero"]) for x in linhas
                  if x["bloco"] is None})
    print("partidos sem bloco (2018 x cadastro 2026):", sem)


if __name__ == "__main__":
    main()
