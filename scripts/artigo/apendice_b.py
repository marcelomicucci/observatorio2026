"""Pacote de reprodução do 1º turno de 2026 (Apêndice B do artigo).

Monta em data/exports/artigo/apendice_b/ o que o artigo cita e o repositório
não guarda, com hash de cada arquivo:

    diario/            cópia do diário de previsões, só leitura
    capturas/          hash e contagem do manifesto das capturas (sem copiar)
    ambiente/          versões exatas do ambiente de execução
    config/            configuração vigente e as cópias anteriores
    referencia_final/  total presidencial do TSE contra o último registro
    NOTA-universo.md   de onde veio o universo de unidades
    MANIFESTO.md       o que é cada coisa
    SHA256SUMS.txt     hash de tudo

O arquivo do TSE em referencia_final/ é baixado à parte (ver MANIFESTO.md):
este script não fala com a rede.

Uso:
    python scripts/artigo/apendice_b.py
"""
import collections
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from obs2026.tse.parsers import ea20  # noqa: E402

OUT = ROOT / "data" / "exports" / "artigo" / "apendice_b"
DIARIO = ROOT / "data" / "previsoes" / "previsoes-oficial2026.jsonl"
MANIFESTO_RAW = ROOT / "data" / "raw" / "oficial2026" / "manifest"
BANCO = ROOT / "data" / "db" / "oficial2026.sqlite"
TSE_BR = OUT / "referencia_final" / "br-c0001-e006257-u.json"
URL_TSE_BR = ("https://resultados.tse.jus.br/oficial/ele2026/6257/dados/br/"
              "br-c0001-e006257-u.json")
TZ = timezone(timedelta(hours=-3))
CONFIG = ("operacao.yaml", "thresholds.yaml", "collector.yaml", "sources.yaml",
          "classificacao_2026.yaml", "linhagem_partidos.yaml",
          "partidos_2026.yaml", "arranjos_capitais.json")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def br(n) -> str:
    return f"{n:,}".replace(",", ".")


def hora(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def copiar(origem: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        os.chmod(destino, stat.S_IWRITE | stat.S_IREAD)
    shutil.copy2(origem, destino)


for sub in ("diario", "capturas", "ambiente", "config", "referencia_final"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ diário
copia = OUT / "diario" / DIARIO.name
copiar(DIARIO, copia)
os.chmod(copia, stat.S_IREAD)
assert sha(copia) == sha(DIARIO)

n = 0
disputas = set()
modelos, sims, hashes, estados = (collections.Counter() for _ in range(4))
sem_snapshot = 0
primeiro = ultimo = None
ultimo_pres = None
for linha in open(DIARIO, encoding="utf-8"):
    r = json.loads(linha)
    n += 1
    disputas.add((r["cargo"], r["unidade"]))
    modelos[f"{r['modelo']} ({r['model_version']})"] += 1
    sims[r["n_sims"]] += 1
    hashes[r["thresholds_hash"]] += 1
    estados[r["estado_publicacao"]] += 1
    sem_snapshot += r.get("snapshot_set_id") is None
    m = r["momento"]
    primeiro = m if primeiro is None or m < primeiro else primeiro
    ultimo = m if ultimo is None or m > ultimo else ultimo
    if r["cargo"] == "1" and (ultimo_pres is None or m > ultimo_pres["momento"]):
        ultimo_pres = r

resumo_diario = {
    "arquivo": f"diario/{DIARIO.name}", "sha256": sha(copia),
    "bytes": copia.stat().st_size, "registros": n, "disputas": len(disputas),
    "primeiro_registro": primeiro, "ultimo_registro": ultimo,
    "modelos": dict(modelos), "n_sims": {str(k): v for k, v in sims.items()},
    "thresholds_hash": dict(hashes), "estado_publicacao": dict(estados),
    "registros_sem_snapshot_set_id": sem_snapshot,
}
(OUT / "diario" / "resumo.json").write_text(
    json.dumps(resumo_diario, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------- capturas
capturas = []
for p in sorted(MANIFESTO_RAW.glob("*.jsonl")):
    kinds, status = collections.Counter(), collections.Counter()
    linhas = corpo = 0
    with open(p, encoding="utf-8") as f:
        for linha in f:
            r = json.loads(linha)
            linhas += 1
            kinds[r["kind"]] += 1
            status[str(r["status"])] += 1
            corpo += r.get("bytes_len", 0) or 0
    capturas.append({"arquivo": f"data/raw/oficial2026/manifest/{p.name}",
                     "sha256": sha(p), "bytes": p.stat().st_size,
                     "entradas": linhas, "por_tipo": dict(kinds),
                     "por_status": dict(status), "bytes_de_conteudo": corpo})
arquivos = em_disco = 0
for raiz, _dirs, fs in os.walk(ROOT / "data" / "raw" / "oficial2026"):
    for f in fs:
        arquivos += 1
        em_disco += os.path.getsize(os.path.join(raiz, f))
resumo_capturas = {"manifestos": capturas, "arquivos_em_disco": arquivos,
                   "bytes_em_disco": em_disco,
                   "entradas": sum(c["entradas"] for c in capturas)}
(OUT / "capturas" / "manifesto-capturas.json").write_text(
    json.dumps(resumo_capturas, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------- ambiente
freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                        capture_output=True, text=True).stdout
(OUT / "ambiente" / "pip-freeze.txt").write_text(freeze, encoding="utf-8")
import numpy  # noqa: E402
ambiente = {"python": sys.version, "plataforma": platform.platform(),
            "maquina": platform.machine(), "numpy": numpy.__version__,
            "gerado_em": datetime.now(TZ).isoformat(timespec="seconds"),
            "commit": git("rev-parse", "HEAD"),
            "arvore_limpa": git("status", "--porcelain", "--untracked-files=no") == ""}
(OUT / "ambiente" / "ambiente.json").write_text(
    json.dumps(ambiente, ensure_ascii=False, indent=2), encoding="utf-8")

# ------------------------------------------------------------------ config
for nome in CONFIG:
    copiar(ROOT / "config" / nome, OUT / "config" / nome)
for pasta in ("operacao.anterior", "thresholds.anterior"):
    for p in sorted((ROOT / "config" / pasta).glob("*")):
        copiar(p, OUT / "config" / pasta / p.name)
# Qual arquivo de limiares o diário cita: o hash é o dos bytes do arquivo.
candidatos = [OUT / "config" / "thresholds.yaml",
              *sorted((OUT / "config" / "thresholds.anterior").glob("*"))]
por_hash = {sha(p): p.relative_to(OUT).as_posix() for p in candidatos}
correspondencia = {h: {"registros": q, "arquivo": por_hash.get(h)}
                   for h, q in hashes.items()}
(OUT / "config" / "correspondencia-limiares.json").write_text(
    json.dumps(correspondencia, ensure_ascii=False, indent=2), encoding="utf-8")

# -------------------------------------------------------- referência final
ref_md = ["# Referência final da eleição presidencial", ""]
if TSE_BR.exists():
    tse = ea20.parse(json.loads(TSE_BR.read_bytes()))
    votos_tse = {c.sqcand: (c.nome_urna or c.nome, c.votos)
                 for c in tse.cargo.candidatos}
    base_tse = tse.votes.vvc
    base_dia = ultimo_pres["concorrentes_observados"]
    linhas = []
    for c in sorted(ultimo_pres["candidatos"], key=lambda c: -c["votos_observados"]):
        nome, v_tse = votos_tse.get(c["sqcand"], (c["nome"], None))
        p_dia = 100 * c["votos_observados"] / base_dia
        p_tse = 100 * v_tse / base_tse if v_tse is not None else None
        linhas.append({"sqcand": c["sqcand"], "nome": c["nome"],
                       "votos_diario": c["votos_observados"], "votos_tse": v_tse,
                       "pct_diario": round(p_dia, 4),
                       "pct_tse": None if p_tse is None else round(p_tse, 4),
                       "dif_pp": None if p_tse is None else round(p_tse - p_dia, 4)})
    comp = {
        "fonte": URL_TSE_BR, "sha256": sha(TSE_BR),
        "tse_gerado_em": tse.generated_at.isoformat(),
        "tse_totalizado_em": tse.totalized_at.isoformat() if tse.totalized_at else None,
        "tse_finalizado": tse.is_final,
        "tse_secoes": [tse.sections.st, tse.sections.ts],
        "tse_eleitorado": [tse.electorate.est, tse.electorate.te],
        "tse_votos_a_concorrentes": base_tse,
        "diario_ultimo_registro": ultimo_pres["momento"],
        "diario_estado": ultimo_pres["estado_publicacao"],
        "diario_secoes": [ultimo_pres["secoes_totalizadas"], ultimo_pres["secoes_total"]],
        "diario_eleitorado": [ultimo_pres["eleitorado_apurado"], ultimo_pres["eleitorado_total"]],
        "diario_votos_a_concorrentes": base_dia,
        "maior_diferenca_pp": max(abs(x["dif_pp"]) for x in linhas if x["dif_pp"] is not None),
        "candidatos": linhas,
    }
    (OUT / "referencia_final" / "presidente-diario-x-tse.json").write_text(
        json.dumps(comp, ensure_ascii=False, indent=2), encoding="utf-8")
    ref_md += [
        f"Arquivo do TSE: `{TSE_BR.name}`, gerado em "
        f"{tse.generated_at.astimezone(TZ):%d/%m/%Y %H:%M:%S}, "
        f"{'finalizado' if tse.is_final else 'NÃO finalizado'}, "
        f"{br(tse.sections.st)} de {br(tse.sections.ts)} seções totalizadas.",
        "",
        f"Último registro presidencial do diário: {hora(ultimo_pres['momento'])}, "
        f"estado `{ultimo_pres['estado_publicacao']}`, "
        f"{br(ultimo_pres['secoes_totalizadas'])} de {br(ultimo_pres['secoes_total'])} "
        f"seções ({br(ultimo_pres['secoes_total'] - ultimo_pres['secoes_totalizadas'])} "
        f"pendentes, {br(ultimo_pres['eleitorado_total'] - ultimo_pres['eleitorado_apurado'])} "
        "eleitores).",
        "",
        "Participação sobre os votos a concorrentes:",
        "",
        "| candidato | votos no diário | votos no TSE | % diário | % TSE | diferença (pp) |",
        "|---|---|---|---|---|---|",
    ]
    for x in linhas:
        ref_md.append(
            f"| {x['nome']} | {br(x['votos_diario'])} | "
            f"{br(x['votos_tse']) if x['votos_tse'] is not None else '—'} | "
            f"{x['pct_diario']:.4f} | "
            f"{x['pct_tse']:.4f} | {x['dif_pp']:+.4f} |" if x["pct_tse"] is not None else
            f"| {x['nome']} | {br(x['votos_diario'])} | — | {x['pct_diario']:.4f} | — | — |")
    ref_md += ["", f"Maior diferença: {comp['maior_diferenca_pp']:.4f} pp."]
else:
    comp = None
    ref_md += [f"Arquivo do TSE ausente. Baixe `{URL_TSE_BR}` para "
               f"`referencia_final/{TSE_BR.name}` e rode de novo."]
(OUT / "referencia_final" / "LEIA.md").write_text("\n".join(ref_md) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- universo
unidades = collections.defaultdict(set)
for p in sorted(MANIFESTO_RAW.glob("*.jsonl")):
    with open(p, encoding="utf-8") as f:
        for linha in f:
            r = json.loads(linha)
            if r["kind"] == "ea20" and r.get("mun"):
                unidades[r["cargo"]].add((r["uf"], r["mun"]))
exterior = {u for u in unidades.get("1", set()) if u[0].lower() == "zz"}
con = sqlite3.connect(f"file:{BANCO.as_posix()}?mode=ro", uri=True)
tabelas = {t: con.execute(f"select count(*) from {t}").fetchone()[0]
           for t in ("electorate", "geography", "source_snapshot", "unit_totals")}
con.close()
universo = {"unidades_com_ea20": {k: len(v) for k, v in sorted(unidades.items())},
            "presidente_exterior": len(exterior),
            "presidente_municipios": len(unidades.get("1", set())) - len(exterior),
            "linhas_no_banco": tabelas}
(OUT / "universo.json").write_text(
    json.dumps(universo, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "NOTA-universo.md").write_text(f"""# De onde veio o universo de unidades

O universo esperado de cada disputa — quais municípios existem e quantos
eleitores cada um tem — veio do próprio EA20 do TSE, capturado na noite de
04/10/2026. O cadastro de eleitorado do SIG Eleição, baixado antes, **não
entrou na rota de produção**: a tabela `electorate` do banco oficial tem
{br(tabelas['electorate'])} linhas.

Unidades com EA20 municipal no manifesto das capturas:

| cargo | unidades |
|---|---|
| Presidente (1) | {br(len(unidades.get('1', ())))} — {br(universo['presidente_municipios'])} municípios e {br(len(exterior))} localidades do exterior |
| Governador (3) | {br(len(unidades.get('3', ())))} |
| Senador (5) | {br(len(unidades.get('5', ())))} |

Consequências para a leitura do artigo:

- "percentual do eleitorado apurado" é medido contra o eleitorado que o TSE
  informa em cada arquivo (`te`), não contra um cadastro externo;
- nenhuma unidade do universo ficou sem captura;
- uma eventual diferença entre o cadastro do SIG e o `te` do EA20 não foi
  medida e não afeta as métricas, que usam só o EA20.

Números em `universo.json`.
""", encoding="utf-8")

# --------------------------------------------------------------- manifesto
h_m3 = next(iter(hashes))
(OUT / "MANIFESTO.md").write_text(f"""# Pacote de reprodução — 1º turno de 2026

Gerado em {datetime.now(TZ):%d/%m/%Y %H:%M} por `scripts/artigo/apendice_b.py`,
no commit `{ambiente['commit']}` de
https://github.com/marcelomicucci/observatorio2026.

## diario/

`{DIARIO.name}` — o diário de previsões como foi gravado na noite, sem
recálculo. Arquivo só de leitura.

- {br(n)} registros, {len(disputas)} disputas
- de {hora(primeiro)} a {hora(ultimo)} (horário de Brasília)
- modelo: {', '.join(f'{k}: {br(v)}' for k, v in modelos.items())}
- simulações por registro: {', '.join(f'{k}: {br(v)}' for k, v in sorted(sims.items()))}
- `snapshot_set_id` nulo em {br(sem_snapshot)} registros
- SHA256: `{resumo_diario['sha256']}`

## capturas/

`manifesto-capturas.json` — hash, contagem por tipo e por status de cada
manifesto diário das capturas do TSE. As capturas em si
({br(arquivos)} arquivos, {em_disco / 1e9:.2f} GB em disco,
{sum(c['bytes_de_conteudo'] for c in capturas) / 1e9:.2f} GB de conteúdo)
ficam em `data/raw/oficial2026/` e não são copiadas para cá.

## ambiente/

`pip-freeze.txt` e `ambiente.json` — versões exatas do ambiente em que o
pacote foi gerado (Python {platform.python_version()}, NumPy {numpy.__version__}).

## config/

A configuração vigente e todas as cópias anteriores gravadas pelo painel.
`correspondencia-limiares.json` liga o `thresholds_hash` de cada registro do
diário ao arquivo de limiares que ele cita: os {br(n)} registros citam
`{h_m3[:16]}…`, que é `{por_hash.get(h_m3)}` — o arquivo
de limiares como estava durante o 1º turno, com a homologação do M3, guardado
pelo painel no instante em que o M3.3 foi homologado.

## referencia_final/

O total presidencial do TSE, baixado de
`{URL_TSE_BR}`,
contra o último registro presidencial do diário. Ver `LEIA.md`.

## NOTA-universo.md

De onde veio o universo de unidades e por que o cadastro do SIG não foi usado.

## SHA256SUMS.txt

Hash de todos os arquivos deste pacote.
""", encoding="utf-8")

somas = []
for p in sorted(OUT.rglob("*")):
    if p.is_file() and p.name != "SHA256SUMS.txt":
        somas.append(f"{sha(p)}  {p.relative_to(OUT).as_posix()}")
(OUT / "SHA256SUMS.txt").write_text("\n".join(somas) + "\n", encoding="utf-8")

print(f"pacote em {OUT}")
print(f"diário: {br(n)} registros, sha256 {resumo_diario['sha256']}")
print(f"limiares citados pelo diário: {por_hash.get(h_m3)}")
if comp:
    print(f"presidente, maior diferença diário × TSE: {comp['maior_diferenca_pp']:.4f} pp")
print(f"{len(somas)} arquivos com hash")
