"""Comparação ano a ano: linhagem partidária e bancada da Câmara (24/09/2026).

O que se protege: uma série partida ao meio por grafia do TSE ("PATRI" em
2018, "PATRIOTA" em 2022; "PC do B" contra "PCdoB"), um número reusado
jogando um partido na série de outro (o 25 era o DEM e é o PRD), e a
bancada de 2018 aparecendo com o nome de hoje sem dizer de onde veio.
"""

from __future__ import annotations

import pytest

from obs2026.politics import linhagem as LIN


@pytest.fixture(scope="module")
def reg():
    r = LIN.load()
    if r.pendente:
        pytest.skip("linhagem não assinada neste checkout")
    return r


def test_patriota_de_2018_e_2022_na_mesma_serie(reg):
    """O TSE grava o 51 como PATRI em 2018 e PATRIOTA em 2022."""
    assert (LIN.serie("PATRI", "51", 2018, reg)[0]
            == LIN.serie("PATRIOTA", "51", 2022, reg)[0]
            == "25|PRD")


def test_pcdob_com_e_sem_espaco_e_o_ppl_na_mesma_serie(reg):
    chave = LIN.serie("PC do B", "65", 2018, reg)[0]
    assert LIN.serie("PCdoB", "65", 2022, reg)[0] == chave
    assert LIN.serie("PPL", "54", 2018, reg)[0] == chave


def test_numero_reusado_depois_da_troca_nao_puxa_outro_partido(reg):
    """PRP → Patriota → PRD (25). Procurar "25 em 2018" achava o DEM e
    fechava um ciclo; a cadeia segue no ano em que o sucessor vale."""
    assert LIN.serie("PRP", "44", 2018, reg)[0] == "25|PRD"
    assert LIN.serie("DEM", "25", 2018, reg)[0] == "44|UNIAO"
    assert LIN.serie("PRD", "25", 2026, reg)[0] == "25|PRD"


def test_numero_fora_da_janela_nao_casa(reg):
    """O 44 de 2022 é o União, não o PRP de 2018; o 14 de 2026 é o
    Missão, não o PTB."""
    assert LIN.serie("UNIÃO", "44", 2022, reg)[0] == "44|UNIAO"
    assert LIN.serie("MISSÃO", "14", 2026, reg)[0] == "14|MISSAO"


def test_linhagem_assinada_valida(reg):
    assert LIN.validar(reg) == []


# ------------------------------------------------------ bancada da Câmara

@pytest.fixture(scope="module")
def cliente_historico(tmp_path_factory):
    """App com um banco histórico de brinquedo: 2018 e 2022, poucas
    cadeiras, montadas para exercitar fusão e troca de nome."""
    from fastapi.testclient import TestClient

    from obs2026.history import banco as B

    caminho = tmp_path_factory.mktemp("hist") / "historico.sqlite"
    _, S = B.abrir(caminho)
    linhas = [
        (2018, "PSL", "17", 3), (2018, "DEM", "25", 2), (2018, "PT", "13", 4),
        (2018, "PR", "22", 1),
        (2022, "UNIÃO", "44", 4), (2022, "PT", "13", 5), (2022, "PL", "22", 6),
    ]
    with S() as s:
        for ano, sg, n, q in linhas:
            for i in range(q):
                s.add(B.HistVoto(ano=ano, turno="1", cargo="6", tipo="uf",
                                 uf="sp", sqcand=f"{ano}{sg}{i}", numero=n + str(i),
                                 partido_sigla=sg, partido_numero=n,
                                 votos=1000 - i, eleito=True,
                                 situacao="ELEITO POR QP"))
            # Um não eleito, que não pode contar.
            s.add(B.HistVoto(ano=ano, turno="1", cargo="6", tipo="uf",
                             uf="sp", sqcand=f"{ano}{sg}x", numero=n + "99",
                             partido_sigla=sg, partido_numero=n, votos=1,
                             eleito=False, situacao="SUPLENTE"))
        # Senado em SP: dois eleitos em 2018, um em 2022.
        # Governador em SP: 2018 decidido no 2º turno.
        maj = [
            (2018, "5", "1", "PSL", "17", 9000, True),
            (2018, "5", "1", "PSDB", "45", 8000, True),
            (2018, "5", "1", "PT", "13", 7000, False),
            (2022, "5", "1", "PSD", "55", 9000, True),
            (2022, "5", "1", "PT", "13", 8000, False),
            (2018, "3", "1", "PSDB", "45", 5000, False),
            (2018, "3", "1", "PSB", "40", 4000, False),
            (2018, "3", "2", "PSB", "40", 4900, False),
            (2018, "3", "2", "PSDB", "45", 5100, True),
            (2022, "3", "1", "REPUBLICANOS", "10", 5000, False),
            (2022, "3", "2", "REPUBLICANOS", "10", 6000, True),
        ]
        for i, (ano, cargo, turno, sg, n, votos, el) in enumerate(maj):
            s.add(B.HistVoto(ano=ano, turno=turno, cargo=cargo, tipo="uf",
                             uf="sp", sqcand=f"m{i}", numero=n + "1",
                             nome_urna=f"cand {i}", partido_sigla=sg,
                             partido_numero=n, votos=votos, eleito=el))
        for ano in (2018, 2022):
            for cargo in ("3", "5"):
                s.add(B.HistTotal(ano=ano, turno="1", cargo=cargo, tipo="uf",
                                  uf="sp", aptos=1000))
        s.commit()
    antes = B.CAMINHO
    B.CAMINHO = caminho
    from obs2026.api.app import app
    yield TestClient(app)
    B.CAMINHO = antes


def test_camara_liga_series_e_diz_de_onde_veio(cliente_historico, reg):
    r = cliente_historico.get("/api/v1/comparativo/camara?anos=2018,2022")
    assert r.status_code == 200
    d = r.json()["dados"]
    assert d["colunas"]["2018"]["eleitos_tse"] == 10
    assert d["colunas"]["2022"]["eleitos_tse"] == 15
    assert d["variacao_par"] == "2018-2022"
    series = {se["serie"]: se for se in d["series"]}

    uniao = series["44|UNIAO"]
    assert uniao["anos"]["2018"]["cadeiras"] == 5
    assert uniao["anos"]["2018"]["composta"] is True
    assert {c["sigla"] for c in uniao["anos"]["2018"]["componentes"]} \
        == {"PSL", "DEM"}
    assert uniao["anos"]["2022"]["composta"] is False
    assert uniao["variacao"]["2018-2022"] == -1

    pl = series["22|PL"]
    assert pl["anos"]["2018"]["componentes"][0]["sigla"] == "PR"
    assert pl["anos"]["2018"]["composta"] is True     # outro nome na época
    assert pl["variacao"]["2018-2022"] == 5

    # A ordem segue o ano mais recente: PL 6, PT 5, União 4.
    assert [se["serie"] for se in d["series"]][:3] == [
        "22|PL", "13|PT", "44|UNIAO"]


def test_camara_blocos_somam_as_cadeiras(cliente_historico):
    d = cliente_historico.get(
        "/api/v1/comparativo/camara?anos=2018,2022").json()["dados"]
    for a in ("2018", "2022"):
        c = d["colunas"][a]
        assert sum(c["por_bloco"].values()) == c["eleitos_tse"]


def test_camara_ano_nao_importado_nao_vira_zero(cliente_historico):
    d = cliente_historico.get(
        "/api/v1/comparativo/camara?anos=2014,2022").json()["dados"]
    assert d["colunas"]["2014"]["sem_dado"] is True
    # Sem par de anos com dado, não há variação a mostrar.
    assert all(se["variacao"] == {} for se in d["series"])


def test_roteiro_aceita_camara_ano_a_ano_e_so_no_comparativo(
        cliente_historico):
    """O painel manda `cargo: "6"` para a bancada ano a ano. O roteiro grava
    como "deputado federal", relê sem erro e sem UF; no mapa, o cargo 6
    continua recusado (não há mapa de deputado)."""
    import os
    from pathlib import Path

    # A cópia de sessão do roteiro (conftest) volta como estava: os testes
    # seguintes não podem ver a tela gravada aqui.
    arq = Path(os.environ["OBS2026_TELAO"])
    original = arq.read_bytes() if arq.exists() else None
    c = cliente_historico
    JSON = {"Content-Type": "application/json"}
    try:
        r = c.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
            {"tipo": "comparativo", "cargo": "6", "uf": "sp",
             "segundos": 30}]})
        assert r.status_code == 200, r.text
        tela = c.get("/api/v1/telao/roteiro").json()["dados"][0]
        assert not tela["erro"]
        assert tela["cargo"] == "6" and tela["uf"] is None
        assert tela["titulo"] == "Câmara — bancadas ao longo das eleições"

        r = c.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
            {"tipo": "mapa", "cargo": "6", "segundos": 20}]})
        assert r.status_code != 200

        # O resumo dos quatro cargos: nacional, sem UF.
        r = c.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
            {"tipo": "comparativo", "cargo": "R", "uf": "rj",
             "segundos": 40}]})
        assert r.status_code == 200, r.text
        tela = c.get("/api/v1/telao/roteiro").json()["dados"][0]
        assert not tela["erro"] and tela["cargo"] == "R"
        assert tela["uf"] is None
        assert tela["titulo"] == "O país eleição a eleição — resumo"
    finally:
        if original is not None:
            arq.write_bytes(original)


def test_assinar_proposta_promove_e_guarda_a_anterior(tmp_path):
    """A proposta espera ao lado da oficial; assinar a promove com a versão
    seguinte e guarda a anterior. Sem proposta e com a oficial assinada,
    assinar de novo é recusado — não se reassina por cima."""
    import shutil
    from datetime import date

    from obs2026.config import CONFIG_DIR

    oficial = tmp_path / "linhagem_partidos.yaml"
    shutil.copy2(CONFIG_DIR / "linhagem_partidos.yaml", oficial)
    proposta = LIN.caminho_proposta(oficial)
    origem = CONFIG_DIR / "linhagem_partidos.proposta.yaml"
    if not origem.exists():
        pytest.skip("sem proposta pendente neste checkout")
    shutil.copy2(origem, proposta)
    anterior = LIN.load(oficial).versao

    reg = LIN.assinar("Pessoa de Teste", oficial, quando=date(2026, 9, 24))
    assert reg.curador == "Pessoa de Teste"
    assert reg.versao == LIN._proxima_versao(anterior, 2026)
    assert not proposta.exists()
    assert (tmp_path / f"linhagem_partidos.{anterior}.yaml").exists()
    assert "ASSINADA por Pessoa de Teste" in oficial.read_text(encoding="utf-8")
    # O Podemos de 2018 chega ao de 2026 (número 20).
    assert LIN.serie("PODE", "19", 2018, reg)[0] == "20|PODE"

    with pytest.raises(LIN.LinhagemError):
        LIN.assinar("Pessoa de Teste", oficial)


def test_proxima_versao():
    assert LIN._proxima_versao("2026.1", 2026) == "2026.2"
    assert LIN._proxima_versao("2025.3", 2026) == "2026.1"
    assert LIN._proxima_versao(None, 2026) == "2026.1"


def test_senado_2018_conta_as_duas_cadeiras(cliente_historico):
    """Em 2018 cada estado elegeu dois senadores; o panorama contava só o
    mais votado e apagava metade da renovação."""
    d = cliente_historico.get(
        "/api/v1/comparativo/governadores?cargo=5&anos=2018,2022"
    ).json()["dados"]
    assert d["unidade"] == "cadeiras"
    sp = next(u for u in d["ufs"] if u["uf"] == "sp")
    assert [v["partido"] for v in sp["anos"]["2018"]["vencedores"]]         == ["PSL", "PSDB"]
    assert len(sp["anos"]["2022"]["vencedores"]) == 1
    assert d["agregado"]["2018"]["vagas"] == 2
    assert d["agregado"]["2022"]["vagas"] == 1
    # Eleitorado repartido entre os eleitos: soma o da UF, não o dobro.
    assert sum(x["eleitorado"] for x in
               d["agregado"]["2018"]["por_bloco"].values()) == 1000
    series = {se["serie"]: se for se in d["series"]}
    assert series["44|UNIAO"]["anos"]["2018"]["cadeiras"] == 1   # ex-PSL
    assert "13|PT" not in series            # não elegeu: fora da contagem


def test_governador_vale_o_segundo_turno(cliente_historico):
    d = cliente_historico.get(
        "/api/v1/comparativo/governadores?cargo=3&anos=2018,2022"
    ).json()["dados"]
    sp = next(u for u in d["ufs"] if u["uf"] == "sp")
    assert sp["anos"]["2018"]["partido"] == "PSDB"
    assert sp["anos"]["2018"]["turno"] == "2"
    series = {se["serie"]: se for se in d["series"]}
    assert series["10|REPUBLICANOS"]["variacao"]["2018-2022"] == 1
    assert series["45|PSDB"]["variacao"]["2018-2022"] == -1


def test_vagas_do_senado_alternam():
    from obs2026.api.historico import vagas_por_uf
    assert [vagas_por_uf("5", a) for a in (2014, 2018, 2022, 2026, 2030)]         == [1, 2, 1, 2, 1]
    assert vagas_por_uf("3", 2018) == 1


def test_senado_composicao_soma_quem_continuava(cliente_historico):
    """Pedido de 24/09: além dos eleitos no ano, a casa inteira. 2022 = o
    eleito de 2022 + os dois de 2018 (+ suplementar entre as eleições,
    marcada). 2018 sem 2014 importado: a composição diz o que falta."""
    from obs2026.history import banco as B
    _, S = B.abrir(B.CAMINHO)
    with S() as s:
        s.add(B.HistVoto(ano=2020, turno="1", cargo="5", tipo="uf", uf="rj",
                         sqcand="sup1", numero="551", nome_urna="SUPLENTE X",
                         partido_sigla="PSD", partido_numero="55",
                         votos=10, eleito=True,
                         situacao="ELEITO EM SUPLEMENTAR (15/11/2020)"))
        s.commit()
    try:
        d = cliente_historico.get(
            "/api/v1/comparativo/governadores?cargo=5&anos=2018,2022"
        ).json()["dados"]
        c22 = d["agregado"]["2022"]["composicao"]
        assert c22["cadeiras"] == 1 + 2 + 1
        assert c22["suplementares"] == [
            {"uf": "rj", "sigla": "PSD", "eleito_em": 2020}]
        c18 = d["agregado"]["2018"]["composicao"]
        assert c18["cadeiras"] == 2
        assert c18["remanescentes"]["estado"] == "indisponivel"
        # A linha "eleitos" continua só com os eleitos no ano.
        assert d["agregado"]["2022"]["vagas"] == 1
        psd = next(x for x in d["series_composicao"]
                   if x["serie"] == "55|PSD")
        assert psd["anos"]["2022"]["cadeiras"] == 2      # eleito + suplementar
    finally:
        with S() as s:
            s.query(B.HistVoto).filter(B.HistVoto.sqcand == "sup1").delete()
            s.commit()
