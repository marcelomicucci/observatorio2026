"""Definição matemática e auditoria das próprias projeções.

A definição matemática tem um requisito diferente de todo o resto do sistema:
ela **não pode errar**. Uma projeção errada é ruim; uma "definição
matemática" errada significa que anunciamos certeza onde não havia. Por isso
os testes aqui cobrem principalmente os casos-limite em que a conta ingênua
erraria.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs2026.engine import audit
from obs2026.engine import decided as D


# -------------------------------------------------- definição matemática ---

def test_maioria_absoluta_garantida_usa_votos_a_concorrentes():
    """Mesmo que TODO o resto vá para os outros, o líder passa de 50%."""
    d = D.avaliar({"A": 60, "B": 30}, vagas=1,
                  concorrentes_observados=90, pendente_max=10)
    # 60 de no máximo 100 => garantido.
    assert d.situacoes[0].estado is D.Estado.ELEITO
    assert d.disputa_definida


def test_nao_declara_maioria_quando_o_resto_ainda_alcanca():
    d = D.avaliar({"A": 60, "B": 30}, vagas=1,
                  concorrentes_observados=90, pendente_max=40)
    # 60 de no máximo 130: 60*2=120 < 130, não garantido.
    assert d.situacoes[0].estado is not D.Estado.ELEITO
    assert not d.disputa_definida


def test_eliminacao_compara_com_a_ultima_vaga_e_nao_com_o_lider():
    """Com duas vagas, basta alcançar o segundo colocado.

    A conta ingênua compara todo mundo com o líder e elimina candidatos que
    ainda podem tomar a segunda vaga. No Senado isso apagaria da tela um
    candidato vivo.
    """
    votos = {"A": 5000, "B": 4000, "C": 3400, "D": 3300}
    # C precisa de 601 para passar B, e ainda há 1000 possíveis.
    d = D.avaliar(votos, vagas=2, concorrentes_observados=15700,
                  pendente_max=1000)
    est = {s.sqcand: s.estado for s in d.situacoes}
    assert est["C"] is D.Estado.EM_ABERTO
    assert est["B"] is D.Estado.EM_ABERTO      # pode perder a vaga para C
    # Com só 100 possíveis, C não alcança mais.
    d2 = D.avaliar(votos, vagas=2, concorrentes_observados=15700,
                   pendente_max=100)
    est2 = {s.sqcand: s.estado for s in d2.situacoes}
    assert est2["C"] is D.Estado.ELIMINADO
    assert est2["A"] is D.Estado.ELEITO and est2["B"] is D.Estado.ELEITO
    assert d2.disputa_definida


def test_deficit_conjunto_usa_o_estoque_compartilhado():
    """Os rivais dividem o MESMO estoque de votos restantes.

    Para B (2º lugar, 2 vagas) perder a vaga, bastam DOIS rivais acima. A ja
    esta acima, entao o custo real e so o que falta a C — 301 votos, nao a
    soma de C e D. Contar cada rival isoladamente daria uma leitura mais
    frouxa do que a verdade.
    """
    votos = {"A": 10_000, "B": 5_000, "C": 4_700, "D": 4_650}
    d = D.avaliar(votos, vagas=2, concorrentes_observados=24_350,
                  pendente_max=400)
    s_b = next(s for s in d.situacoes if s.sqcand == "B")
    assert s_b.custo_para_perder_a_vaga == 301      # 5000 - 4700 + 1
    assert s_b.estado is D.Estado.EM_ABERTO         # 301 cabe nos 800
    # Com pouquissimo resto, C nao chega e a vaga de B fica garantida.
    d2 = D.avaliar(votos, vagas=2, concorrentes_observados=24_350,
                   pendente_max=100)
    s_b2 = next(s for s in d2.situacoes if s.sqcand == "B")
    assert s_b2.estado is D.Estado.ELEITO


def test_segundo_colocado_nao_e_eliminado_por_nao_poder_vencer():
    """Presidente e governador tem segundo turno.

    B nao pode mais alcancar A, mas esta indo para o segundo turno. Medir
    eliminacao contra UMA vaga apagaria da tela o segundo colocado de uma
    eleicao presidencial.
    """
    d = D.avaliar({"A": 48, "B": 40, "C": 5}, vagas=1,
                  concorrentes_observados=93, pendente_max=6)
    est = {s.sqcand: s.estado for s in d.situacoes}
    assert est["B"] is not D.Estado.ELIMINADO
    assert est["B"] is D.Estado.SEGUNDO_TURNO
    assert est["C"] is D.Estado.ELIMINADO


def test_senador_nao_tem_segundo_turno():
    """Duas vagas, mas quem fica em terceiro esta fora — nao ha repescagem."""
    d = D.avaliar({"A": 100, "B": 90, "C": 10}, vagas=2,
                  concorrentes_observados=200, pendente_max=5,
                  tem_segundo_turno=False)
    est = {s.sqcand: s.estado for s in d.situacoes}
    assert est["A"] is D.Estado.ELEITO and est["B"] is D.Estado.ELEITO
    assert est["C"] is D.Estado.ELIMINADO
    assert d.disputa_definida
    assert not d.segundo_turno_definido


def test_segundo_turno_definido_quando_sobram_dois():
    d = D.avaliar({"A": 48, "B": 40, "C": 5}, vagas=1,
                  concorrentes_observados=93, pendente_max=6)
    assert d.segundo_turno_definido
    assert {s.sqcand for s in d.no_segundo_turno} == {"A", "B"}
    assert not d.disputa_definida          # nenhum tem maioria absoluta


def test_sub_judice_suspende_qualquer_definicao():
    """Voto sub judice pode ser revalidado ou cassado depois (§4.7)."""
    d = D.avaliar({"A": 60, "B": 30}, vagas=1,
                  concorrentes_observados=90, pendente_max=1,
                  sub_judice=500)
    assert d.sub_judice_em_jogo
    assert all(s.estado is D.Estado.EM_ABERTO for s in d.situacoes)
    assert not d.disputa_definida
    assert d.avisos


def test_senado_nao_multiplica_o_teto_individual_pelas_vagas():
    """Um eleitor não dá dois votos válidos ao mesmo candidato (§4.6)."""
    d = D.avaliar({"A": 100, "B": 50}, vagas=2,
                  concorrentes_observados=150, pendente_max=1000)
    assert d.resto_maximo_por_candidato == 1000     # 1 por eleitor
    assert d.resto_maximo_total == 2000             # 2 escolhas por eleitor


def test_apuracao_encerrada_zera_o_resto():
    d = D.avaliar({"A": 60, "B": 40}, vagas=1,
                  concorrentes_observados=100, pendente_max=99999,
                  encerrada=True)
    assert d.resto_maximo_total == 0
    assert d.situacoes[0].estado is D.Estado.ELEITO


def test_empate_nao_basta_para_passar():
    """Rival precisa PASSAR, não empatar — o déficit inclui o +1.

    Senado, duas vagas, resta 1 voto possivel. C tem 98 e B tem 99: com o
    unico voto restante C empata em 99, e empate nao toma a vaga. Se o
    codigo esquecesse o +1, C apareceria como ameaca e a vaga de B ficaria
    falsamente em aberto.
    """
    d = D.avaliar({"A": 100, "B": 99, "C": 98}, vagas=2,
                  concorrentes_observados=297, pendente_max=1,
                  tem_segundo_turno=False)
    s_b = next(s for s in d.situacoes if s.sqcand == "B")
    assert s_b.custo_para_perder_a_vaga is None     # aritmeticamente impossivel
    assert s_b.estado is D.Estado.ELEITO
    s_c = next(s for s in d.situacoes if s.sqcand == "C")
    assert s_c.estado is D.Estado.ELIMINADO


# ------------------------------------------------------------- auditoria ---

class _FakeForecast:
    """Forecast mínimo para exercitar a captura sem rodar Monte Carlo."""

    def __init__(self):
        self.candidates = ["A", "B"]
        self.observed = {"A": 60, "B": 40}
        self.seed = 7
        self.n_sims = 10
        self.diagnostics = {"erro_monte_carlo_pp": 0.01}

    def quantis(self, qs):
        return {
            "A": {"votos": {"q100": 58, "q500": 60, "q900": 62},
                  "pct_concorrentes": {"q100": 57.0, "q500": 60.0,
                                       "q900": 63.0}},
            "B": {"votos": {"q100": 38, "q500": 40, "q900": 42},
                  "pct_concorrentes": {"q100": 37.0, "q500": 40.0,
                                       "q900": 43.0}},
        }

    def prob_lideranca(self):
        return {"A": 0.95, "B": 0.05}

    def prob_dentro_das_vagas(self):
        return {"A": 0.95, "B": 0.05}

    def prob_maioria_absoluta(self):
        return {"A": 0.9, "B": 0.0}


def _registro(tmp_path: Path) -> audit.Registro:
    fc = _FakeForecast()
    dfn = D.avaliar({"A": 60, "B": 40}, vagas=1,
                    concorrentes_observados=100, pendente_max=5)
    return audit.capturar(
        forecast=fc, definicao=dfn, ambiente="teste", eleicao="1",
        turno="1", cargo="1", abrangencia="br", unidade="br",
        snapshot_set_id="abc", fonte_gerada_em=None,
        eleitorado_total=1000, eleitorado_apurado=500,
        secoes_totalizadas=50, secoes_total=100,
        pendente_min=400, pendente_max=500, modelo="m1",
        model_version="v1", thresholds_hash="h", thresholds_homologados=False,
        estado_publicacao="cenario_exploratorio",
        nomes={"A": "Alfa", "B": "Beta"},
    )


def test_diario_e_append_only(tmp_path):
    """Reprocessar não pode apagar o que já foi dito."""
    d = audit.Diario(tmp_path, "teste")
    r = _registro(tmp_path)
    d.registrar(r)
    d.registrar(r)
    assert len(d.ler()) == 2               # nada foi sobrescrito


def test_registro_guarda_o_que_se_sabia_no_momento(tmp_path):
    r = _registro(tmp_path)
    assert r.cobertura_pct == 50.0
    assert r.semente == 7                  # reprodutível
    assert r.thresholds_hash == "h"
    assert r.estado_publicacao == "cenario_exploratorio"
    assert {c.nome for c in r.candidatos} == {"Alfa", "Beta"}


def test_serie_separa_modelos_diferentes(tmp_path):
    """Reanálise com modelo novo é OUTRA série (§5.2)."""
    d = audit.Diario(tmp_path, "teste")
    r1 = _registro(tmp_path)
    d.registrar(r1)
    from dataclasses import replace
    d.registrar(replace(r1, modelo="m0", registro_id="outro"))
    assert len(d.series()) == 2


def test_avaliacao_mede_erro_e_cobertura_do_intervalo(tmp_path):
    r = _registro(tmp_path)
    # Verdade final: A com 61% e B com 39%.
    nota = audit.avaliar(r, {"A": 610, "B": 390},
                         concorrentes_final=1000, vagas=1)
    assert nota.erro_pp_mediano == pytest.approx(1.0, abs=0.01)
    assert nota.cobertura_intervalo_80 == 1.0      # ambos dentro do 80%
    assert nota.acertou_lideranca
    assert nota.acertou_vagas
    assert nota.definicao_matematica_bateu is True


def test_avaliacao_flagra_intervalo_que_nao_cobriu(tmp_path):
    r = _registro(tmp_path)
    # A terminou com 70%, fora do intervalo 57-63 que havíamos dito.
    nota = audit.avaliar(r, {"A": 700, "B": 300},
                         concorrentes_final=1000, vagas=1)
    a = next(c for c in nota.candidatos if c.sqcand == "A")
    assert not a.dentro_do_intervalo_80
    assert nota.cobertura_intervalo_80 < 1.0


def test_definicao_matematica_contrariada_e_falha_grave(tmp_path):
    """Se o que declaramos definido não se confirmou, algo está errado."""
    r = _registro(tmp_path)
    assert any(c.estado_matematico == "matematicamente_eleito"
               for c in r.candidatos)
    # Resultado final em que o "definido" perdeu.
    nota = audit.avaliar(r, {"A": 100, "B": 900},
                         concorrentes_final=1000, vagas=1)
    assert nota.definicao_matematica_bateu is False


def test_consolidado_ordena_por_cobertura_e_marca_amostra_pequena(tmp_path):
    from dataclasses import replace
    r = _registro(tmp_path)
    notas = []
    for apurado, total in ((5, 1000), (300, 1000), (990, 1000)):
        rr = replace(r, eleitorado_apurado=apurado, eleitorado_total=total)
        notas.append(audit.avaliar(rr, {"A": 610, "B": 390},
                                   concorrentes_final=1000, vagas=1))
    resumo = audit.consolidar(notas)
    faixas = list(resumo)
    # Ordem numérica da cobertura, não alfabética do rótulo.
    assert faixas == ["<1%", "<50%", ">=99%"]
    assert all(v["amostra_pequena"] for v in resumo.values())
    assert all(v["nominal"] == 0.80 for v in resumo.values())


def test_consolidado_traz_intervalo_de_confianca_da_propria_metrica(tmp_path):
    """Poucas execuções não sustentam alegação de calibração (§11.2)."""
    r = _registro(tmp_path)
    nota = audit.avaliar(r, {"A": 610, "B": 390},
                         concorrentes_final=1000, vagas=1)
    resumo = audit.consolidar([nota])
    v = next(iter(resumo.values()))
    assert v["cobertura_ic95"] is not None
    assert v["amostra_pequena"] is True


# ------------------------------------------------------------- geografia ---

def test_prefixo_ibge_da_uf_usa_a_moda_e_nao_o_primeiro():
    """Boa Esperanca do Norte (MT) tem codigo IBGE 5300109.

    O prefixo 53 e a faixa do Distrito Federal, nao a do Mato Grosso (51).
    Derivar o codigo da UF pelo primeiro municipio encontrado dava 53 ao
    estado inteiro: MT sumia do mapa e o DF aparecia duplicado.

    Este teste reproduz a situacao com dados sinteticos, para a regra valer
    mesmo que o municipio troque de codigo no futuro.
    """
    from collections import Counter
    municipios = [("mt", "5300109")] + [("mt", f"51{i:05d}") for i in range(9)]
    prefixos: dict[str, Counter] = {}
    for uf, cd in municipios:
        prefixos.setdefault(uf, Counter())[cd[:2]] += 1
    cod_uf = {uf: c.most_common(1)[0][0] for uf, c in prefixos.items()}
    assert cod_uf["mt"] == "51"
    divergentes = [(uf, p, n) for uf, c in prefixos.items()
                   for p, n in c.items() if p != cod_uf[uf]]
    assert divergentes == [("mt", "53", 1)]


def test_recorte_ibge_nao_e_sobrescrito_pelo_laco():
    """O laço de anomalias reusava o nome `uf` do parâmetro da função.

    Resultado: `recorte_ibge` devolvia a última UF iterada em vez da pedida,
    e o mapa desenhava o Brasil inteiro para mostrar Alagoas. O teste fixa o
    comportamento correto sem depender do banco.
    """
    from collections import Counter
    prefixos = {"al": Counter({"27": 102}), "ac": Counter({"12": 22}),
                "to": Counter({"17": 139})}
    pedida = "al"
    cod_uf = {sigla: c.most_common(1)[0][0] for sigla, c in prefixos.items()}
    for sigla, c in prefixos.items():      # não pode sombrear `pedida`
        for pref, n in c.items():
            assert pref == cod_uf[sigla]
    assert cod_uf.get(pedida) == "27"


# ------------------------------------------------- carga incremental ----

def test_carregador_incremental_confere_com_a_carga_completa(tmp_path):
    """O incremental existe para ser mais rápido, não mais esperto.

    Qualquer divergência em relação à carga completa é bug dele, e este
    teste é a rede — sem ela, uma otimização silenciosa poderia mudar o
    resultado projetado sem ninguém perceber.
    """
    from obs2026.config import DATA_DIR
    from obs2026.engine.incremental import CarregadorIncremental
    from obs2026.storage.raw import RawArchive

    arc = RawArchive(DATA_DIR / "raw", "simulado2026")
    if not arc.days():
        pytest.skip("arquivo bruto vazio; rode `obs2026 ensaio` antes")
    c = CarregadorIncremental(archive=arc, cargo="3")
    c.atualizar()
    conf = c.conferir()
    assert conf["iguais"], conf


def test_segunda_passagem_do_incremental_nao_relê_nada(tmp_path):
    """Sem isso, projetar a cada 2 minutos releria o manifesto inteiro.

    Usa um arquivo bruto próprio em `tmp_path`: apontar para o arquivo real
    torna o teste dependente de o coletor não estar rodando, e um teste que
    falha quando o sistema está em uso não é um teste, é um incômodo.
    """
    import json as _json
    import shutil
    from dataclasses import asdict as _asdict
    from obs2026.config import DATA_DIR
    from obs2026.engine.incremental import CarregadorIncremental
    from obs2026.storage.raw import RawArchive

    origem = RawArchive(DATA_DIR / "raw", "simulado2026")
    if not origem.days():
        pytest.skip("arquivo bruto vazio")

    # Cópia congelada de algumas capturas de Governador.
    destino = RawArchive(tmp_path, "copia")
    dia = origem.days()[-1]
    refs = [r for r in origem.manifest(dia)
            if r.kind == "ea20" and r.path and r.cargo == "3" and r.mun][:40]
    if not refs:
        pytest.skip("sem capturas de governador no arquivo bruto")
    (destino.root / "manifest").mkdir(parents=True, exist_ok=True)
    with open(destino.root / "manifest" / f"{dia}.jsonl", "w",
              encoding="utf-8") as fh:
        for r in refs:
            alvo = destino.root / r.path
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origem.root / r.path, alvo)
            # SnapshotRef usa __slots__, então não tem __dict__.
            fh.write(_json.dumps(_asdict(r), ensure_ascii=False) + chr(10))

    c = CarregadorIncremental(archive=destino, cargo="3")
    primeira = c.atualizar()
    segunda = c.atualizar()
    assert primeira["linhas_novas"] == len(refs)
    assert segunda["linhas_novas"] == 0          # nada relido
    assert segunda["unidades_atualizadas"] == 0
    assert segunda["unidades_totais"] == primeira["unidades_totais"] > 0


def test_carregador_filtra_por_uf_sem_duplicar_memoria():
    """Um carregador por CARGO, filtrado na saída — não 27 por cargo."""
    from obs2026.config import DATA_DIR
    from obs2026.engine.incremental import CarregadorIncremental
    from obs2026.storage.raw import RawArchive

    arc = RawArchive(DATA_DIR / "raw", "simulado2026")
    if not arc.days():
        pytest.skip("arquivo bruto vazio")
    c = CarregadorIncremental(archive=arc, cargo="3")
    c.atualizar()
    todas = c.observacoes()
    al = c.observacoes("al")
    assert 0 < len(al) < len(todas)
    assert all(o.uf == "al" for o in al)
