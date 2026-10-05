"""Parser do EA11 (`ele-c.json`) — configuração das eleições.

É o ponto de entrada da descoberta. Dele saem ciclo, pleito, eleições, turnos,
cargos por abrangência e os diretórios declarados de cada tipo de arquivo.

Estrutura confirmada no simulado (20/09/2026):

    { dg, hg, f, idg,
      arq: [ {tp, dir}, ... ],
      pl:  [ {cd, cdpr, c, dt, dtlim,
              e: [ {cd, cdt2, sqele, nm, t, tp,
                    abr: [ {cd, cp: [ {cd, ds, tp} ]} ]} ]} ] }

`cdt2` traz o código da eleição de 2º turno (21270 -> 21271, 21272 -> 21273).
Guardar esse vínculo agora evita ter de adivinhá-lo em outubro: o 2º turno é
eleição própria, com parâmetros próprios (§3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .common import ParseError, as_code, as_text, parse_dt, require


@dataclass(frozen=True, slots=True)
class Cargo:
    cd: str          # '1' Presidente, '3' Governador, '5' Senador...
    ds: str
    tp: str          # '1' majoritário, '2' proporcional


@dataclass(frozen=True, slots=True)
class Abrangencia:
    cd: str                     # 'br'
    cargos: tuple[Cargo, ...]


@dataclass(frozen=True, slots=True)
class Eleicao:
    cd: str
    cd_turno2: str | None       # `cdt2`
    sq: str
    nome: str
    turno: str
    tipo: str
    abrangencias: tuple[Abrangencia, ...]

    @property
    def cargos(self) -> tuple[Cargo, ...]:
        return tuple(c for a in self.abrangencias for c in a.cargos)

    def tem_cargo(self, cd: str) -> bool:
        return any(c.cd == str(cd) for c in self.cargos)


@dataclass(frozen=True, slots=True)
class Pleito:
    cd: str
    cd_pr: str | None
    ciclo: str                  # 'ele2026' — vai para a URL
    data: date | None
    data_limite: date | None
    eleicoes: tuple[Eleicao, ...]


#: Valor do campo `f` nos arquivos de produção. O simulado manda 's'. Até
#: 04/10/2026 o código esperava 'f' — nunca visto, porque só havia simulado;
#: o catálogo oficial de 2026 (e os de 2022) trazem 'o'.
FASE_OFICIAL = "o"


@dataclass(frozen=True, slots=True)
class Ea11:
    generated_at: datetime | None
    idg: str | None
    fase: str                   # 's' simulado / 'o' oficial
    diretorios: tuple[dict, ...]
    pleitos: tuple[Pleito, ...]

    def pleito(self, cd: str) -> Pleito:
        for p in self.pleitos:
            if p.cd == str(cd):
                return p
        raise ParseError(f"pleito {cd!r} ausente no EA11")

    def eleicao(self, cd: str) -> tuple[Pleito, Eleicao]:
        for p in self.pleitos:
            for e in p.eleicoes:
                if e.cd == str(cd):
                    return p, e
        raise ParseError(f"eleicao {cd!r} ausente no EA11")


def _date(s) -> date | None:
    dt = parse_dt(s, "00:00:00", "ea11.data") if s else None
    return dt.date() if dt else None


def parse(payload: dict) -> Ea11:
    require(payload, "pl", ctx="EA11")
    pleitos = []
    for p in payload["pl"]:
        require(p, "cd", "c", "e", ctx="EA11.pl")
        eleicoes = []
        for e in p["e"]:
            require(e, "cd", "t", ctx="EA11.pl.e")
            abrs = []
            for a in e.get("abr", []):
                abrs.append(Abrangencia(
                    cd=str(a.get("cd", "")).lower(),
                    cargos=tuple(
                        Cargo(cd=as_code(c["cd"], "cp.cd"),
                              ds=as_text(c.get("ds")),
                              tp=str(c.get("tp", "") or ""))
                        for c in a.get("cp", [])
                    ),
                ))
            eleicoes.append(Eleicao(
                cd=as_code(e["cd"], "e.cd"),
                cd_turno2=(as_code(e["cdt2"], "e.cdt2")
                           if str(e.get("cdt2") or "").strip() else None),
                sq=str(e.get("sqele") or ""),
                nome=as_text(e.get("nm")),
                turno=as_code(e["t"], "e.t"),
                tipo=str(e.get("tp", "") or ""),
                abrangencias=tuple(abrs),
            ))
        pleitos.append(Pleito(
            cd=as_code(p["cd"], "pl.cd"),
            cd_pr=str(p.get("cdpr") or "") or None,
            ciclo=str(p["c"]),
            data=_date(p.get("dt")),
            data_limite=_date(p.get("dtlim")),
            eleicoes=tuple(eleicoes),
        ))
    return Ea11(
        generated_at=parse_dt(payload.get("dg"), payload.get("hg"), "dg/hg"),
        idg=str(payload.get("idg") or "") or None,
        fase=str(payload.get("f", "") or ""),
        diretorios=tuple(dict(a) for a in payload.get("arq", [])),
        pleitos=tuple(pleitos),
    )
