"""Diagnóstico do gate: onde cada modelo erra (cargo, UF, rodada, ordem).

Lê `data/exports/ensaio2022/gate2022.json` (o `detalhe` por disputa) e
grava `diagnostico_gate.md` na mesma pasta. Não roda simulação nenhuma: só
reorganiza o que o gate já mediu, para escolher a próxima melhoria pelos
dados.

Uso:
    python scripts/diagnostico_gate.py [--modelo m3]
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR                               # noqa: E402

PASTA = DATA_DIR / "exports" / "ensaio2022"
FASES = {"início (rodadas 1–3)": (0, 3), "meio (4–6)": (3, 6),
         "fim (7–9)": (6, 9)}


def f2(x):
    return "—" if x is None else f"{x:.2f}".replace(".", ",")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default="m3")
    a = ap.parse_args()
    m = a.modelo
    d = json.loads((PASTA / "gate2022.json").read_text(encoding="utf-8"))
    det = d["detalhe"]
    L = [f"# Diagnóstico do gate 2022 — {m.upper()} contra M0 e M2", "",
         f"Fonte: gate de {d.get('gerado_em', '?')[:16]}, "
         f"{len(det)} disputas. Erro = erro mediano da projeção do líder "
         "(pp) em cada rodada; ganho = erro do M0 − erro do modelo.", ""]

    def erros(disp, ordem, mod, ini, fim):
        return [x["erro"] for x in disp["ordens"][ordem][mod][ini:fim]
                if x["erro"] is not None]

    # 1. Por cargo e fase
    L += ["## 1. Por cargo e fase da apuração", "",
          "| cargo | ordem | fase | M0 | M2 | " + m.upper() + " | ganho "
          + m.upper() + " sobre M0 |", "|---|---|---|---|---|---|---|"]
    cargos = sorted({x["cargo"] for x in det})
    for c in cargos:
        ds = [x for x in det if x["cargo"] == c]
        for ordem in ("real", "aleatoria"):
            for fase, (i, j) in FASES.items():
                med = {k: st.median(
                    [e for x in ds for e in erros(x, ordem, k, i, j)] or [0])
                    for k in ("m0", "m2", m)}
                L.append(f"| {c} | {ordem} | {fase} | {f2(med['m0'])} | "
                         f"{f2(med['m2'])} | {f2(med[m])} | "
                         f"{f2(med['m0'] - med[m])} |")
    L.append("")

    # 2. Disputas onde o modelo mais erra no início
    lin = []
    for x in det:
        for ordem in ("real", "aleatoria"):
            e = {k: st.mean(erros(x, ordem, k, 0, 3) or [0])
                 for k in ("m0", "m2", m)}
            cob = [y["cob80"] for y in x["ordens"][ordem][m]]
            lin.append((x["disputa"], ordem, e, 100 * sum(cob) / len(cob)))
    lin.sort(key=lambda t: -t[2][m])
    L += ["## 2. As 15 disputas com maior erro no início (rodadas 1–3)", "",
          "| disputa | ordem | M0 | M2 | " + m.upper() + " | "
          + m.upper() + " pior que M0? | cobertura 80% |",
          "|---|---|---|---|---|---|---|"]
    for disp, ordem, e, cob in lin[:15]:
        L.append(f"| {disp} | {ordem} | {f2(e['m0'])} | {f2(e['m2'])} | "
                 f"{f2(e[m])} | {'**sim**' if e[m] > e['m0'] else 'não'} | "
                 f"{cob:.0f}% |")
    L.append("")

    # 3. Onde o modelo perde para o M0
    perde = [t for t in lin if t[2][m] > t[2]["m0"]]
    L += [f"## 3. Disputas em que o {m.upper()} erra mais que o M0 no início",
          "", f"{len(perde)} de {len(lin)} (disputa × ordem).", ""]
    for disp, ordem, e, _ in sorted(perde, key=lambda t: t[2]["m0"]
                                    - t[2][m])[:15]:
        L.append(f"- {disp} ({ordem}): {m.upper()} {f2(e[m])} × M0 "
                 f"{f2(e['m0'])} pp")
    L.append("")

    # 4. Cobertura por cargo
    L += ["## 4. Cobertura do intervalo de 80% por cargo", "",
          "| cargo | ordem | M2 | " + m.upper() + " |", "|---|---|---|---|"]
    for c in cargos:
        ds = [x for x in det if x["cargo"] == c]
        for ordem in ("real", "aleatoria"):
            cv = {}
            for k in ("m2", m):
                v = [y["cob80"] for x in ds for y in x["ordens"][ordem][k]]
                cv[k] = 100 * sum(v) / len(v)
            L.append(f"| {c} | {ordem} | {cv['m2']:.1f}% | {cv[m]:.1f}% |")
    L.append("")

    # 5. Confiança errada
    L += ["## 5. Maior probabilidade dada a quem perdeu", "",
          "| disputa | ordem | M2 | " + m.upper() + " |", "|---|---|---|---|"]
    conf = []
    for x in det:
        for ordem in ("real", "aleatoria"):
            v = {k: max(y["errada"] for y in x["ordens"][ordem][k])
                 for k in ("m2", m)}
            conf.append((x["disputa"], ordem, v))
    conf.sort(key=lambda t: -max(t[2].values()))
    for disp, ordem, v in conf[:10]:
        L.append(f"| {disp} | {ordem} | {100 * v['m2']:.0f}% | "
                 f"{100 * v[m]:.0f}% |")
    L.append("")

    out = PASTA / "diagnostico_gate.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
