"""Comando `compartilhar`: as telas do projetor viram imagem e texto.

O trabalho todo é automático — fotografar, legendar, pôr na área de
transferência. O envio não é, e as razões estão em `obs2026/captura.py`.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from . import captura as C
from .config import DATA_DIR

con = Console()


def _indices(s: str | None) -> list[int] | None:
    if not s or s.strip().lower() in ("tudo", "todas", "all"):
        return None
    try:
        return [int(x) for x in s.replace(";", ",").split(",") if x.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            f"lista de telas invalida: {s!r}. Use algo como 0,2,3 ou 'tudo'."
        ) from exc


def registrar(app: typer.Typer, env_opt) -> None:

    @app.command("compartilhar")
    def compartilhar(
        telas: str = typer.Option(
            None, "--telas",
            help="indices do roteiro, ex 0,2,3. Padrao: todas as validas"),
        cargo: str = typer.Option(
            None, "--cargo",
            help="1/3/5 para fotografar um recorte fora do roteiro"),
        uf: str = typer.Option(None, "--uf"),
        municipio: str = typer.Option(
            None, "--municipio", help="codigo TSE de 5 digitos"),
        camada: str = typer.Option("definicao", "--camada"),
        api: str = typer.Option("http://localhost:8026", "--api",
                                help="endereco do painel local"),
        largura: int = typer.Option(1920, "--largura"),
        altura: int = typer.Option(1080, "--altura"),
        navegador: str = typer.Option(
            None, "--navegador",
            help="caminho do chrome.exe ou msedge.exe, se fora do padrao"),
        copiar: bool = typer.Option(
            True, "--copiar/--nao-copiar",
            help="poe as imagens na area de transferencia para colar no "
                 "WhatsApp"),
        abrir: bool = typer.Option(
            False, "--abrir/--nao-abrir", help="abre a pasta no Explorer"),
        legenda: bool = typer.Option(
            True, "--legenda/--sem-legenda",
            help="gera o texto com os numeros do mesmo instante"),
    ):
        """Fotografa as telas do telao e prepara o pacote para o grupo.

        Grava as imagens, escreve a legenda com os numeros daquele instante
        e deixa tudo na area de transferencia. No grupo do WhatsApp e Ctrl+V.

        O envio e do operador de proposito: a API oficial do WhatsApp nao
        entrega para grupos, automatizar o WhatsApp Web arrisca o numero, e
        projecao eleitoral saindo sozinha de um laco automatico e o tipo de
        coisa que precisa de alguem conferindo antes.
        """
        if cargo and telas:
            raise typer.BadParameter(
                "--cargo e --telas sao formas diferentes de escolher o que "
                "fotografar; use uma das duas")
        recorte = ({"cargo": cargo, "uf": (uf or None),
                    "municipio": (municipio or None), "camada": camada}
                   if cargo else None)
        try:
            pacote = C.capturar(
                api=api, telas=_indices(telas), recorte=recorte,
                destino=DATA_DIR / "telao",
                largura=largura, altura=altura,
                navegador=navegador, com_legenda=legenda)
        except C.CapturaError as exc:
            con.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc

        def onde(i: int) -> str:
            return f"tela {i}" if i >= 0 else "recorte avulso"

        for f in pacote.fotos:
            con.print(f"[green]ok[/]  {onde(f.indice)}  {f.titulo}")
            con.print(f"    [dim]{f.imagem}[/]")
        for i, titulo, motivo in pacote.puladas:
            # Pulada e um fato operacional, nao um detalhe: quem esta
            # mandando para o grupo precisa saber o que nao foi.
            con.print(f"[yellow]pulada[/]  {onde(i)}  {titulo}")
            con.print(f"    [yellow]{motivo}[/]")

        if not pacote.fotos:
            con.print("[red]Nenhuma imagem gravada.[/]")
            raise typer.Exit(1)

        if legenda:
            con.print()
            con.print("[bold]Legenda[/] (tambem em "
                      f"{pacote.arquivo_legenda.name}):")
            con.print(pacote.texto, highlight=False, markup=False)

        con.print()
        arquivos: list[Path] = [f.imagem for f in pacote.fotos]
        if copiar and C.para_area_de_transferencia(arquivos):
            con.print(f"[green]{len(arquivos)} imagem(ns) na area de "
                      "transferencia.[/] No grupo do WhatsApp: Ctrl+V.")
        elif copiar:
            con.print("[yellow]Nao consegui usar a area de transferencia; "
                      "as imagens estao na pasta.[/]")
        if abrir:
            C.abrir_pasta(pacote.pasta)
        con.print(f"[dim]pasta: {pacote.pasta}[/]")
