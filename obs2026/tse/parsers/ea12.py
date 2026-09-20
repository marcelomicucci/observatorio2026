"""Parser do EA12 (`mun-e######-cm.json`) — catálogo de municípios.

Achado que muda o projeto: o EA12 traz `cdi`, que é o **código IBGE** de cada
município. A especificação (§2.1) previa construir a correspondência TSE↔IBGE
por nome, com revisão de ambiguidades. Não é preciso: a correspondência é
oficial e vem na própria fonte, preenchida nos 5.571 municípios brasileiros
(7 dígitos, nenhum vazio). Localidades do exterior (`zz`) têm `cdi` vazio, o
que é correto — não são municípios.

O pareamento por nome continua existindo, mas só para conciliar a base de
eleitorado recebida, e como *candidato* a ser confirmado pelo código.

Armadilha real: os nomes vêm com entidade HTML em maiúsculas (`&APOS;`).
Sem unescape, 45 municípios brasileiros deixam de parear com qualquer
catálogo externo. `as_text` resolve.

Estrutura:
    { dg, hg, idg, f,
      abr: [ {cd: 'ac', ds: 'ACRE',
              mu: [ {cd: '01120', cdi: '1200013', nm, c: 's|n', z: ['0008']} ]} ] }
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .common import ParseError, as_code, as_text, fold, parse_dt, require


@dataclass(frozen=True, slots=True)
class Municipio:
    uf: str
    cd: str              # código TSE, 5 dígitos, zeros à esquerda preservados
    cd_ibge: str | None  # `cdi` — vazio no exterior
    nome: str
    nome_fold: str
    capital: bool
    zonas: tuple[str, ...]

    @property
    def is_exterior(self) -> bool:
        return self.uf == "zz"


@dataclass(frozen=True, slots=True)
class Ea12:
    election: str | None
    generated_at: datetime | None
    idg: str | None
    fase: str
    municipios: tuple[Municipio, ...]

    def by_code(self) -> dict[tuple[str, str], Municipio]:
        return {(m.uf, m.cd): m for m in self.municipios}

    def by_ibge(self) -> dict[str, Municipio]:
        return {m.cd_ibge: m for m in self.municipios if m.cd_ibge}

    def por_uf(self) -> dict[str, list[Municipio]]:
        out: dict[str, list[Municipio]] = {}
        for m in self.municipios:
            out.setdefault(m.uf, []).append(m)
        return out

    @property
    def zonas_unicas(self) -> set[tuple[str, str]]:
        """Zonas distintas por UF.

        A quantidade de zonas NÃO é somável por município: uma zona pode
        abranger mais de um município (§2.1). Esta propriedade existe para
        tornar esse fato verificável, não para virar denominador.
        """
        return {(m.uf, z) for m in self.municipios for z in m.zonas}


def parse(payload: dict, election: str | None = None) -> Ea12:
    require(payload, "abr", ctx="EA12")
    muns: list[Municipio] = []
    for a in payload["abr"]:
        uf = str(a.get("cd", "")).lower()
        if not uf:
            raise ParseError("EA12: abrangencia sem UF")
        for m in a.get("mu", []):
            require(m, "cd", "nm", ctx="EA12.mu")
            cd = as_code(m["cd"], "mu.cd")
            if len(cd) != 5 or not cd.isdigit():
                raise ParseError(
                    f"EA12: codigo de municipio fora do formato: {cd!r} ({uf})"
                )
            cdi = str(m.get("cdi") or "").strip() or None
            if cdi is not None and (len(cdi) != 7 or not cdi.isdigit()):
                raise ParseError(f"EA12: cdi fora do formato IBGE: {cdi!r}")
            nome = as_text(m["nm"])
            muns.append(Municipio(
                uf=uf, cd=cd, cd_ibge=cdi, nome=nome, nome_fold=fold(nome),
                capital=str(m.get("c", "n")).lower() == "s",
                zonas=tuple(as_code(z, "mu.z") for z in m.get("z", []) or ()),
            ))
    return Ea12(
        election=election,
        generated_at=parse_dt(payload.get("dg"), payload.get("hg"), "dg/hg"),
        idg=str(payload.get("idg") or "") or None,
        fase=str(payload.get("f", "") or ""),
        municipios=tuple(muns),
    )
