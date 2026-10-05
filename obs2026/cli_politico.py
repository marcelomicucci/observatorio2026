"""Comandos da parte política: classificação ideológica e Senado.

    obs2026 classificacao            mostra a proposta/classificação vigente
    obs2026 assinar-classificacao    assina e liga o mapa ideológico
    obs2026 preparar-senado          baixa a composição atual do Senado
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

con = Console()


def registrar(app: typer.Typer) -> None:

    @app.command("classificacao")
    def classificacao():
        """Mostra a classificação ideológica, partido a partido."""
        from .politics import classification as CL
        reg = CL.load()
        problemas = CL.validar(reg)
        t = Table(title=f"Classificação {reg.versao} — curador: "
                        f"{reg.curador or 'NÃO ASSINADA'}")
        for c in ("nº", "partido", "categoria", "controv.", "justificativa"):
            t.add_column(c, overflow="fold")
        ordem = {c: i for i, c in enumerate(CL.CATEGORIAS)}
        for e in sorted(reg.entradas, key=lambda e: (ordem[e.ideologia],
                                                     e.entidade_id)):
            t.add_row(e.numero or "", e.entidade_id, CL.ROTULOS[e.ideologia],
                      str(e.grau_controversia), e.justificativa)
        con.print(t)
        if reg.curadoria_pendente:
            con.print("[yellow]Proposta não assinada: o mapa ideológico "
                      "fica desligado até `obs2026 assinar-classificacao "
                      "--curador \"Nome\"`.[/]")
        outros = [p for p in problemas if "autor vazio" not in p
                  and "sem curador" not in p]
        for p in outros:
            con.print(f"[red]{p}[/]")

    @app.command("assinar-classificacao")
    def assinar_classificacao(
        curador: str = typer.Option(..., "--curador",
                                    help="nome de quem responde pela "
                                         "classificação"),
        sim: bool = typer.Option(False, "--sim",
                                 help="não pede confirmação"),
    ):
        """Assina a classificação ideológica e liga o mapa ideológico.

        Revise antes com `obs2026 classificacao`. A assinatura põe o seu nome
        como autor de cada entrada sem autor, sobe a versão e guarda a
        anterior em config/classificacao.anterior/.
        """
        from .politics import classification as CL
        reg = CL.load()
        if not sim:
            typer.confirm(
                f"Assinar {len(reg.entradas)} entradas como "
                f"{curador!r}? A classificação passa a aparecer no telão",
                abort=True)
        try:
            r = CL.assinar(curador)
        except CL.ClassificationError as exc:
            con.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        con.print(f"[green]Assinada: versão {r['versao']}, "
                  f"{r['entradas']} entradas, curador {r['curador']}, "
                  f"{r['publicada_em']}.[/]")
        con.print("O telão passa a mostrar a ideologia na próxima "
                  "atualização (10 s); não precisa reiniciar.")

    @app.command("homologar")
    def homologar(
        assinante: str = typer.Option(..., "--assinante",
                                      help="nome de quem assina"),
        sim: bool = typer.Option(False, "--sim", help="sem confirmação"),
    ):
        """Homologa o gate estatístico e libera probabilidade de vitória.

        Recusa se o gate de 2022 (scripts/gate2022.py) não passou."""
        from . import homologacao as H
        try:
            ev = H.ler_evidencia()
        except H.HomologacaoError as exc:
            con.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        recusa = H.motivos_de_recusa(ev)
        if recusa:
            con.print("[red]Homologação recusada: " + "; ".join(recusa)
                      + ".[/]\nO produto segue em cenário exploratório.")
            raise typer.Exit(1)
        con.print(f"Gate de 2022: {ev['disputas']} disputas; M2 vence o M0 "
                  f"em todas as rodadas; cobertura 80% "
                  f"{ev['cobertura80_m2_pct']}.")
        if not sim:
            typer.confirm(f"Homologar como {assinante!r}? Probabilidades de "
                          "vitória passam a aparecer no painel, no telão e "
                          "no WhatsApp", abort=True)
        r = H.homologar(assinante)
        con.print(f"[green]Homologado por {r['assinante']}.[/] Vale na "
                  "próxima projeção (até 3 min); não precisa reiniciar.")

    @app.command("revogar-homologacao")
    def revogar_homologacao(
        motivo: str = typer.Option(..., "--motivo"),
    ):
        """Volta ao cenário exploratório (sem probabilidade), na hora."""
        from . import homologacao as H
        H.revogar(motivo)
        con.print("[yellow]Homologação revogada: sem probabilidade a partir "
                  "da próxima projeção.[/]")

    @app.command("preparar-senado")
    def preparar_senado():
        """Baixa a composição atual do Senado (dados abertos) para o disco.

        Rodar antes da noite: as 27 cadeiras que continuam até 2031 entram
        na tela de composição do Senado sem tocar a rede durante a
        apuração."""
        from .politics import senado as SEN
        try:
            doc = SEN.baixar()
        except Exception as exc:              # noqa: BLE001
            con.print(f"[red]Senado: não foi possível baixar ({exc}). A "
                      f"versão anterior em disco, se houver, continua.[/]")
            raise typer.Exit(1) from exc
        rem = SEN.remanescentes(2026, doc)
        sup = sum(1 for o in rem["cadeiras"] if o["suplente_em_exercicio"])
        con.print(f"Senado: 81 em exercício; {len(rem['cadeiras'])} com "
                  f"mandato até 2031 (remanescentes em 2026)"
                  + (f", {sup} delas com suplente em exercício" if sup else "")
                  + f". Gravado em {SEN.ARQUIVO}.")

    @app.command("assinar-linhagem")
    def assinar_linhagem(curador: str = typer.Option(
            ..., "--curador", help="quem responde pela linhagem")):
        """Assina a linhagem partidária (sucessões, fusões, trocas de nome).

        Sem assinatura, a comparação ano a ano mostra cada partido com o
        nome da época e NÃO liga as séries — PSL e União Brasil aparecem
        separados. Assinar é afirmar que uma legenda continua a outra, e
        isso é ato editorial, não dado do TSE."""
        from .politics import linhagem as LIN
        try:
            reg = LIN.assinar(curador)
        except LIN.LinhagemError as exc:
            con.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        con.print(f"Linhagem assinada por {reg.curador} "
                  f"(versão {reg.versao}): {len(reg.sucessoes)} sucessões. "
                  "A comparação ano a ano passa a ligar as séries.")

    @app.command("importar-historico")
    def importar_historico(
            ano: list[int] = typer.Option(..., "--ano", help="pode repetir"),
            sem_deputados: bool = typer.Option(
                False, help="não importa a bancada de deputado federal"),
            so_suplementares: bool = typer.Option(
                False, help="só os senadores eleitos em suplementar "
                            "posterior (sem reimportar o ano)")):
        """Importa uma eleição passada para o banco histórico.

        Lê os zips do TSE em `Dados/<ano>/` e grava em
        `data/db/historico.sqlite`. Idempotente: reimportar substitui."""
        from .history import importar as IMP
        if so_suplementares:
            for a in ano:
                n = IMP.importar_suplementares_senado(a)
                con.print(f"{a}: {n} senador(es) eleito(s) em suplementar.")
            return
        for a in ano:
            con.print(f"importando {a}…")
            r = IMP.importar(a, com_deputados=not sem_deputados,
                             progresso=lambda t: con.print(f"  {t}"))
            con.print(f"[green]{a}: {r.totais} totais e {r.votos} votos "
                      f"em {r.ufs} UFs.[/]")

    @app.command("montar-ano")
    def montar_ano(
            ano: list[int] = typer.Option(..., "--ano", help="pode repetir"),
            turno: list[str] = typer.Option(["1", "2"], "--turno",
                                            help="1, 2 ou os dois"),
            processos: int = typer.Option(6, help="leitores em paralelo")):
        """Monta uma eleição passada no formato do ao vivo, para o seletor
        de anos (Resultado e Mapa em 2018 ou 2022).

        Um banco por ano e turno (`tse2022t2` etc.). O "2º turno" é o
        resultado final: governador completa com as UFs decididas no 1º,
        marcadas. Leva alguns minutos por turno (gigabytes de CSV). Feche o
        servidor antes: o banco é apagado e refeito."""
        from .history import congelado as CG
        for a in ano:
            for t in turno:
                m = CG.montar(a, t, workers=processos,
                              log=lambda x: con.print(x))
                extra = ""
                if m["governador_decidido_1o_turno"]:
                    extra = (" · governador pelo 1º turno em "
                             + ", ".join(u.upper() for u in
                                         m["governador_decidido_1o_turno"]))
                con.print(f"[green]{m['ambiente']} pronto{extra}.[/]")
