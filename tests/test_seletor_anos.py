"""Seletor de anos: as telas da noite lendo 2018 ou 2022 (24/09/2026).

O que se protege: mostrar 2026 com rótulo de 2022 (pedido de eleição que
não existe sendo ignorado em vez de recusado), escrever num ano passado,
pintar o PSL de 2018 com cor "fora do cadastro" ou classificá-lo pela régua
de 2026, e o 25 de 2018 (DEM) cair no PRD por coincidência de número.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from obs2026 import contexto as CTX
from obs2026.api import app as M
from obs2026.politics import classification as CL
from obs2026.politics import partidos as PA


@pytest.fixture(scope="module")
def cliente():
    return TestClient(M.app)


def _montado(nome: str) -> bool:
    return M.ambiente_congelado(nome)


def test_eleicao_que_nao_e_congelada_e_recusada(cliente):
    """Ignorar o parâmetro mostraria 2026 com a faixa de 2022."""
    r = cliente.get("/api/v1/elections?eleicao_passada=simulado2026")
    assert r.status_code == 400
    r = cliente.get("/api/v1/elections?eleicao_passada=tse1998t1")
    assert r.status_code == 400


def test_eleicao_passada_e_so_leitura(cliente):
    r = cliente.post("/api/v1/telao/roteiro?eleicao_passada=tse2022t1", json={})
    assert r.status_code == 400
    assert "só leitura" in r.json()["detail"]


def test_sem_eleicao_continua_no_ao_vivo(cliente):
    r = cliente.get("/api/v1/elections")
    assert r.status_code == 200
    assert r.json()["ambiente"]["nome"] == M._ENV_NAME
    assert r.json()["ambiente"]["eleicao"] is None


@pytest.mark.skipif(not _montado("tse2022t2"),
                    reason="tse2022t2 não montado neste checkout")
def test_resultado_final_de_2022_le_o_banco_certo(cliente):
    r = cliente.get("/api/v1/results?abrangencia=br&cargo=1"
                    "&eleicao_passada=tse2022t2").json()
    amb = r["ambiente"]
    assert amb["nome"] == "tse2022t2"
    assert amb["eleicao"]["ano"] == 2022 and amb["eleicao"]["turno"] == "2"
    # 12 UFs com 2º turno para governador em 2022; as outras 15, pelo 1º.
    assert len(amb["eleicao"]["governador_decidido_1o_turno"]) == 15
    # O resultado oficial do 2º turno de 2022.
    topo = max(r["dados"][0]["candidatos"], key=lambda c: c["votos"])
    assert topo["nome_urna"] == "LULA" and topo["votos"] == 60345999
    assert round(topo["pct"], 2) == 50.90
    # E o pedido seguinte, sem eleição, volta ao ao vivo.
    r2 = cliente.get("/api/v1/elections").json()
    assert r2["ambiente"]["nome"] == M._ENV_NAME


def test_partido_de_2018_pela_linhagem():
    """PSL de 2018: sigla da época, cor do União (a série de hoje). O 25 de
    2018 é o DEM — também União —, nunca o PRD que usa o 25 em 2026."""
    uniao = PA.resolver("44", "UNIÃO")
    tk = CTX.ANO_ELEICAO.set(2018)
    try:
        psl = PA.resolver("17", "PSL")
        dem = PA.resolver("25", "DEM")
    finally:
        CTX.ANO_ELEICAO.reset(tk)
    assert psl.sigla == "PSL" and psl.numero == "17" and psl.cadastrado
    assert psl.cor == uniao.cor
    assert dem.sigla == "DEM" and dem.cor == uniao.cor
    assert dem.cor != PA.resolver("25", "PRD").cor
    # Fora do contexto, nada muda: o 17 não está no cadastro de 2026.
    assert not PA.resolver("17", "PSL").cadastrado


def test_classificacao_do_ano_do_pedido():
    tk = CTX.ANO_ELEICAO.set(2018)
    try:
        caminho = CL.caminho_padrao()
        reg = CL.load()
        psl = reg.por_partido("PSL", "17")
    finally:
        CTX.ANO_ELEICAO.reset(tk)
    assert caminho.name == "classificacao_2018.yaml"
    assert psl is not None
    assert CL.caminho_padrao().name == "classificacao_2026.yaml"


def test_roteiro_recusa_eleicao_nao_montada_e_fora_do_mapa(cliente):
    import os
    from pathlib import Path

    arq = Path(os.environ["OBS2026_TELAO"])
    original = arq.read_bytes() if arq.exists() else None
    JSON = {"Content-Type": "application/json"}
    try:
        r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={
            "roteiro": [{"cargo": "1", "eleicao": "tse1998t1",
                         "segundos": 20}]})
        assert r.status_code != 200
        r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={
            "roteiro": [{"tipo": "comparativo", "cargo": "1",
                         "eleicao": "tse2022t1", "segundos": 20}]})
        assert r.status_code != 200
        if _montado("tse2022t1"):
            r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={
                "roteiro": [{"cargo": "1", "eleicao": "tse2022t1",
                             "segundos": 20}]})
            assert r.status_code == 200, r.text
            tela = cliente.get("/api/v1/telao/roteiro").json()["dados"][0]
            assert tela["eleicao"] == "tse2022t1"
            assert tela["titulo"].endswith("(2022)")
    finally:
        if original is not None:
            arq.write_bytes(original)


def test_codigos_da_eleicao_ordinaria():
    """O zip de 2018 traz a suplementar de 2020 do Senado em MT (438). Ela
    tem o mesmo ano, turno e cargo; só o código a separa."""
    from obs2026.history import tse_odsele as H
    assert H.ordinarias(2018, "1") == {"295", "297"}
    assert H.ordinarias(2022, "2") == {"545", "547"}
    assert "438" not in H.ordinarias(2018, "1")
    assert H.ordinarias(2014, "1") == {"143"}   # federal e estadual juntos
    assert H.ordinarias(2010, "1") is None     # fora da tabela: não adivinha


@pytest.mark.skipif(not _montado("tse2018t1"),
                    reason="tse2018t1 não montado neste checkout")
def test_senado_2018_nao_poe_o_proximo_na_vaga_sem_eleito(cliente):
    """MT/2018: a eleita foi cassada e não está no arquivo atual do TSE. A
    vaga fica sem eleito — nem o 3º colocado de 2018, nem o eleito da
    suplementar de 2020."""
    d = cliente.get("/api/v1/composicao?casa=senado"
                    "&eleicao_passada=tse2018t1").json()["dados"]
    mt = [c for c in d["cadeiras"] if c["uf"] == "mt"
          and c["origem"] == "eleicao"]
    assert len(mt) == 2
    assert sorted(c["estado"] for c in mt) == ["eleito", "sem_eleito"]
    assert all(c["nome"] != "FÁVARO" for c in mt)
    assert d["em_disputa"]["estados"].get("definido", 0) == 0
    # A definição matemática não se aplica a eleição passada.
    r = cliente.get("/api/v1/decided?cargo=5&uf=mt"
                    "&eleicao_passada=tse2018t1").json()
    assert r["status"] == "nao_aplicavel" and r["dados"] == []
