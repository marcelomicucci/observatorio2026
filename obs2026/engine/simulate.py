"""Motor de simulação — identidade do §4.2 com choques compartilhados.

Para cada unidade g, candidato k e simulação b:

    R[g,b]        = A_pendente[g,b] x comparecimento[g,b] x taxa_valida[g,b]
    V_final[g,k,b] = V_observado[g,k] + R[g,b] x preferencia[g,k,b]

Três decisões estruturais, todas do §4.7:

1. **Choques compartilhados, não independentes.** Se cada município sorteasse
   seu desvio isoladamente, a variância do total nacional cairia com a raiz de
   5.571 e o intervalo publicado seria absurdamente estreito. O erro real de
   uma projeção eleitoral é dominado por um desvio *comum* — a preferência dos
   lugares que faltam difere sistematicamente, não aleatoriamente. Por isso o
   desvio é decomposto em nacional + UF + local, e só o local é independente.

2. **Quantis saem das somas simuladas, nunca somando quantis.** O percentil 95
   do total não é a soma dos percentis 95 municipais.

3. **Percentual é calculado depois da soma**, com o denominador da mesma
   simulação. Somar percentuais municipais é média simples disfarçada, o que
   o §11.1 proíbe.

O `A_pendente` entra como intervalo (ver `pending.py`): o sorteio percorre o
intervalo em vez de fingir um ponto, então a ambiguidade do contrato vira
largura de intervalo em vez de falsa precisão.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, slots=True)
class UnitInput:
    """Uma unidade da partição (§4.1). Município OU zona, nunca os dois."""

    unit: str
    uf: str
    stratum: str                      # agrupamento para regularização
    pending_lower: int
    pending_upper: int
    observed_votes: dict[str, int]    # sqcand -> votos válidos observados
    observed_valid: int
    turnout_hat: float                # comparecimento esperado no pendente
    valid_rate_hat: float             # taxa de válidos esperada no pendente
    pref_hat: dict[str, float]        # preferência esperada no pendente
    electorate_total: int
    has_own_data: bool = True         # False = usa prior de grupo (§4.4)
    #: Senado: escolhas válidas por comparecente, em [0, vagas]. `None` para
    #: cargo de vaga única, onde o equivalente é `valid_rate_hat`.
    q_hat: float | None = None
    #: `van + vansj` já contados nesta unidade. Ficam FORA da preferência
    #: (não são voto que a totalização aceita para candidato apto) e DENTRO
    #: do denominador da maioria absoluta ("votos a concorrentes").
    observed_anulados: int = 0


@dataclass(frozen=True, slots=True)
class SimConfig:
    n_sims: int = 5000
    seed: int = 20261004
    # Desvios em escala logit/log. Propostas; calibração por backtest (§11.2).
    sigma_turnout_nacional: float = 0.06
    sigma_turnout_uf: float = 0.05
    sigma_turnout_local: float = 0.10
    sigma_valid_nacional: float = 0.03
    sigma_valid_uf: float = 0.03
    sigma_valid_local: float = 0.06
    sigma_pref_nacional: float = 0.05
    sigma_pref_uf: float = 0.06
    sigma_pref_local: float = 0.12
    #: Unidade sem dado próprio herda o grupo e recebe incerteza ampliada
    #: (§4.4: "nunca zero como preferência eleitoral").
    inflacao_sem_dado: float = 2.0


@dataclass
class Forecast:
    """Saída de uma execução. `votes` é (n_sims, n_cand), em inteiros."""

    candidates: list[str]
    votes: np.ndarray                 # votos finais simulados
    valid_total: np.ndarray           # votos válidos da mesma simulação
    #: Votos a concorrentes da mesma simulação: válidos + anulados + sub
    #: judice. É o denominador da maioria absoluta, não `valid_total`.
    concorrentes_total: np.ndarray
    observed: dict[str, int]
    seats: int
    n_sims: int
    seed: int
    pending_point: int
    diagnostics: dict = field(default_factory=dict)

    # -- resumos ---------------------------------------------------------

    def quantis(self, qs=(0.025, 0.10, 0.50, 0.90, 0.975)) -> dict:
        out = {}
        for i, k in enumerate(self.candidates):
            v = self.votes[:, i]
            share = np.divide(
                100.0 * v, self.valid_total,
                out=np.full_like(v, np.nan, dtype=float),
                where=self.valid_total > 0,
            )
            conc = np.divide(
                100.0 * v, self.concorrentes_total,
                out=np.full_like(v, np.nan, dtype=float),
                where=self.concorrentes_total > 0,
            )
            out[k] = {
                "votos": {f"q{int(q*1000):03d}": int(np.quantile(v, q))
                          for q in qs},
                "pct_validos": {f"q{int(q*1000):03d}": float(np.quantile(share, q))
                                for q in qs},
                # Percentual sobre votos a concorrentes: é este que decide
                # segundo turno, e por isso vai junto, nunca sozinho o outro.
                "pct_concorrentes": {
                    f"q{int(q*1000):03d}": float(np.quantile(conc, q))
                    for q in qs},
                "votos_mediana": int(np.median(v)),
                "pct_validos_mediana": float(np.median(share)),
                "pct_concorrentes_mediana": float(np.median(conc)),
            }
        return out

    def prob_lideranca(self) -> dict[str, float]:
        """Probabilidade de terminar em primeiro. Empates rateados."""
        n = self.votes.shape[0]
        top = self.votes.max(axis=1, keepdims=True)
        vencedores = (self.votes == top)
        # Empate exato divide o crédito; ordem alfabética como desempate é
        # proibida pelo §3.
        peso = vencedores / vencedores.sum(axis=1, keepdims=True)
        return {k: float(peso[:, i].sum() / n)
                for i, k in enumerate(self.candidates)}

    def prob_dentro_das_vagas(self) -> dict[str, float]:
        """Probabilidade de ficar entre as `seats` primeiras colocações."""
        n, m = self.votes.shape
        s = min(self.seats, m)
        # argsort decrescente; empate exato é raro em contagem inteira grande,
        # e quando ocorre fica registrado no diagnóstico.
        ordem = np.argsort(-self.votes, axis=1, kind="stable")[:, :s]
        cont = np.zeros(m)
        for col in range(s):
            np.add.at(cont, ordem[:, col], 1)
        return {k: float(cont[i] / n) for i, k in enumerate(self.candidates)}

    def prob_maioria_absoluta(self) -> dict[str, float]:
        """Probabilidade de passar de 50% dos VOTOS A CONCORRENTES.

        §3: ultrapassar 50% é diferente de apenas liderar. O denominador são
        os votos a concorrentes (`vv + van + vansj` = `vvc`), conforme o
        leiame do TSE e a CF art. 77 §2º — NÃO `vv`, e não o percentual que o
        TSE exibe na tela. Ver `denominators.py`.

        A comparação é feita em inteiros (`votos*2 > den`) para não depender
        de arredondamento de ponto flutuante numa decisão binária.
        """
        n = self.votes.shape[0]
        den = np.where(self.concorrentes_total > 0, self.concorrentes_total, 1)
        return {
            k: float(((self.votes[:, i] * 2) > den).sum() / n)
            for i, k in enumerate(self.candidates)
        }

    def margem_2_3(self) -> dict:
        """Margem do segundo para o terceiro — pergunta central do Senado."""
        if self.votes.shape[1] < 3:
            return {}
        ord_ = np.sort(self.votes, axis=1)[:, ::-1]
        m = ord_[:, 1] - ord_[:, 2]
        return {
            "mediana": int(np.median(m)),
            "q050": int(np.quantile(m, 0.05)),
            "q950": int(np.quantile(m, 0.95)),
        }

    def combinacoes_eleitas(self, top: int = 8) -> list[dict]:
        """Distribuição das duplas eleitas (§4.6)."""
        n, m = self.votes.shape
        s = min(self.seats, m)
        ordem = np.argsort(-self.votes, axis=1, kind="stable")[:, :s]
        contagem: dict[tuple, int] = {}
        for linha in ordem:
            chave = tuple(sorted(linha.tolist()))
            contagem[chave] = contagem.get(chave, 0) + 1
        saida = [
            {"candidatos": [self.candidates[i] for i in ch],
             "prob": v / n}
            for ch, v in sorted(contagem.items(), key=lambda x: -x[1])[:top]
        ]
        return saida

    def erro_monte_carlo_pp(self) -> float:
        """Erro padrão de Monte Carlo do maior share, em pontos percentuais."""
        den = np.where(self.valid_total > 0, self.valid_total, 1)
        shares = 100.0 * self.votes / den[:, None]
        return float(shares.std(axis=0).max() / np.sqrt(self.n_sims))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _alocar_inteiros(reais: np.ndarray, total: np.ndarray) -> np.ndarray:
    """Maiores restos: conserva o total exatamente e nunca devolve negativo.

    §4.2: "os valores simulados serão inteiros no processamento final, com
    alocação que conserva o total e respeita limites".
    """
    base = np.floor(np.maximum(reais, 0.0)).astype(np.int64)
    resto = total - base.sum(axis=1)
    frac = reais - base
    sobe = np.argsort(-frac, axis=1, kind="stable")
    desce = np.argsort(frac, axis=1, kind="stable")
    for b in range(reais.shape[0]):
        r = int(resto[b])
        if r > 0:
            base[b, sobe[b, :r]] += 1
        elif r < 0:
            # Arredondamento pode deixar o piso acima do total; devolver
            # unidades a quem tem menor fração mantém a soma exata e nunca
            # produz voto negativo.
            faltam = -r
            for k in desce[b]:
                if faltam == 0:
                    break
                tira = min(faltam, int(base[b, k]))
                base[b, k] -= tira
                faltam -= tira
    return base


def run(units: list[UnitInput], candidates: list[str], cfg: SimConfig,
        *, seats: int = 1, senado: bool = False,
        comparecentes_pendentes_cap: bool = True) -> Forecast:
    """Executa a simulação conjunta sobre todas as unidades da disputa."""
    rng = np.random.default_rng(cfg.seed)
    B, K = cfg.n_sims, len(candidates)
    if K == 0 or not units:
        raise ValueError("disputa sem candidatos ou sem unidades")

    idx = {c: i for i, c in enumerate(candidates)}
    ufs = sorted({u.uf for u in units})
    uf_idx = {uf: i for i, uf in enumerate(ufs)}

    # -- choques compartilhados -----------------------------------------
    z_nac_t = rng.standard_normal(B)
    z_uf_t = rng.standard_normal((B, len(ufs)))
    z_nac_v = rng.standard_normal(B)
    z_uf_v = rng.standard_normal((B, len(ufs)))
    z_nac_p = rng.standard_normal((B, K))
    z_uf_p = rng.standard_normal((B, len(ufs), K))

    votos = np.zeros((B, K), dtype=np.int64)
    validos_totais = np.zeros(B, dtype=np.int64)
    anulados_observados = 0
    observado = {c: 0 for c in candidates}
    pendente_ponto = 0
    unidades_sem_dado = 0

    for u in units:
        # Voto já contado é fixo no cenário-base (§4.7): entra igual em todas
        # as simulações. Retotalização cria versão nova, não ruído.
        obs_linha = np.zeros(K, dtype=np.int64)
        for c, v in u.observed_votes.items():
            if c in idx:
                obs_linha[idx[c]] = v
                observado[c] = observado.get(c, 0) + v
        votos += obs_linha
        validos_totais += u.observed_valid
        anulados_observados += u.observed_anulados

        if u.pending_upper <= 0:
            continue
        pendente_ponto += (u.pending_lower + u.pending_upper) // 2
        j = uf_idx[u.uf]
        infl = cfg.inflacao_sem_dado if not u.has_own_data else 1.0
        if not u.has_own_data:
            unidades_sem_dado += 1

        # A_pendente: uniforme no intervalo que o contrato deixa em aberto.
        if u.pending_upper > u.pending_lower:
            apend = rng.integers(u.pending_lower, u.pending_upper + 1, size=B)
        else:
            apend = np.full(B, u.pending_upper, dtype=np.int64)

        # comparecimento
        lt = (_logit(np.full(B, u.turnout_hat))
              + cfg.sigma_turnout_nacional * z_nac_t
              + cfg.sigma_turnout_uf * z_uf_t[:, j]
              + cfg.sigma_turnout_local * infl * rng.standard_normal(B))
        comp = _expit(lt)

        # taxa de votos válidos
        lv = (_logit(np.full(B, u.valid_rate_hat))
              + cfg.sigma_valid_nacional * z_nac_v
              + cfg.sigma_valid_uf * z_uf_v[:, j]
              + cfg.sigma_valid_local * infl * rng.standard_normal(B))
        taxa = _expit(lv)

        comparecentes = apend * comp

        if senado:
            # §4.6: o total de escolhas válidas é C x q, com q em [0, vagas].
            # NÃO se assume que todos fazem duas escolhas válidas — no
            # simulado real q ficou em 1,70, não em 2,00.
            if u.q_hat is None:
                raise ValueError(
                    f"unidade {u.unit}: modelo de Senado exige q_hat "
                    "(escolhas validas por comparecente)"
                )
            frac_q = np.clip(u.q_hat / seats, 1e-6, 1 - 1e-6)
            lq = (_logit(np.full(B, frac_q))
                  + cfg.sigma_valid_nacional * z_nac_v
                  + cfg.sigma_valid_uf * z_uf_v[:, j]
                  + cfg.sigma_valid_local * infl * rng.standard_normal(B))
            escolhas = comparecentes * _expit(lq) * seats
        else:
            escolhas = comparecentes * taxa

        # preferência, com swing compartilhado em escala log
        base = np.array([max(u.pref_hat.get(c, 0.0), 1e-9)
                         for c in candidates])
        alpha = (np.log(base)[None, :]
                 + cfg.sigma_pref_nacional * z_nac_p
                 + cfg.sigma_pref_uf * z_uf_p[:, j, :]
                 + cfg.sigma_pref_local * infl * rng.standard_normal((B, K)))
        alpha -= alpha.max(axis=1, keepdims=True)
        pref = np.exp(alpha)
        pref /= pref.sum(axis=1, keepdims=True)

        reais = pref * escolhas[:, None]

        if senado and comparecentes_pendentes_cap:
            # Restrições do §4.6, impostas EM CADA SIMULAÇÃO:
            #   - votos de um candidato <= comparecentes restantes
            #     (ninguém dá dois votos válidos ao mesmo candidato)
            #   - soma dos votos <= vagas x comparecentes restantes
            teto = comparecentes[:, None]
            excedente = np.maximum(reais - teto, 0.0).sum(axis=1)
            reais = np.minimum(reais, teto)
            # redistribui o excedente entre quem ainda tem folga
            folga = np.maximum(teto - reais, 0.0)
            soma_folga = folga.sum(axis=1)
            ok = soma_folga > 0
            if ok.any():
                reais[ok] += (folga[ok]
                              * (excedente[ok] / soma_folga[ok])[:, None])
            reais = np.minimum(reais, teto)
            total_max = seats * comparecentes
            soma = reais.sum(axis=1)
            demais = soma > total_max
            if demais.any():
                reais[demais] *= (total_max[demais] / soma[demais])[:, None]

        total_int = np.rint(reais.sum(axis=1)).astype(np.int64)
        votos += _alocar_inteiros(reais, total_int)
        validos_totais += total_int

    mc = {
        "unidades": len(units),
        "unidades_sem_dado_proprio": unidades_sem_dado,
        "ufs": len(ufs),
        "pendente_ponto": pendente_ponto,
    }
    # Votos pendentes destinados a candidatos anulados NÃO são modelados: a
    # preferência cobre só candidatos aptos (§4.7 manda tratar sensibilidade
    # judicial em cenário separado). Por isso os anulados entram no
    # denominador como a parcela JÁ observada, sem projeção própria.
    mc["anulados_observados"] = anulados_observados
    fc = Forecast(
        candidates=list(candidates), votes=votos, valid_total=validos_totais,
        concorrentes_total=validos_totais + anulados_observados,
        observed=observado, seats=seats, n_sims=B, seed=cfg.seed,
        pending_point=pendente_ponto, diagnostics=mc,
    )
    fc.diagnostics["erro_monte_carlo_pp"] = fc.erro_monte_carlo_pp()
    return fc
