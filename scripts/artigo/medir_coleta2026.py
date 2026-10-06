"""Operação da coleta na noite de 04/10/2026: capturas, latência, erros."""
import json, collections, statistics as st, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
# O console do Windows (cp1252) não aceita o sinal de menos tipográfico.
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "oficial2026" / "manifest"
TZ = timezone(timedelta(hours=-3))
kinds = collections.Counter(); status = collections.Counter(); lat = collections.defaultdict(list); por_hora = collections.Counter(); dedup = collections.Counter()
ea20_mun = set(); bytes_tot = 0; n = 0; first = None; last = None
for dia in ("2026-10-04", "2026-10-05"):
    with open(ROOT / f"{dia}.jsonl", encoding="utf8") as f:
        for line in f:
            r = json.loads(line); n += 1
            kinds[r["kind"]] += 1; status[r["status"]] += 1; bytes_tot += r.get("bytes_len", 0) or 0
            dedup[r.get("dedup")] += 1
            c = datetime.fromisoformat(r["captured_at"])
            first = c if first is None or c < first else first; last = c if last is None or c > last else last
            por_hora[c.astimezone(TZ).strftime("%d %Hh")] += 1
            if r["kind"] == "ea20" and r.get("source_generated_at") and r["status"] == 200:
                g = datetime.fromisoformat(r["source_generated_at"])
                lat[r["cargo"]].append((c - g).total_seconds())
                if r.get("mun"):
                    ea20_mun.add((r["cargo"], r["uf"], r["mun"]))
print("capturas", n, "bytes", bytes_tot, "primeira", first, "última", last)
print("kinds", kinds); print("status", status); print("dedup", dedup)
print("municípios×cargo com EA20:", len(ea20_mun), collections.Counter(k[0] for k in ea20_mun))
for cargo, ls in lat.items():
    ls.sort()
    print(f"latência captura−geração EA20 cargo {cargo}: n={len(ls)} mediana={st.median(ls):.0f}s p90={ls[int(.9*len(ls))]:.0f}s p99={ls[int(.99*len(ls))]:.0f}s")
for h in sorted(por_hora):
    print(h, por_hora[h])
