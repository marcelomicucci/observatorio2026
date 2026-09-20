"""Testes de contrato contra payloads REAIS do simulado TSE de 2026.

As fixtures em `tests/fixtures/simulado2026/` foram baixadas do servidor do
TSE em 20/09/2026 e não são inventadas. Se o TSE mudar o contrato na janela
de 22–24/09, estes testes quebram — que é exatamente o que se quer: melhor
falhar no CI do que ingerir torto na noite da apuração.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obs2026.engine import denominators as D
from obs2026.engine import pending as PD
from obs2026.quality.checks import CRITICAL, check_ea20
from obs2026.tse.parsers import accompaniment as AB
from obs2026.tse.parsers import ea11, ea12, ea20
from obs2026.tse.parsers.common import ParseError, as_int, as_pct, as_text, fold
from obs2026.tse.urls import UrlBuilder, UrlError, fmt_election, fmt_mun

FIX = Path(__file__).parent / "fixtures" / "simulado2026"
HOSTS = frozenset({"resultados-sim.tse.jus.br"})


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def urls() -> UrlBuilder:
    return UrlBuilder("https://resultados-sim.tse.jus.br/simulado",
                      "simulado2026", "ele2026", HOSTS)


# ---------------------------------------------------------------- URLs ----

def test_urls_batem_com_os_exemplos_publicados_pelo_tse(urls):
    """Cada URL tem de sair idêntica ao exemplo da página técnica do TSE.

    Não é preciosismo: 404 repetido é causa documentada de bloqueio de IP.
    """
    B = "https://resultados-sim.tse.jus.br/simulado/simulado2026"
    assert urls.ea11().url == f"{B}/comum/config/ele-c.json"
    assert urls.ea12("21270").url == \
        f"{B}/ele2026/21270/config/mun-e021270-cm.json"
    assert urls.ea14("21270").url == \
        f"{B}/ele2026/21270/dados/br/br-e021270-ab.json"
    assert urls.ea15("21270", "ac").url == \
        f"{B}/ele2026/21270/dados/ac/ac-e021270-ab.json"
    assert urls.ea20_br("21270", "1").url == \
        f"{B}/ele2026/21270/dados/br/br-c0001-e021270-u.json"
    assert urls.ea20_uf("21272", "3", "ac").url == \
        f"{B}/ele2026/21272/dados/ac/ac-c0003-e021272-u.json"
    assert urls.ea20_mun("21272", "5", "ac", "01120").url == \
        f"{B}/ele2026/21272/dados/ac/ac01120-c0005-e021272-u.json"


@pytest.mark.parametrize("ruim", [1120, "1120", "", "abc", "012345", None])
def test_codigo_de_municipio_invalido_nunca_vira_url(ruim):
    with pytest.raises(UrlError):
        fmt_mun(ruim)


def test_eleicao_recebe_seis_digitos_com_zeros_a_esquerda():
    assert fmt_election("21270") == "021270"
    assert fmt_election("1") == "000001"


def test_host_fora_da_lista_e_recusado():
    with pytest.raises(UrlError):
        UrlBuilder("https://exemplo.invalido/x", "simulado2026", "ele2026",
                   HOSTS)


def test_ambiente_sem_base_recusa_construir():
    """Os códigos de produção ainda não foram publicados pelo TSE."""
    with pytest.raises(UrlError):
        UrlBuilder("", "", "ele2026", HOSTS)


# ------------------------------------------------------------ parsers ----

def test_ea11_descobre_eleicoes_cargos_e_segundo_turno():
    c = ea11.parse(load("ele-c.json"))
    assert c.fase == "s"                       # simulado, não oficial
    p, e = c.eleicao("21270")
    assert p.ciclo == "ele2026"
    assert e.cd_turno2 == "21271"              # 2º turno é eleição própria
    assert e.tem_cargo("1")
    _, est = c.eleicao("21272")
    assert est.tem_cargo("3") and est.tem_cargo("5")
    assert est.cd_turno2 == "21273"


def test_ea12_traz_codigo_ibge_oficial():
    """O crosswalk TSE↔IBGE vem da fonte; não é pareamento por nome."""
    m = ea12.parse(load("ea12_mun.json"), "21270")
    br = [x for x in m.municipios if not x.is_exterior]
    assert len(br) == 5571
    assert all(x.cd_ibge and len(x.cd_ibge) == 7 for x in br)
    acrelandia = m.by_code()[("ac", "01120")]
    assert acrelandia.cd_ibge == "1200013"
    # Exterior não é município: `cdi` vazio é o correto.
    assert all(x.cd_ibge is None for x in m.municipios if x.is_exterior)


def test_ea12_desfaz_entidade_html_no_nome():
    """Sem unescape de `&APOS;`, 45 municípios não pareiam com nada."""
    m = ea12.parse(load("ea12_mun.json"), "21270")
    com_apostrofo = [x for x in m.municipios if "'" in x.nome]
    assert len(com_apostrofo) == 45
    assert not any("&APOS;" in x.nome or "&apos;" in x.nome
                   for x in m.municipios)


def test_nome_de_municipio_nao_e_chave_unica():
    """`PAU D'ARCO` existe em PA e em TO.

    Por isso a associação é decidida por UF + código TSE, e o nome só serve
    para procurar candidatos a correspondência (§2.1).
    """
    m = ea12.parse(load("ea12_mun.json"), "21270")
    nomes = [x.nome for x in m.municipios if "'" in x.nome]
    assert len(nomes) == 45
    assert len(set(nomes)) == 44
    pau = [x for x in m.municipios if x.nome_fold == "PAU D'ARCO"]
    assert {x.uf for x in pau} == {"pa", "to"}
    assert len({x.cd for x in pau}) == 2


def test_ea12_e_por_eleicao_e_exterior_so_existe_na_federal():
    """Catálogos diferentes por eleição — unir é obrigatório ao conciliar."""
    fed = ea12.parse(load("ea12_mun.json"), "21270")
    est = ea12.parse(load("ea12_21272.json"), "21272")
    assert sum(1 for m in fed.municipios if m.is_exterior) == 184
    assert sum(1 for m in est.municipios if m.is_exterior) == 0


def test_zonas_nao_sao_somaveis_por_municipio():
    m = ea12.parse(load("ea12_mun.json"), "21270")
    soma = sum(len(x.zonas) for x in m.municipios)
    distintas = len(m.zonas_unicas)
    assert distintas < soma            # uma zona pode abranger vários


def test_ea15_lista_todos_os_municipios_da_uf_com_token_de_mudanca():
    a = AB.parse(load("ea15_ac_21272.json"), "ea15", "ac")
    assert len(a.municipios) == 22     # bate com o EA12 do AC
    assert a.ufs                       # e também a própria UF
    assert {u.tpabr_raw for u in a.units} == {"uf", "mun"}
    assert all(u.tpabr == "mu" for u in a.municipios)   # normalizado
    tok = a.municipios[0].change_token()
    assert tok and tok == a.municipios[0].change_token()   # estável


def test_ea14_cobre_as_27_ufs_mais_o_brasil():
    b = AB.parse(load("ea14_21272.json"), "ea14")
    assert len(b.ufs) == 27            # sem zz: não há governador no exterior
    assert sum(u.mun_finalizados or 0 for u in b.ufs) == 5571


# -------------------------------------------------- invariantes do EA20 ----

ARQUIVOS = ["ea20_br_pres.json", "ea20_ac_gov.json", "ea20_ac01120_sen.json"]


@pytest.mark.parametrize("nome", ARQUIVOS)
def test_identidades_contabeis(nome):
    r = ea20.parse(load(nome))
    v, e, s = r.votes, r.electorate, r.sections
    assert v.vvc == v.vv + v.van + v.vansj
    assert v.tv == v.vvc + v.vb + v.tvn
    assert e.te == e.esi + e.esni
    assert e.esi == e.esa + e.esna
    assert e.te == e.est + e.esnt
    assert s.ts == s.si + s.sni == s.st + s.snt
    assert s.si == s.sa + s.sna
    # A invariante que fundamenta o §4.3.
    assert e.c + e.a == e.esa


@pytest.mark.parametrize("nome", ARQUIVOS)
def test_soma_dos_validos_bate_com_vv(nome):
    r = ea20.parse(load(nome))
    soma = sum(c.votos for c in r.cargo.candidatos if c.conta_como_valido)
    assert soma == r.votes.vv


@pytest.mark.parametrize("nome", ARQUIVOS)
def test_nenhuma_divergencia_critica(nome):
    r = ea20.parse(load(nome))
    esperado = {"1": 1, "3": 1, "5": 2}[r.cargo.cd]
    graves = [f for f in check_ea20(r, expected_seats=esperado,
                                    official_env=False)
              if f.severity == CRITICAL]
    assert graves == [], [f"{f.code}: {f.observed}" for f in graves]


@pytest.mark.parametrize("nome", ARQUIVOS)
def test_denominador_do_pvap_e_a_soma_dos_candidatos(nome):
    """`pvap` NÃO é sobre `vv` nem sobre `vvc`.

    Esta é a diferença que decide se um candidato "passou em primeiro turno"
    na tela. Erro tolerado: 1e-6 pp.
    """
    r = ea20.parse(load(nome))
    soma = sum(c.votos for c in r.cargo.candidatos)
    for c in r.cargo.candidatos:
        if c.pct_tse is None:
            continue
        assert abs(100.0 * c.votos / soma - c.pct_tse) < 1e-6


@pytest.mark.parametrize("nome", ARQUIVOS)
def test_mapa_completo_de_denominadores_do_bloco_v(nome):
    """O TSE usa CINCO denominadores diferentes no mesmo arquivo.

    Decodificados com erro exatamente zero nos três payloads reais. Cada um
    destes percentuais responde a uma pergunta diferente, e trocá-los muda o
    número que vai à tela:

        pvvcn    = vvc / tv        quanto do total foi computado
        pvvn     = vv  / vvc       quanto do computado é válido
        pvann    = van / vvc       fração anulada
        pvansjn  = vansj / vvc     fração sub judice
        pvbn     = vb  / tv        brancos sobre o total
        ptvnn    = tvn / tv        nulos sobre o total
        pvnomn   = vnom / vv       nominais sobre válidos
        pvapn    = vap / Σvap      percentual do candidato  <- outro ainda
    """
    raw = load(nome)
    v = raw["v"]
    pares = [("pvvcn", "vvc", "tv"), ("pvvn", "vv", "vvc"),
             ("pvann", "van", "vvc"), ("pvansjn", "vansj", "vvc"),
             ("pvbn", "vb", "tv"), ("ptvnn", "tvn", "tv"),
             ("pvnomn", "vnom", "vv")]
    for campo, num, den in pares:
        if campo not in v or int(v[den]) == 0:
            continue
        declarado = as_pct(v[campo], campo)
        calculado = 100.0 * int(v[num]) / int(v[den])
        assert abs(calculado - declarado) < 1e-6, (
            f"{campo}: {num}/{den} deu {calculado}, TSE diz {declarado}")


def test_senado_tem_duas_vagas_e_ate_dois_votos_por_eleitor():
    r = ea20.parse(load("ea20_ac01120_sen.json"))
    assert r.cargo.vagas == 2                       # vem do payload
    assert r.electorate.c <= r.votes.tv <= 2 * r.electorate.c
    q = D.escolhas_por_comparecente(r)
    # No simulado, q = 1,70 — NÃO 2,00. Supor duas escolhas válidas por
    # eleitor superestimaria o total em 17%.
    assert 0 < q < 2
    assert abs(q - 1.7029) < 0.001


def test_cargo_de_vaga_unica_tem_um_voto_por_comparecente():
    for nome in ("ea20_br_pres.json", "ea20_ac_gov.json"):
        r = ea20.parse(load(nome))
        assert r.cargo.vagas == 1
        assert r.votes.tv == r.electorate.c


def test_status_eleito_nao_vem_do_campo_e():
    """No arquivo nacional, `e='s'` marca quem vai ao 2º turno.

    Ler esse campo como "eleito" produziria manchete errada.
    """
    r = ea20.parse(load("ea20_br_pres.json"))
    classificados = [c for c in r.cargo.candidatos
                     if c.classificado_flag.lower() == "s"]
    assert len(classificados) == 2
    assert all(c.vai_para_segundo_turno for c in classificados)
    assert not any(c.eleito_segundo_tse for c in classificados)
    # No Senado municipal, aí sim há eleitos de verdade — mas o status é da
    # UF, não do município.
    sen = ea20.parse(load("ea20_ac01120_sen.json"))
    assert sum(1 for c in sen.cargo.candidatos if c.eleito_segundo_tse) == 2
    assert sen.tpabr == "mu"


def test_sub_judice_fica_fora_da_preferencia_mas_nao_some():
    r = ea20.parse(load("ea20_br_pres.json"))
    obs = PD.observed_of(r)
    sj = [c for c in r.cargo.candidatos if c.dvt == "Anulado sub judice"]
    assert sj, "fixture deveria ter candidato sub judice"
    for c in sj:
        assert c.sqcand not in obs.votos_por_candidato   # fora da preferência
    assert r.votes.vansj == sum(c.votos for c in sj)     # mas contabilizado


# ------------------------------------------------------------ pendente ----

@pytest.mark.parametrize("nome", ARQUIVOS)
def test_pendente_e_zero_quando_a_disputa_terminou(nome):
    r = ea20.parse(load(nome))
    assert r.is_final
    p = PD.of(r)
    assert p.lower == p.upper == 0
    assert p.is_resolved


def test_pendente_e_intervalo_quando_o_contrato_nao_resolve():
    """Seções não instaladas não são voto futuro (§4.2)."""
    from dataclasses import replace
    r = ea20.parse(load("ea20_br_pres.json"))
    e = r.electorate
    parcial = replace(
        r, andamento="p", totalizacao_final=False,
        electorate=replace(e, est=0, esnt=e.te),
    )
    p = PD.of(parcial)
    assert p.upper == e.te
    assert p.lower == e.te - e.esni
    assert p.width == e.esni > 0
    assert p.lower <= p.point <= p.upper


# ----------------------------------------------------------- primitivas ----

def test_percentual_brasileiro():
    assert as_pct("85,15") == pytest.approx(85.15)
    assert as_pct("99,999810947") == pytest.approx(99.999810947)
    assert as_pct("") is None
    with pytest.raises(ParseError):
        as_pct("85.15.2")


def test_inteiro_recusa_lixo():
    assert as_int("138863131") == 138863131
    for ruim in ("", None, "12.5", "abc"):
        with pytest.raises(ParseError):
            as_int(ruim)


def test_unescape_e_fold():
    assert as_text("OLHO D&APOS;AGUA") == "OLHO D'AGUA"
    assert fold("SÃO JOÃO D&APOS;ALIANÇA") == "SAO JOAO D'ALIANCA"


# ------------------------------------------- base da maioria absoluta ----

def test_base_da_maioria_absoluta_sao_os_votos_a_concorrentes():
    """`vvc`, não `vv`.

    O `leiame.pdf` do repositório de dados eleitorais do TSE define o sistema
    majoritário como "a maioria dos votos a concorrentes (votos válidos,
    votos anulados e votos anulados sub judice)", e diz que há segundo turno
    quando ninguém alcança "maioria absoluta destes votos a concorrentes".

    Coerente com a CF art. 77 §2º, que exclui do denominador apenas brancos e
    nulos. Voto ANULADO de candidato não é voto NULO do eleitor: `van` e `vn`
    são campos distintos no EA20.
    """
    assert D.BASE_MAIORIA_ABSOLUTA is D.Base.VALIDOS_COMPUTADOS
    r = ea20.parse(load("ea20_br_pres.json"))
    den = D.of(r)
    # A identidade que define "votos a concorrentes".
    assert den.validos_computados == r.votes.vv + r.votes.van + r.votes.vansj


def test_usar_validos_como_base_inflaria_os_percentuais():
    """Quantifica o erro que a correção evitou.

    Não é diferença cosmética: sobre `vv` todos os percentuais sobem cerca de
    20%, o que pode anunciar maioria absoluta que não existe.
    """
    r = ea20.parse(load("ea20_br_pres.json"))
    den = D.of(r)
    lider = max((c for c in r.cargo.candidatos if c.conta_como_valido),
                key=lambda c: c.votos)
    sobre_vv = den.pct(lider.votos, D.Base.VALIDOS)
    sobre_vvc = den.pct(lider.votos, D.Base.VALIDOS_COMPUTADOS)
    assert sobre_vv > sobre_vvc
    assert sobre_vv / sobre_vvc == pytest.approx(1.195, abs=0.01)
    # Nos payloads do simulado existe candidato anulado E sub judice, então
    # as duas bases divergem de verdade.
    assert r.votes.van > 0 and r.votes.vansj > 0
    assert den.validos_computados > den.validos


def test_maioria_absoluta_usa_o_denominador_corrigido():
    """Um candidato com 55% dos válidos pode NÃO ter maioria absoluta."""
    import numpy as np
    from obs2026.engine import simulate as S
    n = 200
    votos = np.tile(np.array([[55, 45]], dtype=np.int64), (n, 1))
    validos = np.full(n, 100, dtype=np.int64)
    fc = S.Forecast(
        candidates=["a", "b"], votes=votos, valid_total=validos,
        # 100 válidos + 40 anulados = 140 votos a concorrentes.
        concorrentes_total=validos + 40,
        observed={"a": 55, "b": 45}, seats=1, n_sims=n, seed=0,
        pending_point=0,
    )
    # 55/100 = 55% dos válidos, mas 55/140 = 39% dos concorrentes.
    assert fc.prob_maioria_absoluta()["a"] == 0.0
    assert fc.prob_lideranca()["a"] == 1.0      # lidera, sem vencer no 1º
