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
sobre os **votos a concorrentes** — `vv + van + vansj`, que é exatamente
`vvc`. Não é sobre `vv`, e não é sobre o percentual que o TSE exibe na tela.

Fonte: o `leiame.pdf` do repositório de dados eleitorais do TSE define o
sistema majoritário como "a maioria dos votos a concorrentes (votos válidos,
votos anulados e votos anulados sub judice)" e diz que o segundo turno ocorre
quando o candidato "não alcance maioria absoluta destes votos a concorrentes
no primeiro turno (mínimo de 50% + 1)".

Isso é coerente com a Constituição, art. 77 §2º: maioria absoluta de votos,
"não computados os em branco e os nulos". Voto **anulado** de candidato não é
voto **nulo** do eleitor — `van` e `vn` são campos distintos no EA20 — logo o
anulado permanece no denominador.

Por que isso não é detalhe: no arquivo nacional do simulado, `vv` é
100.982.116 e `vvc` é 120.704.576. O mesmo candidato tem 9,90% sobre `vv` e
8,28% sobre `vvc`. Usar `vv` inflaria todos os percentuais em cerca de 20% e
poderia anunciar vitória em primeiro turno que não existe.

Em 2022 a distinção ficou invisível, porque nenhum candidato a presidente
teve votos anulados: `vvc` era igual a `vv`. Nos dados de teste de 2026 há
candidato anulado e outro sub judice, e aí os dois divergem. Um produto
calibrado só contra 2022 herdaria o erro sem perceber.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..tse.parsers.ea20 import DVT_VALIDO, Ea20


class Base(str, Enum):
    #: Σ vap — soma dos votos de TODOS os candidatos, inclusive anulados e
    #: sub judice. É o denominador de `pvap`. Verificado com erro zero.
    NOMINAL_TSE = "nominal_tse"
    #: `vv` — apenas candidatos com destinação 'Válido'. Útil para comparar
    #: candidatos aptos entre si; NÃO é a base da maioria absoluta.
    VALIDOS = "validos"
    #: `vvc` — votos a concorrentes: válidos + anulados + anulados sub
    #: judice. **É a base legal da maioria absoluta** (leiame do TSE;
    #: CF art. 77 §2º).
    VALIDOS_COMPUTADOS = "validos_computados"
    #: `tv` — total de votos, incluindo brancos e nulos. No Senado vai a 2x o
    #: comparecimento.
    TOTAL_VOTOS = "total_votos"
    #: `e.c` — comparecentes. Denominador de "votos por eleitor" no Senado.
    COMPARECIMENTO = "comparecimento"


LABELS = {
    Base.NOMINAL_TSE: "% sobre votos nominais (denominador do TSE)",
    Base.VALIDOS: "% sobre votos válidos (exclui anulados e sub judice)",
    Base.VALIDOS_COMPUTADOS:
        "% sobre votos a concorrentes (base da maioria absoluta)",
    Base.TOTAL_VOTOS: "% sobre o total de votos",
    Base.COMPARECIMENTO: "votos por 100 comparecentes",
}

#: Só esta base decide segundo turno. Ver o cabeçalho do módulo: são os
#: "votos a concorrentes" do leiame do TSE, isto é `vvc`.
BASE_MAIORIA_ABSOLUTA = Base.VALIDOS_COMPUTADOS


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
