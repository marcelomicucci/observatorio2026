"""Construção das entradas do modelo: M0 (diagnóstico) e M1 (produção).

**M0 — extrapolação ingênua (§4.4).** Aplica a preferência, o comparecimento
e a taxa de válidos *nacionais já contados* a todo o eleitorado pendente. É
exatamente o que a especificação proíbe como produto e exige como baseline: o
gate de lançamento do forecast (§11.2) é superar o M0 no erro mediano. Um
modelo que não vence a extrapolação ingênua não merece ir ao ar.

**M1 — estratificação municipal (§4.4).** Cada unidade usa o próprio dado
quando tem, encolhido na direção do estrato conforme o volume observado; usa
o estrato quando não tem; e nunca usa zero como preferência.

A regularização importa mais do que parece. Um município com 40 votos
contados e 12 mil pendentes, se usar a preferência local crua, injeta ruído
amplificado 300 vezes. O encolhimento

    w = n_observado / (n_observado + k0)

faz o peso do dado local crescer com a evidência. `k0` é proposta e sai do
backtest, não de intuição.

O viés de ordem de chegada (§4.5) entra por aqui: o estrato é escolhido para
agrupar unidades que tendem a chegar juntas e votar parecido — UF, porte
eleitoral e capital/interior. Variáveis adicionais só entram com ganho medido
em validação.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tse.parsers.ea20 import Ea20
from . import pending as pend
from .simulate import UnitInput

#: Peso a partir do qual o dado local domina o do estrato. Proposta; o valor
#: final sai do backtest (§11.2).
K0_ENCOLHIMENTO = 500.0

#: Faixas de porte eleitoral para estratificação.
PORTES = ((0, 5_000, "ate5k"), (5_000, 20_000, "5k-20k"),
          (20_000, 100_000, "20k-100k"), (100_000, 500_000, "100k-500k"),
          (500_000, 10**12, "500k+"))


def porte(eleitorado: int) -> str:
    for lo, hi, nome in PORTES:
        if lo <= eleitorado < hi:
            return nome
    return "500k+"


def estrato(uf: str, eleitorado: int, capital: bool = False) -> str:
    """UF x porte x capital. Agrupa quem chega junto e vota parecido (§4.5).

    A UF vem do alvo coletado, não do payload: o EA20 municipal traz
    `cdabr` com o código do município e nada diz sobre a UF.
    """
    return f"{uf.lower()}|{porte(eleitorado)}|{'cap' if capital else 'int'}"


@dataclass(frozen=True, slots=True)
class UnitObservation:
    """Observação de uma unidade, já com sua geografia e estrato."""

    unit: str
    uf: str
    stratum: str
    result: Ea20
    capital: bool = False

    @property
    def obs(self):
        return pend.observed_of(self.result)

    @property
    def pending(self):
        return pend.of(self.result)


def _normalize(d: dict[str, float]) -> dict[str, float]:
    s = sum(d.values())
    return {k: v / s for k, v in d.items()} if s > 0 else {}


def _agregar(obs_list: list[UnitObservation], candidatos: list[str]) -> dict:
    """Agrega votos e denominadores de um conjunto de unidades."""
    votos = {c: 0 for c in candidatos}
    validos = comparec = apurado = total_votos = 0
    for o in obs_list:
        b = o.obs
        for c, v in b.votos_por_candidato.items():
            if c in votos:
                votos[c] += v
        validos += b.validos
        comparec += b.comparecimento
        apurado += b.apurado
        total_votos += b.total_votos
    return {
        "votos": votos, "validos": validos, "comparecimento": comparec,
        "apurado": apurado, "total_votos": total_votos,
        # Sempre razão de somas, nunca média de razões (§11.1).
        "turnout": (comparec / apurado) if apurado else None,
        "valid_rate": (validos / total_votos) if total_votos else None,
        "q": (validos / comparec) if comparec else None,
        "pref": _normalize({c: float(v) for c, v in votos.items()}),
    }


def _fallback(nacional: dict, candidatos: list[str], seats: int) -> tuple:
    """Valores usados quando não há nenhuma evidência ainda.

    Nunca devolve zero como preferência (§4.4): sem dado, a preferência é
    uniforme e a incerteza é ampliada — não uma afirmação de que ninguém
    votaria no candidato.
    """
    turnout = nacional["turnout"] if nacional["turnout"] else 0.80
    vr = nacional["valid_rate"] if nacional["valid_rate"] else 0.90
    q = nacional["q"] if nacional["q"] else 0.90 * seats
    pref = nacional["pref"] or {c: 1.0 / len(candidatos) for c in candidatos}
    return turnout, vr, q, pref


def build_m0(observacoes: list[UnitObservation], candidatos: list[str],
             *, seats: int = 1) -> list[UnitInput]:
    """Baseline ingênuo: o percentual nacional contado vale para todo lugar.

    Existe para ser batido, não para ir ao ar (§4.4, §11.2).
    """
    nac = _agregar(observacoes, candidatos)
    turnout, vr, q, pref = _fallback(nac, candidatos, seats)
    saida = []
    for o in observacoes:
        p = o.pending
        b = o.obs
        saida.append(UnitInput(
            unit=o.unit, uf=o.uf, stratum="nacional",
            pending_lower=p.lower, pending_upper=p.upper,
            observed_votes=dict(b.votos_por_candidato),
            observed_valid=b.validos, observed_anulados=b.anulados,
            turnout_hat=turnout, valid_rate_hat=vr, pref_hat=dict(pref),
            electorate_total=p.total, has_own_data=True, q_hat=q,
        ))
    return saida


def build_m1(observacoes: list[UnitObservation], candidatos: list[str],
             *, seats: int = 1, k0: float = K0_ENCOLHIMENTO) -> list[UnitInput]:
    """Estratificação municipal com encolhimento para o estrato."""
    nac = _agregar(observacoes, candidatos)
    t_nac, vr_nac, q_nac, pref_nac = _fallback(nac, candidatos, seats)

    por_estrato: dict[str, list[UnitObservation]] = {}
    for o in observacoes:
        por_estrato.setdefault(o.stratum, []).append(o)
    agr_estrato = {k: _agregar(v, candidatos) for k, v in por_estrato.items()}

    saida = []
    for o in observacoes:
        p, b = o.pending, o.obs
        e = agr_estrato[o.stratum]

        # nível do estrato, com o nacional como rede quando o estrato é vazio
        t_e = e["turnout"] if e["turnout"] is not None else t_nac
        vr_e = e["valid_rate"] if e["valid_rate"] is not None else vr_nac
        q_e = e["q"] if e["q"] is not None else q_nac
        pref_e = e["pref"] or pref_nac

        n = b.validos
        w = n / (n + k0) if (n + k0) > 0 else 0.0
        tem_dado = n > 0

        t_loc = b.comparecimento_observado
        vr_loc = b.taxa_valida
        q_loc = b.escolhas_por_comparecente
        pref_loc = b.preferencias()

        turnout = w * t_loc + (1 - w) * t_e if t_loc is not None else t_e
        vr = w * vr_loc + (1 - w) * vr_e if vr_loc is not None else vr_e
        q = w * q_loc + (1 - w) * q_e if q_loc is not None else q_e
        if pref_loc:
            pref = _normalize({
                c: w * pref_loc.get(c, 0.0) + (1 - w) * pref_e.get(c, 0.0)
                for c in candidatos
            })
        else:
            pref = dict(pref_e)

        saida.append(UnitInput(
            unit=o.unit, uf=o.uf, stratum=o.stratum,
            pending_lower=p.lower, pending_upper=p.upper,
            observed_votes=dict(b.votos_por_candidato),
            observed_valid=b.validos, observed_anulados=b.anulados,
            turnout_hat=min(max(turnout, 1e-4), 0.9999),
            valid_rate_hat=min(max(vr, 1e-4), 0.9999),
            pref_hat=pref, electorate_total=p.total,
            has_own_data=tem_dado,
            q_hat=min(max(q, 1e-4), seats - 1e-4),
        ))
    return saida
