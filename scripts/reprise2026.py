"""Reprise da noite do 1º turno de 2026, com a apuração em andamento.

    python scripts/reprise2026.py                    # 20h15→01h00 UTC, 8×
    python scripts/reprise2026.py --fator 12
    python scripts/reprise2026.py --so-preparar

Para que serve: depois de 29/09 o TSE não oferece mais simulado (conferido
em 07/10/2026), e há coisas que só se provam com os votos ENTRANDO — a
frase "ainda pode virar" do placar, o gráfico "ao longo da apuração"
crescendo, a tabela da série sendo alimentada a cada projeção registrada, o
selo de vencedor projetado, a faixa mudando de nível.

Como funciona: o arquivo bruto de `oficial2026` guarda cada retrato de cada
município como o TSE o publicou, com a hora da captura. A reprise tem um
ambiente próprio, `reprise2026`, com banco, manifesto, diário e tabela da
série separados, e faz o papel do coletor: de tempos em tempos acrescenta ao
manifesto dela as capturas feitas até o "agora" simulado, na ordem real, e
então roda as mesmas duas peças que o coletor roda na noite —
`carregar_novos` (bruto → banco) e `RegistradorContinuo._rodada` (as 55
projeções, registradas no diário). O servidor, noutra janela, serve o
ambiente como se fosse ao vivo:

    python -m obs2026.cli servir --env reprise2026 --porta 8032

O relógio é acelerado (`--fator`): a noite de 4h45 cabe em ~36 min a 8×.
Cada passo dura o que as 55 projeções levarem, então a reprise tem menos
rodadas que a noite real (que registrava a cada ~2 min); o que se vê em cada
rodada é exatamente o que o coletor tinha naquele instante.

O que NÃO é tocado: banco, manifesto e diário de `oficial2026`. Os arquivos
dos retratos não são copiados — as pastas dos dias em `data/raw/reprise2026`
são junções para as do oficial, só de leitura. Por isso a limpeza daqui
remove junções com `os.rmdir` e NUNCA apaga pasta recursivamente.

Limitações: sem a latência da rede nem o orçamento de requisições do
coletor; deputado federal e estadual entram no bruto e são lidos de lá como
na noite. É UMA eleição, de 1º turno — o 2º turno terá dois candidatos e
outra dinâmica.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import (DATA_DIR, load_collector,       # noqa: E402
                            load_environment, load_thresholds)
from obs2026.engine import audit                             # noqa: E402
from obs2026.engine.registro_continuo import RegistradorContinuo  # noqa: E402
from obs2026.storage.carga import carregar_novos             # noqa: E402
from obs2026.storage.db import (create_all, make_engine,     # noqa: E402
                                make_session_factory)
from obs2026.storage.raw import RawArchive                   # noqa: E402

ORIGEM = "oficial2026"
DESTINO = "reprise2026"
SAIDA = DATA_DIR / "exports" / DESTINO
#: Tabelas com o que foi OBSERVADO na noite: saem do banco copiado. O
#: catálogo (eleição, disputa, geografia, candidatos) fica.
OBSERVADO = ("observed_result", "unit_totals", "source_snapshot",
             "carga_manifesto", "carga_pendente", "forecast_result",
             "forecast_run", "audit_event")
COM_AMBIENTE = ("election", "geography")


def log(*a) -> None:
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def preparar() -> None:
    """Zera o ambiente da reprise: banco, manifesto, diário e série."""
    env_o, env_d = load_environment(ORIGEM), load_environment(DESTINO)
    raiz_o = DATA_DIR / "raw" / ORIGEM
    raiz_d = DATA_DIR / "raw" / DESTINO
    (raiz_d / "manifest").mkdir(parents=True, exist_ok=True)
    for p in (raiz_d / "manifest").glob("*.jsonl"):
        p.unlink()
    # Junções para as pastas dos retratos. `os.rmdir` desfaz a junção sem
    # tocar no destino; `shutil.rmtree` apagaria os arquivos do oficial.
    import _winapi
    for dia in sorted(p.name for p in raiz_o.iterdir()
                      if p.is_dir() and p.name != "manifest"):
        alvo = raiz_d / dia
        if not alvo.exists():
            _winapi.CreateJunction(str(raiz_o / dia), str(alvo))

    # Banco: cópia do oficial pelo mecanismo de backup do SQLite (lê o
    # oficial sem travá-lo), trocando o ambiente e tirando o observado.
    for suf in ("", "-wal", "-shm"):
        p = Path(str(env_d.db_path) + suf)
        if p.exists():
            p.unlink()
    orig = sqlite3.connect(f"file:{env_o.db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(env_d.db_path)
    orig.backup(dest)
    orig.close()
    for t in OBSERVADO:
        dest.execute(f"delete from {t}")
    for t in COM_AMBIENTE:
        dest.execute(f"update {t} set environment = ?", (DESTINO,))
    dest.commit()
    dest.close()

    prev = DATA_DIR / "previsoes"
    for nome in (f"previsoes-{DESTINO}.jsonl", f"conjuntos-{DESTINO}.jsonl",
                 f"serie-{DESTINO}.sqlite"):
        if (prev / nome).exists():
            (prev / nome).unlink()
    SAIDA.mkdir(parents=True, exist_ok=True)
    log("ambiente da reprise zerado:", env_d.db_path.name)


def ler_capturas(ate: str) -> list[tuple[str, str, bytes]]:
    """[(captured_at, dia, linha)] das capturas COM arquivo, em ordem.

    Só as que trouxeram conteúdo: os 304 ("nada mudou") são a maior parte
    do manifesto e não carregam nada. A linha é reescrita com o ambiente da
    reprise; o resto fica como o coletor gravou.
    """
    raiz = DATA_DIR / "raw" / ORIGEM / "manifest"
    saida = []
    for arq in sorted(raiz.glob("*.jsonl")):
        with open(arq, "rb") as fh:
            for bruta in fh:
                if b'"path": null' in bruta or not bruta.strip():
                    continue
                ref = json.loads(bruta)
                if not ref.get("path") or ref["captured_at"] > ate:
                    continue
                ref["environment"] = DESTINO
                saida.append((ref["captured_at"], arq.stem,
                              (json.dumps(ref, ensure_ascii=False)
                               + "\n").encode("utf-8")))
    saida.sort(key=lambda x: x[0])
    return saida


def apurado_br(caminho_db: Path) -> dict:
    """Quanto o banco da reprise diz que está apurado para presidente."""
    con = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
    try:
        r = con.execute(
            "select u.eleitorado_apurado, u.eleitorado_total, u.andamento "
            "from unit_totals u join geography g on g.id = u.geography_id "
            "join contest c on c.id = u.contest_id "
            "join source_snapshot s on s.id = u.snapshot_id "
            "where g.tipo = 'br' and c.cargo_cd = '1' "
            "order by s.captured_at desc limit 1").fetchone()
    except sqlite3.Error:
        r = None
    finally:
        con.close()
    if not r or not r[1]:
        return {"apurado_pct": None, "andamento": None}
    return {"apurado_pct": round(100.0 * r[0] / r[1], 2), "andamento": r[2]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inicio", default="2026-10-04T20:15:00+00:00")
    ap.add_argument("--fim", default="2026-10-05T01:00:00+00:00")
    ap.add_argument("--fator", type=float, default=8.0,
                    help="quantas vezes mais rápido que a noite real")
    ap.add_argument("--passo-min", type=float, default=10.0,
                    help="segundos mínimos entre dois passos")
    ap.add_argument("--so-preparar", action="store_true")
    ap.add_argument("--sem-final", action="store_true",
                    help="não salta para o resultado final ao acabar")
    a = ap.parse_args()

    preparar()
    if a.so_preparar:
        return

    env, cfg = load_environment(DESTINO), load_collector()
    archive = RawArchive(DATA_DIR / "raw", DESTINO)
    eng = make_engine(sqlite_path=env.db_path)
    create_all(eng)
    Sess = make_session_factory(eng)
    reg = RegistradorContinuo(
        env=env, thr=load_thresholds(), archive=archive,
        diario=audit.Diario(DATA_DIR / "previsoes", DESTINO),
        n_sims=cfg.projecao_n_sims)

    log("lendo o manifesto do oficial…")
    capturas = ler_capturas("9999")
    t0 = datetime.fromisoformat(a.inicio)
    t1 = datetime.fromisoformat(a.fim)
    log(f"{len(capturas):,} capturas com arquivo; reprise de {a.inicio[:16]} "
        f"a {a.fim[:16]} UTC, {a.fator:g}× — cerca de "
        f"{(t1 - t0).total_seconds() / a.fator / 60:.0f} min")

    manifestos: dict[str, object] = {}
    i = 0

    def acrescentar(ate_iso: str) -> int:
        nonlocal i
        n = 0
        while i < len(capturas) and capturas[i][0] <= ate_iso:
            _, dia, linha = capturas[i]
            fh = manifestos.get(dia)
            if fh is None:
                fh = open(archive.root / "manifest" / f"{dia}.jsonl", "ab")
                manifestos[dia] = fh
            fh.write(linha)
            i += 1
            n += 1
        for fh in manifestos.values():
            fh.flush()
            os.fsync(fh.fileno())
        return n

    registro = open(SAIDA / "passos.jsonl", "w", encoding="utf-8")

    def passo(simulado: datetime, rotulo: str = "") -> dict:
        p0 = time.monotonic()
        novas = acrescentar(simulado.isoformat())
        with Sess() as s:
            tot = carregar_novos(s, env, archive)
        p1 = time.monotonic()
        rodada = reg._rodada()
        p2 = time.monotonic()
        d = {"real": datetime.now(timezone.utc).isoformat()[:19],
             "simulado_utc": simulado.isoformat()[:19],
             "simulado_brt": (simulado - timedelta(hours=3))
             .strftime("%H:%M"),
             "capturas_novas": novas, "ea20": tot["ea20"],
             "pendentes": tot["pendentes"], "flags": tot["flags"],
             "carga_s": round(p1 - p0, 1),
             "projecoes": rodada["registradas"],
             "projecoes_s": round(p2 - p1, 1),
             "falhas_projecao": reg.stats.falhas,
             **apurado_br(env.db_path), "rotulo": rotulo}
        registro.write(json.dumps(d, ensure_ascii=False) + "\n")
        registro.flush()
        log(f"{d['simulado_brt']} BRT | apurado {d['apurado_pct']}% | "
            f"+{novas} capturas | carga {d['carga_s']}s | "
            f"{d['projecoes']} projeções em {d['projecoes_s']}s"
            + (f" | flags {tot['flags']}" if tot["flags"] else "")
            + (f" | {rotulo}" if rotulo else ""))
        return d

    # Antes do primeiro voto: o catálogo e os arquivos zerados da tarde.
    passo(t0, "antes do primeiro voto")
    partida = time.monotonic()
    while True:
        p0 = time.monotonic()
        simulado = t0 + timedelta(seconds=(p0 - partida) * a.fator)
        if simulado >= t1:
            break
        passo(simulado)
        sobra = a.passo_min - (time.monotonic() - p0)
        if sobra > 0:
            time.sleep(sobra)
    passo(t1, "fim da janela")
    if not a.sem_final:
        # O resto da madrugada e do dia 5 de uma vez: totalização final.
        passo(datetime.fromisoformat("2100-01-01T00:00:00+00:00"),
              "resultado final")
    for fh in manifestos.values():
        fh.close()
    registro.close()
    log("reprise terminada:", json.dumps({
        "rodadas": reg.stats.rodadas, "registros": reg.stats.registros,
        "falhas": reg.stats.falhas,
        "disputas_sem_dado": reg.stats.disputas_sem_dado}))


if __name__ == "__main__":
    main()
