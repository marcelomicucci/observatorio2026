"""Confere o `snapshot_set_id` de cada projeção contra o manifesto das capturas.

Para cada registro do diário anotado em `conjuntos-<ambiente>.jsonl`, refaz o
conjunto de capturas que o motor leu — só com o manifesto e a posição
gravada, sem horário — e compara o hash (`obs2026/engine/conjuntos.py`).

Uso:
    python scripts/artigo/conferir_conjuntos.py [--env oficial2026]

Grava data/exports/artigo/conjuntos-<ambiente>.json e sai com código 1 se
houver divergência.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from obs2026.engine import audit, conjuntos  # noqa: E402
from obs2026.storage.raw import RawArchive  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--env", default="oficial2026")
a = ap.parse_args()

diario = audit.Diario(ROOT / "data" / "previsoes", a.env)
arquivo = RawArchive(ROOT / "data" / "raw", a.env)
anot = conjuntos.ler_anotacoes(diario.caminho_conjuntos)

com_id = sem_id = 0
ids_do_diario = {}
if diario.caminho.exists():
    with open(diario.caminho, encoding="utf-8") as fh:
        for linha in fh:
            if not linha.strip():
                continue
            r = json.loads(linha)
            if r.get("snapshot_set_id"):
                com_id += 1
                ids_do_diario[r["registro_id"]] = r["snapshot_set_id"]
            else:
                sem_id += 1

res = conjuntos.conferir(arquivo, anot)
# A anotação tem de dizer o mesmo que o registro que ela acompanha.
desencontradas = [x["registro_id"] for x in anot
                  if ids_do_diario.get(x["registro_id"]) != x["snapshot_set_id"]]
anotados = {x["registro_id"] for x in anot}
res.update(ambiente=a.env, registros_com_id=com_id, registros_sem_id=sem_id,
           registros_com_id_sem_anotacao=len(set(ids_do_diario) - anotados),
           anotacoes_desencontradas_do_diario=desencontradas[:20])

saida = ROOT / "data" / "exports" / "artigo" / f"conjuntos-{a.env}.json"
saida.parent.mkdir(parents=True, exist_ok=True)
saida.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"{a.env}: {com_id} registros com snapshot_set_id, {sem_id} sem")
print(f"anotações: {res['anotacoes']}; conjunto refeito igual: {res['iguais']}; "
      f"divergentes: {len(res['divergentes'])}")
print(f"registros com id e sem anotação: {res['registros_com_id_sem_anotacao']}; "
      f"anotações que não batem com o diário: {len(desencontradas)}")
for d in res["divergentes"][:10]:
    print("  DIVERGE", d["registro_id"], d["unidades_registradas"],
          "x", d["unidades_refeitas"], d["manifesto_incompleto"])
sys.exit(1 if res["divergentes"] or desencontradas else 0)
