"""Corrige no banco os horários do TSE gravados em Brasília como se fossem UTC.

Até 22/09/2026 a carga gravava `source_generated_at` (e o `totalizado_em` das
unidades) com o horário de Brasília sem converter: o SQLite descarta o fuso,
e a API, que lê tudo como UTC, mostrava o "Fonte TSE" três horas antes. A
carga já grava em UTC; este script conserta o que foi gravado antes.

É exato e idempotente: para cada snapshot, o horário certo é relido do
manifesto do arquivo bruto (que guarda o fuso original). Só é tocado o
snapshot cujo valor gravado é exatamente o certo menos 3 horas — o sinal do
erro —, e junto com ele o `totalizado_em` das suas unidades. Rodar de novo
não muda nada.

Uso:
    python scripts/corrigir_fuso.py [--env simulado2026] [--aplicar]

Sem `--aplicar`, só conta o que mudaria.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR, load_environment              # noqa: E402
from obs2026.storage.raw import RawArchive                          # noqa: E402

TRES_H = timedelta(hours=3)
FMT = "%Y-%m-%d %H:%M:%S.%f"


def _naive_utc(iso: str) -> datetime:
    d = datetime.fromisoformat(iso)
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="simulado2026")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    env = load_environment(a.env)
    arq = RawArchive(DATA_DIR / "raw", env.name)

    certo: dict[tuple[str, str], datetime] = {}
    for dia in arq.days():
        for ref in arq.manifest(dia):
            if ref.kind == "ea20" and ref.source_generated_at:
                chave = (ref.key, _naive_utc(ref.captured_at).strftime(FMT))
                certo[chave] = _naive_utc(ref.source_generated_at)

    con = sqlite3.connect(env.db_path, timeout=60)
    linhas = con.execute(
        "select id, source_key, captured_at, source_generated_at "
        "from source_snapshot where source_generated_at is not null"
    ).fetchall()
    corrigir = []
    for sid, key, cap, gerado in linhas:
        cap_n = datetime.fromisoformat(cap).strftime(FMT)
        c = certo.get((key, cap_n))
        if c is None:
            continue
        g = datetime.fromisoformat(gerado).replace(tzinfo=None)
        if g == c - TRES_H:
            corrigir.append((sid, c))
    print(f"{len(linhas)} snapshots; {len(corrigir)} gravados em Brasília "
          f"como UTC")
    if not a.aplicar:
        print("nada alterado (use --aplicar)")
        return
    with con:
        for sid, c in corrigir:
            con.execute("update source_snapshot set source_generated_at=? "
                        "where id=?", (c.strftime(FMT), sid))
            con.execute("update unit_totals set totalizado_em="
                        "strftime('%Y-%m-%d %H:%M:%S', totalizado_em, "
                        "'+3 hours') || '.000000' where snapshot_id=? and "
                        "totalizado_em is not null", (sid,))
    print(f"corrigidos: {len(corrigir)}")


if __name__ == "__main__":
    main()
