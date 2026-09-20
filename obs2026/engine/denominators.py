"""Denominadores. Um módulo só para isso, de propósito.

O §9 exige que toda resposta da API declare o denominador usado. A razão fica
clara quando se olha o mesmo candidato sob as três bases, no arquivo nacional
real do simulado:

    candidato 57, 10.503.573 votos, destinação 'Anulado sub judice'
        sobre a soma dos votos de candidatos   8,70%   <- o que o TSE exibe
        sobre votos válidos (vv)              10,40%   <- mas ele não está
                                                           em `vv`
        sobre o total de votos (tv)            7,56%

Três números, o mesmo candidato, nenhum errado. O que seria errado é publicar
um deles sem dizer qual é.

**A regra que mais importa:** a maioria absoluta do primeiro turno se apura
sobre **votos válidos** (`vv`), não sobre o percentual que o TSE exibe na
tela. Um candidato pode aparecer com 49% no painel do TSE e ter passado dos
50% dos válidos, ou o contrário. O motor usa `PCT_VALIDOS` para decidir
segundo turno e `PCT_NOMINAL_TSE` para conferir com a fonte.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..tse.parsers.ea20 import DVT_VALIDO, Ea20


class Base(str, Enum):
    #: Σ vap — soma dos votos de TODOS os candidatos, inclusive anulados e
    #: sub judice. É o denominador de `pvap`. Verificado com erro zero.
    NOMINAL_TSE = "nominal_tse"
    #: `vv` — apenas candidatos com destinação 'Válido'. Base legal da
    #: maioria absoluta.
    VALIDOS = "validos"
    #: `vvc` — válidos + anulados + sub judice, do bloco `v`.
    VALIDOS_COMPUTADOS = "validos_computados"
    #: `tv` — total de votos, incluindo brancos e nulos. No Senado vai a 2x o
    #: comparecimento.
    TOTAL_VOTOS = "total_votos"
    #: `e.c` — comparecentes. Denominador de "votos por eleitor" no Senado.
    COMPARECIMENTO = "comparecimento"


LABELS = {
    Base.NOMINAL_TSE: "% sobre votos nominais (denominador do TSE)",
    Base.VALIDOS: "% sobre votos válidos",
    Base.VALIDOS_COMPUTADOS: "% sobre válidos computados",
    Base.TOTAL_VOTOS: "% sobre o total de votos",
    Base.COMPARECIMENTO: "votos por 100 comparecentes",
}

#: Só esta base decide segundo turno.
BASE_MAIORIA_ABSOLUTA = Base.VALIDOS


@dataclass(frozen=True, slots=True)
class Denominators:
    nominal_tse: int
    validos: int
    validos_computados: int
    total_votos: int
    comparecimento: int

    def get(self, base: Base) -> int:
        return {
            Base.NOMINAL_TSE: self.nominal_tse,
            Base.VALIDOS: self.validos,
            Base.VALIDOS_COMPUTADOS: self.validos_computados,
            Base.TOTAL_VOTOS: self.total_votos,
            Base.COMPARECIMENTO: self.comparecimento,
        }[base]

    def pct(self, votos: int, base: Base) -> float | None:
        """Percentual explícito. Denominador zero devolve None, nunca zero.

        §9: "Ausência é `null` com motivo, nunca zero." Um município com zero
        voto contado tem percentual indefinido, não 0%.
        """
        den = self.get(base)
        return (100.0 * votos / den) if den > 0 else None


def of(r: Ea20) -> Denominators:
    return Denominators(
        nominal_tse=sum(c.votos for c in r.cargo.candidatos),
        validos=r.votes.vv,
        validos_computados=r.votes.vvc,
        total_votos=r.votes.tv,
        comparecimento=r.electorate.c,
    )


def escolhas_por_comparecente(r: Ea20) -> float | None:
    """`q` do §4.6: média de escolhas válidas por comparecente.

    No Senado de 2026 vai de 0 a 2. Esta é a grandeza que o modelo precisa
    projetar — não "metade dos votos". Dividir a votação de cada candidato por
    dois é errado e o §4.6 proíbe explicitamente.
    """
    c = r.electorate.c
    return (r.votes.vv / c) if c > 0 else None
