"""Comandos de projeção e auditoria, registrados em `obs2026.cli`."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import DATA_DIR, load_environment, load_thresholds
from .engine import audit, decided
from .engine import runner as R
from .storage.raw import RawArchive

con = Console()
CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}


def br(n) -> str:
    return f"{n:,}".replace(",", ".")


def registrar(app: typer.Typer, env_opt) -> None:

    @app.command("prever")
    def prever(
        cargo: str = typer.Argument(..., help="presidente|governador|senador"),
        uf: str = typer.Option(None, "--uf", help="UF, obrigatorio fora de presidente"),
        env_name: str = env_opt,
        modelo: str = typer.Option("m1", help="m0 (baseline) ou m1"),
        sims: int = typer.Option(None, help="numero de simulacoes"),
    ):
        """Roda a projeção de uma disputa e REGISTRA o que foi dito.

        Todo resultado fica no diário de previsões antes de aparecer na tela.
        Não há caminho que produza projeção sem registro.
        """
        if cargo not in CARGOS:
            raise typer.BadParameter(f"cargo deve ser um de {list(CARGOS)}")
        if cargo != "presidente" and not uf:
            raise typer.BadParameter(f"{cargo} exige --uf: a disputa e por UF")

        env, thr = load_environment(env_name), load_thresholds()
        fc, dfn, ctx, reg = R.executar(
            env, thr, cargo=CARGOS[cargo], uf=(uf.lower() if uf else None),
            modelo=modelo, n_sims=sims,
            archive=RawArchive(DATA_DIR / "raw", env.name),
            diario=audit.Diario(DATA_DIR / "previsoes", env.name),
        )

        con.print(f"\n[bold]{cargo.title()}"
                  f"{' — ' + uf.upper() if uf else ''}[/]  ·  "
                  f"{ctx.unidades} unidades  ·  {ctx.vagas} vaga(s)")
        con.print(f"apurado: {br(ctx.eleitorado_apurado)} de "
                  f"{br(ctx.eleitorado_total)} eleitores "
                  f"({reg.cobertura_pct:.2f}%)  ·  "
                  f"pendente: {br(ctx.pendente_min)}–{br(ctx.pendente_max)}")
        con.print(f"estado: [bold]{reg.estado_publicacao}[/]  ·  "
                  f"modelo {modelo.upper()} semente {fc.seed} "
                  f"({fc.n_sims} simulacoes, erro MC "
                  f"{fc.diagnostics['erro_monte_carlo_pp']:.4f} pp)")

        q = fc.quantis((0.10, 0.50, 0.90))
        vagas_p = fc.prob_dentro_das_vagas()
        maioria = fc.prob_maioria_absoluta()
        estados = {s.sqcand: s for s in dfn.situacoes}

        t = Table("candidato", "observado", "projetado (mediana)",
                  "intervalo 80%", "P(vaga)", "P(>50%)", "definicao",
                  title="Projeção — % sobre votos a concorrentes")
        ordenado = sorted(fc.candidates,
                          key=lambda c: -q[c]["votos"]["q500"])
        for c in ordenado[:12]:
            s = estados.get(c)
            rot = decided.ROTULOS[s.estado] if s else "—"
            publica = reg.estado_publicacao == "projecao_disponivel"
            t.add_row(
                ctx.nomes.get(c, c)[:26],
                br(fc.observed.get(c, 0)),
                f"{q[c]['pct_concorrentes']['q500']:.2f}%",
                f"{q[c]['pct_concorrentes']['q100']:.2f}–"
                f"{q[c]['pct_concorrentes']['q900']:.2f}%",
                f"{vagas_p[c]:.0%}" if publica else "—",
                f"{maioria[c]:.0%}" if publica else "—",
                rot if s and s.definido else "",
            )
        con.print(t)

        if reg.estado_publicacao != "projecao_disponivel":
            con.print("[yellow]Probabilidades ocultas: estado "
                      f"'{reg.estado_publicacao}'. Os intervalos acima sao "
                      "cenario, nao previsao publicada (§4.7).[/]")
        if dfn.disputa_definida:
            con.print(f"[green]Disputa matematicamente definida: "
                      f"{', '.join(ctx.nomes.get(s.sqcand, s.sqcand) for s in dfn.eleitos)}[/]")
        if dfn.segundo_turno_definido:
            con.print("[green]Segundo turno matematicamente definido: "
                      + ", ".join(ctx.nomes.get(s.sqcand, s.sqcand)
                                  for s in dfn.no_segundo_turno) + "[/]")
        for a in dfn.avisos:
            con.print(f"[yellow]! {a}[/]")
        con.print(f"\n[dim]registrado como {reg.registro_id}[/]")

    @app.command("previsoes")
    def previsoes(env_name: str = env_opt,
                  serie: str = typer.Option(None, help="filtra por serie")):
        """Lista o histórico de projeções registradas."""
        env = load_environment(env_name)
        d = audit.Diario(DATA_DIR / "previsoes", env.name)
        series = d.series()
        if not series:
            con.print("nenhuma previsao registrada. Rode `obs2026 prever`.")
            return
        for chave, regs in sorted(series.items()):
            if serie and serie not in chave:
                continue
            con.print(f"\n[bold]{chave}[/]  ({len(regs)} execucoes)")
            t = Table("momento", "cobertura", "estado", "líder projetado",
                      "% (int. 80%)", "definido")
            for r in regs[-15:]:
                top = max(r.candidatos, key=lambda c: c.votos_q500)
                t.add_row(
                    r.momento[:19].replace("T", " "),
                    f"{r.cobertura_pct:.2f}%",
                    r.estado_publicacao,
                    top.nome[:22],
                    f"{top.pct_concorrentes_q500:.2f}% "
                    f"({top.pct_concorrentes_q100:.1f}–"
                    f"{top.pct_concorrentes_q900:.1f})",
                    "sim" if r.disputa_matematicamente_definida else "",
                )
            con.print(t)

    @app.command("auditar-previsoes")
    def auditar_previsoes(env_name: str = env_opt,
                          json_out: Path = typer.Option(
                              None, "--json", help="grava o relatorio")):
        """Confronta as projeções registradas com o resultado final.

        Só avalia séries cuja disputa já terminou: sem resultado final não há
        verdade contra a qual medir.
        """
        env, thr = load_environment(env_name), load_thresholds()
        arc = RawArchive(DATA_DIR / "raw", env.name)
        d = audit.Diario(DATA_DIR / "previsoes", env.name)
        series = d.series()
        if not series:
            con.print("nenhuma previsao registrada.")
            return

        relatorio = {}
        for chave, regs in sorted(series.items()):
            r0 = regs[0]
            uf = None if r0.abrangencia == "br" else r0.unidade
            obs = R.carregar_observacoes(arc, r0.cargo, uf)
            if not obs:
                continue
            ctx = R.montar_contexto(obs, cargo=r0.cargo,
                                    abrangencia=r0.abrangencia,
                                    unidade=r0.unidade)
            if not ctx.encerrada:
                con.print(f"[dim]{chave}: disputa em andamento, "
                          "sem resultado final para comparar[/]")
                continue

            final: dict[str, int] = {}
            for o in obs:
                for c in o.result.cargo.candidatos:
                    if c.conta_como_valido:
                        final[c.sqcand] = final.get(c.sqcand, 0) + c.votos

            notas = [audit.avaliar(r, final,
                                   concorrentes_final=ctx.concorrentes_observados,
                                   vagas=ctx.vagas) for r in regs]
            resumo = audit.consolidar(notas)
            relatorio[chave] = resumo

            con.print(f"\n[bold]{chave}[/]  ({len(notas)} execucoes avaliadas)")
            t = Table("faixa", "n", "erro pp", "cob. 80%", "IC95",
                      "calibrado", "largura", "líder", "vagas", "Brier")
            for faixa, v in resumo.items():
                ic = v["cobertura_ic95"]
                t.add_row(
                    faixa, str(v["execucoes"]),
                    f"{v['erro_pp_mediano']:.2f}",
                    f"{v['cobertura_intervalo_80']:.0%}",
                    f"{ic[0]:.0%}–{ic[1]:.0%}" if ic else "—",
                    {True: "sim", False: "NÃO", None: "—"}[v["calibrado"]],
                    f"{v['largura_media_80_pp']:.2f}",
                    f"{v['acerto_lideranca']:.0%}",
                    f"{v['acerto_vagas']:.0%}",
                    f"{v['brier_vagas']:.4f}" if v["brier_vagas"] is not None
                    else "—",
                )
            con.print(t)
            falhas = [f for v in resumo.values()
                      for f in v["falhas_de_definicao_matematica"]]
            if falhas:
                con.print("[red]FALHA GRAVE: definicao matematica nao se "
                          f"confirmou em {len(falhas)} registro(s). Isso nao "
                          "e erro de modelo — indica retotalizacao ou bug "
                          "de contagem.[/]")
                for f in falhas[:5]:
                    con.print(f"   {f}")
            if any(v["amostra_pequena"] for v in resumo.values()):
                con.print("[yellow]Amostra pequena: poucas execucoes nao "
                          "sustentam alegacao de calibracao (§11.2).[/]")

        if json_out and relatorio:
            json_out.write_text(
                json.dumps(relatorio, ensure_ascii=False, indent=2),
                encoding="utf-8")
            con.print(f"\nrelatorio gravado em {json_out}")


def registrar_ensaio(app: typer.Typer, env_opt) -> None:

    @app.command("ensaiar-auditoria")
    def ensaiar_auditoria(
        cargo: str = typer.Argument(..., help="presidente|governador|senador"),
        uf: str = typer.Option(None, "--uf"),
        env_name: str = env_opt,
        sims: int = typer.Option(800),
    ):
        """Reconstrói uma apuração progressiva e audita as projeções.

        Existe para validar a cadeia de auditoria ANTES da apuração real: a
        partir de uma disputa encerrada, esconde parte das unidades, projeta,
        registra, revela mais, projeta de novo — e no fim confronta tudo com
        o resultado que já se conhece.

        A ordem de chegada é CONSTRUÍDA por nós (municípios menores primeiro,
        o padrão real mais comum). Isto é teste de estresse, não replay
        histórico real (§11.2), e os registros vão para um diário separado
        para não contaminar a auditoria de verdade.
        """
        from .engine import backtest as B
        from .engine import models as M, simulate as S

        if cargo not in CARGOS:
            raise typer.BadParameter(f"cargo deve ser um de {list(CARGOS)}")
        env, thr = load_environment(env_name), load_thresholds()
        arc = RawArchive(DATA_DIR / "raw", env.name)
        obs = R.carregar_observacoes(arc, CARGOS[cargo],
                                     uf.lower() if uf else None)
        if not obs:
            raise typer.Exit(f"sem dados coletados para {cargo}/{uf}")

        ctx = R.montar_contexto(obs, cargo=CARGOS[cargo],
                                abrangencia="br" if not uf else "uf",
                                unidade=uf or "br")
        if not ctx.encerrada:
            con.print("[yellow]disputa em andamento: o ensaio precisa de uma "
                      "disputa encerrada para ter verdade contra a qual "
                      "medir[/]")
            raise typer.Exit(1)

        cands = sorted({c.sqcand for o in obs
                        for c in o.result.cargo.candidatos
                        if c.conta_como_valido})
        final = {c: 0 for c in cands}
        for o in obs:
            for c in o.result.cargo.candidatos:
                if c.sqcand in final:
                    final[c.sqcand] += c.votos

        diario = audit.Diario(DATA_DIR / "previsoes", f"ensaio-{env.name}")
        if diario.caminho.exists():
            diario.caminho.unlink()          # ensaio recomeça limpo

        ordenadas = sorted(obs, key=lambda o: o.result.electorate.te)
        total_el = sum(o.result.electorate.te for o in ordenadas)
        con.print(f"\n[bold]Ensaio de auditoria — {cargo}"
                  f"{'/' + uf.upper() if uf else ''}[/]")
        con.print(f"{len(obs)} unidades, {len(cands)} candidatos, "
                  f"{br(total_el)} eleitores, {ctx.vagas} vaga(s)")
        con.print("[dim]ordem de chegada SINTETICA: menores primeiro[/]\n")

        for corte in (1.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 99.0):
            alvo = total_el * corte / 100.0
            acum, n = 0, 0
            for o in ordenadas:
                if acum >= alvo:
                    break
                acum += o.result.electorate.te
                n += 1
            mascaradas = [
                o if i < n else M.UnitObservation(
                    unit=o.unit, uf=o.uf, stratum=o.stratum,
                    result=B.mask_unit(o.result), capital=o.capital)
                for i, o in enumerate(ordenadas)
            ]
            c2 = R.montar_contexto(mascaradas, cargo=CARGOS[cargo],
                                   abrangencia=ctx.abrangencia,
                                   unidade=ctx.unidade)
            units = M.build_m1(mascaradas, cands, seats=ctx.vagas)
            fc = S.run(units, cands, S.SimConfig(n_sims=sims, seed=20261004),
                       seats=ctx.vagas, senado=ctx.vagas > 1)
            votos_obs = {c: fc.observed.get(c, 0) for c in cands}
            dfn = decided.avaliar(
                votos_obs, vagas=c2.vagas,
                concorrentes_observados=c2.concorrentes_observados,
                pendente_max=c2.pendente_max, sub_judice=c2.sub_judice,
                encerrada=False, unidade=c2.unidade)
            diario.registrar(audit.capturar(
                forecast=fc, definicao=dfn, ambiente=f"ensaio-{env.name}",
                eleicao=c2.eleicao, turno=c2.turno, cargo=CARGOS[cargo],
                abrangencia=c2.abrangencia, unidade=c2.unidade,
                snapshot_set_id=f"ensaio-corte-{corte:.0f}",
                fonte_gerada_em=c2.fonte_gerada_em,
                eleitorado_total=c2.eleitorado_total,
                eleitorado_apurado=c2.eleitorado_apurado,
                secoes_totalizadas=c2.secoes_totalizadas,
                secoes_total=c2.secoes_total,
                pendente_min=c2.pendente_min, pendente_max=c2.pendente_max,
                modelo="m1", model_version="m1-0.1-ensaio",
                thresholds_hash=thr.config_hash,
                thresholds_homologados=thr.homologated,
                estado_publicacao="cenario_exploratorio", nomes=c2.nomes))

        notas = [audit.avaliar(
            r, final, concorrentes_final=ctx.concorrentes_observados,
            vagas=ctx.vagas) for r in diario.ler()]

        t = Table("cobertura", "erro pp", "dentro do int. 80%", "largura 80%",
                  "líder", "vagas", "definição",
                  title="Coerência das projeções ao longo da apuração")
        for n in notas:
            t.add_row(
                f"{n.cobertura_pct:.1f}%",
                f"{n.erro_pp_mediano:.2f}",
                f"{n.cobertura_intervalo_80:.0%}",
                f"{n.largura_media_80_pp:.2f}",
                "acertou" if n.acertou_lideranca else "ERROU",
                "acertou" if n.acertou_vagas else "ERROU",
                {True: "confirmada", False: "FALHOU",
                 None: "—"}[n.definicao_matematica_bateu],
            )
        con.print(t)

        resumo = audit.consolidar(notas)
        con.print("\n[bold]Consolidado por faixa[/]")
        for faixa, v in resumo.items():
            ic = v["cobertura_ic95"]
            faixa_ic = (f" (IC95 {ic[0]:.0%}–{ic[1]:.0%})" if ic else "")
            con.print(
                f"  {faixa:>6}  n={v['execucoes']}  "
                f"erro {v['erro_pp_mediano']:.2f} pp  "
                f"cobertura {v['cobertura_intervalo_80']:.0%}{faixa_ic}")
        falhas = [f for v in resumo.values()
                  for f in v["falhas_de_definicao_matematica"]]
        if falhas:
            con.print(f"[red]DEFINICAO MATEMATICA FALHOU em {len(falhas)} "
                      "registro(s) — isso nao pode acontecer[/]")
        else:
            con.print("[green]Nenhuma definicao matematica foi contrariada "
                      "pelo resultado final.[/]")
        con.print(f"\n[dim]registros em {diario.caminho}[/]")
