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

import math
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


# --------------------------------------------------------------------- M2 ---

#: Desvio do choque de grupo (escala log da preferência) quando o grupo
#: ainda não tem dado. Medida bruta em 2018, capital x interior da mesma UF,
#: candidatos com 5% ou mais: governador 0,486, Senado 0,490, presidente
#: 0,377. O valor usado é menor porque os choques de UF e local já cobrem
#: parte dessa distância: calibrado no ensaio de 2018 (54 disputas, ordem
#: real, cidades grandes fatiadas por zona) para o intervalo de 80% conter o
#: final em ~80% dos casos — 0,20 deu 76-77%, 0,30 deu 84-85%, o M1 57-60%.
#: 2022 não entra no ajuste: é a eleição de teste.
SIGMA_GRUPO_VAZIO = 0.25
#: Piso do choque de grupo quando o grupo está quase todo contado.
SIGMA_GRUPO_PISO = 0.02
#: Encolhimento do grupo para a UF e do subestrato (porte) para o grupo, em
#: votos válidos observados. Proposta; ajustável pelo ensaio de 2018.
K_GRUPO = 2000.0
K_SUBESTRATO = 2000.0


def _shrink(loc, alvo, w):
    if loc is None:
        return alvo
    return w * loc + (1 - w) * alvo


def _shrink_pref(loc: dict, alvo: dict, w: float, cands) -> dict:
    if not loc:
        return dict(alvo)
    return _normalize({c: w * loc.get(c, 0.0) + (1 - w) * alvo.get(c, 0.0)
                       for c in cands})


def _nivel(agr: dict, alvo: tuple, k: float, cands) -> tuple:
    """Estimativa de um nível, encolhida para o nível de cima.

    `alvo` é (turnout, valid_rate, q, pref) do nível de cima. Devolve a mesma
    tupla para este nível. Sem voto observado, herda o de cima inteiro.
    """
    n = agr["validos"]
    w = n / (n + k) if n > 0 else 0.0
    t, vr, q, pref = alvo
    return (_shrink(agr["turnout"], t, w), _shrink(agr["valid_rate"], vr, w),
            _shrink(agr["q"], q, w), _shrink_pref(agr["pref"], pref, w, cands))


def build_m2(observacoes: list[UnitObservation], candidatos: list[str],
             *, seats: int = 1, k0: float = K0_ENCOLHIMENTO,
             sigma_grupo_vazio: float = SIGMA_GRUPO_VAZIO,
             expoente: float = 0.5,
             inclinacao: dict[str, float] | None = None,
             k_inclinacao: float | None = None,
             conformal: bool = False,
             m0_conformal: float | None = None,
             gamma_conformal: float | None = None,
             blocos: dict[str, str | None] | None = None) -> list[UnitInput]:
    """Estimativa hierárquica: município -> porte -> grupo -> UF.

    O grupo é capital (capital + cidades do arranjo dela, IBGE) ou interior,
    por UF (`territorio.py`). Duas diferenças em relação ao M1:

    1. **Cada unidade pendente olha para o próprio grupo**, não para a média
       do que já foi contado na UF. No começo da noite essa média é quase
       só interior; usá-la para a capital é o erro do M1 medido no ensaio de
       2022 (Senado MG 2018: BH chegou por último e o M1 dava 90% ao
       candidato errado para a segunda vaga). O total da UF sai da soma das
       unidades, então cada grupo já pesa pelo eleitorado dele — que é
       conhecido antes da eleição —, e não pelo que já chegou.

    2. **O grupo carrega um choque próprio**, compartilhado por todas as
       suas unidades, com desvio `sigma_grupo_vazio * sqrt(1 - f)`, onde `f`
       é a fração do eleitorado do grupo já apurada (`expoente` 0,5; o
       ensaio de 2018 compara outras curvas). Grupo vazio: incerteza
       cheia (a distância típica capital x interior). Grupo quase todo
       contado: incerteza quase nula. É isso que faz o intervalo começar
       largo e estreitar conforme o grupo chega, em vez de nascer estreito e
       errado.

    Com `inclinacao` (M3, ver `build_m3`), a estimativa do estrato de cada
    unidade pendente é corrigida pela inclinação histórica dela. Sem
    `inclinacao`, nada muda: o M2 é exatamente o de antes.
    """
    from . import territorio as T

    nac = _agregar(observacoes, candidatos)
    nac_hat = _fallback(nac, candidatos, seats)

    def gr(o):
        return (o.uf, T.CAPITAL if o.capital else T.INTERIOR)

    def sub(o):
        return gr(o) + (porte(o.result.electorate.te),)

    por_uf, por_g, por_s = {}, {}, {}
    for o in observacoes:
        por_uf.setdefault(o.uf, []).append(o)
        por_g.setdefault(gr(o), []).append(o)
        por_s.setdefault(sub(o), []).append(o)

    # Numa disputa estadual há uma UF só e este nível coincide com o total;
    # no presidente, cada UF é puxada para o nacional antes de virar alvo
    # dos grupos dela.
    est_uf = {uf: _nivel(_agregar(lst, candidatos), nac_hat, K_GRUPO,
                         candidatos) for uf, lst in por_uf.items()}
    est_g, sig_g = {}, {}
    for g, lst in por_g.items():
        est_g[g] = _nivel(_agregar(lst, candidatos), est_uf[g[0]], K_GRUPO,
                          candidatos)
        total = sum(o.result.electorate.te for o in lst)
        apurado = sum(o.pending.apurado for o in lst)
        f = min(apurado / total, 1.0) if total else 1.0
        sig_g[g] = max(sigma_grupo_vazio * (1.0 - f) ** expoente,
                       SIGMA_GRUPO_PISO if f < 1.0 else 0.0)
    est_s = {s: _nivel(_agregar(lst, candidatos), est_g[s[:2]],
                       K_SUBESTRATO, candidatos)
             for s, lst in por_s.items()}

    # M3: inclinação média efetiva de cada nível (mesmo encolhimento da
    # preferência) e a resposta de cada candidato à inclinação.
    x_s, slopes = {}, {}
    if inclinacao is not None:
        x_s, slopes = _inclinacao_por_nivel(
            observacoes, candidatos, inclinacao, por_uf, por_g, por_s,
            gr, sub, k_inclinacao or K_INCLINACAO, blocos=blocos)

    # M3.2: fator do choque local e do choque de grupo, medido nos
    # municípios já contados (ver `_escala_conformal`).
    escala = 1.0
    if conformal:
        escala = _escala_conformal(
            observacoes, candidatos, por_s, sub, est_g, nac,
            inclinacao if slopes else None, x_s, slopes,
            m0_conformal or M0_CONFORMAL, gamma_conformal)

    saida = []
    for o in observacoes:
        p, b = o.pending, o.obs
        t_e, vr_e, q_e, pref_e = est_s[sub(o)]
        if slopes:
            pref_e = _corrige_por_inclinacao(
                pref_e, o, inclinacao, x_s.get(sub(o)), slopes)
        n = b.validos
        w = n / (n + k0) if (n + k0) > 0 else 0.0
        turnout = _shrink(b.comparecimento_observado, t_e, w)
        vr = _shrink(b.taxa_valida, vr_e, w)
        q = _shrink(b.escolhas_por_comparecente, q_e, w)
        pref = _shrink_pref(b.preferencias(), pref_e, w, candidatos)
        saida.append(UnitInput(
            unit=o.unit, uf=o.uf, stratum="|".join(sub(o)),
            pending_lower=p.lower, pending_upper=p.upper,
            observed_votes=dict(b.votos_por_candidato),
            observed_valid=b.validos, observed_anulados=b.anulados,
            turnout_hat=min(max(turnout, 1e-4), 0.9999),
            valid_rate_hat=min(max(vr, 1e-4), 0.9999),
            pref_hat=pref, electorate_total=p.total,
            has_own_data=n > 0,
            q_hat=min(max(q, 1e-4), seats - 1e-4),
            grupo="|".join(gr(o)), sigma_grupo=sig_g[gr(o)] * escala,
            escala_pref=escala,
        ))
    return saida


# --------------------------------------------------------------------- M3 ---

#: Votos válidos observados em que a resposta à inclinação vale metade do
#: estimado. Com pouca apuração o M3 fica perto do M2; com mais, a
#: correção entra inteira. Proposta a priori, NÃO ajustada em 2022 (que é a
#: eleição de teste): 50 mil votos é o tamanho de uma cidade média — abaixo
#: disso a inclinação medida na noite ainda é ruído de poucos lugares.
K_INCLINACAO = 50_000.0
#: Limite da correção por unidade, em escala log da preferência. Um
#: município muito fora da faixa dos já contados não pode receber uma
#: extrapolação sem freio.
CORRECAO_MAX = 1.5


def _inclinacao_por_nivel(observacoes, candidatos, inclinacao, por_uf, por_g,
                          por_s, gr, sub, k_inc, blocos=None):
    """Média efetiva da inclinação por subestrato e a resposta (slope) de
    cada candidato, estimadas só com o que já foi contado."""
    from . import historico as HI

    def x_de(o):
        v = inclinacao.get(HI.chave(o.unit))
        return None if v is None else HI.logit(v)

    def media(lst):
        num = den = 0.0
        for o in lst:
            x = x_de(o)
            n = o.obs.validos
            if x is None or n <= 0:
                continue
            num += n * x
            den += n
        return (num / den if den else None), den

    x_nac, _ = media(observacoes)
    if x_nac is None:
        return {}, {}
    # Mesmo encolhimento da preferência: nível sem dado herda o de cima.
    x_uf = {}
    for uf, lst in por_uf.items():
        m, n = media(lst)
        w = n / (n + K_GRUPO) if n > 0 else 0.0
        x_uf[uf] = _shrink(m, x_nac, w)
    x_g = {}
    for g, lst in por_g.items():
        m, n = media(lst)
        w = n / (n + K_GRUPO) if n > 0 else 0.0
        x_g[g] = _shrink(m, x_uf[g[0]], w)
    x_s = {}
    for s_, lst in por_s.items():
        m, n = media(lst)
        w = n / (n + K_SUBESTRATO) if n > 0 else 0.0
        x_s[s_] = _shrink(m, x_g[s_[:2]], w)

    # Resposta de cada candidato: regressão ponderada do log da fatia
    # observada na inclinação, sobre as unidades já contadas. Um só slope
    # por candidato na disputa (no presidente, nacional); encolhido para
    # zero conforme o volume contado — com pouca apuração, M3 ≈ M2.
    pts = []
    for o in observacoes:
        x = x_de(o)
        b = o.obs
        if x is None or b.validos <= 0:
            continue
        pts.append((x, b.validos, b.votos_por_candidato))
    W = sum(w for _, w, _ in pts)
    if W <= 0 or len(pts) < 3:
        return x_s, {}
    xbar = sum(w * x for x, w, _ in pts) / W
    sxx = sum(w * (x - xbar) ** 2 for x, w, _ in pts)
    if sxx <= 0:
        return x_s, {}
    fator = W / (W + k_inc)
    slopes = {}
    for c in candidatos:
        ys = [(x, w, math.log((v.get(c, 0) + 0.5) / (w + 0.5)))
              for x, w, v in pts]
        ybar = sum(w * y for _, w, y in ys) / W
        sxy = sum(w * (x - xbar) * (y - ybar) for x, w, y in ys)
        bruto = sxy / sxx
        if blocos is None:
            slopes[c] = fator * bruto
            continue
        # M3.1: encolhimento bayesiano empírico. Erro-padrão robusto (HC0)
        # da regressão ponderada: sente quando poucos municípios puxam a
        # reta — 50 mil votos de uma cidade só não medem slope nenhum.
        resid = sum((w * (x - xbar)) ** 2 * (y - ybar - bruto * (x - xbar))
                    ** 2 for x, w, y in ys)
        n = len(ys)
        var = (resid / (sxx * sxx)) * (n / (n - 2) if n > 2 else 1.0)
        mu, tau = PRIOR_BLOCO.get(blocos.get(c), PRIOR_BLOCO[None])
        peso = tau * tau / (tau * tau + var) if var > 0 else 1.0
        slopes[c] = mu + (bruto - mu) * peso
    return x_s, slopes


def _corrige_por_inclinacao(pref, o, inclinacao, x_ref, slopes):
    from . import historico as HI
    v = inclinacao.get(HI.chave(o.unit))
    if v is None or x_ref is None or not pref:
        return pref
    dx = HI.logit(v) - x_ref
    novo = {}
    for c, p in pref.items():
        ajuste = max(-CORRECAO_MAX, min(CORRECAO_MAX,
                                        slopes.get(c, 0.0) * dx))
        novo[c] = p * math.exp(ajuste)
    return _normalize(novo)


def build_m3(observacoes: list[UnitObservation], candidatos: list[str],
             *, seats: int = 1, inclinacao: dict[str, float] | None = None,
             ano_referencia: int | None = None,
             **kw) -> list[UnitInput]:
    """M2 corrigido pela inclinação histórica de cada município.

    A hierarquia e a incerteza são as do M2 (calibradas em 2018). A
    diferença: a estimativa do estrato de cada unidade pendente é ajustada
    pela distância entre a inclinação histórica dela e a inclinação média
    do que já foi contado naquele estrato, com a resposta de cada
    candidato à inclinação medida na própria noite:

        log p[g,k] = log p_estrato[k] + slope[k] * (x[g] - x_estrato)

    Se chegou primeiro o interior de um lado, a capital do outro lado que
    falta já entra corrigida. Sem histórico para a unidade (município novo),
    fica o M2.

    `inclinacao` é {"uf:mun": fatia}; sem ela, usa a de `ano_referencia`
    (padrão: 2022, a eleição anterior a 2026).
    """
    if inclinacao is None:
        from . import historico as HI
        inclinacao = HI.inclinacao(ano_referencia or 2022)
    return build_m2(observacoes, candidatos, seats=seats,
                    inclinacao=inclinacao, **kw)


# ------------------------------------------------------------------- M3.2 ---

#: Desvio do choque local de preferência que o simulador supõe (escala log).
#: É a régua do calibrador: se os municípios já contados se desviam da
#: previsão mais do que isso, o intervalo está estreito; menos, está largo.
SIGMA_LOCAL_REF = 0.12
#: Municípios usados pelo calibrador em que o fator medido vale metade. Com
#: poucos municípios contados o fator fica perto de 1 (o M3 como está).
#: Proposta a priori; calibração em 2018, não em 2022.
M0_CONFORMAL = 30.0
#: Fator bruto típico (mediana) medido no ensaio de 2018 — a régua da versão
#: 2. `None` = régua absoluta (versão 1, reprovada: alargava tudo).
BRUTO_REF: float | None = 3.59
#: Amortecimento: escala = (bruto / BRUTO_REF) ** GAMMA. O sinal existe mas
#: é fraco em 2018 (Spearman −0,22 entre o fator e a cobertura do M3 por
#: disputa: bruto baixo → 86% de cobertura, alto → 76%); aplicado cheio,
#: redistribuiria largura demais. Calibrado no ensaio de 2018.
GAMMA_CONFORMAL = 0.5
#: Coleta das medições para calibrar `BRUTO_REF` (scripts); None = desligada.
MEDICOES_CONFORMAL: list | None = None
#: Limites do fator: o calibrador corrige a régua, não a reinventa.
ESCALA_MIN, ESCALA_MAX = 0.5, 3.0
#: Só entram municípios com votos bastantes para o desvio não ser só ruído
#: amostral (que é descontado, mas explode em poucos votos).
N_MIN_CONFORMAL = 300
#: Candidatos com menos que isto do total contado ficam fora da medida: o
#: log de uma fatia minúscula é instável.
FATIA_MIN_CONFORMAL = 0.05


def _escala_conformal(observacoes, candidatos, por_s, sub, est_g, nac,
                      inclinacao, x_s, slopes, m0,
                      gamma: float | None = None) -> float:
    """Fator do calibrador dinâmico (M3.2), a cada projeção.

    Validação cruzada nos municípios já contados: cada um é previsto como se
    ainda estivesse pendente — o subestrato dele SEM o próprio voto,
    encolhido para o grupo, corrigido pela inclinação (M3) — e a previsão é
    comparada com o que ele deu de fato. Do quadrado do desvio em log sai o
    ruído amostral esperado para aquele número de votos; o que sobra é o
    desvio real entre municípios, e a razão dele para o que o simulador
    supõe (`SIGMA_LOCAL_REF`) é o fator:

        escala = sqrt( média(r² − ruído amostral) / σ_local² )

    É a ideia do conformal (Washington Post 2020/2024; Gibbs & Candès 2021
    para a versão adaptativa): o intervalo se mede pelo erro que o próprio
    modelo comete nos lugares que já chegaram, não por uma constante
    escolhida antes. Estado heterogêneo (RR, AP) alarga; eleição em que a
    inclinação explica quase tudo (presidente) estreita.
    """
    tot = sum(nac["votos"].values())
    if tot <= 0:
        return 1.0
    principais = [c for c in candidatos
                  if nac["votos"].get(c, 0) / tot >= FATIA_MIN_CONFORMAL]
    if not principais:
        return 1.0
    agr_s = {s_: _agregar(lst, candidatos) for s_, lst in por_s.items()}
    soma = 0.0
    termos = 0
    unidades = 0
    for o in observacoes:
        b = o.obs
        n = b.validos
        if n < N_MIN_CONFORMAL:
            continue
        a = agr_s[sub(o)]
        # Subestrato sem este município: o que um pendente dali receberia.
        votos_loo = {c: a["votos"][c] - b.votos_por_candidato.get(c, 0)
                     for c in candidatos}
        n_loo = a["validos"] - n
        w = n_loo / (n_loo + K_SUBESTRATO) if n_loo > 0 else 0.0
        pref_loo = _normalize({c: float(max(v, 0))
                               for c, v in votos_loo.items()})
        pred = _shrink_pref(pref_loo, est_g[sub(o)[:2]][3], w, candidatos)
        if slopes and inclinacao is not None:
            pred = _corrige_por_inclinacao(pred, o, inclinacao,
                                           x_s.get(sub(o)), slopes)
        if not pred:
            continue
        unidades += 1
        for c in principais:
            p_obs = (b.votos_por_candidato.get(c, 0) + 0.5) / (n + 1.0)
            p_hat = max(pred.get(c, 0.0), 1e-6)
            r = math.log(p_obs) - math.log(p_hat)
            ruido = (1.0 - p_obs) / (n * p_obs)
            soma += r * r - ruido
            termos += 1
    if unidades == 0 or termos == 0:
        return 1.0
    if soma <= 0:
        # Desvio todo explicado pelo ruído amostral (ex.: DF, uma unidade
        # fatiada em zonas): sem informação, fica o M3.
        return 1.0
    bruto = math.sqrt(soma / termos) / SIGMA_LOCAL_REF
    if MEDICOES_CONFORMAL is not None:
        MEDICOES_CONFORMAL.append((bruto, unidades))
    # Versão 2: o fator é RELATIVO ao típico medido em 2018. Na média fica
    # em 1 (a cobertura geral, já calibrada no M2/M3, não muda); o que muda
    # é a distribuição da largura entre disputas heterogêneas e homogêneas.
    rel = (bruto / (BRUTO_REF or 1.0)) ** (GAMMA_CONFORMAL if gamma is None
                                           else gamma)
    peso = unidades / (unidades + m0)
    return min(ESCALA_MAX, max(ESCALA_MIN, 1.0 + (rel - 1.0) * peso))


def build_m32(observacoes: list[UnitObservation], candidatos: list[str],
              *, seats: int = 1, **kw) -> list[UnitInput]:
    """M3 com o calibrador dinâmico da incerteza (M3.2).

    O número central é o do M3; muda só a largura do intervalo, pelo fator
    de `_escala_conformal` aplicado ao choque local e ao choque de grupo.
    """
    return build_m3(observacoes, candidatos, seats=seats, conformal=True,
                    **kw)


# ------------------------------------------------------------------- M3.1 ---

#: Resposta à inclinação por bloco (média, desvio entre candidatos), medida
#: em 2018 — governador e senador, apuração completa, inclinação de 2014,
#: candidatos com 3%+ (`scripts/medir_blocos.py`). A direção confirma a
#: ideia (esquerda +, direita −), mas o desvio dentro do bloco é maior que a
#: distância entre blocos: o bloco é ponto de partida, não verdade. Partido
#: sem classificação: média zero e o desvio de todos.
PRIOR_BLOCO: dict[str | None, tuple[float, float]] = {
    "esquerda": (0.134, 0.619),
    "centro": (0.031, 0.445),
    "direita": (-0.134, 0.415),
    None: (0.0, 0.587),
}

_CLASSIF_MEMO: dict = {}


def blocos_dos_candidatos(observacoes: list[UnitObservation]
                          ) -> dict[str, str | None]:
    """Bloco de cada candidato pela classificação assinada (número + sigla
    do partido, como no painel). Partido que não bate com o cadastro de hoje
    — o 17 de 2018 era PSL — fica sem bloco, não herda o do número."""
    from ..politics import classification as CL
    caminho = CL.caminho_padrao()
    chave = (str(caminho), caminho.stat().st_mtime if caminho.exists()
             else None)
    reg = _CLASSIF_MEMO.get(chave)
    if reg is None:
        reg = _CLASSIF_MEMO[chave] = CL.load()
    out: dict[str, str | None] = {}
    for o in observacoes:
        for c in o.result.cargo.candidatos:
            if c.sqcand not in out:
                out[c.sqcand] = reg.bloco(c.partido_sigla, c.partido_numero)
    return out


def build_m31(observacoes: list[UnitObservation], candidatos: list[str],
              *, seats: int = 1, blocos: dict | None = None,
              **kw) -> list[UnitInput]:
    """M3 com a resposta à inclinação freada pela própria incerteza (M3.1).

    Igual ao M3, menos o slope de cada candidato: em vez de encolher para
    zero por volume de votos (`K_INCLINACAO`), encolhe para a média do bloco
    ideológico dele, com peso dado pelo erro-padrão robusto da regressão —
    firme quando muitos municípios concordam, fraco quando poucos puxam a
    reta. Ataca o caso em que o M3 perde para o M0: candidato regional cujo
    voto não segue o eixo presidencial, medido cedo em poucos lugares.
    """
    if blocos is None:
        blocos = blocos_dos_candidatos(observacoes)
    return build_m3(observacoes, candidatos, seats=seats, blocos=blocos,
                    **kw)

