"""Ensaio das projeções sobre a noite REAL do 1º turno de 2026.

    python scripts/ensaio2026.py                       # as 55 disputas
    python scripts/ensaio2026.py --so presidente
    python scripts/ensaio2026.py --variantes base,k0.65,k0.5 --processos 8

Diferente de `ensaio2022.py`, aqui nada é reconstruído: o arquivo bruto de
`oficial2026` guarda cada retrato de cada município como o TSE o publicou
durante a apuração. O ensaio volta o relógio para alguns instantes da noite,
monta a disputa com o último arquivo de cada município **até aquele
instante** — cidade grande parcial incluída, do jeito que veio — e roda o
modelo. É a mesma entrada que o coletor teve, então a variante `base` tem de
reproduzir o que o diário registrou.

Serve para responder à decisão de 05/10/2026 de estreitar o intervalo: na
noite real a faixa de 80% conteve o resultado em 98–100% dos casos entre 6%
e 95% apurado. Cada variante multiplica os choques de preferência do
simulador (nacional, UF, local e de grupo) por um fator `k`:

    base      o M3 de produção                      (k = 1)
    k0.8 …    os mesmos choques × k
    inc10k    K_INCLINACAO = 10 mil (correção pela inclinação entra mais
              cedo) — a pista do presidente sub-corrigido
    inc10k+k0.65   as duas coisas

O que se mede por variante e faixa de apuração: erro mediano, cobertura das
faixas de 80% e 90%, largura, acerto das vagas e — a trava de segurança —
quantas vezes uma chance de 99% ou mais foi dada a quem não confirmou.
Estreitar a faixa aumenta a confiança; se isso aparecer, o fator é
agressivo demais.

Limitação que o relatório repete: é UMA eleição. O fator escolhido aqui só
vira modelo depois de conferido em 2018 e 2022 (`ensaio2022_lote.py`) e do
gate.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR                       # noqa: E402
from obs2026.engine import models as M                    # noqa: E402
from obs2026.engine import simulate as S                  # noqa: E402
from obs2026.engine import territorio                     # noqa: E402
from obs2026.storage.raw import RawArchive                # noqa: E402
from obs2026.tse.parsers import ea20 as ea20_parser       # noqa: E402

ENV = "oficial2026"
SAIDA = DATA_DIR / "exports" / "ensaio2026"
CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}
NOME = {v: k for k, v in CARGOS.items()}
#: Apurado (%) em que cada disputa é fotografada.
ALVOS = (2, 5, 10, 17, 25, 35, 50, 65, 80, 92)
FAIXAS = ((0, 6), (6, 15), (15, 30), (30, 50), (50, 80), (80, 95))
N_SIMS = 1500
SEMENTE = 20261004

#: nome → (fator dos choques de preferência, K_INCLINACAO ou None)
VARIANTES = {
    "base": (1.0, None), "k0.8": (0.8, None), "k0.65": (0.65, None),
    "k0.5": (0.5, None), "k0.4": (0.4, None),
    "inc10k": (1.0, 10_000.0), "inc10k+k0.65": (0.65, 10_000.0),
}


def indexar(archive: RawArchive) -> dict:
    """{(cargo, uf|'br'): {(uf, mun): [(captured_at, ref), …]}}."""
    idx: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for dia in archive.days():
        for ref in archive.manifest(dia):
            if (ref.kind != "ea20" or not ref.path or not ref.mun
                    or ref.cargo not in NOME):
                continue
            chave = (ref.cargo, "br" if ref.cargo == "1" else ref.uf)
            idx[chave][(ref.uf or "", ref.mun)].append(
                (ref.captured_at, ref))
    for d in idx.values():
        for lst in d.values():
            lst.sort(key=lambda x: x[0])
    return idx


def instantes(chave: tuple[str, str]) -> list[str]:
    """Do diário: o momento em que a disputa cruzou cada alvo de apuração."""
    out, alvos = [], list(ALVOS)
    with open(DATA_DIR / "previsoes" / f"previsoes-{ENV}.jsonl",
              encoding="utf-8") as fh:
        regs = []
        for linha in fh:
            if f'"cargo": "{chave[0]}"' not in linha:
                continue
            r = json.loads(linha)
            if r["cargo"] == chave[0] and r["unidade"] == chave[1]:
                regs.append((r["momento"], 100.0 * r["eleitorado_apurado"]
                             / max(1, r["eleitorado_total"])))
    regs.sort()
    for momento, cov in regs:
        while alvos and cov >= alvos[0]:
            alvo = alvos.pop(0)
            # Um salto grande passa por vários alvos de uma vez: fica um só.
            if not alvos or cov < alvos[0]:
                out.append(momento)
    return out


def _disputa(chave, munis, momentos, variantes):
    """Roda uma disputa em todos os instantes e variantes (processo filho)."""
    cargo, unidade = chave
    archive = RawArchive(DATA_DIR / "raw", ENV)
    memo: dict[str, object] = {}

    def observar(ate: str | None):
        obs = []
        for (uf, _mun), lst in munis.items():
            if ate is None:
                ref = lst[-1][1]
            else:
                i = bisect.bisect_right([c for c, _ in lst], ate)
                if i == 0:
                    continue
                ref = lst[i - 1][1]
            r = memo.get(ref.path)
            if r is None:
                try:
                    r = memo[ref.path] = ea20_parser.parse(archive.load(ref))
                except Exception:                     # noqa: BLE001
                    continue
            obs.append(M.UnitObservation(
                unit=f"{uf}:{r.cdabr}", uf=uf,
                stratum=M.estrato(uf, r.electorate.te), result=r,
                capital=territorio.e_capital(uf, r.cdabr)))
        return obs

    def validos(obs):
        v: dict[str, int] = collections.defaultdict(int)
        for o in obs:
            for c in o.result.cargo.candidatos:
                if c.conta_como_valido or c.dvt == ea20_parser.DVT_ANULADO_SJ:
                    v[c.sqcand] += c.votos
        return v

    fim = observar(None)
    vf = validos(fim)
    tot = sum(vf.values()) or 1
    final = {k: 100.0 * v / tot for k, v in vf.items()}
    vagas = fim[0].result.cargo.vagas
    ordem = sorted(final, key=lambda k: -final[k])
    dentro = set(ordem[:vagas])
    maioria = vagas == 1 and final[ordem[0]] > 50.0
    relev = [k for k in ordem if final[k] >= 5.0]

    linhas = []
    for ate in momentos:
        obs = observar(ate)
        if not obs:
            continue
        te = sum(o.result.electorate.te for o in obs)
        esa = sum(o.result.electorate.esa for o in obs)
        cov = 100.0 * esa / te if te else 0.0
        if cov <= 0 or cov >= 99.99:
            continue
        cands = sorted(validos(obs))
        if not cands:
            continue
        vo = validos(obs)
        to = sum(vo.values()) or 1
        for nome in variantes:
            k, kinc = VARIANTES[nome]
            kw = {"sigma_grupo_vazio": M.SIGMA_GRUPO_VAZIO * k}
            if kinc:
                kw["k_inclinacao"] = kinc
            units = M.build_m3(obs, cands, seats=vagas, **kw)
            p = S.SimConfig(n_sims=N_SIMS, seed=SEMENTE)
            cfg = replace(p, sigma_pref_nacional=p.sigma_pref_nacional * k,
                          sigma_pref_uf=p.sigma_pref_uf * k,
                          sigma_pref_local=p.sigma_pref_local * k)
            fc = S.run(units, cands, cfg, seats=vagas, senado=vagas > 1)
            q = fc.quantis((0.025, 0.05, 0.10, 0.50, 0.90, 0.95, 0.975))
            pv = fc.prob_dentro_das_vagas()
            pm = fc.prob_maioria_absoluta()
            cs = []
            for c in relev:
                if c not in q:
                    continue
                x = q[c]["pct_concorrentes"]
                cs.append({"f": final[c], "raw": 100.0 * vo.get(c, 0) / to,
                           **{kk: x[kk] for kk in ("q500", "q100", "q900",
                                                    "q050", "q950")}})
            op = sorted(cands, key=lambda c: -q[c]["pct_concorrentes"]["q500"])
            # A pior confiança errada: 99%+ a quem não confirmou.
            errada = max(
                [pv.get(c, 0.0) for c in cands if c not in dentro]
                + ([pm.get(c, 0.0) for c in cands
                    if not (maioria and c == ordem[0])] if vagas == 1 else []),
                default=0.0)
            certa = min([pv.get(c, 0.0) for c in dentro] or [0.0])
            linhas.append({
                "cargo": cargo, "unidade": unidade, "variante": nome,
                "momento": ate, "cov": cov, "cands": cs,
                "vagas_ok": set(op[:vagas]) == dentro,
                "p_errada_max": errada, "p_certa_min": certa})
    return chave, linhas


def resumir(linhas: list[dict], variantes: list[str]) -> str:
    out = []
    for titulo, filtro in (
            ("TODAS AS DISPUTAS", lambda x: True),
            ("PRESIDENTE", lambda x: x["cargo"] == "1"),
            ("GOVERNADOR", lambda x: x["cargo"] == "3"),
            ("SENADOR", lambda x: x["cargo"] == "5")):
        out.append(f"\n{titulo}")
        out.append(f"{'variante':14} {'apurado':>8} {'n':>4} {'erro':>6} "
                   f"{'apur':>6} {'cob80':>6} {'cob90':>6} {'larg90':>7} "
                   f"{'vagas':>6} {'99% errado':>10} {'99% certo':>10}")
        for v in variantes:
            for a, b in FAIXAS:
                ls = [x for x in linhas if x["variante"] == v and filtro(x)
                      and a <= x["cov"] < b and x["cands"]]
                if not ls:
                    continue
                cs = [c for x in ls for c in x["cands"]]
                out.append(
                    f"{v:14} {f'{a}-{b}%':>8} {len(ls):>4} "
                    f"{st.median(abs(c['q500'] - c['f']) for c in cs):>6.2f} "
                    f"{st.median(abs(c['raw'] - c['f']) for c in cs):>6.2f} "
                    f"{100 * st.mean(c['q100'] <= c['f'] <= c['q900'] for c in cs):>5.0f}% "
                    f"{100 * st.mean(c['q050'] <= c['f'] <= c['q950'] for c in cs):>5.0f}% "
                    f"{st.mean(c['q950'] - c['q050'] for c in cs):>7.2f} "
                    f"{100 * st.mean(x['vagas_ok'] for x in ls):>5.0f}% "
                    f"{sum(x['p_errada_max'] >= .99 for x in ls):>10} "
                    f"{100 * st.mean(x['p_certa_min'] >= .99 for x in ls):>9.0f}%")
            out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", choices=sorted(CARGOS), action="append")
    ap.add_argument("--uf", action="append")
    ap.add_argument("--variantes", default=",".join(VARIANTES))
    ap.add_argument("--processos", type=int, default=8)
    ap.add_argument("--nome", default="ensaio2026")
    a = ap.parse_args()
    variantes = [v for v in a.variantes.split(",") if v]
    for v in variantes:
        if v not in VARIANTES:
            sys.exit(f"variante desconhecida: {v} (use {list(VARIANTES)})")

    t0 = time.time()
    archive = RawArchive(DATA_DIR / "raw", ENV)
    idx = indexar(archive)
    cargos = {CARGOS[c] for c in (a.so or CARGOS)}
    chaves = sorted(k for k in idx if k[0] in cargos
                    and (not a.uf or k[1] in a.uf or k[1] == "br"))
    print(f"índice: {len(idx)} disputas em {time.time() - t0:.0f}s; "
          f"rodando {len(chaves)} × {len(variantes)} variantes", flush=True)

    linhas: list[dict] = []
    with ProcessPoolExecutor(max_workers=a.processos) as ex:
        fut = {ex.submit(_disputa, k, dict(idx[k]), instantes(k), variantes):
               k for k in chaves}
        for n, f in enumerate(as_completed(fut), 1):
            k, ls = f.result()
            linhas.extend(ls)
            print(f"  [{n}/{len(chaves)}] {NOME[k[0]]} {k[1]}: "
                  f"{len(ls)} rodadas ({time.time() - t0:.0f}s)", flush=True)

    SAIDA.mkdir(parents=True, exist_ok=True)
    (SAIDA / f"{a.nome}.json").write_text(
        json.dumps(linhas, ensure_ascii=False), encoding="utf-8")
    texto = resumir(linhas, variantes)
    (SAIDA / f"{a.nome}.txt").write_text(texto, encoding="utf-8")
    print(texto)
    print(f"\ngravado em {SAIDA / (a.nome + '.json')}  "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
