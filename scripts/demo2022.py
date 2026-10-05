"""Monta o ambiente `demo2022`: os relatórios do telão com partidos reais.

O simulado do TSE tem partidos fictícios ("P 9998"), então nele o mapa por
partido e a composição saem com cores geradas e rótulos sem sentido. Para
ver como as telas ficam com dado real antes da noite, este script monta um
banco com o resultado histórico, no mesmo esquema que o coletor usa:

    presidente         1º turno de 2022 (Brasil, UF, município, exterior)
    governador         1º turno de 2022
    senador            2018 — duas vagas por UF, como em 2026. Somado às 27
                       cadeiras eleitas em 2022 (lidas do Senado), dá o
                       Senado que tomou posse em 2023, que é o de hoje
    deputado federal   2022, gravado no arquivo bruto no formato do EA20,
                       para a composição da Câmara passar pelo mesmo caminho
                       do coletor

Uso:
    python scripts/demo2022.py
    obs2026 servir --env demo2022 --porta 8027

Fontes: `votacao_candidato_munzona_{2018,2022}.zip` e
`detalhe_votacao_munzona_{2018,2022}.zip` em `eleicao2026/Dados/<ano>/`.
Leva alguns minutos (lê ~9 GB de CSV, uma UF por processo). Tudo no banco
fica com andamento `f` e 100% apurado; é um retrato final, não uma noite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR, load_environment               # noqa: E402
from obs2026.history import tse_odsele as H                        # noqa: E402
from obs2026.storage.db import create_all, make_engine              # noqa: E402
from obs2026.storage.raw import RawArchive                         # noqa: E402
from obs2026.tse.parsers.common import fold                        # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
HIST = RAIZ.parent
ENV = "demo2022"
UFS = """ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr
sc sp se to""".split()

GERADO = datetime(2022, 10, 3, 2, 59, tzinfo=timezone.utc)  # 02/10 23h59 BRT
DET_CAMPOS = ("aptos", "comparecimento", "abstencoes", "total_votos",
              "concorrentes", "validos", "anulados", "anulados_sub_judice",
              "brancos", "nulos", "secoes_total", "secoes_nao_instaladas",
              "eleitores_secoes_nao_instaladas")


def _mun(cd: str) -> str:
    return str(cd).strip().zfill(5)


def agregar(tarefa: tuple) -> dict:
    """Uma UF (ou o arquivo BR) → votos por município e candidato, detalhe
    por município, e o cadastro dos candidatos. Roda num processo à parte."""
    zv, zd, alvo, cargos, com_mun = tarefa
    votos: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    cands: dict = defaultdict(dict)
    for v in H.ler_votos(Path(zv), alvo, cargos=cargos):
        chave = _mun(v.cd_municipio) if v.cargo in com_mun else "uf"
        votos[v.cargo][chave][v.sq_candidato] += v.votos
        if v.sq_candidato not in cands[v.cargo]:
            cands[v.cargo][v.sq_candidato] = (
                v.nr_candidato, v.nm_urna, v.nr_partido, v.sg_partido,
                v.situacao, v.destinacao, v.uf)
    det: dict = defaultdict(lambda: defaultdict(lambda: [0] * len(DET_CAMPOS)))
    for d in H.ler_detalhe(Path(zd), alvo, cargos=tuple(c for c in cargos
                                                       if c in com_mun)):
        acc = det[d.cargo][_mun(d.cd_municipio)]
        for i, k in enumerate(DET_CAMPOS):
            acc[i] += getattr(d, k)
    return {"alvo": alvo,
            "votos": {c: {m: dict(x) for m, x in ms.items()}
                      for c, ms in votos.items()},
            "cands": {c: dict(x) for c, x in cands.items()},
            "det": {c: dict(x) for c, x in det.items()}}


def main() -> None:
    t0 = time.time()
    env = load_environment(ENV)
    zv22 = H.arquivo("votacao_candidato_munzona_2022.zip", 2022)
    zd22 = H.arquivo("detalhe_votacao_munzona_2022.zip", 2022)
    zv18 = H.arquivo("votacao_candidato_munzona_2018.zip", 2018)
    zd18 = H.arquivo("detalhe_votacao_munzona_2018.zip", 2018)

    tarefas = [(str(zv22), str(zd22), "br", ("1",), ("1",))]
    tarefas += [(str(zv22), str(zd22), uf, ("3", "6"), ("3",)) for uf in UFS]
    tarefas += [(str(zv18), str(zd18), uf, ("5",), ("5",)) for uf in UFS]

    print(f"lendo {len(tarefas)} arquivos em paralelo…", flush=True)
    partes = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        for i, r in enumerate(ex.map(agregar, tarefas), 1):
            partes.append(r)
            print(f"  {i}/{len(tarefas)} {r['alvo']}", flush=True)

    # ------------------------------------------------------------- banco
    db = env.db_path
    try:
        if db.exists():
            db.unlink()
        for suf in ("-wal", "-shm"):
            Path(str(db) + suf).unlink(missing_ok=True)
    except PermissionError:
        sys.exit(f"\n{db.name} está aberto por outro processo — quase sempre "
                 "o servidor da demonstração (porta 8027). Feche-o e rode "
                 "de novo.")
    create_all(make_engine(sqlite_path=db))
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=OFF")
    cur = con.cursor()

    # Territórios: os mesmos do simulado (códigos TSE e IBGE do EA12).
    sim = sqlite3.connect(load_environment("simulado2026").db_path)
    geos = sim.execute(
        "select tipo, cd_tse, cd_ibge, uf, nome, nome_fold, capital, "
        "exterior from geography").fetchall()
    sim.close()
    gid: dict[tuple[str, str], int] = {}
    for g in geos:
        cur.execute(
            "insert into geography (environment, tipo, cd_tse, cd_ibge, uf, "
            "nome, nome_fold, capital, exterior) values (?,?,?,?,?,?,?,?,?)",
            (ENV, *g))
        gid[(g[0], g[1])] = cur.lastrowid
    uf_de_mun = {g[1]: g[3] for g in geos if g[0] == "mu"}

    def eleicao(cd, nome):
        cur.execute(
            "insert into election (environment, official, ciclo, cd_pleito, "
            "cd_eleicao, cd_eleicao_turno2, turno, nome, data) values "
            "(?,?,?,?,?,?,?,?,?)",
            (ENV, 0, "ele2022", "406", cd, None, "1", nome,
             "2022-10-02 00:00:00.000000"))
        return cur.lastrowid

    e_fed = eleicao("544", "Demonstração — Federal 2022, 1º turno")
    e_est = eleicao("546", "Demonstração — Estadual 2022 (Senado 2018)")

    def disputa(el, cd, nome, abr, vagas, ext):
        cur.execute(
            "insert into contest (election_id, cargo_cd, cargo_nome, "
            "abrangencia_decisoria, vagas, inclui_exterior) values "
            "(?,?,?,?,?,?)", (el, cd, nome, abr, vagas, ext))
        return cur.lastrowid

    ct = {"1": disputa(e_fed, "1", "Presidente", "br", 1, 1),
          "3": disputa(e_est, "3", "Governador", "uf", 1, 0),
          "5": disputa(e_est, "5", "Senador", "uf", 2, 0)}

    votos = {c: defaultdict(lambda: defaultdict(int)) for c in ct}
    cands: dict = {c: {} for c in ct}
    det = {c: defaultdict(lambda: [0] * len(DET_CAMPOS)) for c in ct}
    dep_votos: dict = defaultdict(lambda: defaultdict(int))
    dep_cands: dict = defaultdict(dict)
    for p in partes:
        for c, ms in p["votos"].items():
            if c == "6":
                for sq, v in ms.get("uf", {}).items():
                    dep_votos[p["alvo"]][sq] += v
                dep_cands[p["alvo"]].update(p["cands"]["6"])
                continue
            for m, x in ms.items():
                for sq, v in x.items():
                    votos[c][m][sq] += v
            cands[c].update(p["cands"][c])
        for c, ms in p["det"].items():
            for m, acc in ms.items():
                a = det[c][m]
                for i in range(len(acc)):
                    a[i] += acc[i]

    agora = datetime.now(timezone.utc).isoformat(sep=" ")
    cand_id: dict = {}
    for c, cs in cands.items():
        for sq, (nr, nome, npart, spart, sit, dest, _) in cs.items():
            dvt = "Válido" if fold(dest) == "VALIDO" else dest
            cur.execute(
                "insert into candidate (contest_id, sqcand, numero, nome, "
                "nome_urna, partido_numero, partido_sigla, dvt, situacao, "
                "eleito) values (?,?,?,?,?,?,?,?,?,?)",
                (ct[c], sq, nr, nome, nome, npart, spart, dvt,
                 sit.capitalize() if sit else None,
                 1 if fold(sit) == "ELEITO" else 0))
            cand_id[(c, sq)] = (cur.lastrowid, dvt)

    def gravar(c: str, tipo: str, cd: str, vs: dict, d: list) -> None:
        g = gid.get((tipo, cd))
        if g is None:
            return
        cur.execute(
            "insert into source_snapshot (environment, kind, source_key, url, "
            "status, sha256, bytes_len, source_generated_at, captured_at, "
            "idg, etag, parser_version, blob_path) values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ENV, "ea20", f"demo:{c}:{tipo}:{cd}", f"demo2022://{c}/{cd}",
             200, None, 0, GERADO.isoformat(sep=" "), agora, None, None,
             "demo", None))
        sid = cur.lastrowid
        (aptos, comp, abst, tv, conc, val, anul, sj, br, nul, sec, sec_ni,
         el_ni) = d
        nominal = sum(v for sq, v in vs.items()
                      if cand_id.get((c, sq), (0, ""))[1] == "Válido")
        cur.execute(
            "insert into unit_totals (snapshot_id, contest_id, geography_id, "
            "andamento, totalizado_em, secoes_total, secoes_totalizadas, "
            "secoes_instaladas, secoes_apuradas, eleitorado_total, "
            "eleitorado_totalizado, eleitorado_nao_totalizado, "
            "eleitorado_nao_instalado, eleitorado_apurado, comparecimento, "
            "abstencao, total_votos, validos, validos_computados, "
            "nominal_tse, anulados, anulados_sub_judice, brancos, nulos, "
            "pendente_min, pendente_max, quality_flags) values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, ct[c], g, "f", GERADO.isoformat(sep=" "), sec, sec,
             sec - sec_ni, sec - sec_ni, aptos, aptos, 0, el_ni,
             aptos - el_ni, comp, abst, tv, val, conc, nominal, anul, sj,
             br, nul, 0, 0, None))
        cur.executemany(
            "insert into observed_result (snapshot_id, contest_id, "
            "geography_id, candidate_id, votos, dvt, revisao) values "
            "(?,?,?,?,?,?,?)",
            [(sid, ct[c], g, cand_id[(c, sq)][0], v, cand_id[(c, sq)][1], 0)
             for sq, v in vs.items() if (c, sq) in cand_id])

    for c in ct:
        por_uf_v: dict = defaultdict(lambda: defaultdict(int))
        por_uf_d: dict = defaultdict(lambda: [0] * len(DET_CAMPOS))
        br_v: dict = defaultdict(int)
        br_d = [0] * len(DET_CAMPOS)
        for m, vs in votos[c].items():
            uf = uf_de_mun.get(m)
            d = det[c].get(m, [0] * len(DET_CAMPOS))
            gravar(c, "mu", m, vs, d)
            if uf is None:
                continue
            for sq, v in vs.items():
                por_uf_v[uf][sq] += v
                br_v[sq] += v
            for i, x in enumerate(d):
                por_uf_d[uf][i] += x
                br_d[i] += x
        for uf, vs in por_uf_v.items():
            gravar(c, "uf", uf, vs, por_uf_d[uf])
        if c == "1":
            gravar(c, "br", "br", br_v, br_d)
        con.commit()
        print(f"cargo {c}: {len(votos[c])} municípios gravados", flush=True)
    con.close()

    # ---------------------------------------------- Câmara, no bruto
    arq = RawArchive(DATA_DIR / "raw", ENV)
    total_eleitos = 0
    for uf in UFS:
        cs = dep_cands.get(uf, {})
        vs = dep_votos.get(uf, {})
        partidos: dict = defaultdict(list)
        eleitos = 0
        for sq, (nr, nome, npart, spart, sit, dest, _) in cs.items():
            if fold(sit).startswith("ELEITO"):
                eleitos += 1
            partidos[(npart, spart)].append({
                "n": nr, "sqcand": sq, "nm": nome, "nmu": nome,
                "dvt": dest, "st": (sit or "").capitalize(),
                "e": "s" if fold(sit).startswith("ELEITO") else "n",
                "vap": str(vs.get(sq, 0))})
        total_eleitos += eleitos
        aptos = sum(d[0] for m, d in det["3"].items()
                    if uf_de_mun.get(m) == uf)
        payload = {
            "ele": "546", "t": "1", "f": "s", "sup": "n", "tpabr": "uf",
            "cdabr": uf, "dg": "02/10/2022", "hg": "23:59:00", "idg": "0",
            "dt": "02/10/2022", "ht": "23:59:00", "dv": "s", "tf": "s",
            "and": "f", "esae": "n", "mnae": [],
            "carg": [{"cd": "6", "nmn": "Deputado Federal", "nv": str(eleitos),
                      "qe": "", "fed": [],
                      "agr": [{"n": "", "nm": "", "tp": "", "com": "",
                               "vag": "",
                               "par": [{"n": n, "sg": sg, "nm": sg,
                                        "cand": c}
                                       for (n, sg), c in partidos.items()]}]}],
            "s": {"ts": "1", "st": "1", "snt": "0", "si": "1", "sni": "0",
                  "sa": "1", "sna": "0"},
            "e": {"te": str(aptos), "est": str(aptos), "esnt": "0",
                  "esi": str(aptos), "esni": "0", "esa": str(aptos),
                  "esna": "0", "c": "0", "a": "0"},
            "v": {},
        }
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        arq.store(key=f"ea20:546:6:{uf}:-", kind="ea20",
                  url=f"demo2022://546/{uf}-c0006-e000546-u.json",
                  status=200, body=corpo,
                  sha256=hashlib.sha256(corpo).hexdigest(),
                  captured_at=datetime.now(timezone.utc),
                  source_generated_at=GERADO, idg=None, etag=None,
                  last_modified=None, parser_version="demo",
                  election="546", cargo="6", uf=uf)
    print(f"câmara: {total_eleitos} deputados eleitos gravados no bruto")
    print(f"pronto em {time.time() - t0:.0f}s — "
          f"obs2026 servir --env {ENV} --porta 8027")


if __name__ == "__main__":
    main()
