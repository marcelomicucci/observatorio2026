"""Construção de URLs da divulgação TSE.

Este módulo é o ponto mais sensível do coletor: o TSE bloqueia o IP após uma
série de 404, e a causa documentada mais comum é código de município sem os
zeros à esquerda. Por isso nada aqui aceita `int`; todo código é string e é
validado contra o formato antes de virar URL.

Padrões confirmados na página técnica do TSE (consulta em 2026-09-20) e
conferidos contra o servidor de simulado no mesmo dia:

    ele-c.json (EA11)
        {base}/{ambiente}/comum/config/ele-c.json
    mun-cm.json (EA12)
        {base}/{ambiente}/{ciclo}/{ele}/config/mun-e{ele:0>6}-cm.json
    EA14 (acompanhamento Brasil)
        {base}/{ambiente}/{ciclo}/{ele}/dados/br/br-e{ele:0>6}-ab.json
    EA15 (acompanhamento UF)
        {base}/{ambiente}/{ciclo}/{ele}/dados/{uf}/{uf}-e{ele:0>6}-ab.json
    EA20 (resultado unificado)
        br:  .../dados/br/br-c{cargo:0>4}-e{ele:0>6}-u.json
        uf:  .../dados/{uf}/{uf}-c{cargo:0>4}-e{ele:0>6}-u.json
        mun: .../dados/{uf}/{uf}{mun:0>5}-c{cargo:0>4}-e{ele:0>6}-u.json

O diretório de cada tipo também vem declarado em `ele-c.json > arq[]`; o
`UrlBuilder` confere os templates recebidos contra os padrões acima e registra
divergência em vez de assumir que o conhecido continua valendo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

UF_CODES = frozenset(
    """ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr
    sc sp se to zz""".split()
)

#: UFs com disputa majoritária estadual. ZZ (exterior) nunca entra.
UF_DISPUTAS = frozenset(UF_CODES - {"zz"})

_RE_ELECTION = re.compile(r"^\d{1,6}$")
_RE_CARGO = re.compile(r"^\d{1,4}$")
_RE_MUN = re.compile(r"^\d{5}$")
_RE_CICLO = re.compile(r"^[a-z0-9]{1,16}$")
_RE_AMBIENTE = re.compile(r"^[a-z0-9_-]{1,32}$")


class UrlError(ValueError):
    """URL malformada. Nunca deve virar requisição — 404 custa bloqueio."""


def fmt_election(cd: str) -> str:
    """`21270` -> `021270`. Seis dígitos, zeros à esquerda preservados."""
    cd = str(cd).strip()
    if not _RE_ELECTION.match(cd):
        raise UrlError(f"codigo de eleicao invalido: {cd!r}")
    return cd.zfill(6)


def fmt_cargo(cd: str) -> str:
    """`1` -> `0001`, `5` -> `0005`."""
    cd = str(cd).strip()
    if not _RE_CARGO.match(cd):
        raise UrlError(f"codigo de cargo invalido: {cd!r}")
    return cd.zfill(4)


def fmt_mun(cd: str) -> str:
    """Código TSE de município com exatamente 5 dígitos.

    Não aceita int nem string curta: `1120` é erro, não `01120`. Quem tiver um
    código de 4 dígitos tem um bug de parsing a montante, e adivinhar aqui
    esconderia esse bug até virar uma série de 404.
    """
    if isinstance(cd, int):
        raise UrlError(
            f"codigo de municipio veio como int ({cd}); "
            "zeros a esquerda ja foram perdidos no parsing"
        )
    cd = str(cd).strip()
    if not _RE_MUN.match(cd):
        raise UrlError(f"codigo de municipio deve ter 5 digitos: {cd!r}")
    return cd


def norm_uf(uf: str) -> str:
    uf = str(uf).strip().lower()
    if uf not in UF_CODES:
        raise UrlError(f"UF desconhecida: {uf!r}")
    return uf


@dataclass(frozen=True, slots=True)
class Target:
    """Uma unidade coletável. `key` é estável e serve de chave de fila."""

    kind: str          # ea11 | ea12 | ea14 | ea15 | ea20
    url: str
    election: str | None = None
    cargo: str | None = None
    uf: str | None = None
    mun: str | None = None
    abr: str | None = None   # br | uf | mu

    @property
    def key(self) -> str:
        parts = [self.kind, self.election or "-", self.cargo or "-",
                 self.uf or "-", self.mun or "-"]
        return ":".join(parts)


@dataclass(frozen=True)
class UrlBuilder:
    base: str
    ambiente: str
    ciclo: str
    allowed_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if not self.base or not self.ambiente:
            raise UrlError(
                "ambiente sem base/ambiente configurados — o TSE ainda nao "
                "publicou os codigos de producao; coleta recusada"
            )
        if not _RE_AMBIENTE.match(self.ambiente):
            raise UrlError(f"ambiente invalido: {self.ambiente!r}")
        if not _RE_CICLO.match(self.ciclo or ""):
            raise UrlError(f"ciclo invalido: {self.ciclo!r}")
        host = urlsplit(self.base).hostname or ""
        if host not in self.allowed_hosts:
            raise UrlError(f"host nao permitido: {host!r}")

    # -- raiz ------------------------------------------------------------

    def _root(self) -> str:
        return f"{self.base.rstrip('/')}/{self.ambiente}"

    # -- alvos -----------------------------------------------------------

    def ea11(self) -> Target:
        """Configuração das eleições. Único arquivo fora da árvore de ciclo."""
        return Target("ea11", f"{self._root()}/comum/config/ele-c.json")

    def ea12(self, election: str) -> Target:
        e = fmt_election(election)
        url = f"{self._root()}/{self.ciclo}/{election}/config/mun-e{e}-cm.json"
        return Target("ea12", url, election=election)

    def ea14(self, election: str) -> Target:
        e = fmt_election(election)
        url = f"{self._root()}/{self.ciclo}/{election}/dados/br/br-e{e}-ab.json"
        return Target("ea14", url, election=election, abr="br")

    def ea15(self, election: str, uf: str) -> Target:
        e = fmt_election(election)
        u = norm_uf(uf)
        url = f"{self._root()}/{self.ciclo}/{election}/dados/{u}/{u}-e{e}-ab.json"
        return Target("ea15", url, election=election, uf=u, abr="uf")

    def ea20_br(self, election: str, cargo: str) -> Target:
        e, c = fmt_election(election), fmt_cargo(cargo)
        url = (f"{self._root()}/{self.ciclo}/{election}/dados/br/"
               f"br-c{c}-e{e}-u.json")
        return Target("ea20", url, election=election, cargo=cargo, abr="br")

    def ea20_uf(self, election: str, cargo: str, uf: str) -> Target:
        e, c, u = fmt_election(election), fmt_cargo(cargo), norm_uf(uf)
        url = (f"{self._root()}/{self.ciclo}/{election}/dados/{u}/"
               f"{u}-c{c}-e{e}-u.json")
        return Target("ea20", url, election=election, cargo=cargo, uf=u, abr="uf")

    def ea20_mun(self, election: str, cargo: str, uf: str, mun: str) -> Target:
        e, c = fmt_election(election), fmt_cargo(cargo)
        u, m = norm_uf(uf), fmt_mun(mun)
        url = (f"{self._root()}/{self.ciclo}/{election}/dados/{u}/"
               f"{u}{m}-c{c}-e{e}-u.json")
        return Target("ea20", url, election=election, cargo=cargo,
                      uf=u, mun=m, abr="mu")

    # -- verificação contra o ele-c.json ---------------------------------

    def check_declared_dirs(self, arq: list[dict]) -> list[str]:
        """Confronta `ele-c.json > arq[]` com os padrões que este módulo usa.

        Devolve a lista de divergências. Não corrige nada automaticamente:
        uma mudança de layout no TSE precisa de decisão humana, não de um
        fallback silencioso que pode gerar 404 em massa.
        """
        expected = {
            "cm": "<base>/<ambiente>/<ciclo>/<cd_eleicao>/config",
            "ab": "<base>/<ambiente>/<ciclo>/<cd_eleicao>/dados/<uf>",
            "u": "<base>/<ambiente>/<ciclo>/<cd_eleicao>/dados/<uf>",
        }
        problems = []
        seen = {a.get("tp"): a.get("dir") for a in arq}
        for tp, want in expected.items():
            got = seen.get(tp)
            if got is None:
                problems.append(f"ele-c.json nao declara arq.tp={tp!r}")
            elif got != want:
                problems.append(
                    f"layout de {tp!r} mudou: esperado {want!r}, veio {got!r}"
                )
        return problems
