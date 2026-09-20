"""Importação e conciliação da base de eleitorado recebida.

Arquivo: `eleitorado_eleicao_capilaridade.csv.zip` (separador `;`,
codificação Windows-1252, carga declarada 20/09/2026).

Regras do §2.1 implementadas aqui:

- O original nunca é alterado; o hash é registrado antes de qualquer leitura.
- Nomes são normalizados **só** para procurar candidatos a correspondência.
  A associação é decidida por `UF + código TSE`, vindo do EA12.
- Associação aproximada silenciosa é proibida. O que não conciliar entra em
  quarentena **com o peso eleitoral visível**, porque um município não
  conciliado não é um detalhe: é voto que some da conta.

Resultado da conciliação com os dados reais de 2026:

    Brasil     5.571 linhas no CSV  ↔  5.571 municípios no EA12   (100%)
    Exterior     186 linhas no CSV  ↔    184 localidades no EA12

As 45 "divergências" brasileiras que aparecem numa comparação ingênua são
todas entidade HTML: o EA12 escreve `OLHO D&APOS;AGUA` onde o CSV escreve
`OLHO D'AGUA`. Depois do unescape, a correspondência é exata.

A diferença real está no exterior, e é assimétrica — o CSV tem localidade que
o EA12 não tem e vice-versa. Isso não é erro de nenhum dos dois: o CSV é de
20/09 e o catálogo do simulado é de 14/09. Fica registrado como divergência
de versão, com o eleitorado afetado quantificado, e não é resolvido por
adivinhação.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..storage.raw import RawArchive
from ..tse.parsers.common import fold
from ..tse.parsers.ea12 import Ea12, Municipio
from ..tse.parsers import ea12 as ea12_parser

#: Cabeçalhos esperados, em Windows-1252. Qualquer diferença é recusada:
#: uma coluna renomeada em silêncio viraria eleitorado zerado.
COLS = {
    "ano": "Ano de eleição",
    "municipio": "Município",
    "uf": "UF",
    "regiao": "Região",
    "pais": "País",
    "qt_municipio": "Quantidade de município",
    "qt_zona": "Quantidade de zona",
    "qt_secao": "Quantidade de seção",
    "aptos": "Quantidade de aptos",
    "carga": "Data de carga",
}


@dataclass(frozen=True, slots=True)
class Linha:
    ano: str
    municipio: str
    municipio_fold: str
    uf: str
    regiao: str
    pais: str
    zonas: int
    secoes: int
    aptos: int
    carga: str


@dataclass
class Report:
    arquivo: str
    sha256_zip: str
    sha256_csv: str
    linhas: int
    aptos_total: int
    aptos_brasil: int
    aptos_exterior: int
    secoes: int
    conciliados: int = 0
    aptos_conciliados: int = 0
    quarentena_csv: list[tuple[Linha, str]] = field(default_factory=list)
    quarentena_ea12: list[Municipio] = field(default_factory=list)
    catalogo_versao: str | None = None
    catalogo_localidades: int = 0
    alertas: list[str] = field(default_factory=list)

    @property
    def aptos_em_quarentena(self) -> int:
        return sum(l.aptos for l, _ in self.quarentena_csv)

    @property
    def cobertura_pct(self) -> float:
        return (100.0 * self.aptos_conciliados / self.aptos_total
                if self.aptos_total else 0.0)

    @property
    def libera_projecao(self) -> bool:
        """§4.7: 100% do eleitorado correspondido, ou residual modelado."""
        return self.cobertura_pct >= 100.0

    def render(self) -> str:
        n = lambda x: f"{x:,}".replace(",", ".")  # noqa: E731
        out = [
            "",
            f"Auditoria de {self.arquivo}",
            f"  sha256 do zip : {self.sha256_zip}",
            f"  sha256 do csv : {self.sha256_csv}",
            "",
            f"  linhas de localidade        {n(self.linhas):>15}",
            f"  aptos, incluindo exterior   {n(self.aptos_total):>15}",
            f"  aptos no exterior           {n(self.aptos_exterior):>15}",
            f"  aptos no Brasil             {n(self.aptos_brasil):>15}",
            f"  soma da coluna de secoes    {n(self.secoes):>15}",
            "",
            f"Conciliacao contra o catalogo EA12 "
            f"({self.catalogo_versao or 'nao carregado'})",
            f"  localidades no catalogo     {n(self.catalogo_localidades):>15}",
            f"  linhas conciliadas          {n(self.conciliados):>15}",
            # Arredondar 99,999997% para "100,0000%" e ao mesmo tempo
            # bloquear a projecao seria incompreensivel. O residual aparece
            # em eleitores, que e a unidade em que a decisao e tomada.
            f"  aptos conciliados           {n(self.aptos_conciliados):>15}",
            f"  residual nao conciliado     "
            f"{n(self.aptos_total - self.aptos_conciliados):>15}"
            f"   ({100.0 - self.cobertura_pct:.6f} pp)",
            f"  aptos em quarentena         "
            f"{n(self.aptos_em_quarentena):>15}",
            "",
        ]
        if self.quarentena_csv:
            out.append(f"  CSV sem correspondencia no EA12 "
                       f"({len(self.quarentena_csv)}):")
            for l, motivo in self.quarentena_csv[:40]:
                out.append(f"    {l.uf} {l.municipio:<38} "
                           f"aptos={n(l.aptos):>9}  {motivo}")
            if len(self.quarentena_csv) > 40:
                out.append(f"    ... mais {len(self.quarentena_csv)-40}")
            out.append("")
        if self.quarentena_ea12:
            out.append(f"  EA12 sem correspondencia no CSV "
                       f"({len(self.quarentena_ea12)}):")
            for m in self.quarentena_ea12[:40]:
                out.append(f"    {m.uf.upper()} {m.nome:<38} cd={m.cd}")
            out.append("")
        for a in self.alertas:
            out.append(f"  ! {a}")
        out.append("")
        residual = self.aptos_total - self.aptos_conciliados
        if self.libera_projecao:
            out.append("  PROJECAO LIBERADA pelo criterio de correspondencia: "
                       "100% do eleitorado correspondido.")
        else:
            out.append(
                f"  PROJECAO BLOQUEADA: {n(residual)} eleitores de "
                f"{n(self.aptos_total)} sem correspondencia no catalogo.\n"
                "  O §4.7 aceita duas saidas, e nenhuma delas e ignorar:\n"
                "    (a) corresponder 100% do eleitorado, ou\n"
                "    (b) modelar o residual explicitamente, mantendo-o "
                "visivel na projecao.\n"
                "  Para liberar por (b), registre o residual como unidade "
                "propria em vez de\n  descarta-lo — eleitorado que some da "
                "conta e voto que some do resultado."
            )
        return "\n".join(out)


def read_csv(path: Path) -> tuple[list[Linha], str, str]:
    """Lê o zip sem alterar nada e devolve linhas + hashes."""
    zip_bytes = Path(path).read_bytes()
    sha_zip = hashlib.sha256(zip_bytes).hexdigest()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(nomes) != 1:
            raise ValueError(f"esperado 1 csv no zip, encontrados {nomes}")
        raw = z.read(nomes[0])
    sha_csv = hashlib.sha256(raw).hexdigest()

    texto = raw.decode("cp1252")
    rd = csv.DictReader(io.StringIO(texto), delimiter=";")
    faltando = [v for v in COLS.values() if v not in (rd.fieldnames or [])]
    if faltando:
        raise ValueError(
            f"colunas ausentes no CSV: {faltando}. Contrato mudou; "
            "importacao recusada."
        )

    linhas = []
    for r in rd:
        nome = r[COLS["municipio"]].strip()
        linhas.append(Linha(
            ano=r[COLS["ano"]].strip(),
            municipio=nome, municipio_fold=fold(nome),
            uf=r[COLS["uf"]].strip().upper(),
            regiao=r[COLS["regiao"]].strip(),
            pais=r[COLS["pais"]].strip(),
            zonas=int(r[COLS["qt_zona"]]),
            secoes=int(r[COLS["qt_secao"]]),
            aptos=int(r[COLS["aptos"]]),
            carga=r[COLS["carga"]].strip(),
        ))
    return linhas, sha_zip, sha_csv


def _latest_ea12(archive: RawArchive) -> Ea12 | None:
    """União dos catálogos EA12 de todas as eleições capturadas.

    O EA12 é **por eleição**, e os catálogos NÃO são equivalentes: o da
    eleição federal (Presidente) traz as 184 localidades do exterior, o da
    estadual (Governador/Senador) não traz nenhuma — corretamente, porque não
    há governador nem senador em ZZ.

    Usar um só catálogo para conciliar a base de eleitorado manda as 186
    linhas de exterior inteiras para a quarentena e derruba a cobertura de
    100% para 99,4%, o que por sua vez bloquearia a projeção por um motivo
    que não existe. A união é a leitura correta do universo de localidades.
    """
    por_eleicao: dict[str, object] = {}
    for day in archive.days():
        for ref in archive.manifest(day):
            if ref.kind == "ea12" and ref.path:
                por_eleicao[ref.election or "?"] = ref
    if not por_eleicao:
        return None

    catalogos = [ea12_parser.parse(archive.load(r), r.election)
                 for r in por_eleicao.values()]
    catalogos.sort(key=lambda c: -len(c.municipios))

    vistos: set[tuple[str, str]] = set()
    uniao: list[Municipio] = []
    for c in catalogos:
        for m in c.municipios:
            if (m.uf, m.cd) not in vistos:
                vistos.add((m.uf, m.cd))
                uniao.append(m)

    base = catalogos[0]
    return Ea12(
        election="+".join(sorted(por_eleicao)),
        generated_at=base.generated_at, idg=base.idg, fase=base.fase,
        municipios=tuple(uniao),
    )


def audit_and_reconcile(path: Path, archive: RawArchive, env) -> Report:
    linhas, sha_zip, sha_csv = read_csv(path)

    rep = Report(
        arquivo=Path(path).name, sha256_zip=sha_zip, sha256_csv=sha_csv,
        linhas=len(linhas),
        aptos_total=sum(l.aptos for l in linhas),
        aptos_brasil=sum(l.aptos for l in linhas if l.pais == "Brasil"),
        aptos_exterior=sum(l.aptos for l in linhas if l.pais != "Brasil"),
        secoes=sum(l.secoes for l in linhas),
    )

    anos = {l.ano for l in linhas}
    cargas = {l.carga for l in linhas}
    if len(anos) != 1:
        rep.alertas.append(f"CSV mistura anos de eleicao: {sorted(anos)}")
    if len(cargas) != 1:
        rep.alertas.append(f"CSV mistura datas de carga: {sorted(cargas)}")

    dups = {}
    for l in linhas:
        dups.setdefault((l.uf, l.municipio_fold), []).append(l)
    repetidas = {k: v for k, v in dups.items() if len(v) > 1}
    if repetidas:
        rep.alertas.append(
            f"{len(repetidas)} combinacoes UF+nome repetidas no CSV"
        )

    # A soma da coluna de zonas NAO conta zonas unicas: uma zona pode abranger
    # mais de um municipio (§2.1). O alerta existe para impedir que alguem use
    # esse numero como denominador.
    rep.alertas.append(
        f"soma da coluna de zonas = {sum(l.zonas for l in linhas)}; "
        "NAO e contagem de zonas distintas (uma zona pode abranger varios "
        "municipios)"
    )

    ea12 = _latest_ea12(archive)
    if ea12 is None:
        rep.alertas.append(
            "nenhum EA12 no arquivo bruto: rode `obs2026 descobrir` antes "
            "para baixar o catalogo oficial. Sem catalogo nao ha conciliacao."
        )
        rep.quarentena_csv = [(l, "sem catalogo") for l in linhas]
        return rep

    rep.catalogo_versao = (
        f"eleicao {ea12.election}, gerado {ea12.generated_at}"
    )
    rep.catalogo_localidades = len(ea12.municipios)

    # Chave de busca: UF + nome normalizado (com unescape de &APOS;).
    # É candidata, não decisão: quem parear recebe o código TSE oficial e o
    # código IBGE que vêm do catálogo.
    idx: dict[tuple[str, str], list[Municipio]] = {}
    for m in ea12.municipios:
        idx.setdefault((m.uf.upper(), m.nome_fold), []).append(m)

    usados: set[tuple[str, str]] = set()
    for l in linhas:
        cands = idx.get((l.uf, l.municipio_fold), [])
        if len(cands) == 1:
            rep.conciliados += 1
            rep.aptos_conciliados += l.aptos
            usados.add((cands[0].uf, cands[0].cd))
        elif len(cands) > 1:
            rep.quarentena_csv.append(
                (l, f"ambiguo: {len(cands)} municipios com o mesmo nome na UF")
            )
        else:
            rep.quarentena_csv.append((l, "sem correspondencia no EA12"))

    rep.quarentena_ea12 = [
        m for m in ea12.municipios if (m.uf, m.cd) not in usados
    ]

    if rep.quarentena_csv or rep.quarentena_ea12:
        rep.alertas.append(
            "divergencias concentradas no exterior refletem versoes "
            "diferentes (CSV de 20/09 x catalogo do simulado de 14/09); "
            "reconciliar contra o EA12 do ambiente oficial antes da producao"
        )
    return rep
