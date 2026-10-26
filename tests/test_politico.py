"""Partido, ideologia, composição e chaves de operação (21/09/2026).

O que se protege aqui é o que daria erro de manchete: pintar um partido com
a cor de outro por coincidência de número, ligar ideologia sem autoria,
contar "liderando" como "eleito", ou um comando velho do painel disparar
sozinho num reinício do coletor.
"""

from __future__ import annotations

import collections
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from obs2026 import operacao as OP
from obs2026.config import CONFIG_DIR
from obs2026.politics import camara as CAM
from obs2026.politics import classification as CL
from obs2026.politics import partidos as PA
from obs2026.politics import senado as SEN
from obs2026.storage.raw import RawArchive
from obs2026.tse.urls import Target


# ------------------------------------------------------------- partidos ---

def test_numero_reusado_nao_herda_o_partido():
    """Em 2022 o 14 era o PTB; em 2026 é o Missão. Mesmo número, partido
    diferente: fica fora do cadastro, com cor própria."""
    ptb = PA.resolver("14", "PTB")
    missao = PA.resolver("14", "MISSÃO")
    assert not ptb.cadastrado
    assert missao.cadastrado and missao.sigla == "MISSÃO"
    assert ptb.cor != missao.cor


def test_sigla_do_senado_e_do_g1_resolvem_pelo_cadastro():
    assert PA.resolver(None, "PODEMOS").sigla == "PODE"
    assert PA.resolver(None, "REPUBLICANOS").numero == "10"
    assert PA.resolver("10", "REP").cadastrado          # grafia do G1
    assert PA.resolver(None, "União").numero == "44"      # acento e caixa


def test_sem_partido_nao_e_partido_desconhecido():
    p = PA.resolver(None, "S/Partido")
    assert p.sigla == "sem partido" and not p.cadastrado


def test_partido_ficticio_do_simulado_tem_cor_estavel():
    a = PA.resolver("61", "P 9979")
    b = PA.resolver("61", "P 9979")
    assert not a.cadastrado and a.cor == b.cor


def test_cadastro_tem_os_30_partidos_de_2026_sem_cor_repetida():
    lista = PA.cadastro()["lista"]
    assert len(lista) == 30
    cores = [p["cor"].upper() for p in lista]
    assert len(set(cores)) == len(cores)


def test_gravar_cor_edita_so_a_linha_do_partido(tmp_path):
    f = tmp_path / "p.yaml"
    shutil.copy(CONFIG_DIR / "partidos_2026.yaml", f)
    antes = f.read_text(encoding="utf-8")
    PA.gravar_cores({"13": "#123456"}, caminho=f)
    depois = f.read_text(encoding="utf-8")
    assert '"#123456"' in depois
    diff = [(a, b) for a, b in zip(antes.splitlines(), depois.splitlines())
            if a != b]
    assert len(diff) == 1 and 'numero: "13"' in diff[0][1]
    with pytest.raises(ValueError):
        PA.gravar_cores({"13": "vermelho"}, caminho=f)
    with pytest.raises(ValueError):
        PA.gravar_cores({"999": "#000000"}, caminho=f)


# -------------------------------------------------------- classificação ---

@pytest.fixture()
def proposta(tmp_path, monkeypatch):
    """Cópia da proposta de 2026 com curador e autores zerados — o estado de
    antes da assinatura, qualquer que seja o do arquivo vivo."""
    import re
    f = tmp_path / "classificacao.yaml"
    t = (CONFIG_DIR / "classificacao_2026.yaml").read_text(encoding="utf-8")
    t = re.sub(r"(?m)^curador:.*$", "curador: null", t)
    t = re.sub(r'autor: "[^"]*", decidido_em: [0-9-]+', "autor: null", t)
    f.write_text(t, encoding="utf-8")
    monkeypatch.setenv("OBS2026_CLASSIFICACAO", str(f))
    return f


def test_proposta_cobre_o_cadastro_e_so_falta_autoria(proposta):
    reg = CL.load(proposta)
    assert reg.curadoria_pendente
    problemas = CL.validar(reg)
    assert problemas
    # Todo problema é de autoria: fonte, justificativa e número batem.
    assert all("autor vazio" in p or "sem curador" in p for p in problemas)
    assert len(CL.por_numero(reg)) == 30


def test_assinar_liga_e_preserva_os_comentarios(proposta):
    r = CL.assinar("Maria Curadora", path=proposta)
    texto = proposta.read_text(encoding="utf-8")
    reg = CL.load(proposta)
    assert not reg.curadoria_pendente and not CL.validar(reg)
    assert reg.curador == "Maria Curadora"
    assert all(e.autor == "Maria Curadora" for e in reg.entradas)
    assert "MÉTODO DA PROPOSTA" in texto                  # comentários ficam
    assert "# ASSINADA por Maria Curadora" in texto
    assert r["versao"] == "2026.1"
    assert (proposta.parent / "classificacao.anterior").is_dir()


def test_assinatura_exige_nome(proposta):
    with pytest.raises(CL.ClassificationError):
        CL.assinar("  ", path=proposta)
    assert CL.load(proposta).curadoria_pendente


def test_edicao_vira_versao_nova_com_autor_so_no_que_mudou(proposta):
    CL.assinar("Maria Curadora", path=proposta)
    r = CL.gravar_versao("João Revisor", [
        {"numero": "55", "ideologia": "centro", "grau_controversia": 3,
         "justificativa": "revisão", "fonte": "debate"}], path=proposta)
    reg = CL.load(proposta)
    assert r["versao"] == "2026.2" and r["alteradas"] == 1
    assert reg.curador == "João Revisor"
    assert reg.por_partido("PSD", "55").autor == "João Revisor"
    assert reg.por_partido("PT", "13").autor == "Maria Curadora"
    texto = proposta.read_text(encoding="utf-8")
    assert "*id" not in texto and "&id" not in texto


def test_tirar_classificacao_de_um_partido_e_hachura_nao_centro(proposta):
    CL.gravar_versao("Maria Curadora", [{"numero": "35", "ideologia": None}],
                     path=proposta)
    reg = CL.load(proposta)
    assert reg.por_partido("DEMOCRATA", "35") is None
    assert reg.bloco("DEMOCRATA", "35") is None


def test_ideologia_do_partido_de_2022_com_numero_reusado_e_nula(proposta):
    CL.assinar("Maria Curadora", path=proposta)
    reg = CL.load(proposta)
    assert reg.por_partido("PTB", "14") is None           # não é o Missão
    assert reg.por_partido("MISSÃO", "14").ideologia == "direita"


# ---------------------------------------------------------------- câmara ---

def _payload_dep(uf: str, eleitos: list[tuple[str, str, str]],
                 andamento: str = "f", dg: str = "02/10/2026",
                 hg: str = "23:00:00") -> dict:
    par: dict = {}
    for num, sg, st in eleitos:
        par.setdefault((num, sg), []).append(
            {"n": num + "01", "nmu": f"CAND {sg}", "st": st, "vap": "1000"})
    return {"cdabr": uf, "and": andamento, "tf": "s", "dg": dg, "hg": hg,
            "carg": [{"cd": "6", "nv": str(len(eleitos)), "qe": "50000",
                      "agr": [{"nm": "", "vag": str(len(eleitos)),
                               "par": [{"n": n, "sg": s, "cand": c}
                                       for (n, s), c in par.items()]}]}],
            "e": {"te": "100", "est": "100"}, "s": {"ts": "10", "st": "10"}}


def test_conta_so_eleito_por_qp_e_por_media():
    r = CAM.resumir_uf(_payload_dep("ac", [
        ("22", "PL", "Eleito por QP"), ("13", "PT", "Eleito por média"),
        ("13", "PT", "Suplente"), ("30", "NOVO", "Não eleito")]))
    assert [e["partido_sigla"] for e in r["eleitos"]] == ["PL", "PT"]
    assert r["vagas"] == 4 and r["apurado_pct"] == 100.0


def test_leitor_da_camara_fica_com_o_arquivo_mais_novo(tmp_path):
    arq = RawArchive(tmp_path, "teste")
    for dg, eleitos in (("02/10/2026", [("22", "PL", "Eleito por QP")]),
                        ("03/10/2026", [("13", "PT", "Eleito por QP")])):
        corpo = json.dumps(_payload_dep("ac", eleitos, dg=dg)).encode()
        import hashlib
        arq.store(key="ea20:21272:6:ac:-", kind="ea20", url="u",
                  status=200, body=corpo,
                  sha256=hashlib.sha256(corpo).hexdigest(),
                  captured_at=datetime.now(timezone.utc),
                  source_generated_at=datetime.strptime(
                      dg, "%d/%m/%Y").replace(tzinfo=timezone.utc),
                  idg=None, etag=None, last_modified=None,
                  parser_version="t", election="21272", cargo="6", uf="ac")
    por_uf = CAM.LeitorCamara(arq).por_uf()
    assert [e["partido_sigla"] for e in por_uf["ac"]["eleitos"]] == ["PT"]
    comp = CAM.composicao(por_uf, PA.resolver, lambda p: None)
    assert comp["eleitos"] == 1 and comp["sem_eleito"] == 512


# ---------------------------------------------------------------- senado ---

def _bruto_senado(n_2031: int, n_2027: int) -> dict:
    ps = []
    for i in range(n_2031 + n_2027):
        fim = "2031-01-31" if i < n_2031 else "2027-01-31"
        ps.append({"IdentificacaoParlamentar": {
            "CodigoParlamentar": str(i), "NomeParlamentar": f"S{i}",
            "SiglaPartidoParlamentar": "PL", "UfParlamentar": "SP"},
            "Mandato": {"DescricaoParticipacao":
                        "1º Suplente" if i == 0 else "Titular",
                        "PrimeiraLegislaturaDoMandato": {
                            "DataInicio": "2023-02-01"},
                        "SegundaLegislaturaDoMandato": {"DataFim": fim}}})
    return {"ListaParlamentarEmExercicio": {"Parlamentares": {
        "Parlamentar": ps}}}


def test_remanescentes_sao_os_de_mandato_ate_2031():
    oc = SEN.normalizar(_bruto_senado(27, 54))
    r = SEN.remanescentes(2026, {"ocupantes": oc, "obtido_em": "x"})
    assert len(r["cadeiras"]) == 27
    assert r["cadeiras"][0]["suplente_em_exercicio"] is True


def test_sem_composicao_baixada_a_tela_diz_o_motivo(tmp_path):
    r = SEN.remanescentes(2026, SEN.carregar(tmp_path / "nao-existe.json"))
    assert r["estado"] == "indisponivel" and "preparar-senado" in r["motivo"]


# ------------------------------------------------------------- operação ---

def test_operacao_valida_e_guarda_a_anterior(tmp_path):
    f = tmp_path / "operacao.yaml"
    OP.gravar({"modelo_projecao": "m1"}, caminho=f)
    OP.gravar({"coleta": {"deputados_federais": False}}, caminho=f)
    d = OP.carregar(f)
    assert d["modelo_projecao"] == "m1"
    assert d["coleta"]["deputados_federais"] is False
    assert d["coleta"]["pular_finalizados"] is True       # padrão por baixo
    assert list((tmp_path / "operacao.anterior").glob("*.yaml"))
    with pytest.raises(ValueError):
        OP.gravar({"modelo_projecao": "m9"}, caminho=f)


def test_modelo_ativo_vem_da_chave(tmp_path, monkeypatch):
    f = tmp_path / "operacao.yaml"
    OP.gravar({"modelo_projecao": "m1"}, caminho=f)
    monkeypatch.setattr(OP, "ARQUIVO", f)
    OP._memo.update(mtime=None, dados=None)
    assert OP.modelo_ativo() == "m1"


def _coletor_com_comandos(tmp_path, monkeypatch):
    import asyncio
    from obs2026.tse.collector import Collector
    monkeypatch.setattr(OP, "PASTA_COMANDOS", tmp_path)
    uf = Target("ea20", "https://x/pa-c0003", election="21272", cargo="3",
                uf="pa", abr="uf")
    dep = Target("ea20", "https://x/pa-c0006", election="21272", cargo="6",
                 uf="pa", abr="uf")
    cat = SimpleNamespace(
        control_targets=lambda: [uf], result_targets=lambda: [],
        contests=[], env=SimpleNamespace(name="teste"),
        proporcional_targets=lambda: [("deputado_federal", 120.0, dep)])
    cfg = SimpleNamespace(aging_boost_per_minute=0.15, control_weight=1e6,
                          reconcile_interval_s=5.0, pular_finalizados=True)
    return Collector(cat, client=None, archive=None, cfg=cfg), uf, dep, asyncio


def test_comando_de_antes_do_coletor_nao_dispara(tmp_path, monkeypatch):
    monkeypatch.setattr(OP, "PASTA_COMANDOS", tmp_path)
    OP.pedir("teste", "forcar_totais")
    col, uf, dep, _ = _coletor_com_comandos(tmp_path, monkeypatch)
    assert col.atender_comandos() is None
    assert len(col.queue) == 0


def test_forcar_totais_busca_ate_o_finalizado(tmp_path, monkeypatch):
    col, uf, dep, _ = _coletor_com_comandos(tmp_path, monkeypatch)
    col.finalizados |= {uf.key, dep.key}
    OP.pedir("teste", "forcar_totais")
    r = col.atender_comandos()
    assert r == {"acao": "forcar_totais", "alvos": 2}
    assert uf.key not in col.finalizados
    est = OP.ler_estado_coletor("teste")
    assert est["ultimo_comando"] == "forcar_totais"
    assert col.atender_comandos() is None                 # uma vez só


def test_deputados_tem_cadencia_propria_e_chave(tmp_path, monkeypatch):
    col, uf, dep, asyncio = _coletor_com_comandos(tmp_path, monkeypatch)
    assert asyncio.run(col.cycle_proporcional(0.0)) == 1
    col.queue.pop()
    assert asyncio.run(col.cycle_proporcional(60.0)) == 0      # antes de 120 s
    assert asyncio.run(col.cycle_proporcional(121.0)) == 1
    col.queue.pop()
    col.deputados_ativos = False
    assert asyncio.run(col.cycle_proporcional(999.0)) == 0


def test_resultado_de_deputado_nao_vai_para_o_banco(tmp_path, monkeypatch):
    """`on_result` alimenta banco e motor; deputado fica só no bruto."""
    col, uf, dep, _ = _coletor_com_comandos(tmp_path, monkeypatch)
    assert "6" in col._cds_proporcionais and "3" not in col._cds_proporcionais


# ------------------------------------------------------------------ API ---

@pytest.fixture(scope="module")
def cliente():
    from fastapi.testclient import TestClient
    from obs2026.api.app import app
    return TestClient(app)


def test_roteiro_recusa_tela_politica_mal_formada(cliente):
    """Nada é gravado: o servidor recusa o roteiro inteiro."""
    antes = (CONFIG_DIR / "telao.yaml").read_bytes()
    for tela, trecho in (({"tipo": "composicao"}, "casa"),
                         ({"tipo": "politico", "cargo": "senador"},
                          "presidente ou governador"),
                         ({"tipo": "politico", "cargo": "presidente",
                           "uf": "pa"}, "nacional")):
        r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": [tela]})
        assert r.status_code == 422, tela
        assert trecho in " ".join(r.json()["detail"]["erros"])
    assert (CONFIG_DIR / "telao.yaml").read_bytes() == antes


def test_composicao_do_senado_tem_81_cadeiras(cliente):
    d = cliente.get("/api/v1/composicao?casa=senado").json()["dados"]
    assert d["total"] == 81 and len(d["cadeiras"]) == 81
    e = d["em_disputa"]["estados"]
    assert sum(e.values()) == d["em_disputa"]["n"]


def test_ideologia_nula_enquanto_nao_assinada(cliente, proposta):
    d = cliente.get("/api/v1/politico/resumo?cargo=3").json()["dados"]
    assert d["classificacao"]["estado"] == "curadoria_pendente"
    assert d["votos"]["por_bloco"] is None
    assert all(p["ideologia"] is None for p in d["votos"]["por_partido"])


def test_configuracao_so_grava_da_propria_maquina(cliente):
    r = cliente.post("/api/v1/configuracao/operacao",
                     content="modelo_projecao=m1",
                     headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_perfil_de_2026_nao_enriquece_outra_eleicao():
    """22-PL em 2022 é Jair Bolsonaro; em 2026, Flávio. Casar a lista de
    2026 com outro ano pelo número troca a pessoa — a regra é só 2026."""
    from obs2026.candidates import osidosos as OS
    assert OS.ANO_ENRIQUECIMENTO == 2026
    import inspect
    from obs2026.api import candidatos
    fonte = inspect.getsource(candidatos)
    assert "OS.ANO_ENRIQUECIMENTO" in fonte


# ---------------------------------------------- apontar vencedor (§4.7) ---
#
# A afirmação mais cara do produto. A régua (99%, estável em 3 registros,
# 20% apurado) saiu do ensaio: em 2018 houve um caso de 99% dado a quem
# perdeu — Senado de RO, ordem aleatória —, e ele não era estável.

def _reg_chamada(probs, *, apurado=40.0, estado="projecao_disponivel"):
    from types import SimpleNamespace as NS
    r = NS(candidatos=[NS(sqcand=k, prob_lideranca=v, prob_dentro_vagas=v)
                       for k, v in probs.items()],
           estado_publicacao=estado)
    r.cobertura_pct = apurado
    return r


def test_chamada_exige_99_estabilidade_e_apuracao():
    from obs2026.engine import chamada as CH
    bom = [_reg_chamada({"a": 0.995, "b": 0.005})] * 3
    assert CH.avaliar(bom).apta
    # 98% não basta
    assert not CH.avaliar([_reg_chamada({"a": 0.98, "b": 0.02})] * 3).apta
    # apuração baixa não basta
    assert not CH.avaliar(
        [_reg_chamada({"a": 0.999, "b": 0.001}, apurado=10.0)] * 3).apta
    # virada no meio quebra a estabilidade
    piscou = [_reg_chamada({"a": 0.995, "b": 0.005}),
              _reg_chamada({"b": 0.995, "a": 0.005}),
              _reg_chamada({"a": 0.995, "b": 0.005})]
    ch = CH.avaliar(piscou)
    assert not ch.apta and "estável" in ch.motivo


def test_chamada_nao_sai_sem_projecao_publicavel():
    from obs2026.engine import chamada as CH
    ch = CH.avaliar([_reg_chamada({"a": 1.0}, estado="cenario_exploratorio")]
                    * 3)
    assert not ch.apta and "public" in ch.motivo


def test_chamada_no_senado_precisa_das_duas_vagas():
    from obs2026.engine import chamada as CH
    probs = {"a": 1.0, "b": 0.995, "c": 0.005}
    assert CH.avaliar([_reg_chamada(probs)] * 3, vagas=2).apta
    # segunda vaga em disputa: ninguém é apontado
    probs2 = {"a": 1.0, "b": 0.6, "c": 0.4}
    ch = CH.avaliar([_reg_chamada(probs2)] * 3, vagas=2)
    assert not ch.apta and ch.sqcands == ()


def test_nivel_do_intervalo_sobe_com_o_apurado():
    from obs2026.engine import nivel as NV
    assert NV.nivel_por_apurado(0)[0] == 80
    assert NV.nivel_por_apurado(14.9)[0] == 80
    assert NV.nivel_por_apurado(15.0)[0] == 90
    # Para em 90%: em 2018 a faixa de 95% entregou 92% de cobertura.
    assert NV.nivel_por_apurado(50.1)[0] == 90
    assert NV.nivel_por_apurado(99.0)[0] == 90
    # registro antigo, sem os quantis largos: cai para 80% em vez de mentir
    assert NV.faixa({"q100": 1.0, "q900": 9.0}, 95) == (1.0, 9.0)
    assert NV.faixa({"q100": 1.0, "q900": 9.0, "q025": 0.5, "q975": 9.5},
                    95) == (0.5, 9.5)


# ------------------------------------------- sub judice que não decide ---
#
# PA, simulado de 22/09: 100% apurado, um candidato em 3º com todos os votos
# sub judice. A regra antiga suspendia a definição de qualquer jeito, e a
# tela dizia "em aberto" numa disputa encerrada. Validados ou cassados
# aqueles votos, os mesmos dois nomes vão ao 2º turno — então a conta vale.

def test_sub_judice_que_nao_muda_o_resultado_nao_suspende():
    from obs2026.engine import decided as DC
    votos = {"a": 450279, "b": 444819, "sj": 439937, "d": 439003}
    d = DC.avaliar(votos, vagas=1, concorrentes_observados=4370000,
                   pendente_max=0, sub_judice=439937, encerrada=True,
                   sub_judice_por_candidato={"sj": 439937})
    assert not d.sub_judice_em_jogo
    estados = {s.sqcand: s.estado.value for s in d.situacoes}
    assert estados["a"] == estados["b"] == "matematicamente_no_segundo_turno"
    assert "não alteram o resultado" in " ".join(d.avisos)


def test_sub_judice_em_lugar_decisivo_suspende():
    from obs2026.engine import decided as DC
    votos = {"a": 450279, "sj": 449000, "c": 100000}
    d = DC.avaliar(votos, vagas=1, concorrentes_observados=1000000,
                   pendente_max=0, sub_judice=449000, encerrada=True,
                   sub_judice_por_candidato={"sj": 449000})
    assert d.sub_judice_em_jogo


def test_sem_detalhe_por_candidato_mantem_a_regra_conservadora():
    from obs2026.engine import decided as DC
    votos = {"a": 450279, "b": 444819, "sj": 439937}
    d = DC.avaliar(votos, vagas=1, concorrentes_observados=4370000,
                   pendente_max=0, sub_judice=439937, encerrada=True)
    assert d.sub_judice_em_jogo


# -------------------------------- retrato de ciclo anterior no mapa ------
#
# 23/09, 2ª janela do simulado: o TSE zerou os totais de UF às 8h41 e os
# arquivos municipais só foram trocados aos poucos. O mapa mostrava voto
# dentro de um estado zerado. A regra usa uma invariante: município não tem
# mais seções totalizadas que o estado inteiro.

def _tot(gid, st, snap):
    from types import SimpleNamespace as NS
    return NS(geography_id=gid, secoes_totalizadas=st, snapshot_id=snap)


def test_mapa_descarta_retrato_de_ciclo_anterior(monkeypatch):
    """Depois da reabertura, o retrato da janela passada sai do mapa."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace as NS
    from obs2026.api import geo as G

    reabriu = datetime(2026, 9, 23, 16, 11, tzinfo=timezone.utc)
    antes = reabriu - timedelta(hours=2)
    depois = reabriu + timedelta(minutes=3)
    monkeypatch.setattr(G, "_marco_do_ciclo", lambda s, ct: reabriu)
    monkeypatch.setattr(G, "_QUANDO", {100: antes, 101: depois})
    totais = [NS(geography_id=10, snapshot_id=100),   # janela anterior
              NS(geography_id=11, snapshot_id=101)]   # janela atual

    class Fake:
        def execute(self, q):
            return []

    mantidos, fora = G._sem_ciclo_antigo(Fake(), NS(id=7), {}, totais)
    assert fora == 1
    assert [t.geography_id for t in mantidos] == [11]


def test_mapa_sem_reabertura_nao_descarta_nada(monkeypatch):
    from types import SimpleNamespace as NS
    from obs2026.api import geo as G
    monkeypatch.setattr(G, "_marco_do_ciclo", lambda s, ct: None)
    totais = [NS(geography_id=10, snapshot_id=100)]
    mantidos, fora = G._sem_ciclo_antigo(None, NS(id=7), {}, totais)
    assert fora == 0 and mantidos == totais


def test_marco_do_ciclo_exige_retrocesso(monkeypatch):
    """Zero num agregado só vira marco de reabertura se aquela unidade já
    teve seção contada. Sem isso, uma UF que ainda não começou publicando
    0% no meio da noite esvaziaria o mapa inteiro."""
    from datetime import datetime, timezone
    from types import SimpleNamespace as NS
    from obs2026.api import app as APP, geo as G

    agora = datetime(2026, 9, 23, 16, 11, tzinfo=timezone.utc)
    geos_uf = [NS(id=1, uf="sp"), NS(id=2, uf="ap")]

    class Fake:
        def __init__(self, maximos):
            self.maximos = maximos
            self.n = 0

        def execute(self, q):
            self.n += 1
            if self.n == 1:                      # geografias br/uf
                return NS(scalars=lambda: NS(all=lambda: geos_uf))
            if self.n == 2:                      # máximos por unidade
                return NS(all=lambda: list(self.maximos.items()))
            return [NS(id=9, source_generated_at=agora)]

    monkeypatch.setattr(APP, "_latest_snapshot_ids",
                        lambda s, cid, ids: [NS(geography_id=2,
                                                secoes_totalizadas=0,
                                                snapshot_id=9)])
    # AP nunca contou nada: 0% é o começo dela, não reabertura.
    assert G._marco_do_ciclo(Fake({1: 5000, 2: 0}), NS(id=7)) is None
    # AP já tinha contado e voltou a zero: reabertura.
    assert G._marco_do_ciclo(Fake({1: 5000, 2: 120}), NS(id=7)) == agora


def _reg_turno(cands, *, apurado=60.0):
    """`cands`: {sq: (prob_lideranca, prob_maioria, q025, q500, q975)}."""
    from types import SimpleNamespace as NS
    r = NS(candidatos=[NS(sqcand=k, prob_lideranca=v[0],
                          prob_dentro_vagas=v[0], prob_maioria_absoluta=v[1],
                          pct_concorrentes_q025=v[2],
                          pct_concorrentes_q500=v[3],
                          pct_concorrentes_q975=v[4])
                       for k, v in cands.items()],
           estado_publicacao="projecao_disponivel")
    r.cobertura_pct = apurado
    return r


def test_chamada_liderar_sem_maioria_e_segundo_turno_nao_vencedor():
    """Presidente em 04/10/2026: 100% de chance de liderar, projeção de
    47,2% — o telão dizia "vencedor projetado". É 2º turno projetado."""
    from obs2026.engine import chamada as CH
    reg = _reg_turno({"a": (1.0, 0.0, 46.6, 47.2, 47.8),
                      "b": (0.0, 0.0, 44.4, 44.9, 45.5),
                      "c": (0.0, 0.0, 2.9, 2.9, 3.0)})
    ch = CH.avaliar([reg] * 3)
    assert ch.apta and ch.tipo == CH.TIPO_SEGUNDO_TURNO
    assert set(ch.sqcands) == {"a", "b"}
    # Maioria absoluta com 99%: aí sim, vencedor — e só ele.
    venc = _reg_turno({"a": (1.0, 0.995, 51.0, 52.0, 53.0),
                       "b": (0.0, 0.0, 44.0, 45.0, 46.0)})
    ch = CH.avaliar([venc] * 3)
    assert ch.apta and ch.tipo == CH.TIPO_VENCEDOR and ch.sqcands == ("a",)
    # Na dúvida entre fechar no 1º turno e ir ao 2º: nada é apontado.
    duv = _reg_turno({"a": (1.0, 0.6, 49.0, 50.2, 51.0),
                      "b": (0.0, 0.0, 44.0, 45.0, 46.0)})
    assert not CH.avaliar([duv] * 3).apta
    # 2º turno certo, mas a segunda vaga em disputa: sem nomes, sem selo.
    emb = _reg_turno({"a": (1.0, 0.0, 39.0, 40.0, 41.0),
                      "b": (0.0, 0.0, 27.0, 28.0, 29.0),
                      "c": (0.0, 0.0, 26.5, 27.5, 28.5)})
    assert not CH.avaliar([emb] * 3).apta


def test_probabilidade_so_a_partir_de_6_por_cento_apurado():
    """Decisão de 05/10/2026: com 0,0% apurado o modelo deu 97,6% de chance
    de maioria a quem foi ao 2º turno."""
    from obs2026.engine import nivel as NV
    assert not NV.publica_probabilidade(0.0)
    assert not NV.publica_probabilidade(5.99)
    assert NV.publica_probabilidade(6.0)
    assert not NV.publica_probabilidade(None)


def test_presidente_publica_probabilidade_a_partir_de_2_5_por_cento():
    """Decisão de 25/10/2026, noite do 2º turno: só o presidente desce."""
    from obs2026.engine import nivel as NV
    assert not NV.publica_probabilidade(2.49, "1")
    assert NV.publica_probabilidade(2.5, "1")
    assert not NV.publica_probabilidade(2.5, "3")
    assert NV.publica_probabilidade(6.0, "3")


def test_faixa_trava_a_largura_a_partir_de_95_por_cento():
    """A mediana é a de agora; a largura, no mínimo a do último registro
    abaixo de 95% apurado."""
    from obs2026.engine import nivel as NV
    ref = {"q500": 47.0, "q100": 46.6, "q900": 47.4, "q050": 46.4,
           "q950": 47.6, "q025": 46.2, "q975": 47.8}
    agora = {"q500": 47.2, "q100": 47.18, "q900": 47.22, "q050": 47.17,
             "q950": 47.23, "q025": 47.16, "q975": 47.24}
    t = NV.travar_largura(agora, ref)
    assert t["q500"] == 47.2
    assert abs(t["q050"] - 46.6) < 1e-9 and abs(t["q950"] - 47.8) < 1e-9
    assert abs(t["q100"] - 46.8) < 1e-9 and abs(t["q900"] - 47.6) < 1e-9
    # nunca estreita o que o modelo já dá de mais largo
    largo = dict(agora, q050=45.0, q950=49.0)
    t = NV.travar_largura(largo, ref)
    assert t["q050"] == 45.0 and t["q950"] == 49.0
    # sem referência, nada muda; e a faixa não sai de 0–100
    assert NV.travar_largura(agora, None) == agora
    borda = NV.travar_largura({"q500": 0.1, "q100": 0.1, "q900": 0.1},
                              {"q500": 5.0, "q100": 4.0, "q900": 6.0})
    assert borda["q100"] == 0.0 and abs(borda["q900"] - 1.1) < 1e-9


def _reg_alerta(momento, cands, *, apurado, pendente, secoes=(50, 100),
                cargo="3", unidade="sp"):
    """`cands`: {nome: (votos, estado_matematico, prob_lider, prob_maioria)}."""
    from types import SimpleNamespace as NS
    C = sum(v[0] for v in cands.values())
    r = NS(cargo=cargo, unidade=unidade, modelo="m3", momento=momento,
           concorrentes_observados=C, pendente_max=pendente,
           secoes_totalizadas=secoes[0], secoes_total=secoes[1],
           estado_publicacao="projecao_disponivel",
           candidatos=[NS(sqcand=k, nome=k.upper(), votos_observados=v[0],
                          estado_matematico=v[1], prob_lideranca=v[2],
                          prob_dentro_vagas=v[2], prob_maioria_absoluta=v[3],
                          pct_concorrentes_q500=100.0 * v[0] / C,
                          pct_concorrentes_q025=100.0 * v[0] / C - 1,
                          pct_concorrentes_q975=100.0 * v[0] / C + 1)
                       for k, v in cands.items()])
    r.cobertura_pct = apurado
    return r


def test_alertas_saem_uma_vez_e_na_ordem_dos_fatos():
    """Pedido de 05/10/2026: a faixa das últimas novidades. Um alerta é uma
    transição — dito uma vez, não se repete a cada registro."""
    from obs2026.engine import alertas as AL
    ab, el = "em_aberto", "matematicamente_eleito"
    ac = AL.Acompanhador()
    # liderança apertada, nada a dizer
    assert ac.alimentar(_reg_alerta("t1", {"ana": (60, ab, .6, .3),
                                          "bia": (55, ab, .4, .1)},
                                    apurado=12, pendente=900)) == []
    # virada na contagem
    a = ac.alimentar(_reg_alerta("t2", {"ana": (200, ab, .2, .1),
                                        "bia": (230, ab, .8, .6)},
                                 apurado=30, pendente=600))
    assert [x.tipo for x in a] == ["virada"] and "Bia passa Ana" in a[0].texto
    # três registros estáveis com 99%+ de maioria: vencedor projetado, 1 vez
    proj = []
    for t in ("t3", "t4", "t5", "t6"):
        proj += ac.alimentar(_reg_alerta(t, {"ana": (300, ab, 0, 0),
                                             "bia": (500, ab, 1, .995)},
                                         apurado=60, pendente=300))
    assert [x.tipo for x in proj] == ["projetado"]
    assert proj[0].natureza == "probabilidade"
    assert "vencedor projetado" in proj[0].texto
    # a conta fecha: matematicamente eleito, e depois o 100%
    a = ac.alimentar(_reg_alerta("t7", {"ana": (350, "matematicamente_eliminado", 0, 0),
                                        "bia": (640, el, 1, 1)},
                                 apurado=96, pendente=20))
    assert [x.tipo for x in a] == ["matematico"] and a[0].natureza == "conta"
    assert ac.alimentar(_reg_alerta("t8", {"ana": (352, "matematicamente_eliminado", 0, 0),
                                           "bia": (645, el, 1, 1)},
                                    apurado=97, pendente=10)) == []
    a = ac.alimentar(_reg_alerta("t9", {"ana": (360, "matematicamente_eliminado", 0, 0),
                                        "bia": (650, el, 1, 1)},
                                 apurado=100, pendente=0, secoes=(100, 100)))
    assert [x.tipo for x in a] == ["encerrada"]
    assert "vence no 1º turno" in a[0].texto and "eleit" not in a[0].texto


def test_alertas_do_2o_turno_nao_dizem_1o_turno():
    """25/10/2026: a faixa disse "Lula está matematicamente eleito no 1º
    turno" na noite do 2º."""
    from obs2026.engine import alertas as AL
    el, fora = "matematicamente_eleito", "matematicamente_eliminado"
    ac = AL.Acompanhador()
    r = _reg_alerta("t1", {"ana": (350, fora, 0, 0), "bia": (640, el, 1, 1)},
                    apurado=96, pendente=20, cargo="1", unidade="br")
    r.turno = "2"
    a = ac.alimentar(r)
    assert [x.tipo for x in a] == ["matematico"]
    assert "1º turno" not in a[0].texto and "eleito" in a[0].texto
    r = _reg_alerta("t2", {"ana": (360, fora, 0, 0), "bia": (650, el, 1, 1)},
                    apurado=100, pendente=0, secoes=(100, 100), cargo="1",
                    unidade="br")
    r.turno = "2"
    a = ac.alimentar(r)
    assert [x.tipo for x in a] == ["encerrada"]
    assert "1º turno" not in a[0].texto and "vence com" in a[0].texto


def test_alerta_de_segundo_turno_exige_que_ninguem_alcance_a_maioria():
    from obs2026.engine import alertas as AL
    s2 = "matematicamente_no_segundo_turno"
    cands = {"ana": (480, s2, 1, 0), "bia": (400, s2, 0, 0),
             "caio": (50, "matematicamente_eliminado", 0, 0)}
    ac = AL.Acompanhador()
    # Ana ainda pode fechar: 2·480 + 100 > 930 — não há 2º turno definido.
    a = ac.alimentar(_reg_alerta("t1", cands, apurado=85, pendente=100))
    assert "segundo_turno" not in [x.tipo for x in a]
    a = ac.alimentar(_reg_alerta("t2", {"ana": (455, s2, 1, 0),
                                        "bia": (420, s2, 0, 0),
                                        "caio": (55, "matematicamente_eliminado", 0, 0)},
                                 apurado=97, pendente=10))
    assert [x.tipo for x in a] == ["segundo_turno"]
    assert "Ana × Bia" in a[0].texto


def test_roteiro_aceita_o_placar(cliente):
    """Placar: disputa de uma vaga. Presidente dispensa UF, governador a
    exige, senador é recusado; `abrangencia` (mapa com ou sem municípios)
    vai e volta do arquivo."""
    r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": [
        {"tipo": "placar", "cargo": "presidente"},
        {"tipo": "placar", "cargo": "presidente", "abrangencia": "mu"},
        {"tipo": "placar", "cargo": "governador", "uf": "sp",
         "abrangencia": "uf"},
    ]})
    assert r.status_code == 200, r.text
    telas = cliente.get("/api/v1/telao/roteiro").json()["dados"]
    assert [t["tipo"] for t in telas] == ["placar"] * 3
    assert not any(t["erro"] for t in telas)
    assert [t["abrangencia"] for t in telas] == [None, "mu", "uf"]
    assert telas[0]["uf"] is None and telas[2]["uf"] == "sp"
    assert "SP" in telas[2]["titulo"]
    for ruim in ({"tipo": "placar", "cargo": "senador", "uf": "sp"},
                 {"tipo": "placar", "cargo": "governador"},
                 {"tipo": "placar", "cargo": "presidente",
                  "abrangencia": "zona"}):
        r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": [ruim]})
        assert r.status_code == 422, (ruim, r.text)


def test_roteiro_aceita_as_telas_de_resumo(cliente):
    r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": [
        {"tipo": "panorama", "cargo": "governador"},
        {"tipo": "panorama", "cargo": "senador"},
        {"tipo": "duelo", "cargo": "presidente"},
        {"tipo": "participacao", "cargo": "presidente"},
        {"tipo": "estado", "uf": "sp"},
    ]})
    assert r.status_code == 200, r.text
    telas = cliente.get("/api/v1/telao/roteiro").json()["dados"]
    assert [t["tipo"] for t in telas] == ["panorama", "panorama", "duelo",
                                          "participacao", "estado"]
    assert not any(t["erro"] for t in telas)
    assert telas[4]["uf"] == "sp" and "SP" in telas[4]["titulo"]
    # sem UF, o resumo do estado é recusado
    r = cliente.post("/api/v1/telao/roteiro", json={"roteiro": [
        {"tipo": "estado"}]})
    assert r.status_code == 422


def _uf_assembleia(uf, vagas, eleitos):
    """`eleitos`: [(nome, votos, numero_do_partido, sigla)]."""
    return {"uf": uf, "vagas": vagas, "andamento": "f",
            "totalizacao_final": True, "apurado_pct": 100.0,
            "gerado_em": "2026-10-05T11:48:53-03:00", "listas": [],
            "eleitos": [{"nome": n, "numero": "1", "votos": v,
                         "partido_numero": num, "partido_sigla": sg,
                         "situacao": "Eleito por QP"}
                        for n, v, num, sg in eleitos]}


def test_assembleia_conta_cadeiras_por_partido_e_bloco():
    """Deputado estadual (05/10/2026): o plenário de uma casa e o quadro
    das 27, com o bloco ideológico de cada partido."""
    from obs2026.politics import camara as CAM
    blocos = {"13": "esquerda", "22": "direita", "15": "centro"}
    ideo = lambda p: ({"bloco": blocos[p.numero], "cor": "#000"}  # noqa: E731
                      if p.numero in blocos else None)
    sp = _uf_assembleia("sp", 5, [("A", 90, "22", "PL"), ("B", 80, "22", "PL"),
                                  ("C", 70, "22", "PL"), ("D", 60, "13", "PT"),
                                  ("E", 50, "15", "MDB")])
    a = CAM.assembleia(sp, PA.resolver, ideo)
    assert a["vagas"] == 5 and a["maioria"] == 3 and a["eleitos"] == 5
    assert [c["nome"] for c in a["cadeiras"]] == ["A", "B", "C", "D", "E"]
    assert {b: v["total"] for b, v in a["por_bloco"].items()} == {
        "esquerda": 1, "centro": 1, "direita": 3, "sem_classificacao": 0}
    assert a["partidos"][0]["partido"]["sigla"] == "PL"
    # casa incompleta: cadeira sem ocupante fica "aguardando"
    rr = _uf_assembleia("rr", 3, [("X", 9, "13", "PT")])
    b = CAM.assembleia(rr, PA.resolver, ideo)
    assert [c["estado"] for c in b["cadeiras"]] == ["eleito", "aguardando",
                                                    "aguardando"]
    pais = CAM.assembleias({"sp": sp, "rr": rr}, PA.resolver, ideo)
    assert pais["total"] == 8 and pais["eleitos"] == 6
    por = {u["uf"]: u for u in pais["ufs"]}
    assert por["sp"]["bloco_com_maioria"] == "direita"
    assert por["rr"]["bloco_com_maioria"] is None      # 1 de 3 não é maioria
    assert pais["por_bloco"]["direita"]["total"] == 3


def test_deputado_distrital_so_no_df_e_estadual_fora_dele():
    """O arquivo do cargo 7 do DF não existe (404): lá o cargo é o 8. Sem o
    filtro por UF o coletor pediria 27 arquivos inexistentes a cada volta."""
    from types import SimpleNamespace as NS
    from obs2026.config import load_environment
    from obs2026.tse.catalog import Catalog
    from obs2026.tse.urls import UrlBuilder
    env = load_environment("oficial2026")
    urls = UrlBuilder(env.base, env.ambiente, env.ciclo, env.allowed_hosts)
    ele = NS(cd="6259", turno="1", tem_cargo=lambda cd: cd in "35678")
    cat = Catalog(env=env, urls=urls,
                  ea11=NS(pleitos=[NS(eleicoes=[ele])]),
                  ea12_por_eleicao={"6259": object()}, contests=())
    alvos = collections.defaultdict(set)
    for chave, _iv, t in cat.proporcional_targets():
        alvos[chave].add(t.uf)
    assert len(alvos["deputado_federal"]) == 27
    assert alvos["deputado_distrital"] == {"df"}
    assert len(alvos["deputado_estadual"]) == 26
    assert "df" not in alvos["deputado_estadual"]
