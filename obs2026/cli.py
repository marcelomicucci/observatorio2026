"""Interface de linha de comando do Observatório.

Comandos de operação:

    obs2026 descobrir       lê EA11/EA12 e imprime o catálogo real
    obs2026 orcamento       calcula o custo de requisições de cada estratégia
    obs2026 ensaio          ensaio completo contra o ambiente, com invariantes
    obs2026 coletar         coleta contínua (é o que roda no simulado)
    obs2026 auditar-csv     audita a base de eleitorado recebida
    obs2026 prever          projeta uma disputa e REGISTRA o que foi dito
    obs2026 previsoes       lista o historico de projecoes
    obs2026 auditar-previsoes  confronta as projecoes com o resultado final
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import (DATA_DIR, load_collector, load_environment,
                     load_thresholds)
from .storage.raw import RawArchive
from .tse import catalog as catalog_mod
from .tse.client import IpLock, TseClient
from .tse.collector import Collector
from .tse.parsers import ea11 as ea11_parser
from .tse.parsers import ea12 as ea12_parser
from .tse.urls import UrlBuilder
from .quality.checks import CRITICAL, check_ea20, summarize

app = typer.Typer(add_completion=False, help="Observatório Eleitoral 2026")
con = Console()

ENV_OPT = typer.Option("simulado2026", "--env", "-e", help="ambiente")


def br(n) -> str:
    """Milhar com ponto, como se escreve em portugues."""
    return f"{n:,}".replace(",", ".")


def _client(env, cfg) -> TseClient:
    return TseClient(
        env.allowed_hosts, max_rps=cfg.max_rps, burst=cfg.burst,
        jitter_ms=cfg.jitter_ms, connect_timeout_s=cfg.connect_timeout_s,
        read_timeout_s=cfg.read_timeout_s, max_attempts=cfg.max_attempts,
        backoff_base_s=cfg.backoff_base_s, backoff_max_s=cfg.backoff_max_s,
        retry_on_status=cfg.retry_on_status,
        not_found_suspend_s=cfg.not_found_suspend_s,
        error_streak_trip=cfg.error_streak_trip,
        error_streak_cooldown_s=cfg.error_streak_cooldown_s,
        blocked_cooldown_s=cfg.blocked_cooldown_s,
    )


async def _discover(env, cfg, client: TseClient, archive: RawArchive):
    """EA11 -> eleições -> EA12 de cada eleição -> catálogo."""
    urls = UrlBuilder(env.base, env.ambiente, env.ciclo, env.allowed_hosts)

    t = urls.ea11()
    f = await client.fetch(t.url, key=t.key)
    if not f.ok:
        raise RuntimeError(f"EA11 indisponivel ({f.status}) em {t.url}")
    payload = json.loads(f.body.decode("utf-8"))
    ea11 = ea11_parser.parse(payload)
    archive.store(key=t.key, kind="ea11", url=t.url, status=f.status,
                  body=f.body, sha256=f.sha256, captured_at=f.captured_at,
                  source_generated_at=ea11.generated_at, idg=ea11.idg,
                  etag=f.etag, last_modified=f.last_modified,
                  parser_version=f.parser_version)

    # O ciclo vem do catálogo, não do seed.
    ciclos = {p.ciclo for p in ea11.pleitos}
    if len(ciclos) == 1 and (c := ciclos.pop()) != env.ciclo:
        con.print(f"[yellow]ciclo do catalogo ({c}) difere do seed "
                  f"({env.ciclo}); usando o catalogo[/]")
        urls = UrlBuilder(env.base, env.ambiente, c, env.allowed_hosts)

    ea12s = {}
    alvo = {c.cd for c in env.cargos.values()}
    for p in ea11.pleitos:
        for e in p.eleicoes:
            if not any(e.tem_cargo(cd) for cd in alvo):
                continue
            t12 = urls.ea12(e.cd)
            f12 = await client.fetch(t12.url, key=t12.key)
            if not f12.ok:
                con.print(f"[red]EA12 de {e.cd} indisponivel ({f12.status})[/]")
                continue
            pl = json.loads(f12.body.decode("utf-8"))
            ea12 = ea12_parser.parse(pl, e.cd)
            ea12s[e.cd] = ea12
            archive.store(key=t12.key, kind="ea12", url=t12.url,
                          status=f12.status, body=f12.body, sha256=f12.sha256,
                          captured_at=f12.captured_at,
                          source_generated_at=ea12.generated_at, idg=ea12.idg,
                          etag=f12.etag, last_modified=f12.last_modified,
                          parser_version=f12.parser_version, election=e.cd)

    return catalog_mod.build(env, urls, ea11, ea12s)


@app.command("descobrir")
def descobrir(env_name: str = ENV_OPT):
    """Lê o catálogo oficial e mostra o que existe, sem inventar nada."""
    env, cfg = load_environment(env_name), load_collector()
    archive = RawArchive(DATA_DIR / "raw", env.name)

    async def run():
        client = _client(env, cfg)
        try:
            return await _discover(env, cfg, client, archive)
        finally:
            await client.aclose()

    cat = asyncio.run(run())

    con.print(f"\n[bold]{env.label}[/]")
    con.print(f"fase do catalogo: [bold]{cat.ea11.fase}[/] "
              f"('s'=simulado, 'f'=oficial)  ·  "
              f"gerado em {cat.ea11.generated_at}")

    t = Table("pleito", "eleicao", "turno", "2º turno", "nome")
    for p in cat.ea11.pleitos:
        for e in p.eleicoes:
            t.add_row(p.cd, e.cd, e.turno, e.cd_turno2 or "—", e.nome[:52])
    con.print(t)

    t = Table("disputa", "cargo", "eleicao", "UFs", "municípios", "exterior")
    for c in cat.contests:
        t.add_row(c.cargo.key, c.cargo_nome, c.election, str(len(c.ufs)),
                  br(len(c.municipios)),
                  "sim" if c.cargo.includes_exterior else "não")
    con.print(t)

    for ea12 in cat.ea12_por_eleicao.values():
        com_ibge = sum(1 for m in ea12.municipios if m.cd_ibge)
        con.print(f"EA12 eleicao {ea12.election}: "
                  f"{br(len(ea12.municipios))} localidades, "
                  f"{br(com_ibge)} com codigo IBGE, "
                  f"{br(len(ea12.zonas_unicas))} zonas distintas")
        break

    if cat.quality_flags:
        con.print("\n[yellow]sinalizacoes de qualidade:[/]")
        for q in cat.quality_flags:
            con.print(f"  · {q}")
    else:
        con.print("\n[green]catalogo sem divergencias[/]")


@app.command("orcamento")
def orcamento(env_name: str = ENV_OPT):
    """Compara o custo de varrer tudo com o de detectar e buscar."""
    env, cfg = load_environment(env_name), load_collector()
    archive = RawArchive(DATA_DIR / "raw", env.name)

    async def run():
        client = _client(env, cfg)
        try:
            return await _discover(env, cfg, client, archive)
        finally:
            await client.aclose()

    cat = asyncio.run(run())

    control = len(cat.control_targets())
    acc = len(cat.accompaniment_targets())
    full = len(cat.result_targets())
    rps = cfg.max_rps

    t = Table("estratégia", "requisições", "segundos a %.0f req/s" % rps,
              "req/s na cadência", title="Orçamento de requisições")
    t.add_row("varredura municipal completa", br(full), br(round(full/rps)),
              f"{full/cfg.reconcile_interval_s:.1f} (a cada "
              f"{cfg.reconcile_interval_s:.0f}s)")
    t.add_row("acompanhamento EA14+EA15", br(acc), f"{acc/rps:.1f}",
              f"{acc/cfg.accompaniment_interval_s:.1f} (a cada "
              f"{cfg.accompaniment_interval_s:.0f}s)")
    t.add_row("controle EA20 br+UF", br(control), f"{control/rps:.1f}",
              f"{control/cfg.control_interval_s:.1f} (a cada "
              f"{cfg.control_interval_s:.0f}s)")
    con.print(t)

    b = cfg.budget_floor_rps(n_accompaniment=acc, n_control=control,
                             n_municipal=full)
    cor = "green" if b["cabe"] else "red"
    con.print(
        f"\npiso de requisições em regime: [bold]{b['piso_rps']:.1f} req/s[/] "
        f"(detecção + controle + reconciliação), "
        f"restando [{cor}][bold]{b['folga_rps']:.1f} req/s[/][/] do teto "
        f"interno de {rps:.0f} para o EA20 dos municípios que mudarem."
    )
    if not b["cabe"]:
        con.print("[red]A cadência configurada NÃO cabe no teto. "
                  "O coletor vai recusar iniciar.[/]")
    con.print(
        f"limite do TSE: 100 req/s por IP. Teto interno em "
        f"{100*rps/100:.0f}% disso, por segurança — o limite é por IP, "
        "não por processo."
    )
    con.print(
        f"\n[dim]Uma varredura completa leva {full/rps/60:.1f} min. "
        "Por isso ela é rede de segurança periódica, não o mecanismo "
        "principal de atualização.[/]"
    )


@app.command("ensaio")
def ensaio(env_name: str = ENV_OPT,
           amostra: int = typer.Option(40, help="municípios amostrados"),
           segundos: float = typer.Option(0, help="se >0, roda o coletor")):
    """Ensaio ponta a ponta: descobre, coleta amostra e roda as invariantes.

    É este comando que deve rodar nos simulados de 22, 23 e 24/09 antes de
    ligar a coleta contínua.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    env, cfg = load_environment(env_name), load_collector()
    thr = load_thresholds()
    archive = RawArchive(DATA_DIR / "raw", env.name)
    findings_all = []
    seen = {"results": 0, "units": set()}

    def on_result(r, ref, target):
        seen["results"] += 1
        seen["units"].add(f"{r.election}:{r.cargo.cd}:{r.unit_key}")
        cc = env.cargo_by_cd(r.cargo.cd)
        findings_all.extend(check_ea20(
            r, expected_seats=cc.expected_seats if cc else None,
            official_env=env.official,
        ))

    async def run():
        client = _client(env, cfg)
        try:
            cat = await _discover(env, cfg, client, archive)
            col = Collector(cat, client, archive, cfg, on_result=on_result)
            await col.cycle_control()
            await col.drain(len(cat.control_targets()))
            if amostra > 0:
                for c in cat.contests:
                    for m in c.municipios[:amostra]:
                        col.queue.push(
                            cat.urls.ea20_mun(c.election, c.cargo.cd,
                                              m.uf, m.cd),
                            1.0, reason="amostra")
                # Sem teto arbitrario: a amostra pedida tem de ser coletada
                # inteira. Um teto fixo de 10.000 truncava a varredura
                # completa (16.897 arquivos) sem avisar.
                while len(col.queue):
                    await col.drain(cfg.max_inflight_results)
            if segundos > 0:
                await col.run(max_seconds=segundos)
            return cat, col.stats.as_dict(client, col.queue)
        finally:
            await client.aclose()

    with IpLock(str(env.lock_path)):
        cat, stats = asyncio.run(run())

    con.print(f"\n[bold]Ensaio — {env.label}[/]")
    con.print(f"arquivos EA20 lidos: {seen['results']}  ·  "
              f"unidades distintas: {len(seen['units'])}")
    con.print(f"http: {json.dumps(stats['http'], ensure_ascii=False)}")
    con.print(f"req/s efetivo: {stats['effective_rps']}")

    s = summarize(findings_all)
    t = Table("severidade", "ocorrências")
    for k, v in s["counts"].items():
        t.add_row(k, str(v))
    con.print(t)

    if s["codes"]:
        con.print("\ncódigos observados:")
        shown = set()
        for f in findings_all:
            if f.code in shown:
                continue
            shown.add(f.code)
            color = {"critical": "red", "warning": "yellow"}.get(
                f.severity, "dim")
            con.print(f"  [{color}]{f.severity:8}[/] {f.code}: {f.message}")
            con.print(f"           {f.unit}  {f.observed}")

    if s["blocks_forecast"]:
        con.print("\n[red]Há divergência contábil crítica: "
                  "a projeção ficaria suspensa.[/]")
    else:
        con.print("\n[green]Nenhuma divergência contábil crítica.[/]")

    if not thr.homologated:
        con.print("[yellow]thresholds.yaml não homologado: o produto "
                  "operaria em 'cenário exploratório', sem probabilidades.[/]")


@app.command("coletar")
def coletar(env_name: str = ENV_OPT,
            segundos: float = typer.Option(0, help="0 = sem limite")):
    """Coleta contínua com detecção de mudança."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    env, cfg = load_environment(env_name), load_collector()
    archive = RawArchive(DATA_DIR / "raw", env.name)
    counters = {"results": 0, "critical": 0}

    def on_result(r, ref, target):
        counters["results"] += 1
        cc = env.cargo_by_cd(r.cargo.cd)
        fs = check_ea20(r, expected_seats=cc.expected_seats if cc else None,
                        official_env=env.official)
        for f in fs:
            if f.severity == CRITICAL:
                counters["critical"] += 1
                logging.error("CRITICO %s %s %s", f.code, f.unit, f.observed)

    async def run():
        client = _client(env, cfg)
        try:
            cat = await _discover(env, cfg, client, archive)
            # Recusa iniciar se a cadência configurada estoura o teto interno.
            b = cfg.assert_budget(
                n_accompaniment=len(cat.accompaniment_targets()),
                n_control=len(cat.control_targets()),
                n_municipal=len(cat.result_targets()),
            )
            con.print(f"catalogo: {len(cat.contests)} disputas, "
                      f"{br(len(cat.result_targets()))} arquivos municipais")
            con.print(f"orcamento: piso {b['piso_rps']:.1f} req/s, "
                      f"folga {b['folga_rps']:.1f} req/s")
            col = Collector(cat, client, archive, cfg, on_result=on_result)
            return await col.run(max_seconds=segundos or None)
        finally:
            await client.aclose()

    with IpLock(str(env.lock_path)):
        stats = asyncio.run(run())
    con.print(json.dumps({**stats, **counters}, ensure_ascii=False, indent=2))


@app.command("auditar-csv")
def auditar_csv(
    caminho: Path = typer.Argument(..., help="eleitorado_*.csv.zip"),
    env_name: str = ENV_OPT,
):
    """Audita e concilia a base de eleitorado recebida contra o EA12."""
    from .ingest.electorate_csv import audit_and_reconcile
    env = load_environment(env_name)
    archive = RawArchive(DATA_DIR / "raw", env.name)
    rep = audit_and_reconcile(caminho, archive, env)
    con.print(rep.render())



@app.command("carregar")
def carregar(env_name: str = ENV_OPT,
             recriar: bool = typer.Option(False, help="apaga e recria o banco")):
    """Carrega o arquivo bruto no banco. Reexecutável sem duplicar."""
    import asyncio as _a
    from .storage.db import create_all, drop_all, make_engine, make_session_factory
    from .storage.ingest import ingest_ea20, sync_catalog
    from .tse.parsers import ea20 as ea20_parser

    env, cfg = load_environment(env_name), load_collector()
    archive = RawArchive(DATA_DIR / "raw", env.name)
    eng = make_engine(sqlite_path=env.db_path)
    if recriar:
        drop_all(eng)
    create_all(eng)
    Sess = make_session_factory(eng)

    async def run():
        client = _client(env, cfg)
        try:
            return await _discover(env, cfg, client, archive)
        finally:
            await client.aclose()

    cat = _a.run(run())
    with Sess() as s:
        cstats = sync_catalog(s, cat)
        con.print(f"catalogo: {cstats}")

        tot = {"snapshot": 0, "candidates": 0, "results": 0}
        flags: dict[str, int] = {}
        n = 0
        for day in archive.days():
            for ref in archive.manifest(day):
                if ref.kind != "ea20" or not ref.path:
                    continue
                try:
                    r = ea20_parser.parse(archive.load(ref))
                except Exception as exc:       # noqa: BLE001
                    flags[f"parse:{type(exc).__name__}"] = \
                        flags.get(f"parse:{type(exc).__name__}", 0) + 1
                    continue
                out = ingest_ea20(s, env, ref, r)
                for k in tot:
                    tot[k] += out[k]
                for f in out["flags"]:
                    flags[f] = flags.get(f, 0) + 1
                n += 1
                if n % 200 == 0:
                    s.commit()
        s.commit()

    con.print(f"EA20 processados: {br(n)}")
    con.print(f"gravados: {tot}")
    if flags:
        con.print("[yellow]sinalizacoes:[/]")
        for k, v in sorted(flags.items(), key=lambda x: -x[1]):
            con.print(f"  {v:>6}x  {k}")
    else:
        con.print("[green]sem sinalizacoes[/]")
    con.print(f"banco: {env.db_path}")

from .cli_prever import (registrar as _registrar_previsao,  # noqa: E402
                         registrar_ensaio as _registrar_ensaio)

_registrar_previsao(app, ENV_OPT)
_registrar_ensaio(app, ENV_OPT)


if __name__ == "__main__":
    app()
