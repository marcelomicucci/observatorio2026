"""Avaliação ex ante do diário oficial de 2026 (1º turno).

Lê data/previsoes/previsoes-oficial2026.jsonl como foi gravado na noite,
sem recalcular nada. Referência final = último registro de cada disputa
(estado resultado_oficial), participação = votos / soma dos votos dos
concorrentes (mesma base do pct_concorrentes do diário).
"""
import json, collections, statistics as st, math, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "exports" / "artigo"
OUT.mkdir(parents=True, exist_ok=True)
ALVOS = (2, 5, 10, 17, 25, 35, 50, 65, 80, 92)
FAIXAS = ((0, 6), (6, 15), (15, 30), (30, 50), (50, 80), (80, 95), (95, 99), (99, 100))
NOME = {"1": "presidente", "3": "governador", "5": "senador"}
TZ = timezone(timedelta(hours=-3))

disp = collections.defaultdict(list)
with open(ROOT / "data/previsoes/previsoes-oficial2026.jsonl", encoding="utf8") as f:
    for line in f:
        r = json.loads(line)
        disp[(r["cargo"], r["unidade"])].append(r)
for k in disp:
    disp[k].sort(key=lambda r: r["momento"])

def local(m):
    return datetime.fromisoformat(m).astimezone(TZ)

linhas = []          # uma por (disputa, alvo)
todas = []           # todas as emissões em projecao_disponivel
por_disputa = {}
series = {}
for chave, regs in disp.items():
    cargo, uni = chave
    fim = regs[-1]
    ref_oficial = fim["estado_publicacao"] == "resultado_oficial"
    ref_cov = 100.0 * fim["eleitorado_apurado"] / fim["eleitorado_total"]
    assert ref_cov >= 99.99, chave
    vf = {c["sqcand"]: c["votos_observados"] for c in fim["candidatos"]}
    nomes = {c["sqcand"]: c["nome"] for c in fim["candidatos"]}
    tot = sum(vf.values())
    final = {k: 100.0 * v / tot for k, v in vf.items()}
    vagas = 2 if cargo == "5" else 1
    ordem = sorted(final, key=lambda k: -final[k])
    dentro = set(ordem[:vagas])
    maioria = vagas == 1 and final[ordem[0]] > 50.0
    relev = [k for k in ordem if final[k] >= 5.0]
    margem = (final[ordem[vagas - 1]] - final[ordem[vagas]]) if len(ordem) > vagas else None
    # alvos: primeiro registro que cruza cada alvo (um salto por vários alvos conta uma vez)
    alvos = list(ALVOS); sel = []
    emiss = []
    for r in regs:
        cov = 100.0 * r["eleitorado_apurado"] / max(1, r["eleitorado_total"])
        r["_cov"] = cov
        if r["estado_publicacao"] != "projecao_disponivel" or cov <= 0 or cov >= 99.99:
            continue
        emiss.append(r)
        while alvos and cov >= alvos[0]:
            a = alvos.pop(0)
            if not alvos or cov < alvos[0]:
                sel.append((a, r))
    def medir(r, alvo=None):
        q = {c["sqcand"]: c for c in r["candidatos"]}
        vo = {k: q[k]["votos_observados"] for k in q}
        to = sum(vo.values()) or 1
        cs = []
        for c in relev:
            if c not in q:
                continue
            x = q[c]
            cs.append({"sq": c, "f": final[c], "raw": 100.0 * vo[c] / to,
                       "q500": x["pct_concorrentes_q500"], "q100": x["pct_concorrentes_q100"], "q900": x["pct_concorrentes_q900"],
                       "q050": x["pct_concorrentes_q050"], "q950": x["pct_concorrentes_q950"],
                       "q025": x["pct_concorrentes_q025"], "q975": x["pct_concorrentes_q975"],
                       "p_vaga": x["prob_dentro_vagas"], "p_lid": x["prob_lideranca"], "p_maior": x["prob_maioria_absoluta"],
                       "eleito": c in dentro, "lider": c == ordem[0], "maioria": c == ordem[0] and maioria})
        op = sorted(q, key=lambda c: -q[c]["pct_concorrentes_q500"])
        raw_op = sorted(q, key=lambda c: -vo[c])
        errada = max([q[c]["prob_dentro_vagas"] for c in q if c not in dentro]
                     + ([q[c]["prob_maioria_absoluta"] for c in q if not (maioria and c == ordem[0])] if vagas == 1 else []), default=0.0)
        certa = min([q[c]["prob_dentro_vagas"] for c in dentro if c in q] or [0.0])
        return {"cargo": cargo, "unidade": uni, "alvo": alvo, "momento": r["momento"], "cov": r["_cov"], "cands": cs,
                "vagas_ok": set(op[:vagas]) == dentro, "contagem_ok": set(raw_op[:vagas]) == dentro,
                "p_errada_max": errada, "p_certa_min": certa, "n_sims": r["n_sims"]}
    for a, r in sel:
        linhas.append(medir(r, a))
    for r in emiss:
        todas.append(medir(r))
    # estabilização: primeiro instante em que todos os eleitos têm p_vaga >= 0.9 e isso não cai mais
    def estab(limiar):
        ok_desde = None
        for r in emiss:
            q = {c["sqcand"]: c["prob_dentro_vagas"] for c in r["candidatos"]}
            cond = all(q.get(c, 0) >= limiar for c in dentro)
            if cond and ok_desde is None:
                ok_desde = r
            elif not cond:
                ok_desde = None
        return ok_desde
    def estab_contagem():
        ok_desde = None
        for r in emiss:
            vo = {c["sqcand"]: c["votos_observados"] for c in r["candidatos"]}
            raw_op = sorted(vo, key=lambda c: -vo[c])
            cond = set(raw_op[:vagas]) == dentro
            if cond and ok_desde is None:
                ok_desde = r
            elif not cond:
                ok_desde = None
        return ok_desde
    e90, e99, ec = estab(0.9), estab(0.99), estab_contagem()
    # pior confiança dada a quem não entrou
    pior = max(((max([c["prob_dentro_vagas"] for c in r["candidatos"] if c["sqcand"] not in dentro] or [0]), r["_cov"]) for r in emiss), default=(0, 0))
    por_disputa[chave] = {
        "disputa": f"{NOME[cargo]}-{uni}", "cargo": NOME[cargo], "uf": uni, "vagas": vagas,
        "eleitos": [nomes[c] for c in ordem[:vagas]], "final_eleitos": [round(final[c], 2) for c in ordem[:vagas]],
        "margem_pp": round(margem, 2) if margem is not None else None, "maioria": maioria,
        "n_emissoes": len(emiss), "n_registros": len(regs), "primeira": emiss[0]["momento"] if emiss else None,
        "cov_primeira": round(emiss[0]["_cov"], 2) if emiss else None,
        "ultima_emissao": emiss[-1]["momento"] if emiss else None, "oficial_em": fim["momento"],
        "estab90_cov": round(e90["_cov"], 1) if e90 else None, "estab90_hora": local(e90["momento"]).strftime("%H:%M") if e90 else None,
        "estab99_cov": round(e99["_cov"], 1) if e99 else None, "estab99_hora": local(e99["momento"]).strftime("%H:%M") if e99 else None,
        "contagem_cov": round(ec["_cov"], 1) if ec else None,
        "pior_errada": round(pior[0], 3), "pior_errada_cov": round(pior[1], 1),
        "eleitorado": fim["eleitorado_total"], "ref_oficial": ref_oficial, "ref_cov": round(ref_cov, 4),
        "pendente_ref": fim["eleitorado_total"] - fim["eleitorado_apurado"],
    }
    # séries para figuras
    series[chave] = [{"t": r["momento"], "cov": r["_cov"],
                      "c": {c["sqcand"]: (c["pct_concorrentes_q500"], c["pct_concorrentes_q100"], c["pct_concorrentes_q900"], c["pct_concorrentes_q050"], c["pct_concorrentes_q950"], c["votos_observados"]) for c in r["candidatos"] if c["sqcand"] in relev}}
                     for r in regs]
    series[chave + ("final",)] = {c: (nomes[c], final[c]) for c in relev}

def iscore(l, u, y, alpha):
    return (u - l) + (2 / alpha) * (l - y) * (y < l) + (2 / alpha) * (y - u) * (y > u)

def tabela(ls, titulo):
    out = [f"\n{titulo}", f"{'faixa':>8} {'n':>4} {'cand':>5} {'MAE':>6} {'medAE':>6} {'RMSE':>6} {'vies':>6} {'MAEapur':>7} {'cob80':>6} {'cob90':>6} {'cob95':>6} {'larg80':>7} {'larg90':>7} {'larg95':>7} {'IS80':>6} {'IS90':>6} {'vagas':>6} {'cont':>6} {'>=99err':>8} {'>=90err':>8} {'99cert':>7} {'Brier':>6}"]
    for a, b in FAIXAS:
        sub = [x for x in ls if a <= x["cov"] < b and x["cands"]]
        if not sub:
            continue
        cs = [c for x in sub for c in x["cands"]]
        e = [c["q500"] - c["f"] for c in cs]
        out.append(f"{f'{a}-{b}%':>8} {len(sub):>4} {len(cs):>5} {st.mean(map(abs, e)):>6.2f} {st.median(map(abs, e)):>6.2f} {math.sqrt(st.mean(x*x for x in e)):>6.2f} {st.mean(e):>6.2f} "
                   f"{st.mean(abs(c['raw'] - c['f']) for c in cs):>7.2f} "
                   f"{100*st.mean(c['q100'] <= c['f'] <= c['q900'] for c in cs):>5.0f}% {100*st.mean(c['q050'] <= c['f'] <= c['q950'] for c in cs):>5.0f}% {100*st.mean(c['q025'] <= c['f'] <= c['q975'] for c in cs):>5.0f}% "
                   f"{st.mean(c['q900'] - c['q100'] for c in cs):>7.2f} {st.mean(c['q950'] - c['q050'] for c in cs):>7.2f} {st.mean(c['q975'] - c['q025'] for c in cs):>7.2f} "
                   f"{st.mean(iscore(c['q100'], c['q900'], c['f'], 0.2) for c in cs):>6.2f} {st.mean(iscore(c['q050'], c['q950'], c['f'], 0.1) for c in cs):>6.2f} "
                   f"{100*st.mean(x['vagas_ok'] for x in sub):>5.0f}% {100*st.mean(x['contagem_ok'] for x in sub):>5.0f}% "
                   f"{sum(x['p_errada_max'] >= .99 for x in sub):>8} {sum(x['p_errada_max'] >= .90 for x in sub):>8} {100*st.mean(x['p_certa_min'] >= .99 for x in sub):>6.0f}% "
                   f"{st.mean((c['p_vaga'] - c['eleito'])**2 for c in cs):>6.3f}")
    return out

rel = []
for titulo, filtro in (("TODAS AS DISPUTAS", lambda x: True), ("PRESIDENTE", lambda x: x["cargo"] == "1"),
                       ("GOVERNADOR", lambda x: x["cargo"] == "3"), ("SENADOR", lambda x: x["cargo"] == "5")):
    rel += tabela([x for x in linhas if filtro(x)], titulo + " — primeiro registro por alvo de apuração")
rel += tabela(todas, "TODAS AS DISPUTAS — todas as emissões (dependentes)")
# só o líder / eleitos
rel.append("\nSÓ OS ELEITOS (alvos)")
for a, b in FAIXAS:
    cs = [c for x in linhas if a <= x["cov"] < b for c in x["cands"] if c["eleito"]]
    if cs:
        e = [c["q500"] - c["f"] for c in cs]
        rel.append(f"{a}-{b}%: n={len(cs)} MAE={st.mean(map(abs,e)):.2f} vies={st.mean(e):+.2f} cob80={100*st.mean(c['q100']<=c['f']<=c['q900'] for c in cs):.0f}% cob90={100*st.mean(c['q050']<=c['f']<=c['q950'] for c in cs):.0f}%")
# calibração das probabilidades de vaga (alvos)
rel.append("\nCALIBRAÇÃO prob_dentro_vagas (alvos; todos os candidatos relevantes)")
bins = [(0, .05), (.05, .2), (.2, .4), (.4, .6), (.6, .8), (.8, .95), (.95, .99), (.99, 1.01)]
cs_all = [c for x in linhas for c in x["cands"]]
for lo, hi in bins:
    sub = [c for c in cs_all if lo <= c["p_vaga"] < hi]
    if sub:
        rel.append(f"  p in [{lo:.2f},{hi:.2f}): n={len(sub):4d} p_medio={st.mean(c['p_vaga'] for c in sub):.3f} freq_eleito={st.mean(c['eleito'] for c in sub):.3f}")
rel.append("\nCALIBRAÇÃO prob_maioria_absoluta (executivos, alvos)")
cs_ex = [c for x in linhas if x["cargo"] != "5" for c in x["cands"]]
for lo, hi in bins:
    sub = [c for c in cs_ex if lo <= c["p_maior"] < hi]
    if sub:
        rel.append(f"  p in [{lo:.2f},{hi:.2f}): n={len(sub):4d} p_medio={st.mean(c['p_maior'] for c in sub):.3f} freq_maioria={st.mean(c['maioria'] for c in sub):.3f}")
rel.append(f"Brier maioria (executivos, alvos): {st.mean((c['p_maior']-c['maioria'])**2 for c in cs_ex):.4f}; Brier vaga (todos, alvos): {st.mean((c['p_vaga']-c['eleito'])**2 for c in cs_all):.4f}; Brier liderança: {st.mean((c['p_lid']-c['lider'])**2 for c in cs_all):.4f}")
# n_sims
rel.append(f"\nn_sims nos alvos: {collections.Counter(x['n_sims'] for x in linhas)}")
rel.append(f"linhas-alvo: {len(linhas)}; emissões: {len(todas)}; candidatos relevantes (final>=5%): {len({(x['cargo'],x['unidade'],c['sq']) for x in linhas for c in x['cands']})}")

# por disputa
rel.append("\nPOR DISPUTA")
rel.append(f"{'disputa':16} {'eleitos':40} {'final':14} {'margem':>6} {'emis':>5} {'1ª cov':>6} {'estab90':>8} {'h90':>5} {'estab99':>8} {'h99':>5} {'contag':>7} {'pior err':>8} {'@cov':>5}")
for k in sorted(por_disputa, key=lambda k: (k[0], k[1])):
    d = por_disputa[k]
    rel.append(f"{d['disputa']:16} {', '.join(d['eleitos'])[:40]:40} {'/'.join(map(str, d['final_eleitos'])):14} {d['margem_pp'] if d['margem_pp'] is not None else '':>6} {d['n_emissoes']:>5} {d['cov_primeira']:>6} {str(d['estab90_cov']):>8} {str(d['estab90_hora']):>5} {str(d['estab99_cov']):>8} {str(d['estab99_hora']):>5} {str(d['contagem_cov']):>7} {d['pior_errada']:>8.2f} {d['pior_errada_cov']:>5}")

# resumo de estabilização
for cargo in ("governador", "senador"):
    ds = [d for d in por_disputa.values() if d["cargo"] == cargo]
    e90 = [d["estab90_cov"] for d in ds if d["estab90_cov"] is not None]
    e99 = [d["estab99_cov"] for d in ds if d["estab99_cov"] is not None]
    ec = [d["contagem_cov"] for d in ds if d["contagem_cov"] is not None]
    rel.append(f"\n{cargo}: estab90 n={len(e90)} mediana={st.median(e90):.1f}% p90={sorted(e90)[int(0.9*len(e90))-1]:.1f}%; estab99 n={len(e99)} mediana={st.median(e99):.1f}% p90={sorted(e99)[int(0.9*len(e99))-1]:.1f}%; contagem n={len(ec)} mediana={st.median(ec):.1f}% p90={sorted(ec)[int(0.9*len(ec))-1]:.1f}%; pior errada>=0.9: {sum(d['pior_errada']>=.9 for d in ds)}/{len(ds)}; >=0.99: {sum(d['pior_errada']>=.99 for d in ds)}/{len(ds)}; margem<2pp: {sum((d['margem_pp'] or 99)<2 for d in ds)}")

# horários
pres = por_disputa[("1", "br")]
rel.append(f"\nPresidente: {pres}")
rel.append(f"primeira emissão geral: {min(d['primeira'] for d in por_disputa.values())} ; última oficial: {max(d['oficial_em'] for d in por_disputa.values())}")
rel.append(f"UFs oficializadas até 04/10 23:59 local: {sum(local(d['oficial_em']) < datetime(2026,10,5,0,0,tzinfo=TZ) for d in por_disputa.values())} de 55")
# cadência: intervalo entre emissões por disputa
gaps = []
for chave, regs in disp.items():
    ts = [datetime.fromisoformat(r["momento"]) for r in regs if r["estado_publicacao"] == "projecao_disponivel"]
    gaps += [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
rel.append(f"intervalo entre emissões (s): mediana={st.median(gaps):.0f} p90={sorted(gaps)[int(.9*len(gaps))]:.0f} max={max(gaps):.0f} n={len(gaps)}; >600s: {sum(g>600 for g in gaps)}")

open(OUT / "exante2026.txt", "w", encoding="utf8").write("\n".join(rel))
json.dump({"linhas": linhas, "por_disputa": {"-".join(k): v for k, v in por_disputa.items()}}, open(OUT / "exante2026.json", "w", encoding="utf8"), ensure_ascii=False)
json.dump({("-".join(k)): v for k, v in series.items()}, open(OUT / "series2026.json", "w", encoding="utf8"), ensure_ascii=False)
print("\n".join(rel))
