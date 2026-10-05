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


def test_ambiente_sem_codigos_recusa_operar(tmp_path):
    """Ambiente com base/ambiente nulos é recusado, nunca adivinhado."""
    (tmp_path / "sources.yaml").write_text(
        "environments:\n  vazio:\n    base: null\n    ambiente: null\n"
        "    seed: {ciclo: null, cd_pleito: null, elections: []}\n",
        encoding="utf-8")
    with pytest.raises(ConfigError, match="incompleto"):
        load_environment("vazio", tmp_path)


def test_ambiente_de_producao_usa_os_codigos_publicados_pelo_tse():
    """Códigos do catálogo de produção, publicados em 02/10/2026."""
    env = load_environment("oficial2026")
    assert env.official and not env.demonstracao
    assert (env.base, env.ambiente) == ("https://resultados.tse.jus.br",
                                        "oficial")
    assert env.ciclo == "ele2026"
    assert {"6257", "6259"} <= set(env.seed_elections)


def test_fase_oficial_do_tse_e_o():
    """Produção manda f='o'; simulado, f='s'. A barreira de ambiente
    recusa o cruzamento nos dois sentidos."""
    from dataclasses import replace
    from obs2026.quality.checks import check_ea20
    r = ea20.parse(json.loads(
        (FIX / "ea20_br_pres.json").read_text(encoding="utf-8")))
    cod = lambda fs: {f.code for f in fs}                    # noqa: E731
    assert "AMBIENTE_CONTAMINADO" in cod(check_ea20(r, official_env=True))
    assert "AMBIENTE_CONTAMINADO" not in cod(
        check_ea20(r, official_env=False))
    oficial = replace(r, fase="o")
    assert "AMBIENTE_CONTAMINADO" not in cod(
        check_ea20(oficial, official_env=True))
    assert "AMBIENTE_CONTAMINADO" in cod(
        check_ea20(oficial, official_env=False))


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

def test_mapa_ideologico_sem_assinatura_fica_desabilitado(monkeypatch,
                                                           tmp_path):
    """Proposta sem autor não liga o mapa: assinar por alguém seria inventar
    autoria. Testado sobre uma proposta controlada, não sobre o arquivo
    vivo — que pode estar assinado."""
    f = tmp_path / "c.yaml"
    f.write_text(
        "versao: '2026.1-proposta'\nano: 2026\ncurador: null\n"
        "publicada_em: null\nentradas:\n"
        "  - {entidade_tipo: partido, entidade_id: PT, numero: '13',\n"
        "     ideologia: esquerda, grau_controversia: 1, autor: null,\n"
        "     fonte: survey, justificativa: teste}\n", encoding="utf-8")
    monkeypatch.setenv("OBS2026_CLASSIFICACAO", str(f))
    m = CL.metodologia()
    assert m["estado"] == "curadoria_pendente"
    assert m["motivo"]
    assert m["entradas"] == 1
    assert any("autor vazio" in p for p in m["problemas"])


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
    # Sem base explícita, a API responde na base que decide o 1º turno.
    assert d["denominador"]["base"] == "validos_computados"
    assert "maioria absoluta" in d["denominador"]["rotulo"]


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


def test_forecast_nao_publica_probabilidade_sem_homologacao(cliente,
                                                          monkeypatch):
    # Independe da homologação real (assinada em 22/09): o teste é sobre o
    # comportamento SEM ela.
    import dataclasses
    from obs2026.api import app as M
    real = M.load_thresholds
    monkeypatch.setattr(M, "load_thresholds", lambda *a, **k:
                        dataclasses.replace(real(*a, **k), homologated=False))
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


def test_recorte_por_uf_em_disputa_nacional_nao_devolve_o_brasil(cliente):
    """Filtro que nao pode ser aplicado nao pode virar outra pergunta.

    `/candidates?cargo=1&uf=pa` pede o voto para presidente no Para. A
    abrangencia que DECIDE presidente e `br`, e o codigo antigo so filtrava
    por UF quando a abrangencia era `uf` ou `mu` — para presidente a
    condicao era falsa e o `uf` sumia em silencio. A rota devolvia o total
    do Brasil com a cara de resultado do Para: 10,5 milhoes de votos num
    estado cujo total de votos a concorrentes e 4,6 milhoes.

    Numero errado que parece certo e pior que erro visivel, e este ia para
    o telao e para o grupo de WhatsApp.
    """
    br = cliente.get("/api/v1/candidates?cargo=1").json()
    pa = cliente.get("/api/v1/candidates?cargo=1&uf=pa").json()
    sp = cliente.get("/api/v1/candidates?cargo=1&uf=sp").json()

    def votos(d):
        return sum(c["oficial"]["votos"] for c in d["dados"][0]["candidatos"])

    assert votos(pa) < votos(br)
    assert votos(sp) < votos(br)
    assert votos(pa) != votos(sp)

    # E o recorte tem que fechar com o total daquela UF, nao com o do pais.
    res = cliente.get("/api/v1/results?cargo=1&abrangencia=uf&uf=pa").json()
    maior = max(c["oficial"]["votos"] for c in pa["dados"][0]["candidatos"])
    assert maior <= res["dados"][0]["votos"]["validos_computados"]


def test_recorte_por_uf_nao_afirma_eleito_em_disputa_nacional(cliente):
    """Presidente nao e eleito por estado (§3).

    O selo "eleito segundo TSE" so pode aparecer na abrangencia que decide.
    Num recorte, ele fica em branco — nao em `false`, que seria afirmar que
    a pessoa nao foi eleita.
    """
    pa = cliente.get("/api/v1/candidates?cargo=1&uf=pa").json()
    for c in pa["dados"][0]["candidatos"]:
        assert c["oficial"]["eleito_segundo_tse"] is None

    br = cliente.get("/api/v1/candidates?cargo=1").json()
    assert any(c["oficial"]["eleito_segundo_tse"] is not None
               for c in br["dados"][0]["candidatos"])


def test_decided_marca_o_recorte_por_uf_como_nao_decisorio(cliente):
    d = cliente.get("/api/v1/decided?cargo=1&uf=pa").json()
    assert d["dados"][0]["recorte_nao_decisorio"] is True
    nacional = cliente.get("/api/v1/decided?cargo=1").json()
    assert nacional["dados"][0]["recorte_nao_decisorio"] is False


def test_nomes_antigos_de_camada_viram_nomes_atuais():
    """Todo apelido tem que apontar para uma camada que existe.

    Quando as camadas do mapa foram fundidas, a lista do roteiro continuou
    oferecendo quatro nomes e o mapa passou a conhecer dois. `cobertura`
    chegava ao mapa como nome desconhecido e caia em silencio no mapa de
    lideres: a tela "onde ainda falta apurar" mostrava quem lidera, e
    ninguem via erro nenhum.
    """
    from obs2026.api.telao import APELIDOS_CAMADA, CAMADAS
    for antigo, atual in APELIDOS_CAMADA.items():
        assert atual in CAMADAS, (antigo, atual)
        assert antigo not in CAMADAS, antigo
    assert APELIDOS_CAMADA["cobertura"] == "apurar"


def test_roteiro_so_entrega_nomes_atuais_de_camada(cliente,
                                                   roteiro_preservado):
    """O telao nao pode receber nome que o mapa nao conhece.

    O nome antigo de camada ("cobertura") vira o atual ("apurar") na
    leitura. O teste GRAVA a tela que precisa — antes ele dependia de o
    roteiro de produção conter a tela "onde ainda falta apurar", e quebrou
    quando o usuário a tirou do roteiro dele, em 23/09. Configuração de
    operação é do operador; o teste traz a sua."""
    from obs2026.api.telao import CAMADAS
    r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
        {"cargo": "1", "camada": "cobertura", "segundos": 15,
         "titulo": "Presidente — onde ainda falta apurar"}]})
    assert r.status_code == 200
    d = cliente.get("/api/v1/telao/roteiro").json()
    for t in d["dados"]:
        if not t["erro"]:
            assert t["camada"] in CAMADAS, t
    falta = [t for t in d["dados"] if "falta apurar" in t["titulo"]]
    assert falta and falta[0]["camada"] == "apurar"


# ------------------------------------------------- gravação do roteiro ----

@pytest.fixture
def roteiro_preservado():
    """Roteiro usado nos testes — uma CÓPIA, nunca o de produção.

    O conftest aponta `OBS2026_TELAO` para um arquivo temporário; aqui o
    conteúdo volta ao original a cada teste, para um teste não ver o que o
    outro gravou."""
    import os

    from obs2026.config import CONFIG_DIR
    arq = Path(os.environ.get("OBS2026_TELAO")
               or Path(CONFIG_DIR) / "telao.yaml")
    pasta = arq.parent / "telao.anterior"
    original = arq.read_bytes()
    antes = set(pasta.glob("*")) if pasta.exists() else set()
    yield arq
    arq.write_bytes(original)
    if pasta.exists():
        for f in set(pasta.glob("*")) - antes:
            f.unlink()
        if not any(pasta.iterdir()):
            pasta.rmdir()


JSON = {"Content-Type": "application/json"}


def test_gravar_roteiro_valido_escreve_o_arquivo(cliente, roteiro_preservado):
    corpo = {"roteiro": [
        {"cargo": "1", "camada": "lider", "segundos": 45,
         "titulo": "Presidente — Brasil"},
        {"cargo": "3", "uf": "pa", "camada": "apurar", "segundos": 10},
    ]}
    r = cliente.post("/api/v1/telao/roteiro", json=corpo, headers=JSON)
    assert r.status_code == 200, r.text
    texto = roteiro_preservado.read_text(encoding="utf-8")
    # A documentação do arquivo sobrevive à gravação.
    assert "# CAMPOS" in texto and "Gravado pelo painel" in texto

    lido = cliente.get("/api/v1/telao/roteiro").json()["dados"]
    assert [(t["cargo"], t["segundos"], t["camada"]) for t in lido] == \
        [("1", 45, "lider"), ("3", 10, "apurar")]
    # Título escrito à mão fica; título montado não é congelado no arquivo.
    assert lido[0]["titulo_livre"] == "Presidente — Brasil"
    assert lido[1]["titulo_livre"] is None
    assert "titulo" not in texto.split("cargo: governador")[1]


def test_gravar_guarda_a_versao_anterior(cliente, roteiro_preservado):
    original = roteiro_preservado.read_bytes()
    r = cliente.post("/api/v1/telao/roteiro", headers=JSON,
                     json={"roteiro": [{"cargo": "1", "segundos": 20}]})
    assert r.status_code == 200
    copias = sorted((roteiro_preservado.parent / "telao.anterior")
                    .glob("*.yaml"))
    assert copias and copias[-1].read_bytes() == original


def test_roteiro_com_tela_quebrada_nao_e_gravado(cliente, roteiro_preservado):
    """Tela que não resolve impede a gravação inteira.

    Gravar e deixar o telão pular a tela seria descobrir o erro com o
    projetor ligado.
    """
    antes = roteiro_preservado.read_bytes()
    r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
        {"cargo": "1", "segundos": 20},
        {"cargo": "3", "segundos": 20},               # governador sem UF
    ]})
    assert r.status_code == 422
    assert "tela 2" in str(r.json())
    assert roteiro_preservado.read_bytes() == antes


def test_gravacao_recusa_formulario_de_outra_pagina(cliente,
                                                    roteiro_preservado):
    """Loopback não basta: o navegador do operador também está em loopback.

    Um formulário de qualquer site aberto nele chega como text/plain, sem
    pré-verificação de CORS. Por isso só JSON é aceito, e só da mesma
    origem.
    """
    antes = roteiro_preservado.read_bytes()
    r = cliente.post("/api/v1/telao/roteiro",
                     content='{"roteiro": []}',
                     headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": []},
                     headers={**JSON, "Origin": "https://exemplo.com"})
    assert r.status_code == 403
    assert roteiro_preservado.read_bytes() == antes


def test_tempo_fora_da_faixa_e_trazido_para_dentro(cliente,
                                                   roteiro_preservado):
    r = cliente.post("/api/v1/telao/roteiro", headers=JSON, json={"roteiro": [
        {"cargo": "1", "segundos": 1}, {"cargo": "1", "segundos": 9999}]})
    assert r.status_code == 200
    assert [t["segundos"] for t in r.json()["dados"]] == [5, 300]


# ------------------------------------------------ compartilhar (WhatsApp) ---

def test_compartilhar_so_aceita_o_proprio_painel(cliente):
    """A rota roda um navegador sem cabeça nesta máquina: mesma trava da
    gravação do roteiro — formulário de outra página não passa."""
    r = cliente.post("/api/v1/compartilhar", content='{"telas": null}',
                     headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    r = cliente.post("/api/v1/compartilhar", json={"telas": None},
                     headers={"Content-Type": "application/json",
                              "Origin": "https://exemplo.com"})
    assert r.status_code == 403


def test_compartilhar_recusa_pedido_malformado(cliente):
    r = cliente.post("/api/v1/compartilhar", json={"telas": ["x"]},
                     headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = cliente.post("/api/v1/compartilhar", json={"recorte": {"uf": "pa"}},
                     headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_imagem_nao_sai_de_data_telao(cliente):
    """A rota de imagem só serve o padrão de nome da captura; caminho com
    ponto-ponto ou extensão diferente não chega ao disco."""
    for dia, nome in (("..", "config.yaml"), ("2026-09-21", "..%2F..%2Fx.png"),
                      ("2026-09-21", "000000-0-x.txt")):
        r = cliente.get(f"/api/v1/compartilhar/imagem/{dia}/{nome}")
        assert r.status_code in (400, 404), (dia, nome, r.status_code)


def _coletor_para_fim(andamento, completo):
    from obs2026.tse import collector as C
    col = C.Collector.__new__(C.Collector)
    col.encerrar_ao_finalizar = True
    col._viu_aberto = True
    col._tudo_final_desde = None
    col._carencia = C.CARENCIA_ENCERRAMENTO_S
    col._andamento_totais = andamento
    col._completo_totais = completo
    return col, C


def test_coletor_para_quando_o_tse_marca_tudo_finalizado():
    col, C = _coletor_para_fim({"6257:br": "f", "6257:uf:sp": "f"}, {})
    assert not col.verificar_fim(0.0)
    assert not col.verificar_fim(C.CARENCIA_ENCERRAMENTO_S - 1)
    assert col.verificar_fim(C.CARENCIA_ENCERRAMENTO_S)


def test_coletor_para_com_brasil_completo_sem_a_marca_de_finalizado():
    """1º turno de 2026: UFs finalizadas, Brasil em "p" com 100% das seções.
    O coletor ficou 7 horas no ar esperando uma marca que não veio."""
    tot = {"6257:br": "p", "6257:uf:sp": "f", "6259:uf:sp": "f"}
    col, C = _coletor_para_fim(tot, {"6257:br": True})
    assert not col.verificar_fim(0.0)
    assert not col.verificar_fim(C.CARENCIA_ENCERRAMENTO_S)   # espera mais
    assert col.verificar_fim(C.CARENCIA_SEM_MARCA_S)
    # Brasil incompleto, ou uma UF ainda aberta: não para.
    col, C = _coletor_para_fim(tot, {"6257:br": False})
    assert not col.verificar_fim(0.0) and not col.verificar_fim(1e6)
    col, C = _coletor_para_fim({**tot, "6259:uf:am": "p"},
                               {"6257:br": True, "6259:uf:am": True})
    assert not col.verificar_fim(0.0) and not col.verificar_fim(1e6)


def test_homologacao_vale_so_para_o_modelo_assinado():
    """A assinatura é da evidência de um modelo. Em 05/10/2026 a produção
    passou ao M3.3 com a assinatura do M3 ainda no arquivo: a probabilidade
    seguia no ar sob uma homologação que não era a dele."""
    from dataclasses import replace
    from obs2026.config import load_thresholds
    t = replace(load_thresholds(), homologated=True, modelo_homologado="m3")
    assert t.vale_para("m3") and not t.vale_para("m33")
    assert not replace(t, homologated=False).vale_para("m3")
    # arquivo antigo, sem o modelo registrado: vale como antes
    assert replace(t, modelo_homologado=None).vale_para("m33")
