"""Eleições passadas no formato do ao vivo: um banco por ano e turno.

O seletor de anos (24/09/2026) mostra 2018 e 2022 nas MESMAS telas da noite
— Resultado, Mapa, composição —, e essas telas leem o banco do coletor, não
o histórico. Em vez de ensinar cada tela a ler outro formato, este módulo
monta, para cada ano e turno, um banco idêntico ao que o coletor escreveria
se a apuração tivesse acabado: território, disputas, candidatos, totais por
unidade e votos, tudo com andamento `f` e 100% apurado. A Câmara vai para o
arquivo bruto no formato do EA20, como no coletor.

    ambiente     conteúdo
    tse2018t1    presidente, governador, senador (2 vagas), deputado federal
    tse2018t2    presidente e governador — resultado FINAL (ver abaixo)
    tse2022t1    presidente, governador, senador (1 vaga), deputado federal
    tse2022t2    presidente e governador — resultado FINAL

**Governador no 2º turno é o resultado final** (decisão de 21/09/2026): as
UFs com 2º turno usam o 2º turno; as demais completam com o 1º turno, que
foi o decisivo nelas. Essas UFs ficam listadas nos metadados do ambiente
(`<banco>.meta.json`), e as telas as marcam — o mapa de 2º turno não pode
parecer ter havido 2º turno em São Paulo e no Acre ao mesmo tempo sem dizer.

Fontes: `votacao_candidato_munzona_AAAA.zip` e
`detalhe_votacao_munzona_AAAA.zip` do TSE, em `Dados/<ano>/`. Os
territórios (códigos TSE e IBGE) são os do simulado de 2026 — a malha
municipal é a mesma; município que não existia fica sem dado, não inventado.

Nome de urna vem do próprio arquivo do TSE daquele ano (casado por
SQ_CANDIDATO). O enriquecimento (foto, perfil) é só de 2026.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import DATA_DIR, load_environment
from ..storage.db import create_all, make_engine
from ..storage.raw import RawArchive
from ..tse.parsers.common import fold
from . import tse_odsele as H

UFS = """ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr
sc sp se to""".split()

#: Dia de cada turno. O horário gravado é 23h59 de Brasília: é um retrato do
#: fim da apuração, não da noite.
DATAS = {(2018, "1"): (2018, 10, 7), (2018, "2"): (2018, 10, 28),
         (2022, "1"): (2022, 10, 2), (2022, "2"): (2022, 10, 30)}

DET_CAMPOS = ("aptos", "comparecimento", "abstencoes", "total_votos",
              "concorrentes", "validos", "anulados", "anulados_sub_judice",
              "brancos", "nulos", "secoes_total", "secoes_nao_instaladas",
              "eleitores_secoes_nao_instaladas")

CARGO_NOME = {"1": "Presidente", "3": "Governador", "5": "Senador",
              "6": "Deputado Federal"}


def nome_ambiente(ano: int, turno: str) -> str:
    return f"tse{ano}t{turno}"


def caminho_meta(db: Path) -> Path:
    return db.with_suffix(".meta.json")


def ler_meta(env_name: str) -> dict | None:
    """Metadados de um ambiente congelado, ou None se não for um."""
    try:
        env = load_environment(env_name)
    except Exception:                                   # noqa: BLE001
        return None
    p = caminho_meta(env.db_path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def vagas_senado(ano: int) -> int:
    """Duas em 2010, 2018, 2026…; uma em 2014, 2022, 2030."""
    return 2 if (ano - 2018) % 8 == 0 else 1


def _mun(cd: str) -> str:
    return str(cd).strip().zfill(5)


def agregar(tarefa: tuple) -> dict:
    """Uma UF (ou o arquivo BR) e um turno → votos por município e
    candidato, detalhe por município e o cadastro dos candidatos. Roda num
    processo à parte: são gigabytes de CSV."""
    zv, zd, alvo, cargos, com_mun, turno, ano = tarefa
    # Só a eleição ordinária: o zip traz também as suplementares posteriores.
    ords = H.ordinarias(ano, turno)
    votos: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    cands: dict = defaultdict(dict)
    eleicao: dict = {}
    for v in H.ler_votos(Path(zv), alvo, cargos=cargos, turno=turno,
                         eleicoes=ords):
        chave = _mun(v.cd_municipio) if v.cargo in com_mun else "uf"
        votos[v.cargo][chave][v.sq_candidato] += v.votos
        eleicao.setdefault(v.cargo, v.cd_eleicao)
        if v.sq_candidato not in cands[v.cargo]:
            cands[v.cargo][v.sq_candidato] = (
                v.nr_candidato, v.nm_urna, v.nr_partido, v.sg_partido,
                v.situacao, v.destinacao, v.uf)
    det: dict = defaultdict(lambda: defaultdict(lambda: [0] * len(DET_CAMPOS)))
    for d in H.ler_detalhe(Path(zd), alvo, turno=turno, eleicoes=ords,
                           cargos=tuple(c for c in cargos if c in com_mun)):
        acc = det[d.cargo][_mun(d.cd_municipio)]
        for i, k in enumerate(DET_CAMPOS):
            acc[i] += getattr(d, k)
    return {"alvo": alvo, "turno": turno, "eleicao": eleicao,
            "votos": {c: {m: dict(x) for m, x in ms.items()}
                      for c, ms in votos.items()},
            "cands": {c: dict(x) for c, x in cands.items()},
            "det": {c: dict(x) for c, x in det.items()}}


def montar(ano: int, turno: str, *, workers: int = 6, log=print) -> dict:
    """Monta o ambiente `tse{ano}t{turno}` do zero. Devolve os metadados."""
    turno = str(turno)
    if (ano, turno) not in DATAS:
        raise ValueError(f"sem data conhecida para {ano}, {turno}º turno")
    env_name = nome_ambiente(ano, turno)
    env = load_environment(env_name)
    y, mo, d = DATAS[(ano, turno)]
    gerado = datetime(y, mo, d, 23, 59, tzinfo=timezone(timedelta(hours=-3))
                      ).astimezone(timezone.utc)
    zv = H.arquivo(f"votacao_candidato_munzona_{ano}.zip", ano)
    zd = H.arquivo(f"detalhe_votacao_munzona_{ano}.zip", ano)

    if turno == "1":
        tarefas = [(str(zv), str(zd), "br", ("1",), ("1",), "1", ano)]
        tarefas += [(str(zv), str(zd), uf, ("3", "5", "6"), ("3", "5"), "1",
                     ano) for uf in UFS]
    else:
        # 2º turno: presidente, e governador nos dois turnos — o 1º completa
        # as UFs que se decidiram nele.
        tarefas = [(str(zv), str(zd), "br", ("1",), ("1",), "2", ano)]
        tarefas += [(str(zv), str(zd), uf, ("3",), ("3",), t, ano)
                    for uf in UFS for t in ("2", "1")]

    log(f"{env_name}: lendo {len(tarefas)} arquivos em paralelo…")
    partes = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(agregar, tarefas), 1):
            partes.append(r)
            if i % 10 == 0 or i == len(tarefas):
                log(f"  {i}/{len(tarefas)}")

    # Governador no 2º turno: por UF, o turno que decidiu.
    gov_1t: list[str] = []
    if turno == "2":
        com_2t = {p["alvo"] for p in partes
                  if p["turno"] == "2" and p["votos"].get("3")}
        gov_1t = sorted(set(UFS) - com_2t)
        partes = [p for p in partes
                  if p["alvo"] == "br" or p["turno"] == "2"
                  or p["alvo"] in gov_1t]

    # ------------------------------------------------------------- banco
    db = env.db_path
    try:
        if db.exists():
            db.unlink()
        for suf in ("-wal", "-shm"):
            Path(str(db) + suf).unlink(missing_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"{db.name} está aberto por outro processo — quase sempre o "
            "servidor. Feche-o e rode de novo.") from exc
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
            (env_name, *g))
        gid[(g[0], g[1])] = cur.lastrowid
    uf_de_mun = {g[1]: g[3] for g in geos if g[0] == "mu"}

    cd_el: dict[str, str] = {}
    for p in partes:
        for c, e in p["eleicao"].items():
            cd_el.setdefault(c, e)

    rotulo_turno = "1º turno" if turno == "1" else "resultado final"

    def eleicao(cd, nome):
        cur.execute(
            "insert into election (environment, official, ciclo, cd_pleito, "
            "cd_eleicao, cd_eleicao_turno2, turno, nome, data) values "
            "(?,?,?,?,?,?,?,?,?)",
            (env_name, 0, f"ele{ano}", "", cd or "", None, turno, nome,
             gerado.isoformat(sep=" ")))
        return cur.lastrowid

    e_fed = eleicao(cd_el.get("1"), f"Federal {ano} — {rotulo_turno}")
    e_est = eleicao(cd_el.get("3"), f"Estadual {ano} — {rotulo_turno}")

    def disputa(el, cd, abr, vagas, ext):
        cur.execute(
            "insert into contest (election_id, cargo_cd, cargo_nome, "
            "abrangencia_decisoria, vagas, inclui_exterior) values "
            "(?,?,?,?,?,?)", (el, cd, CARGO_NOME[cd], abr, vagas, ext))
        return cur.lastrowid

    ct = {"1": disputa(e_fed, "1", "br", 1, 1),
          "3": disputa(e_est, "3", "uf", 1, 0)}
    if turno == "1":
        ct["5"] = disputa(e_est, "5", "uf", vagas_senado(ano), 0)

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
            if c not in ct:
                continue
            for m, x in ms.items():
                for sq, v in x.items():
                    votos[c][m][sq] += v
            cands[c].update(p["cands"][c])
        for c, ms in p["det"].items():
            if c not in ct:
                continue
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
                 1 if fold(sit).startswith("ELEITO") else 0))
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
            (env_name, "ea20", f"tse{ano}:{turno}:{c}:{tipo}:{cd}",
             f"tse-odsele://{ano}/{turno}/{c}/{cd}", 200, None, 0,
             gerado.isoformat(sep=" "), agora, None, None, "congelado",
             None))
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
            (sid, ct[c], g, "f", gerado.isoformat(sep=" "), sec, sec,
             sec - sec_ni, sec - sec_ni, aptos, aptos, 0, el_ni,
             aptos - el_ni, comp, abst, tv, val, conc, nominal, anul, sj,
             br, nul, 0, 0, None))
        cur.executemany(
            "insert into observed_result (snapshot_id, contest_id, "
            "geography_id, candidate_id, votos, dvt, revisao) values "
            "(?,?,?,?,?,?,?)",
            [(sid, ct[c], g, cand_id[(c, sq)][0], v, cand_id[(c, sq)][1], 0)
             for sq, v in vs.items() if (c, sq) in cand_id])

    contagem = {}
    for c in ct:
        por_uf_v: dict = defaultdict(lambda: defaultdict(int))
        por_uf_d: dict = defaultdict(lambda: [0] * len(DET_CAMPOS))
        br_v: dict = defaultdict(int)
        br_d = [0] * len(DET_CAMPOS)
        for m, vs in votos[c].items():
            uf = uf_de_mun.get(m)
            dm = det[c].get(m, [0] * len(DET_CAMPOS))
            gravar(c, "mu", m, vs, dm)
            if uf is None:
                continue
            for sq, v in vs.items():
                por_uf_v[uf][sq] += v
                br_v[sq] += v
            for i, x in enumerate(dm):
                por_uf_d[uf][i] += x
                br_d[i] += x
        for uf, vs in por_uf_v.items():
            gravar(c, "uf", uf, vs, por_uf_d[uf])
        if c == "1":
            gravar(c, "br", "br", br_v, br_d)
        con.commit()
        contagem[c] = {"municipios": len(votos[c]), "ufs": len(por_uf_v)}
        log(f"  cargo {c}: {len(votos[c])} municípios, {len(por_uf_v)} UFs")
    con.close()

    # ---------------------------------------------- Câmara, no bruto
    eleitos_camara = 0
    if turno == "1":
        arq = RawArchive(DATA_DIR / "raw", env_name)
        el6 = cd_el.get("6", "")
        for uf in UFS:
            cs = dep_cands.get(uf, {})
            vs = dep_votos.get(uf, {})
            partidos: dict = defaultdict(list)
            eleitos = 0
            for sq, (nr, nome, npart, spart, sit, dest, _) in cs.items():
                e = fold(sit).startswith("ELEITO")
                eleitos += e
                partidos[(npart, spart)].append({
                    "n": nr, "sqcand": sq, "nm": nome, "nmu": nome,
                    "dvt": dest, "st": (sit or "").capitalize(),
                    "e": "s" if e else "n", "vap": str(vs.get(sq, 0))})
            eleitos_camara += eleitos
            aptos = sum(dd[0] for m, dd in det["3"].items()
                        if uf_de_mun.get(m) == uf)
            data_br = f"{d:02d}/{mo:02d}/{y}"
            payload = {
                "ele": el6, "t": "1", "f": "s", "sup": "n", "tpabr": "uf",
                "cdabr": uf, "dg": data_br, "hg": "23:59:00", "idg": "0",
                "dt": data_br, "ht": "23:59:00", "dv": "s", "tf": "s",
                "and": "f", "esae": "n", "mnae": [],
                "carg": [{"cd": "6", "nmn": "Deputado Federal",
                          "nv": str(eleitos), "qe": "", "fed": [],
                          "agr": [{"n": "", "nm": "", "tp": "", "com": "",
                                   "vag": "",
                                   "par": [{"n": n, "sg": sg, "nm": sg,
                                            "cand": cl}
                                           for (n, sg), cl
                                           in partidos.items()]}]}],
                "s": {"ts": "1", "st": "1", "snt": "0", "si": "1",
                      "sni": "0", "sa": "1", "sna": "0"},
                "e": {"te": str(aptos), "est": str(aptos), "esnt": "0",
                      "esi": str(aptos), "esni": "0", "esa": str(aptos),
                      "esna": "0", "c": "0", "a": "0"},
                "v": {},
            }
            corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            arq.store(key=f"ea20:{el6}:6:{uf}:-", kind="ea20",
                      url=f"tse-odsele://{ano}/{uf}-c0006.json",
                      status=200, body=corpo,
                      sha256=hashlib.sha256(corpo).hexdigest(),
                      captured_at=datetime.now(timezone.utc),
                      source_generated_at=gerado, idg=None, etag=None,
                      last_modified=None, parser_version="congelado",
                      election=el6, cargo="6", uf=uf)
        log(f"  câmara: {eleitos_camara} deputados eleitos no bruto")

    meta = {
        "ambiente": env_name, "ano": ano, "turno": turno,
        "rotulo": f"{ano} — {rotulo_turno}",
        "montado_em": datetime.now(timezone.utc).isoformat(),
        "fontes": [zv.name, zd.name],
        "cargos": sorted(ct) + (["6"] if turno == "1" else []),
        "contagem": contagem,
        "camara_eleitos": eleitos_camara or None,
        # UFs em que o governador do "resultado final" é o do 1º turno.
        "governador_decidido_1o_turno": gov_1t,
    }
    caminho_meta(db).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
