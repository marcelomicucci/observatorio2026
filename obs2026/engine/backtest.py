"""Backtest por cortes de cobertura (§11.2).

Dada uma disputa **encerrada**, esconde parte das unidades como se ainda não
tivessem sido totalizadas, roda o modelo sobre o que sobrou e compara com o
resultado que já se conhece.

Honestidade obrigatória, exigida pelo §11.2: quando só existem resultados
finais, a ordem de chegada é **construída por nós**, não observada. Isto é um
**teste de estresse**, não um "replay histórico real". Por isso:

- `ArrivalOrder` é explícito e aparece em todo relatório;
- o relatório carrega `ordem_e_sintetica=True` quando for o caso;
- nenhuma métrica daqui sozinha libera o forecast. O gate exige também
  cobertura de intervalo compatível com o nominal e ausência de falha grave
  por território.

Ordens de chegada implementadas, todas plausíveis e nenhuma inocente:

    ELEITORADO_CRESCENTE  municípios pequenos primeiro — o padrão real mais
                          comum, e o que mais engana a extrapolação ingênua,
                          porque o interior chega antes das capitais
    ELEITORADO_DECRESCENTE  o inverso, como contraprova
    ALEATORIA             ordem sorteada — o caso benigno que a extrapolação
                          ingênua acerta e que, por isso, não prova nada
    POR_UF                UF inteira por vez, como a totalização costuma se
                          comportar quando um tribunal regional adianta
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from ..tse.parsers.ea20 import Ea20, ElectorateCounts, SectionCounts, VoteCounts
from . import models as M
from . import simulate as S


class ArrivalOrder(str, Enum):
    ELEITORADO_CRESCENTE = "eleitorado_crescente"
    ELEITORADO_DECRESCENTE = "eleitorado_decrescente"
    ALEATORIA = "aleatoria"
    POR_UF = "por_uf"


def mask_unit(r: Ea20) -> Ea20:
    """Devolve a mesma unidade como se nada tivesse sido totalizado.

    O eleitorado total é preservado — ele é conhecido antes da eleição. O que
    some é o resultado. Seções entram como instaladas e não apuradas, que é o
    estado de uma seção que vai votar e ainda não reportou.
    """
    e, s = r.electorate, r.sections
    e2 = ElectorateCounts(
        te=e.te, est=0, esnt=e.te, esi=e.te, esni=0, esa=0, esna=e.te,
        c=0, a=0,
    )
    s2 = SectionCounts(ts=s.ts, st=0, snt=s.ts, si=s.ts, sni=0, sa=0, sna=s.ts)
    v2 = VoteCounts(tv=0, vvc=0, vv=0, vnom=0, van=0, vansj=0, vb=0, tvn=0,
                    vn=0, vnt=0, vscv=0, raw={})
    cands = tuple(replace(c, votos=0, pct_tse=None) for c in r.cargo.candidatos)
    return replace(
        r, electorate=e2, sections=s2, votes=v2,
        cargo=replace(r.cargo, candidatos=cands),
        andamento="n", totalizacao_final=False, totalized_at=None,
    )


@dataclass(frozen=True, slots=True)
class CutReport:
    corte_alvo_pct: float
    cobertura_eleitorado_pct: float
    cobertura_unidades_pct: float
    modelo: str
    ordem: str
    ordem_e_sintetica: bool
    erro_abs_pp_mediano: float
    erro_abs_pp_maximo: float
    erro_votos_mediano: int
    cobertura_intervalo_80: float
    cobertura_intervalo_95: float
    largura_media_80_pp: float
    acertou_ranking_topo: bool
    acertou_vagas: bool
    erro_mc_pp: float

    def row(self) -> list[str]:
        return [
            f"{self.corte_alvo_pct:.0f}%",
            f"{self.cobertura_eleitorado_pct:.1f}%",
            self.modelo.upper(),
            f"{self.erro_abs_pp_mediano:.2f}",
            f"{self.erro_abs_pp_maximo:.2f}",
            f"{self.cobertura_intervalo_80*100:.0f}%",
            f"{self.cobertura_intervalo_95*100:.0f}%",
            f"{self.largura_media_80_pp:.2f}",
            "sim" if self.acertou_vagas else "NÃO",
        ]


def _ordenar(obs: list[M.UnitObservation], ordem: ArrivalOrder,
             seed: int) -> list[M.UnitObservation]:
    if ordem is ArrivalOrder.ELEITORADO_CRESCENTE:
        return sorted(obs, key=lambda o: o.result.electorate.te)
    if ordem is ArrivalOrder.ELEITORADO_DECRESCENTE:
        return sorted(obs, key=lambda o: -o.result.electorate.te)
    if ordem is ArrivalOrder.POR_UF:
        return sorted(obs, key=lambda o: (o.uf, -o.result.electorate.te))
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(obs))
    return [obs[i] for i in idx]


def verdade(obs: list[M.UnitObservation], candidatos: list[str]) -> dict:
    """Resultado final conhecido: votos por candidato e denominador."""
    votos = {c: 0 for c in candidatos}
    validos = 0
    for o in obs:
        for c, v in o.obs.votos_por_candidato.items():
            if c in votos:
                votos[c] += v
        validos += o.obs.validos
    return {"votos": votos, "validos": validos,
            "pct": {c: (100.0 * v / validos if validos else float("nan"))
                    for c, v in votos.items()}}


def run_cut(obs: list[M.UnitObservation], candidatos: list[str],
            corte_pct: float, *, ordem: ArrivalOrder, modelo: str,
            seats: int = 1, senado: bool = False,
            cfg: S.SimConfig | None = None, seed: int = 7) -> CutReport:
    cfg = cfg or S.SimConfig(n_sims=1500, seed=seed)
    real = verdade(obs, candidatos)

    ordenadas = _ordenar(obs, ordem, seed)
    total_el = sum(o.result.electorate.te for o in ordenadas)
    alvo = total_el * corte_pct / 100.0

    acum, contadas = 0, 0
    for o in ordenadas:
        if acum >= alvo:
            break
        acum += o.result.electorate.te
        contadas += 1

    mascaradas = [
        o if i < contadas else M.UnitObservation(
            unit=o.unit, uf=o.uf, stratum=o.stratum,
            result=mask_unit(o.result), capital=o.capital)
        for i, o in enumerate(ordenadas)
    ]

    build = M.build_m0 if modelo == "m0" else M.build_m1
    units = build(mascaradas, candidatos, seats=seats)
    fc = S.run(units, candidatos, cfg, seats=seats, senado=senado)

    q = fc.quantis((0.025, 0.10, 0.50, 0.90, 0.975))
    erros_pp, erros_votos = [], []
    dentro80 = dentro95 = 0
    larguras = []
    for c in candidatos:
        alvo_pct = real["pct"][c]
        alvo_votos = real["votos"][c]
        est = q[c]
        erros_pp.append(abs(est["pct_validos_mediana"] - alvo_pct))
        erros_votos.append(abs(est["votos_mediana"] - alvo_votos))
        lo80, hi80 = est["pct_validos"]["q100"], est["pct_validos"]["q900"]
        lo95, hi95 = est["pct_validos"]["q025"], est["pct_validos"]["q975"]
        dentro80 += int(lo80 <= alvo_pct <= hi80)
        dentro95 += int(lo95 <= alvo_pct <= hi95)
        larguras.append(hi80 - lo80)

    k = len(candidatos)
    ordem_real = sorted(candidatos, key=lambda c: -real["votos"][c])
    ordem_est = sorted(candidatos, key=lambda c: -q[c]["votos_mediana"])

    return CutReport(
        corte_alvo_pct=corte_pct,
        cobertura_eleitorado_pct=100.0 * acum / total_el if total_el else 0.0,
        cobertura_unidades_pct=100.0 * contadas / len(ordenadas),
        modelo=modelo, ordem=ordem.value, ordem_e_sintetica=True,
        erro_abs_pp_mediano=float(np.median(erros_pp)),
        erro_abs_pp_maximo=float(np.max(erros_pp)),
        erro_votos_mediano=int(np.median(erros_votos)),
        cobertura_intervalo_80=dentro80 / k,
        cobertura_intervalo_95=dentro95 / k,
        largura_media_80_pp=float(np.mean(larguras)),
        acertou_ranking_topo=ordem_real[0] == ordem_est[0],
        acertou_vagas=set(ordem_real[:seats]) == set(ordem_est[:seats]),
        erro_mc_pp=float(fc.diagnostics["erro_monte_carlo_pp"]),
    )


#: Cortes exigidos pelo §11.2.
CORTES = (1.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 99.0)


def run_suite(obs: list[M.UnitObservation], candidatos: list[str], *,
              seats: int = 1, senado: bool = False,
              ordens=(ArrivalOrder.ELEITORADO_CRESCENTE,
                      ArrivalOrder.ALEATORIA),
              cortes=CORTES, n_sims: int = 1200) -> list[CutReport]:
    saida = []
    for ordem in ordens:
        for corte in cortes:
            for modelo in ("m0", "m1"):
                saida.append(run_cut(
                    obs, candidatos, corte, ordem=ordem, modelo=modelo,
                    seats=seats, senado=senado,
                    cfg=S.SimConfig(n_sims=n_sims, seed=20261004),
                ))
    return saida


def gate_m1_vence_m0(reports: list[CutReport]) -> dict:
    """Gate do §11.2: o M1 tem de superar o M0 no erro mediano.

    Não passar não é falha do projeto — é motivo para lançar apuração e
    cenários e manter as probabilidades ocultas.
    """
    pares: dict[tuple, dict] = {}
    for r in reports:
        pares.setdefault((r.ordem, r.corte_alvo_pct), {})[r.modelo] = r
    vitorias = empates = derrotas = 0
    piores = []
    for (ordem, corte), d in sorted(pares.items()):
        if "m0" not in d or "m1" not in d:
            continue
        a, b = d["m1"].erro_abs_pp_mediano, d["m0"].erro_abs_pp_mediano
        if a < b:
            vitorias += 1
        elif abs(a - b) < 1e-9:
            empates += 1
        else:
            derrotas += 1
            piores.append({"ordem": ordem, "corte": corte,
                           "m1": round(a, 3), "m0": round(b, 3)})
    total = vitorias + empates + derrotas
    return {
        "cortes_avaliados": total,
        "m1_melhor": vitorias, "empates": empates, "m1_pior": derrotas,
        "passou": total > 0 and derrotas == 0,
        "derrotas": piores,
    }
