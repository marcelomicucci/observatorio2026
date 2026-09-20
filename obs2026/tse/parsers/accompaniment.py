"""Parser do EA14 (acompanhamento Brasil) e EA15 (acompanhamento UF).

Estes dois arquivos são o mecanismo de detecção de mudança do coletor, e é
deles que sai a economia que torna o projeto operável (§8.1).

Uma varredura municipal completa dos três cargos são ~16,9 mil arquivos: a
20 req/s, cerca de 14 minutos. Mas o EA15 de uma UF lista TODOS os municípios
dela com `and` (andamento) e `ht` (hora da totalização). Logo:

    2 eleições x (1 EA14 + 27 EA15)  =  56 requisições por ciclo

detectam exatamente quais municípios mudaram. O EA20 é buscado só para esses.
A 20 s de cadência isso custa ~2,8 req/s, dentro do teto interno de 20 e muito
abaixo do limite de 100 req/s do TSE.

A FAQ avisa que EA14/EA15 podem chegar antes OU depois do EA20. Por isso a
detecção nunca é a única via: a varredura de reconciliação (§8.1) busca o EA20
de todas as unidades periodicamente, independentemente do que o acompanhamento
disser. O acompanhamento acelera; ele não é fonte de verdade.

Inconsistência de contrato a registrar: o EA20 usa `tpabr='mu'` para município,
enquanto o EA15 usa `tpabr='mun'`. `norm_tpabr` unifica para 'mu' e preserva o
valor original.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .common import as_code, as_int, as_int_opt, as_text, parse_dt, require
from .ea20 import ElectorateCounts, SectionCounts, _electorate, _sections


def norm_tpabr(v: str) -> str:
    v = str(v or "").lower()
    return "mu" if v in ("mu", "mun") else v


@dataclass(frozen=True, slots=True)
class UnitProgress:
    """Andamento de uma unidade territorial em um instante."""

    tpabr: str                  # br | uf | mu (normalizado)
    tpabr_raw: str
    cdabr: str
    andamento: str              # n = não recebido, p = parcial, f = finalizado
    totalized_at: datetime | None    # dt + ht
    sections: SectionCounts | None
    electorate: ElectorateCounts | None
    # Só no EA14 (nível UF): contagem de municípios por andamento.
    mun_nao_recebidos: int | None = None
    mun_parciais: int | None = None
    mun_finalizados: int | None = None

    @property
    def key(self) -> str:
        return f"{self.tpabr}:{self.cdabr}"

    @property
    def is_final(self) -> bool:
        return self.andamento == "f"

    def change_token(self) -> str:
        """Assinatura barata de mudança.

        Compara andamento, hora de totalização e seções totalizadas. Se
        qualquer um mudou, a unidade entra na fila do EA20. Não usamos `idg`
        do arquivo pai porque ele muda a cada geração, mesmo sem mudança na
        unidade — isso reenfileiraria o país inteiro a cada ciclo.
        """
        ht = self.totalized_at.isoformat() if self.totalized_at else "-"
        st = self.sections.st if self.sections else -1
        sa = self.sections.sa if self.sections else -1
        return f"{self.andamento}|{ht}|{st}|{sa}"


@dataclass(frozen=True, slots=True)
class Accompaniment:
    kind: str                   # 'ea14' | 'ea15'
    election: str
    turno: str
    fase: str
    generated_at: datetime | None
    idg: str | None
    uf: str | None              # preenchido no EA15
    units: tuple[UnitProgress, ...]

    def by_key(self) -> dict[str, UnitProgress]:
        return {u.key: u for u in self.units}

    @property
    def municipios(self) -> tuple[UnitProgress, ...]:
        return tuple(u for u in self.units if u.tpabr == "mu")

    @property
    def ufs(self) -> tuple[UnitProgress, ...]:
        return tuple(u for u in self.units if u.tpabr == "uf")


def parse(payload: dict, kind: str, uf: str | None = None) -> Accompaniment:
    require(payload, "ele", "t", "abr", ctx=kind.upper())
    units: list[UnitProgress] = []
    for a in payload["abr"]:
        require(a, "tpabr", "cdabr", ctx=f"{kind.upper()}.abr")
        raw_tp = str(a["tpabr"])
        units.append(UnitProgress(
            tpabr=norm_tpabr(raw_tp),
            tpabr_raw=raw_tp,
            cdabr=as_code(a["cdabr"], "abr.cdabr"),
            andamento=str(a.get("and", "") or ""),
            totalized_at=parse_dt(a.get("dt"), a.get("ht"), "abr.dt/ht"),
            sections=_sections(a["s"]) if "s" in a else None,
            electorate=_electorate(a["e"]) if "e" in a else None,
            mun_nao_recebidos=as_int_opt(a.get("munnr"), "abr.munnr"),
            mun_parciais=as_int_opt(a.get("munpt"), "abr.munpt"),
            mun_finalizados=as_int_opt(a.get("munf"), "abr.munf"),
        ))
    return Accompaniment(
        kind=kind,
        election=as_code(payload["ele"], "ele"),
        turno=as_code(payload["t"], "t"),
        fase=str(payload.get("f", "") or ""),
        generated_at=parse_dt(payload.get("dg"), payload.get("hg"), "dg/hg"),
        idg=str(payload.get("idg") or "") or None,
        uf=(str(uf).lower() if uf else None),
        units=tuple(units),
    )
