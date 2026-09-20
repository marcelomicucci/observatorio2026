"""Coleta completa de uma UF para um cargo, para alimentar backtests.

    python scripts/coletar_uf.py al senador

Usa o mesmo cliente do coletor — mesmo teto, mesmo circuito, mesmo lockfile.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.cli import _client, _discover           # noqa: E402
from obs2026.config import DATA_DIR, load_collector, load_environment  # noqa
from obs2026.storage.raw import RawArchive           # noqa: E402
from obs2026.tse.parsers.common import parse_dt      # noqa: E402


async def main(uf: str, cargo_key: str, env_name: str = "simulado2026"):
    env, cfg = load_environment(env_name), load_collector()
    arc = RawArchive(DATA_DIR / "raw", env.name)
    client = _client(env, cfg)
    try:
        cat = await _discover(env, cfg, client, arc)
        c = next(x for x in cat.contests if x.cargo.key == cargo_key)
        muns = [m for m in c.municipios if m.uf == uf]
        print(f"{cargo_key} {uf}: {len(muns)} municipios")
        ok = 0
        for m in muns:
            t = cat.urls.ea20_mun(c.election, c.cargo.cd, m.uf, m.cd)
            f = await client.fetch(t.url, key=t.key)
            if not f.ok:
                continue
            p = json.loads(f.body.decode("utf-8"))
            arc.store(key=t.key, kind="ea20", url=t.url, status=f.status,
                      body=f.body, sha256=f.sha256, captured_at=f.captured_at,
                      source_generated_at=parse_dt(p.get("dg"), p.get("hg")),
                      idg=str(p.get("idg") or ""), etag=f.etag,
                      last_modified=f.last_modified,
                      parser_version=f.parser_version, election=c.election,
                      cargo=c.cargo.cd, uf=m.uf, mun=m.cd)
            ok += 1
        print(f"  capturados: {ok}   http={client.stats}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("uso: coletar_uf.py <uf> <presidente|governador|senador>")
    asyncio.run(main(sys.argv[1].lower(), sys.argv[2].lower()))
