"""M2: grupo capital x interior, e a garantia de que o M1 não mudou.

O M2 nasceu do ensaio de 2022 (`scripts/ensaio2022.py`): quando a capital
ainda não chegou, o M1 supunha que ela vota como o interior já contado, com
incerteza de UF (0,06 em log). Em 2018 a distância típica capital x interior
foi 0,49. Estes testes fixam as propriedades que justificam o M2 sem
depender dos zips históricos.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from obs2026.engine import models as M
from obs2026.engine import simulate as S
from obs2026.engine import territorio as T
from obs2026.tse.parsers import ea12
from obs2026.tse.parsers.ea20 import (Candidate, CargoResult, DVT_VALIDO,
                                      Ea20, ElectorateCounts, SectionCounts,
                                      VoteCounts)

FIX = Path(__file__).parent / "fixtures" / "simulado2026"
CANDS = ("a", "b")


def _mun(cd: str, te: int, votos: dict | None, capital: bool,
         uf: str = "mg") -> M.UnitObservation:
    """Município encerrado (`votos`) ou ainda não apurado (`None`)."""
    apurado = votos is not None
    vv = sum(votos.values()) if apurado else 0
    c = int(te * 0.8) if apurado else 0
    a = te - c if apurado else 0
    esa = te if apurado else 0
    r = Ea20(
        election="x", turno="1", fase="s", suplementar=False, tpabr="mu",
        cdabr=cd, generated_at=None, totalized_at=None, idg=None,
        andamento="f" if apurado else "n", divulga_votos=True,
        totalizacao_final=apurado, esae=None, mnae=(),
        cargo=CargoResult(cd="3", nome="", vagas=1, candidatos=tuple(
            Candidate(numero=k, sqcand=k, nome=k, nome_urna=k, seq=None,
                      dvt=DVT_VALIDO, classificado_flag="n", situacao="",
                      votos=(votos or {}).get(k, 0), pct_tse=None,
                      partido_numero="", partido_sigla="", partido_nome="",
                      agr_numero="", agr_nome="", agr_tipo="",
                      agr_composicao="") for k in CANDS)),
        sections=SectionCounts(ts=10, st=10 if apurado else 0,
                               snt=0 if apurado else 10, si=10, sni=0,
                               sa=10 if apurado else 0,
                               sna=0 if apurado else 10),
        electorate=ElectorateCounts(te=te, est=esa, esnt=te - esa, esi=te,
                                    esni=0, esa=esa, esna=te - esa, c=c,
                                    a=a),
        votes=VoteCounts(tv=vv, vvc=vv, vv=vv, vnom=vv, van=0, vansj=0,
                         vb=0, tvn=0, vn=0, vnt=None, vscv=None),
    )
    return M.UnitObservation(unit=f"{uf}:{cd}", uf=uf,
                             stratum=M.estrato(uf, te), result=r,
                             capital=capital)


def _cenario_capital_atrasada():
    """Interior todo contado e favorável a `a`; capital inteira pendente."""
    interior = [_mun(f"{i:05d}", 20_000, {"a": 10_000, "b": 6_000}, False)
                for i in range(1, 11)]
    capital = [_mun("41238", 1_000_000, None, True)]
    return interior + capital


def test_lista_de_capitais_cobre_as_27_ufs_e_separa_o_interior():
    d = json.loads(T.ARQUIVO.read_text(encoding="utf-8"))["ufs"]
    assert len(d) == 27
    assert T.e_capital("mg", "41238")          # Belo Horizonte
    assert T.e_capital("mg", "43710")          # Contagem, arranjo de BH
    assert not T.e_capital("mg", "47333")      # Juiz de Fora: interior
    for uf, v in d.items():
        assert v["capital"] in v["membros"]


def test_codigos_da_lista_existem_no_cadastro_de_2026():
    """A lista foi montada com o cadastro de 2022; o código TSE tem de
    continuar valendo no de 2026, com o mesmo código IBGE."""
    m = ea12.parse(json.loads((FIX / "ea12_mun.json").read_text(
        encoding="utf-8")), "21270").by_code()
    d = json.loads(T.ARQUIVO.read_text(encoding="utf-8"))["ufs"]
    faltam = [(uf, cd) for uf, v in d.items() for cd in v["membros"]
              if (uf, cd) not in m]
    assert not faltam


def test_m1_nao_muda_com_a_marca_de_capital():
    """O choque de grupo usa gerador próprio: M1 reproduz os mesmos votos
    com ou sem a marca, e com ou sem o M2 existir."""
    obs = _cenario_capital_atrasada()
    sem = [M.UnitObservation(o.unit, o.uf, o.stratum, o.result, False)
           for o in obs]
    cfg = S.SimConfig(n_sims=300, seed=3)
    a = S.run(M.build_m1(obs, list(CANDS)), list(CANDS), cfg)
    b = S.run(M.build_m1(sem, list(CANDS)), list(CANDS), cfg)
    assert np.array_equal(a.votes, b.votes)


def test_grupo_vazio_recebe_incerteza_cheia_e_grupo_contado_nenhuma():
    units = {u.unit: u for u in M.build_m2(_cenario_capital_atrasada(),
                                            list(CANDS))}
    assert units["mg:41238"].sigma_grupo == M.SIGMA_GRUPO_VAZIO
    assert units["mg:00001"].sigma_grupo == 0.0


def test_m2_nao_se_diz_seguro_sobre_a_capital_que_nao_chegou():
    """O caso de BH em 2018: interior contado, capital pendente e maior que
    o interior inteiro. O M1 fecha a questão com o interior; o M2 não."""
    obs = _cenario_capital_atrasada()
    cfg = S.SimConfig(n_sims=2000, seed=5)
    m1 = S.run(M.build_m1(obs, list(CANDS)), list(CANDS), cfg)
    m2 = S.run(M.build_m2(obs, list(CANDS)), list(CANDS), cfg)
    assert m1.prob_lideranca()["a"] > 0.95
    assert m2.prob_lideranca()["a"] < m1.prob_lideranca()["a"] - 0.05
    l1 = m1.quantis()["a"]["pct_validos"]
    l2 = m2.quantis()["a"]["pct_validos"]
    # Com SIGMA_GRUPO_VAZIO calibrado em 2018 (0,25), o intervalo do M2 aqui
    # é ~1,35x o do M1; o M1 já alarga um pouco unidade sem dado nenhum.
    assert (l2["q900"] - l2["q100"]) > 1.25 * (l1["q900"] - l1["q100"])


def test_m3_sem_agro_nao_muda_e_com_agro_colinear_desiste():
    """A covariável do agro (05/10/2026) é opcional: sem ela o M3 é o de
    sempre, e quando não dá para separá-la da inclinação a regressão
    conjunta não é usada."""
    from obs2026.engine import models as M
    pts = []

    class O:                                   # o mínimo que a função lê
        def __init__(self, x, a, n, v):
            self.x, self.a = x, a
            self.obs = type("B", (), {"validos": n,
                                      "votos_por_candidato": v})()
    for i in range(12):
        x = -1 + i * 0.2
        pts.append(O(x, 0.1 + 0.03 * i, 1000, {"a": 400 + 20 * i,
                                               "b": 600 - 20 * i}))
    # agro = função linear exata da inclinação: colineares → sem resposta
    assert M._respostas_duas(pts, ["a", "b"], lambda o: o.x, lambda o: o.a,
                             50_000.0) == {}
    # agro com variação própria: sai um par de respostas por candidato
    for i, o in enumerate(pts):
        o.a = 0.1 + 0.05 * ((i * 7) % 5)
    r = M._respostas_duas(pts, ["a", "b"], lambda o: o.x, lambda o: o.a,
                          50_000.0)
    assert set(r) == {"a", "b"} and all(len(v) == 2 for v in r.values())
    assert r["a"][0] > 0 > r["b"][0]           # "a" cresce com a inclinação
