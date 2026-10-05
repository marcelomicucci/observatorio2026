"""Mede o fator bruto do calibrador (M3.2) no ensaio de 2018.

A versão 2 do M3.2 usa um fator RELATIVO: o bruto de cada projeção dividido
pelo típico de 2018 (`models.BRUTO_REF`). Este script mede esse típico —
e só em 2018: 2022 é a eleição de teste e não entra na régua.

    python scripts/medir_conformal.py [--processos 4]

Poucas simulações bastam: o fator sai dos municípios já contados, antes da
simulação. Grava `data/exports/ensaio2022/medicao_conformal_2018.json`.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ensaio2022 as E                                    # noqa: E402
from obs2026.config import load_thresholds                # noqa: E402
from obs2026.engine import models as M                    # noqa: E402
from obs2026.engine.registro_continuo import UFS          # noqa: E402


def uma(cargo_nome: str, uf: str) -> dict:
    M.MEDICOES_CONFORMAL = []
    M.BRUTO_REF = None
    cargo = E.CARGOS[cargo_nome]
    disputa = f"2018-{cargo_nome}-{uf}"
    horas = E.carregar_ordem()
    muns, vagas, eleicao = E.carregar_historico(2018, cargo, uf)
    thr = load_thresholds()
    out = {"disputa": disputa, "ordens": {}}
    for ordem in ("real", "aleatoria"):
        M.MEDICOES_CONFORMAL = []
        E.rodar(muns, vagas, eleicao, horas, ordem=ordem, rodadas=10,
                sims=50, seed=7, cargo=cargo, disputa=disputa, thr=thr,
                modelos=("m32",), registrar=False)
        out["ordens"][ordem] = list(M.MEDICOES_CONFORMAL)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processos", type=int, default=4)
    a = ap.parse_args()
    tarefas = [(c, uf) for c in ("governador", "senador") for uf in UFS
               if uf not in ("br", "zz")]
    with ProcessPoolExecutor(a.processos) as ex:
        res = list(ex.map(uma, *zip(*tarefas)))
    todos = [b for r in res for o in r["ordens"].values() for b, _ in o]
    ref = st.median(todos)
    por = {r["disputa"]: round(st.median(
        [b for o in r["ordens"].values() for b, _ in o] or [0]), 3)
        for r in res}
    d = {"n": len(todos), "mediana": round(ref, 4),
         "p10": round(st.quantiles(todos, n=10)[0], 4),
         "p90": round(st.quantiles(todos, n=10)[-1], 4),
         "por_disputa": por}
    saida = E.SAIDA / "medicao_conformal_2018.json"
    saida.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print(json.dumps({k: v for k, v in d.items() if k != "por_disputa"}))
    for k, v in sorted(por.items(), key=lambda kv: kv[1]):
        print(f"{k}\t{v}")


if __name__ == "__main__":
    main()
