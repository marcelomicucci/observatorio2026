"""Roda o ensaio em lote (todas as UFs) e faz o quadro-resumo por modelo.

    python scripts/ensaio2022_lote.py --so governador-2022 --so senador-2022
    python scripts/ensaio2022_lote.py --so governador-2018 --so senador-2018 \
        --modelos m1,m2@0.2,m2@0.3,m2@0.45 --sem-diario --nome calibracao2018

Cada disputa roda como em `ensaio2022.py` (ordem real e aleatória, cidades
grandes fatiadas por zona, todos os modelos sobre o mesmo retrato), em
processos paralelos. No fim escreve `data/exports/ensaio2022/<nome>.json` e
`<nome>.md` e imprime o quadro.

Método combinado com o usuário: **calibrar em 2018, testar em 2022**. A
eleição de teste não entra no ajuste; senão o modelo parece melhor do que é.

Métricas, por modelo e ordem, sobre as rodadas 1..N-1 (a última é o
resultado):

    acerta   rodada a partir da qual a projeção acerta o vencedor (ou as duas
             vagas do Senado 2018) e não erra mais
    cob80    fração dos candidatos cujo final caiu no intervalo de 80% —
             bem calibrado fica perto de 80%; abaixo, confiante demais;
             acima, largo demais
    errada   maior chance dada a um candidato que não venceu — o número que
             envergonharia no telão
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ensaio2022 as E                                    # noqa: E402
from obs2026.config import load_thresholds                # noqa: E402
from obs2026.engine.registro_continuo import UFS          # noqa: E402

GRUPOS = {
    "governador-2022": ("governador", 2022),
    "senador-2022": ("senador", 2022),
    "governador-2018": ("governador", 2018),
    "senador-2018": ("senador", 2018),
}


def uma(cargo_nome, ano, uf, rodadas, sims, semente, modelos, registrar):
    cargo = E.CARGOS[cargo_nome]
    disputa = f"{ano}-{cargo_nome}-{uf}"
    horas = E.carregar_ordem()
    muns, vagas, eleicao = E.carregar_historico(ano, cargo, uf)
    thr = load_thresholds()
    saida = {"disputa": disputa, "grupo": f"{cargo_nome}-{ano}", "uf": uf,
             "municipios": len(muns)}
    resultados = []
    for ordem in ("real", "aleatoria"):
        res = E.rodar(muns, vagas, eleicao, horas, ordem=ordem,
                      rodadas=rodadas, sims=sims, seed=semente, cargo=cargo,
                      disputa=disputa, thr=thr, modelos=modelos,
                      registrar=registrar)
        resultados.append(res)
        saida[ordem] = {m: E.resumo_modelo(res["modelos"][m], res["comum"],
                                           vagas) for m in modelos}
        saida[ordem]["contagem"] = E.estavel(
            [c["lider_contado_certo"] for c in res["comum"]])
    ult = resultados[0]["modelos"][modelos[0]][-1]["candidatos"]
    saida["vencedor"] = ", ".join(c["nome"] for c in ult[:vagas])
    if registrar:
        E.SAIDA.mkdir(parents=True, exist_ok=True)
        (E.SAIDA / f"{disputa}.json").write_text(json.dumps({
            "disputa": disputa, "gerado_em": datetime.now().isoformat(),
            "rodadas": rodadas, "sims": sims, "resultados": resultados},
            ensure_ascii=False, indent=1), encoding="utf-8")
    return saida


def fmt(v, pct=False):
    if v is None:
        return "—"
    return f"{v:.0%}" if pct else str(v)


def _dist(xs):
    """(mediana, 90º percentil) de % apurado; None conta como 100%."""
    v = sorted(100.0 if x is None else x for x in xs)
    if not v:
        return ("—", "—")
    med = v[len(v) // 2]
    p90 = v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]
    return (f"{med:.0f}%", f"{p90:.0f}%")


def quadro(linhas, modelos) -> str:
    out = []
    grupos = sorted({l["grupo"] for l in linhas})
    out.append("\n## Consolidado\n")
    out.append("| grupo | ordem | modelo | acerta até r3 | ≥90% num perdedor "
               "| cob80 | cob90 | cob95 | erro r1 (pp) |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for g in grupos:
        ls = [l for l in linhas if l["grupo"] == g]
        n = len(ls)
        for ordem in ("real", "aleatoria"):
            for m in modelos:
                cedo = sum(1 for l in ls if (l[ordem][m]["acerta"] or 99) <= 3)
                vex = sum(1 for l in ls if l[ordem][m]["errada"] >= 0.9)
                cob = sum(l[ordem][m]["cob80"] for l in ls) / n
                c90 = sum(l[ordem][m].get("cob90", 0) for l in ls) / n
                c95 = sum(l[ordem][m].get("cob95", 0) for l in ls) / n
                e1 = sorted(l[ordem][m]["erro_r1"] for l in ls)[n // 2]
                out.append(f"| {g} | {ordem} | {m} | {cedo}/{n} | {vex}/{n} "
                           f"| {cob:.0%} | {c90:.0%} | {c95:.0%} "
                           f"| {e1:.2f} |")
    out.append("\n## Com quanto apurado o número é confiável (ordem real)\n")
    out.append("% dos votos válidos apurados. *Vencedor*: a projeção aponta o "
               "vencedor certo e não muda mais. *Número*: o percentual dos "
               f"principais fica a menos de {E.TOLERANCIA_PP:.0f} pp do final "
               "e não sai mais. Mediana das UFs e o pior caso (90º "
               "percentil).\n")
    out.append("| grupo | modelo | vencedor: mediana | vencedor: 90% das UFs "
               "| número: mediana | número: 90% das UFs "
               "| contagem mostra o vencedor: mediana |")
    out.append("|---|---|---|---|---|---|---|")
    for g in grupos:
        ls = [l for l in linhas if l["grupo"] == g]
        for m in modelos:
            v = _dist([l["real"][m].get("vencedor_pct") for l in ls])
            n = _dist([l["real"][m].get("numero_pct") for l in ls])
            c = _dist([l["real"][m].get("contagem_pct") for l in ls])
            out.append(f"| {g} | {m} | {v[0]} | {v[1]} | {n[0]} | {n[1]} "
                       f"| {c[0]} |")
    for g in grupos:
        ls = sorted((l for l in linhas if l["grupo"] == g),
                    key=lambda x: x["uf"])
        out.append(f"\n### {g} — ordem real\n")
        out.append("| UF | vencedor | contagem | " + " | ".join(
            f"{m} acerta | {m} errada | {m} cob80" for m in modelos) + " |")
        out.append("|---|---|---|" + "---|---|---|" * len(modelos))
        for l in ls:
            r = l["real"]
            out.append(f"| {l['uf'].upper()} | {l['vencedor']} | "
                       f"{fmt(r['contagem'])} | " + " | ".join(
                           f"{fmt(r[m]['acerta'])} | {fmt(r[m]['errada'], True)}"
                           f" | {fmt(r[m]['cob80'], True)}" for m in modelos)
                       + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", choices=GRUPOS, action="append")
    ap.add_argument("--uf", action="append")
    ap.add_argument("--rodadas", type=int, default=10)
    ap.add_argument("--sims", type=int, default=1500)
    ap.add_argument("--semente", type=int, default=7)
    ap.add_argument("--modelos", default="m1,m2")
    ap.add_argument("--processos", type=int, default=10)
    ap.add_argument("--sem-diario", action="store_true",
                    help="não grava diários nem JSON por disputa (calibração)")
    ap.add_argument("--nome", default="resumo")
    a = ap.parse_args()

    modelos = tuple(m.strip() for m in a.modelos.split(","))
    ufs = [u.lower() for u in a.uf] if a.uf else list(UFS)
    tarefas = [(GRUPOS[g][0], GRUPOS[g][1], uf)
               for g in (a.so or ("governador-2022", "senador-2022",
                                  "senador-2018")) for uf in ufs]
    print(f"{len(tarefas)} disputas, modelos {', '.join(modelos)}, "
          f"{a.processos} processos", flush=True)

    linhas, falhas = [], []
    with ProcessPoolExecutor(max_workers=a.processos) as ex:
        fut = {ex.submit(uma, c, ano, uf, a.rodadas, a.sims, a.semente,
                         modelos, not a.sem_diario): (c, ano, uf)
               for c, ano, uf in tarefas}
        for f in as_completed(fut):
            c, ano, uf = fut[f]
            try:
                l = f.result()
                linhas.append(l)
                print(f"  ok  {l['disputa']}", flush=True)
            except Exception as exc:              # noqa: BLE001
                falhas.append((c, ano, uf, repr(exc)))
                print(f"  ERRO {ano}-{c}-{uf}: {exc!r}", flush=True)

    md = quadro(linhas, modelos)
    E.SAIDA.mkdir(parents=True, exist_ok=True)
    (E.SAIDA / f"{a.nome}.json").write_text(json.dumps(
        {"gerado_em": datetime.now().isoformat(), "rodadas": a.rodadas,
         "sims": a.sims, "modelos": modelos, "disputas": linhas,
         "falhas": falhas}, ensure_ascii=False, indent=1), encoding="utf-8")
    (E.SAIDA / f"{a.nome}.md").write_text(
        f"# Ensaio — {a.nome}\n\nOrdem de chegada real = hora de totalização "
        "da eleição 544 (2022); 2018 usa a ordem de 2022. Cidades do grupo "
        "capital e com 200 mil eleitores ou mais entram fatiadas por zona.\n"
        + md + (f"\n\nFalhas: {falhas}\n" if falhas else "\n"),
        encoding="utf-8")
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(md)
    if falhas:
        print(f"\n{len(falhas)} falha(s)")


if __name__ == "__main__":
    main()
