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
    obs2026 preparar        baixa perfis, fotos, malha e Senado para rodar sem rede
"""

from __future__ import annotations

import asyncio
import dataclasses
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

#: Padrão desde 04/10/2026: o oficial. Com o simulado como padrão, um
#: `servir` sem `--env` no dia da eleição pôs no ar os dados de teste de
#: setembro. O simulado do TSE já responde 403; quem quiser o banco antigo
#: pede `--env simulado2026`. Desde 24/10/2026 o padrão é o 2º turno; o
#: banco do 1º turno é `--env oficial2026`.
ENV_OPT = typer.Option("oficial2026t2", "--env", "-e", help="ambiente")


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
    if env.pleitos:
        # O arquivo bruto guarda o catálogo inteiro; daqui em diante só
        # existem os pleitos do ambiente (o 2º turno é pleito à parte).
        ea11 = dataclasses.replace(ea11, pleitos=tuple(
            p for p in ea11.pleitos if env.aceita_pleito(p.cd)))
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
        # O catálogo de produção traz pleitos de outro ciclo (suplementares
        # de ele2024, uma delas de Governador): pedir o EA12 delas na árvore
        # de ele2026 é 404 certo, e 404 em série custa bloqueio de IP.
        if p.ciclo != urls.ciclo:
            continue
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
              f"('s'=simulado, 'o'=oficial)  ·  "
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
            segundos: float = typer.Option(0, help="0 = sem limite"),
            projetar: bool = typer.Option(
                None, help="registra projecoes periodicamente "
                           "(padrao: config/collector.yaml)"),
            pular_finalizados: bool = typer.Option(
                None, "--pular-finalizados/--buscar-finalizados",
                help="nao busca de novo o que o TSE ja finalizou; segue "
                     "vigiando pelo EA14/EA15 (padrao: config)")):
    """Coleta contínua, registrando projeções ao longo da apuração.

    A cada `projecao.intervalo_s` o sistema projeta todas as disputas e grava
    no diário de auditoria. São esses registros que dão pontos ao gráfico de
    convergência: sem eles, o gráfico tem um ponto só e não prova nada.

    A simulação roda em thread separada e nunca bloqueia a coleta; falha nela
    é registrada e a apuração segue.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    env, cfg = load_environment(env_name), load_collector()
    if env.demonstracao:
        con.print(f"[red]{env.name} é ambiente de demonstração, montado de "
                  "arquivo histórico: não há o que coletar no TSE.[/]")
        raise typer.Exit(1)
    thr = load_thresholds()
    archive = RawArchive(DATA_DIR / "raw", env.name)
    counters = {"results": 0, "critical": 0}
    quer_projetar = cfg.projecao_ativa if projetar is None else projetar

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
                proporcional_rps=sum(1.0 / iv for _, iv, _ in
                                     cat.proporcional_targets()),
            )
            n_prop = len(cat.proporcional_targets())
            if n_prop:
                con.print(f"deputado federal: {n_prop} arquivos de UF, so o "
                          "resultado (sem projecao, sem municipios)")
            con.print(f"catalogo: {len(cat.contests)} disputas, "
                      f"{br(len(cat.result_targets()))} arquivos municipais")
            con.print(f"orcamento: piso {b['piso_rps']:.1f} req/s, "
                      f"folga {b['folga_rps']:.1f} req/s")
            col = Collector(cat, client, archive, cfg, on_result=on_result)

            # Retoma do que o arquivo bruto já sabe. Sem isto, um coletor
            # reiniciado no meio da noite trataria os 16.897 arquivos como
            # novos e passaria uns 14 minutos baixando de novo o que já tem,
            # cego para as mudanças reais nesse intervalo.
            if pular_finalizados is not None:
                col.pular_finalizados = pular_finalizados
                col.pular_fixo = True
            else:
                col.aplicar_operacao()
            rest = await asyncio.to_thread(col.restaurar_do_arquivo)
            if rest["etags"]:
                con.print(f"retomada: {br(rest['etags'])} fontes com ETag, "
                          f"{br(rest['tokens'])} municipios ja em dia")
            if col.pular_finalizados:
                con.print(f"finalizados: {br(rest['finalizados'])} arquivos "
                          f"fora das buscas (vigiados pelo EA14/EA15)")
            else:
                con.print("[yellow]pular finalizados desligado: busca "
                          "tudo, sempre[/]")

            # Laços auxiliares. O da coleta manda: quando ele termina, os
            # outros são encerrados.
            auxiliares: list[tuple[str, object, asyncio.Task]] = []

            if cfg.carga_ativa:
                from .storage.carga import CargaContinua
                carga = CargaContinua(env, archive, cfg.carga_intervalo_s)
                await asyncio.to_thread(carga.sincronizar_catalogo, cat)
                con.print(f"banco: carga continua a cada "
                          f"{cfg.carga_intervalo_s:.0f}s, Brasil e UF antes "
                          f"dos municipios")
                auxiliares.append(("carga", carga,
                                   asyncio.create_task(carga.rodar())))
            else:
                con.print("[yellow]carga continua desligada: o painel so "
                          "muda com `carregar` a mao[/]")

            if quer_projetar:
                from .engine import audit as _audit
                from .engine.registro_continuo import RegistradorContinuo
                reg = RegistradorContinuo(
                    env=env, thr=thr, archive=archive,
                    diario=_audit.Diario(DATA_DIR / "previsoes", env.name),
                    intervalo_s=cfg.projecao_intervalo_s,
                    n_sims=cfg.projecao_n_sims,
                )
                con.print(f"projecoes: a cada "
                          f"{cfg.projecao_intervalo_s:.0f}s, "
                          f"{cfg.projecao_n_sims} simulacoes, em thread "
                          f"separada")
                auxiliares.append(("projecao", reg,
                                   asyncio.create_task(reg.rodar())))
            else:
                con.print("[yellow]registro de projecoes desligado: o "
                          "grafico de convergencia ficara sem pontos[/]")

            extras: dict = {}
            try:
                stats_coleta = await col.run(max_seconds=segundos or None)
            finally:
                for nome, laco, tarefa in auxiliares:
                    laco.parar()
                    try:
                        await asyncio.wait_for(tarefa, timeout=60)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        tarefa.cancel()
                    st = getattr(laco, "stats")
                    extras[nome] = st if isinstance(st, dict) else st.__dict__
            return stats_coleta, extras
        finally:
            await client.aclose()

    with IpLock(str(env.lock_path)):
        stats, extras = asyncio.run(run())
    saida = {**stats, **counters, **extras}
    con.print(json.dumps(saida, ensure_ascii=False, indent=2))


@app.command("servir")
def servir(env_name: str = ENV_OPT,
           porta: int = typer.Option(8026, "--porta", "-p")):
    """Sobe o painel e o telão para um ambiente.

    `--env demo2022 --porta 8027` serve a demonstração ao lado do simulado;
    na noite, `--env oficial2026`."""
    import os
    import socket
    import uvicorn
    env = load_environment(env_name)
    with socket.socket() as sk:
        if sk.connect_ex(("127.0.0.1", porta)) == 0:
            con.print(f"[red]A porta {porta} já está em uso — provavelmente "
                      f"um servidor do Observatório já está no ar. Abra "
                      f"http://localhost:{porta} ou feche o outro antes.[/]")
            raise typer.Exit(1)
    os.environ["OBS2026_ENV"] = env.name
    con.print(f"servindo {env.label} em http://localhost:{porta}")
    uvicorn.run("obs2026.api.app:app", host="127.0.0.1", port=porta,
                log_level="warning")


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
             recriar: bool = typer.Option(False, help="apaga e recria o banco"),
             completa: bool = typer.Option(
                 False, help="relê o arquivo bruto inteiro, não só o novo")):
    """Carrega no banco o que chegou ao arquivo bruto desde a última carga.

    Incremental: guarda no banco até onde cada dia do manifesto já foi lido
    e retoma dali. A primeira carga lê tudo; as seguintes, só o novo — o
    custo passa a acompanhar o que mudou, não o tamanho da noite.
    Reexecutável sem duplicar, como antes.
    """
    import asyncio as _a
    from .storage.db import create_all, drop_all, make_engine, make_session_factory
    from .storage.ingest import sync_catalog

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

        from .storage.carga import carregar_novos
        tot = carregar_novos(s, env, archive, completa=completa)

    flags = tot.pop("flags")
    con.print(f"linhas novas no manifesto: {br(tot['linhas'])}"
              + ("" if tot["linhas"] else "  (nada novo desde a última carga)"))
    con.print(f"EA20 processados: {br(tot['ea20'])} "
              f"({br(tot['grandes'])} de Brasil e UF, gravados primeiro)")
    con.print(f"gravados: snapshot={tot['snapshot']} "
              f"candidatos={tot['candidates']} resultados={tot['results']}")
    if flags:
        con.print("[yellow]sinalizacoes:[/]")
        for k, v in sorted(flags.items(), key=lambda x: -x[1]):
            con.print(f"  {v:>6}x  {k}")
    else:
        con.print("[green]sem sinalizacoes[/]")
    con.print(f"banco: {env.db_path}")

from .cli_prever import (registrar as _registrar_previsao,  # noqa: E402
                         registrar_ensaio as _registrar_ensaio,
                         registrar_preparo as _registrar_preparo)
from .cli_compartilhar import registrar as _registrar_compartilhar  # noqa: E402
from .cli_politico import registrar as _registrar_politico  # noqa: E402

_registrar_previsao(app, ENV_OPT)
_registrar_ensaio(app, ENV_OPT)
_registrar_preparo(app, ENV_OPT)
_registrar_compartilhar(app, ENV_OPT)
_registrar_politico(app)


if __name__ == "__main__":
    app()
