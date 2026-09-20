"""Comportamento do coletor sob condições adversas e contrato da API.

O que se testa aqui não é o caminho feliz — é o que acontece quando o TSE
responde 404, 429 ou lixo. Errar aqui custa bloqueio de IP de 10 minutos no
meio da apuração.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from obs2026.config import ConfigError, load_collector, load_environment
from obs2026.engine import backtest as B
from obs2026.engine import models as M
from obs2026.engine import simulate as S
from obs2026.politics import classification as CL
from obs2026.storage.raw import RawArchive
from obs2026.tse.collector import PriorityQueue
from obs2026.tse.parsers import ea20
from obs2026.tse.urls import Target

FIX = Path(__file__).parent / "fixtures" / "simulado2026"


# ------------------------------------------------------------ orçamento ----

def test_cadencia_que_estoura_o_teto_e_recusada():
    """A configuração original do §8.1 não cabia — e não pode passar calada."""
    from dataclasses import replace
    proibida = replace(load_collector(), reconcile_interval_s=900.0)
    with pytest.raises(ConfigError, match="cadencia inviavel"):
        proibida.assert_budget(n_accompaniment=57, n_control=83,
                               n_municipal=16897)


def test_cadencia_atual_cabe_com_folga():
    b = load_collector().assert_budget(n_accompaniment=57, n_control=83,
                                       n_municipal=16897)
    assert b["cabe"]
    assert b["piso_rps"] < b["teto_rps"]
    assert b["folga_rps"] > 0


def test_teto_interno_nunca_passa_do_limite_do_tse():
    assert load_collector().max_rps <= 100.0


def test_ambiente_de_producao_recusa_operar_sem_codigos_oficiais():
    """O TSE ainda não publicou os códigos de produção."""
    with pytest.raises(ConfigError, match="incompleto"):
        load_environment("oficial2026")


# ----------------------------------------------------------------- fila ----

def test_fila_deduplica_e_mantem_a_maior_prioridade():
    """Mesmo município enfileirado por detecção e por reconciliação.

    Buscá-lo duas vezes gasta cota e não traz informação.
    """
    q = PriorityQueue()
    t = Target("ea20", "http://x", election="1", cargo="5", uf="ac",
               mun="01120", abr="mu")
    assert q.push(t, 1.0, "reconciliacao") is True
    assert q.push(t, 9999.0, "mudanca") is False      # não duplica
    assert len(q) == 1
    assert q.pop().target is t
    assert q.pop() is None


def test_fila_envelhece_para_nao_deixar_unidade_sem_atendimento():
    q = PriorityQueue(aging_boost_per_minute=1000.0)
    pequeno = Target("ea20", "u1", mun="00001", abr="mu")
    grande = Target("ea20", "u2", mun="00002", abr="mu")
    q.push(pequeno, 1.0)
    q.push(grande, 500.0)
    q._heap[0].enqueued_at -= 600      # dez minutos de espera
    q._heap[1].enqueued_at -= 600
    q.age()
    assert len(q) == 2


# ------------------------------------------------------------- simulação ----

def _obs_alagoas(cargo: str):
    """Observações reais de AL, se já tiverem sido coletadas."""
    arc = RawArchive(Path(__file__).parent.parent / "data" / "raw",
                     "simulado2026")
    vistos = {}
    for day in arc.days():
        for ref in arc.manifest(day):
            if (ref.kind == "ea20" and ref.path and ref.cargo == cargo
                    and ref.mun and ref.uf == "al"):
                vistos[ref.mun] = ref
    out = []
    for mun, ref in vistos.items():
        r = ea20.parse(arc.load(ref))
        out.append(M.UnitObservation(
            unit=f"al:{mun}", uf="al",
            stratum=M.estrato("al", r.electorate.te), result=r))
    return out


def test_simulacao_com_tudo_apurado_nao_inventa_incerteza():
    obs = _obs_alagoas("3")
    if not obs:
        pytest.skip("dados de AL ainda não coletados")
    cands = [c.sqcand for c in obs[0].result.cargo.candidatos
             if c.conta_como_valido]
    units = M.build_m1(obs, cands, seats=1)
    fc = S.run(units, cands, S.SimConfig(n_sims=200, seed=1), seats=1)
    # Nada pendente => projeção é o resultado, com intervalo de largura zero.
    assert fc.pending_point == 0
    q = fc.quantis()
    for c in cands:
        assert q[c]["votos"]["q025"] == q[c]["votos"]["q975"]
        assert q[c]["votos_mediana"] == fc.observed[c]


def test_alocacao_inteira_conserva_o_total_e_nunca_fica_negativa():
    import numpy as np
    rng = np.random.default_rng(0)
    for _ in range(50):
        reais = rng.random((8, 5)) * 1000
        total = np.rint(reais.sum(axis=1)).astype(np.int64)
        aloc = S._alocar_inteiros(reais, total)
        assert (aloc >= 0).all()
        assert (aloc.sum(axis=1) == total).all()


def test_senado_exige_q_e_respeita_o_teto_de_dois_votos():
    obs = _obs_alagoas("5")
    if not obs:
        pytest.skip("dados de AL ainda não coletados")
    cands = [c.sqcand for c in obs[0].result.cargo.candidatos
             if c.conta_como_valido]
    units = M.build_m1(obs, cands, seats=2)
    assert all(u.q_hat is not None and 0 < u.q_hat < 2 for u in units)
    # Sem q_hat, o modelo de Senado recusa rodar em vez de supor 2 escolhas.
    from dataclasses import replace
    sem_q = [replace(u, q_hat=None, pending_lower=100, pending_upper=100)
             for u in units[:1]]
    with pytest.raises(ValueError, match="q_hat"):
        S.run(sem_q, cands, S.SimConfig(n_sims=10), seats=2, senado=True)


def test_backtest_mascara_unidade_preservando_o_eleitorado():
    """O eleitorado é conhecido antes da eleição; o resultado não."""
    r = ea20.parse(json.loads((FIX / "ea20_ac_gov.json").read_text("utf-8")))
    m = B.mask_unit(r)
    assert m.electorate.te == r.electorate.te      # eleitorado permanece
    assert m.votes.vv == 0 and m.votes.tv == 0     # resultado some
    assert m.electorate.c == 0
    assert m.electorate.esna == m.electorate.te    # tudo por apurar
    assert m.andamento == "n"
    assert all(c.votos == 0 for c in m.cargo.candidatos)


def test_gate_recusa_quando_m1_nao_supera_m0():
    """O gate tem de reprovar, não apenas existir."""
    fake = [
        B.CutReport(50.0, 50.0, 50.0, "m0", "aleatoria", True,
                    0.20, 0.5, 100, 0.8, 0.95, 2.0, True, True, 0.01),
        B.CutReport(50.0, 50.0, 50.0, "m1", "aleatoria", True,
                    0.40, 0.7, 200, 0.8, 0.95, 3.0, True, True, 0.01),
    ]
    g = B.gate_m1_vence_m0(fake)
    assert g["passou"] is False
    assert g["m1_pior"] == 1


# -------------------------------------------------------- classificação ----

def test_mapa_ideologico_nasce_desabilitado():
    """Preencher a classificação por conta própria seria inventar autoria."""
    m = CL.metodologia()
    assert m["estado"] == "curadoria_pendente"
    assert m["motivo"]
    assert m["entradas"] == 0


def test_ausencia_de_classificacao_nao_e_centro():
    reg = CL.load()
    assert reg.bloco("QUALQUER") is None          # None, não "centro"
    assert CL.SEM_CLASSIFICACAO["cor"] != CL.PALETA["centro"]
    assert "hachura" in CL.SEM_CLASSIFICACAO["padrao"]


def test_paleta_e_a_pedida_e_tem_simbolo_por_categoria():
    assert CL.PALETA["esquerda"] == "#C62828"
    assert CL.PALETA["centro"] == "#F2C94C"
    assert CL.PALETA["direita"] == "#1565C0"
    # Leitura não pode depender só de cor (§11.3).
    assert set(CL.SIMBOLOS) == set(CL.PALETA)


def test_saldo_exige_classificacao_e_nao_vira_medida_de_polarizacao():
    assert CL.saldo(None, None, None, None)["saldo"] is None
    s = CL.saldo(40.0, 35.0, 20.0, 5.0)
    assert s["saldo"] == pytest.approx(5.0)
    assert s["pct_centro"] == 20.0                # sempre acompanha
    assert "polarização afetiva" in s["aviso"]


# ------------------------------------------------------------------ API ----

@pytest.fixture(scope="module")
def cliente():
    from fastapi.testclient import TestClient
    from obs2026.api.app import app, configure
    env = load_environment("simulado2026")
    if not env.db_path.exists():
        pytest.skip("banco ainda não carregado; rode `obs2026 carregar`")
    configure("simulado2026")
    return TestClient(app)


def test_envelope_traz_os_metadados_obrigatorios(cliente):
    d = cliente.get("/api/v1/results?cargo=1&abrangencia=br").json()
    for campo in ("as_of", "source_generated_at", "captured_at",
                  "snapshot_set_id", "status", "quality_flags",
                  "model_version", "classification_version", "denominador"):
        assert campo in d, campo
    assert d["denominador"]["base"] == "validos"


def test_ambiente_de_simulado_e_anunciado_na_resposta(cliente):
    d = cliente.get("/api/v1/elections").json()
    assert d["ambiente"]["oficial"] is False
    assert "SIMULADO" in d["ambiente"]["aviso"]


def test_percentual_sobre_validos_e_nulo_para_candidato_sub_judice(cliente):
    d = cliente.get("/api/v1/results?cargo=1&abrangencia=br"
                    "&base=validos").json()
    sj = [c for c in d["dados"][0]["candidatos"]
          if c["destinacao_voto"] != "Válido"]
    assert sj, "fixture deveria ter candidato sub judice"
    for c in sj:
        assert c["pct"] is None                      # null, não zero
        assert c["pct_indisponivel_motivo"]          # e com motivo


def test_base_nominal_reproduz_o_percentual_do_tse(cliente):
    """8,712251427 é o `pvapn` do candidato 57 no payload real.

    Cuidado com a armadilha: 8,701884674 também aparece no mesmo arquivo,
    em `v.pvansjn` — mas aquele é a fração dos votos sub judice sobre `vvc`,
    outro número com outro denominador. Trocar um pelo outro é exatamente o
    erro que o §9 previne exigindo a base declarada.
    """
    d = cliente.get("/api/v1/results?cargo=1&abrangencia=br"
                    "&base=nominal_tse").json()
    topo = d["dados"][0]["candidatos"][0]
    assert topo["pct"] == pytest.approx(8.712251427, abs=1e-6)


def test_forecast_nao_publica_probabilidade_sem_homologacao(cliente):
    d = cliente.get("/api/v1/forecasts?cargo=1").json()
    assert d["status"] == "cenario_exploratorio"
    assert d["dados"] is None
    assert "thresholds" in d["status_motivo"]
    assert d["thresholds_homologados"] is False


def test_situacao_de_eleito_so_vale_na_abrangencia_que_decide(cliente):
    mun = cliente.get("/api/v1/results?cargo=5&abrangencia=mu&uf=al").json()
    for u in mun["dados"][:5]:
        for c in u["candidatos"]:
            # None, nunca False: "não se decide aqui" ≠ "não foi eleito".
            assert c["eleito_segundo_tse"] is None
            assert c["situacao_definida_nesta_abrangencia"] is False
    uf = cliente.get("/api/v1/results?cargo=5&abrangencia=uf&uf=al").json()
    eleitos = [c for c in uf["dados"][0]["candidatos"]
               if c["eleito_segundo_tse"]]
    assert len(eleitos) == 2                        # duas vagas


def test_senado_tem_duas_vagas_no_catalogo(cliente):
    d = cliente.get("/api/v1/elections").json()
    vagas = {c["cargo_cd"]: c["vagas"]
             for e in d["dados"] for c in e["disputas"]}
    assert vagas["5"] == 2
    assert vagas["1"] == 1 and vagas["3"] == 1


def test_exportacao_tem_limite_obrigatorio(cliente):
    assert cliente.get("/api/v1/exports?cargo=5&limite=999999").status_code \
        == 400
