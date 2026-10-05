"""Gate estatístico do §11.2 sobre a eleição real de 2022: cada modelo contra o M0.

O `thresholds.yaml` só pode ser homologado se o modelo de produção:

1. vencer o M0 (referência ingênua) no **erro mediano**, em toda rodada da
   apuração — a mesma regra de `backtest.gate_m1_vence_m0`: nenhuma derrota;
2. entregar intervalo de 80% com cobertura dentro de
   `interval_coverage_tolerance_pp` do nominal.

Método: o do ensaio de 21/09 (`ensaio2022.py`) — municípios em rodadas na
ordem real de chegada de 2022 e em ordem aleatória, cidades grandes
fatiadas por zona, cada modelo sobre o mesmo retrato. As 55 disputas de
2022: presidente, 27 governadores, 27 senadores. A última rodada (100%) fica
fora, como no ensaio: com tudo apurado não há o que projetar.

Uso:
    python scripts/gate2022.py [--processos 8] [--sims 1500]

Grava `data/exports/ensaio2022/gate2022.{json,md}`.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ensaio2022 as E                                    # noqa: E402
from obs2026.config import load_thresholds                # noqa: E402
from obs2026.engine.registro_continuo import UFS          # noqa: E402

MODELOS = ("m0", "m2", "m3")      # padrão; --modelos troca


def uma(cargo_nome: str, uf: str | None, rodadas: int, sims: int,
        semente: int, modelos: tuple = MODELOS) -> dict:
    cargo = E.CARGOS[cargo_nome]
    disputa = f"2022-{cargo_nome}-{uf or 'br'}"
    horas = E.carregar_ordem()
    muns, vagas, eleicao = E.carregar_historico(2022, cargo, uf)
    thr = load_thresholds()
    out = {"disputa": disputa, "cargo": cargo_nome, "ordens": {}}
    for ordem in ("real", "aleatoria"):
        res = E.rodar(muns, vagas, eleicao, horas, ordem=ordem,
                      rodadas=rodadas, sims=sims, seed=semente, cargo=cargo,
                      disputa=disputa, thr=thr, modelos=modelos,
                      registrar=False)
        out["ordens"][ordem] = {
            m: [{"erro": l["erro_pp_mediano"],
                 "cob80": l["cobertura_80_todos"],
                 "errada": l["confianca_errada"],
                 "certo": l["lider_projetado_certo"]}
                for l in res["modelos"][m][:-1]]
            for m in modelos}
    return out


def consolidar(disputas: list[dict], tol_pp: float, m: str = "m2") -> dict:
    """Gate de um modelo `m` contra o M0 (o nome dos campos segue `m2` por
    compatibilidade com o relatório e a homologação)."""
    por_rodada = []
    derrotas = []
    for ordem in ("real", "aleatoria"):
        n_rod = min(len(d["ordens"][ordem][m]) for d in disputas)
        for r in range(n_rod):
            med = {k: st.median(d["ordens"][ordem][k][r]["erro"]
                                for d in disputas) for k in ("m0", m)}
            linha = {"ordem": ordem, "rodada": r + 1,
                     "erro_m0": round(med["m0"], 3),
                     "erro_m2": round(med[m], 3),
                     "m2_melhor": med[m] < med["m0"]}
            por_rodada.append(linha)
            if med[m] >= med["m0"]:
                derrotas.append(linha)
    cob = {}
    for ordem in ("real", "aleatoria"):
        vals = [x["cob80"] for d in disputas
                for x in d["ordens"][ordem][m]]
        cob[ordem] = 100 * sum(vals) / len(vals)
    errada = {ordem: sum(1 for d in disputas if max(
        x["errada"] for x in d["ordens"][ordem][m]) >= 0.9)
        for ordem in ("real", "aleatoria")}
    passa_erro = not derrotas
    passa_cob = all(abs(c - 80.0) <= tol_pp for c in cob.values())
    return {
        "modelo": m,
        "disputas": len(disputas),
        "por_rodada": por_rodada,
        "derrotas_m2": derrotas,
        "cobertura80_m2_pct": {k: round(v, 1) for k, v in cob.items()},
        "tolerancia_pp": tol_pp,
        "disputas_com_90pct_num_perdedor_m2": errada,
        "passa_erro_mediano": passa_erro,
        "passa_cobertura": passa_cob,
        "passou": passa_erro and passa_cob,
    }


def markdown(c: dict, t_s: float) -> str:
    M = c.get("modelo", "m2").upper()
    L = [f"## {M} contra M0", "",
         f"{c['disputas']} disputas (presidente, governadores, senadores), "
         f"ordem real de chegada e aleatória; rodadas 1 a 9 de 10. "
         f"Rodado em {datetime.now():%d/%m/%Y %H:%M} ({t_s / 60:.0f} min).",
         "",
         f"**Resultado: {'PASSOU' if c['passou'] else 'NÃO PASSOU'}** — "
         f"erro mediano: {(M + ' vence o M0 em todas as rodadas') if c['passa_erro_mediano'] else str(len(c['derrotas_m2'])) + ' rodada(s) em que o ' + M + ' não vence'}; "
         f"cobertura do intervalo de 80%: real "
         f"{c['cobertura80_m2_pct']['real']}%, aleatória "
         f"{c['cobertura80_m2_pct']['aleatoria']}% (tolerância "
         f"±{c['tolerancia_pp']:.0f} pp).", "",
         f"| ordem | rodada | erro mediano M0 (pp) | erro mediano {M} (pp) | {M} melhor |",
         "|---|---|---|---|---|"]
    for r in c["por_rodada"]:
        L.append(f"| {r['ordem']} | {r['rodada']} | {r['erro_m0']:.2f} | "
                 f"{r['erro_m2']:.2f} | {'sim' if r['m2_melhor'] else 'NÃO'} |")
    L += ["", f"Disputas em que o {M} chegou a dar 90% ou mais a um perdedor: "
          f"real {c['disputas_com_90pct_num_perdedor_m2']['real']}, aleatória "
          f"{c['disputas_com_90pct_num_perdedor_m2']['aleatoria']}."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processos", type=int, default=8)
    ap.add_argument("--sims", type=int, default=1500)
    ap.add_argument("--rodadas", type=int, default=10)
    ap.add_argument("--semente", type=int, default=7)
    ap.add_argument("--modelos", default=",".join(MODELOS),
                    help="sempre inclui m0 (a referência)")
    ap.add_argument("--so-uf", action="append",
                    help="só estas UFs (teste rápido); sem presidente")
    ap.add_argument("--nome", default=None,
                    help="nome da saída. Rodada exploratória (variantes de "
                         "largura, por exemplo) NÃO pode gravar em "
                         "`gate2022`: é a evidência que a homologação lê.")
    a = ap.parse_args()
    t0 = time.time()
    modelos = tuple(dict.fromkeys(["m0"] + [m.strip() for m in
                                            a.modelos.split(",")]))
    tarefas = []
    if not a.so_uf:
        tarefas.append(("presidente", None))
    for uf in (a.so_uf or UFS):
        tarefas += [("governador", uf), ("senador", uf)]
    disputas = []
    with ProcessPoolExecutor(max_workers=a.processos) as ex:
        fut = {ex.submit(uma, c, uf, a.rodadas, a.sims, a.semente,
                         modelos): (c, uf)
               for c, uf in tarefas}
        for i, f in enumerate(as_completed(fut), 1):
            disputas.append(f.result())
            print(f"  {i}/{len(tarefas)} {disputas[-1]['disputa']}",
                  flush=True)
    tol = float(load_thresholds().gates.get(
        "interval_coverage_tolerance_pp", 5.0))
    por_modelo = {m: consolidar(disputas, tol, m) for m in modelos
                  if m != "m0"}
    params = {"sims": a.sims, "rodadas": a.rodadas, "semente": a.semente,
              "modelos": list(modelos), "parcial": bool(a.so_uf)}
    agora = datetime.now().isoformat()
    for c in por_modelo.values():
        c["gerado_em"], c["parametros"] = agora, params
    # Topo do arquivo = M2 (compatível com a leitura antiga); cada modelo
    # tem o seu em `por_modelo`, e a homologação lê o do modelo em produção.
    topo = por_modelo.get("m2") or next(iter(por_modelo.values()))
    nome = a.nome or ("gate2022" + ("-teste" if a.so_uf else ""))
    E.SAIDA.mkdir(parents=True, exist_ok=True)
    (E.SAIDA / f"{nome}.json").write_text(json.dumps(
        {**topo, "por_modelo": por_modelo, "detalhe": disputas},
        ensure_ascii=False, indent=1), encoding="utf-8")
    txt = ("# Gate estatístico — eleição de 2022\n\n"
           + "\n".join(markdown(c, time.time() - t0)
                       for c in por_modelo.values()))
    (E.SAIDA / f"{nome}.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
