import json, statistics as st, math, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

S = Path(__file__).resolve().parents[2] / "data" / "exports" / "artigo"
R = S.parent / "ensaio2022"
TZ = timezone(timedelta(hours=-3))
C = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#eda100"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({"font.size": 7.5, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "legend.frameon": False, "figure.dpi": 220, "savefig.dpi": 220})
W = 1584 / 220

series = json.load(open(S / "series2026.json", encoding="utf8"))
ex = json.load(open(S / "exante2026.json", encoding="utf8"))
FAIXAS = ((0, 6), (6, 15), (15, 30), (30, 50), (50, 80), (80, 95))
ROT = ["0–6", "6–15", "15–30", "30–50", "50–80", "80–95"]

# ---------- Figura 3: trajetórias ex ante ----------
casos = [("1-br", "Presidente (Brasil)", 2), ("3-rn", "Governador — RN", 2), ("5-pa", "Senador — PA (2 vagas)", 3), ("5-rn", "Senador — RN (2 vagas)", 3)]
fig, axs = plt.subplots(2, 2, figsize=(W, 5.6))
for ax, (k, titulo, ncand) in zip(axs.flat, casos):
    fin = series[k + "-final"]
    ordem = sorted(fin, key=lambda c: -fin[c][1])[:ncand]
    regs = [r for r in series[k] if 0 < r["cov"] < 99.99]
    t = [datetime.fromisoformat(r["t"]).astimezone(TZ) for r in regs]
    for i, c in enumerate(ordem):
        q500 = [r["c"][c][0] for r in regs]; q100 = [r["c"][c][1] for r in regs]; q900 = [r["c"][c][2] for r in regs]
        q050 = [r["c"][c][3] for r in regs]; q950 = [r["c"][c][4] for r in regs]
        ax.fill_between(t, q050, q950, color=C[i], alpha=0.12, linewidth=0)
        ax.fill_between(t, q100, q900, color=C[i], alpha=0.25, linewidth=0)
        ax.plot(t, q500, color=C[i], linewidth=1.4, label=fin[c][0].title())
        ax.axhline(fin[c][1], color=C[i], linewidth=0.8, linestyle=(0, (3, 2)))
    # marcos de apuração
    for alvo in (10, 50, 90):
        for r, tt in zip(regs, t):
            if r["cov"] >= alvo:
                ax.axvline(tt, color=INK2, linewidth=0.5, alpha=0.5)
                ax.text(tt, ax.get_ylim()[1], f"{alvo}% apurado", fontsize=6, color=INK2, rotation=90, va="top", ha="right")
                break
    ax.set_title(titulo, fontsize=8.5, loc="left", color=INK)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh", tz=TZ))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=TZ))
    ax.set_ylabel("% dos votos a concorrentes")
    ax.legend(fontsize=6.5, loc="lower right")
fig.text(0.01, 0.005, "Linha: mediana projetada; faixas: 80% (escura) e 90% (clara); tracejado: resultado de referência. Modelo M3 (m3-0.1), B = 1.500, diário de 04/10/2026. Horário de Brasília.", fontsize=6, color=INK2)
fig.tight_layout(rect=(0, 0.02, 1, 1))
fig.savefig(S / "fig3_exante.png"); plt.close(fig)

# ---------- Figura 4: cobertura e largura por estágio ----------
linhas = ex["linhas"]
cob = {80: [], 90: [], 95: []}; larg90 = []
for a, b in FAIXAS:
    cs = [c for x in linhas if a <= x["cov"] < b for c in x["cands"]]
    cob[80].append(100 * st.mean(c["q100"] <= c["f"] <= c["q900"] for c in cs))
    cob[90].append(100 * st.mean(c["q050"] <= c["f"] <= c["q950"] for c in cs))
    cob[95].append(100 * st.mean(c["q025"] <= c["f"] <= c["q975"] for c in cs))
    larg90.append(st.mean(c["q950"] - c["q050"] for c in cs))
replay = {"M3 (replay, k = 1)": [12.39, 11.32, 9.38, 6.99, 3.88, 0.98], "k = 0,8 (M3.3)": [10.00, 9.13, 7.55, 5.62, 3.12, 0.78], "k = 0,65": [8.16, 7.45, 6.16, 4.58, 2.54, 0.64], "k = 0,5": [6.30, 5.75, 4.75, 3.53, 1.96, 0.49]}
replay_cob80 = {"M3 (replay, k = 1)": [96, 98, 100, 100, 100, 95], "k = 0,8 (M3.3)": [95, 98, 100, 100, 99, 92], "k = 0,65": [90, 97, 98, 99, 98, 89], "k = 0,5": [83, 93, 96, 98, 92, 81]}
fig, (a1, a2) = plt.subplots(1, 2, figsize=(W, 3.2))
x = range(len(ROT))
for i, (nv, ls) in enumerate(((80, cob[80]), (90, cob[90]), (95, cob[95]))):
    a1.plot(x, ls, marker="o", markersize=4, color=C[i], linewidth=1.6, label=f"intervalo de {nv}% (diário ex ante)")
    a1.axhline(nv, color=C[i], linewidth=0.7, linestyle=(0, (3, 2)))
a1.plot(x, replay_cob80["k = 0,8 (M3.3)"], marker="s", markersize=3.5, color=C[3], linewidth=1.2, label="intervalo de 80%, replay k = 0,8 (M3.3)")
a1.set_ylim(60, 101); a1.set_xticks(list(x)); a1.set_xticklabels(ROT); a1.set_xlabel("eleitorado apurado (%)"); a1.set_ylabel("cobertura empírica (%)")
a1.set_title("A. Cobertura por estágio (tracejado = nominal)", fontsize=8.5, loc="left"); a1.legend(fontsize=6)
a2.plot(x, larg90, marker="o", markersize=4, color=C[0], linewidth=1.8, label="diário ex ante (M3)")
for i, (nome, ls) in enumerate(replay.items()):
    a2.plot(x, ls, marker="s", markersize=3, color=[C[1], C[3], C[2], C[4]][i], linewidth=1.1, linestyle="-" if i else (0, (2, 2)), label=nome)
a2.set_xticks(list(x)); a2.set_xticklabels(ROT); a2.set_xlabel("eleitorado apurado (%)"); a2.set_ylabel("largura média do intervalo de 90% (pp)")
a2.set_title("B. Largura por estágio: diário e replay por fator k", fontsize=8.5, loc="left"); a2.legend(fontsize=6)
fig.tight_layout(); fig.savefig(S / "fig4_cobertura.png"); plt.close(fig)

# ---------- Figura 5: calibração ----------
bins = [(0, .05), (.05, .2), (.2, .4), (.4, .6), (.6, .8), (.8, .95), (.95, .99), (.99, 1.01)]
cs_all = [c for x in linhas for c in x["cands"]]
cs_ex = [c for x in linhas if x["cargo"] != "5" for c in x["cands"]]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(W, 3.0))
for ax, cs, pk, ek, titulo in ((a1, cs_all, "p_vaga", "eleito", "A. Probabilidade de ocupar vaga (55 disputas)"), (a2, cs_ex, "p_maior", "maioria", "B. Maioria absoluta (28 disputas executivas)")):
    ax.plot([0, 1], [0, 1], color=INK2, linewidth=0.7, linestyle=(0, (3, 2)))
    xs, ys, ns = [], [], []
    for lo, hi in bins:
        sub = [c for c in cs if lo <= c[pk] < hi]
        if sub:
            xs.append(st.mean(c[pk] for c in sub)); ys.append(st.mean(c[ek] for c in sub)); ns.append(len(sub))
    ax.plot(xs, ys, color=C[0], linewidth=1.2)
    ax.scatter(xs, ys, s=[8 + 60 * math.sqrt(n / max(ns)) for n in ns], color=C[0], edgecolor="white", linewidth=0.8, zorder=3)
    for xx, yy, n in zip(xs, ys, ns):
        if xx < 0.9:
            ax.annotate(f"n={n}", (xx, yy), textcoords="offset points", xytext=(6, -9), fontsize=5.5, color=INK2)
    ax.annotate(f"n={sum(n for xx, n in zip(xs, ns) if xx >= 0.9)} (p ≥ 0,8)", (xs[-1], ys[-1]), textcoords="offset points", xytext=(-4, -10), fontsize=5.5, color=INK2, ha="right")
    ax.set_xlabel("probabilidade média emitida"); ax.set_ylabel("frequência observada"); ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.set_title(titulo, fontsize=8.5, loc="left")
fig.text(0.01, 0.005, "Primeiro registro de cada disputa em cada alvo de apuração (2, 5, 10, 17, 25, 35, 50, 65, 80, 92%); candidatos com 5% ou mais no resultado final.", fontsize=6, color=INK2)
fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(S / "fig5_calibracao.png"); plt.close(fig)

# ---------- Figura 6: curvas de apuração ----------
fig, ax = plt.subplots(figsize=(W, 3.1))
ufs = sorted(k for k in series if k.startswith("3-") and not k.endswith("final"))
h95 = {}
for k in ufs:
    regs = series[k]
    t = [datetime.fromisoformat(r["t"]).astimezone(TZ) for r in regs]; cov = [r["cov"] for r in regs]
    ax.plot(t, cov, color=INK2, linewidth=0.5, alpha=0.45)
    for tt, cc in zip(t, cov):
        if cc >= 95:
            h95[k[2:].upper()] = tt; break
regs = series["1-br"]; t = [datetime.fromisoformat(r["t"]).astimezone(TZ) for r in regs]; cov = [r["cov"] for r in regs]
ax.plot(t, cov, color=C[0], linewidth=2, label="Brasil (presidente)")
cedo = sorted(h95, key=h95.get)[:2]; tarde = sorted(h95, key=h95.get)[-3:]
for i, uf in enumerate(cedo + tarde):
    ax.annotate(f"{uf} {h95[uf]:%H:%M}", (h95[uf], 95), textcoords="offset points", xytext=(3, -10) if uf in cedo else (4 + 14 * (i - 2), 4 + 9 * (i - 2)), fontsize=6, color=INK)
ax.axhline(95, color=INK2, linewidth=0.5, linestyle=(0, (3, 2)))
ax.set_xlim(datetime(2026, 10, 4, 17, 0, tzinfo=TZ), datetime(2026, 10, 5, 1, 0, tzinfo=TZ))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh", tz=TZ)); ax.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=TZ))
ax.set_ylabel("eleitorado apurado (%)"); ax.set_xlabel("horário de Brasília, 4–5 de outubro de 2026")
ax.set_title("Eleitorado apurado: Brasil (presidente) e 27 UFs (governador, cinza); rótulos = UFs que cruzaram 95% mais cedo e mais tarde", fontsize=8, loc="left")
ax.legend(fontsize=6.5, loc="lower right")
fig.tight_layout(); fig.savefig(S / "fig6_apuracao.png"); plt.close(fig)
print("h95:", {k: v.strftime("%H:%M") for k, v in sorted(h95.items(), key=lambda kv: kv[1])})

# ---------- Figura 7: gate 2022 por rodada ----------
g33 = json.load(open(R / "gate2022-m33.json", encoding="utf8"))["por_modelo"]
g22 = json.load(open(R / "gate2022.json", encoding="utf8"))["por_modelo"]
gm1 = json.load(open(R / "gate2022-m1.json", encoding="utf8"))["por_modelo"] if (R / "gate2022-m1.json").exists() else None
fig, axs = plt.subplots(1, 2, figsize=(W, 3.0), sharey=True)
for ax, ordem in zip(axs, ("real", "aleatoria")):
    rows = [r for r in g33["m3"]["por_rodada"] if r["ordem"] == ordem]
    x = [r["rodada"] for r in rows]
    ax.plot(x, [r["erro_m0"] for r in rows], marker="o", markersize=3.5, color=INK2, linewidth=1.6, label="M0 (extrapolação)")
    if gm1:
        rows1 = [r for r in gm1["m1"]["por_rodada"] if r["ordem"] == ordem]
        ax.plot(x, [r["erro_m2"] for r in rows1], marker="o", markersize=3.5, color=C[4], linewidth=1.3, label="M1 (estratos)")
    rows2 = [r for r in g22["m2"]["por_rodada"] if r["ordem"] == ordem]
    ax.plot(x, [r["erro_m2"] for r in rows2], marker="o", markersize=3.5, color=C[1], linewidth=1.3, label="M2 (hierarquia)")
    ax.plot(x, [r["erro_m2"] for r in rows], marker="o", markersize=3.5, color=C[0], linewidth=1.8, label="M3 (inclinação histórica)")
    rows3 = [r for r in g33["m33"]["por_rodada"] if r["ordem"] == ordem]
    ax.plot(x, [r["erro_m2"] for r in rows3], marker="s", markersize=3, color=C[2], linewidth=1.1, linestyle=(0, (2, 2)), label="M3.3 (choques × 0,8)")
    ax.set_xticks(x); ax.set_xlabel("rodada (décimos do número de municípios)")
    ax.set_title(f"Ordem {'real reconstruída' if ordem == 'real' else 'aleatória'}", fontsize=8.5, loc="left")
axs[0].set_ylabel("erro mediano do líder (pp), 55 disputas de 2022"); axs[0].legend(fontsize=6)
fig.tight_layout(); fig.savefig(S / "fig7_gate.png"); plt.close(fig)
print("ok")
