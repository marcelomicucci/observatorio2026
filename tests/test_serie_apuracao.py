"""Tabela pronta do gráfico "ao longo da apuração" (engine/serie_apuracao)."""

import json

from obs2026.engine import serie_apuracao as SA


def _linha(momento, apurado, votos, *, turno="1", modelo="m33",
           cargo="1", unidade="br", largura=10.0):
    total = sum(votos.values())
    return json.dumps({
        "eleicao": "6257", "turno": turno, "cargo": cargo,
        "unidade": unidade, "modelo": modelo, "momento": momento,
        "eleitorado_total": 1000, "eleitorado_apurado": apurado,
        "pendente_max": 1000 - apurado, "concorrentes_observados": total,
        "secoes_total": 1000, "secoes_totalizadas": apurado,
        "candidatos": [{
            "sqcand": sq, "nome": sq.upper(), "votos_observados": v,
            "pct_concorrentes_q100": 50.0 - largura,
            "pct_concorrentes_q500": 50.0,
            "pct_concorrentes_q900": 50.0 + largura,
            "pct_concorrentes_q050": 50.0 - 2 * largura,
            "pct_concorrentes_q950": 50.0 + 2 * largura}
            for sq, v in votos.items()],
    }) + "\n"


def _diario(tmp_path):
    return tmp_path / "previsoes-teste.jsonl"


def test_sincroniza_so_o_que_e_novo(tmp_path):
    d = _diario(tmp_path)
    d.write_text(_linha("2026-10-04T20:00:00+00:00", 100, {"a": 60, "b": 40})
                 + _linha("2026-10-04T20:02:00+00:00", 200,
                          {"a": 110, "b": 90}), encoding="utf-8")
    assert SA.sincronizar(tmp_path, "teste") == 2
    assert SA.sincronizar(tmp_path, "teste") == 0        # nada duplicado
    with open(d, "a", encoding="utf-8") as fh:
        fh.write(_linha("2026-10-04T20:04:00+00:00", 300,
                        {"a": 150, "b": 150}))
    assert SA.sincronizar(tmp_path, "teste") == 1

    s = SA.consultar(tmp_path, "teste", "1", "br")
    assert s["execucoes"] == 3
    a = next(c for c in s["candidatos"] if c["sqcand"] == "a")
    assert [p["cobertura_pct"] for p in a["pontos"]] == [10.0, 20.0, 30.0]
    assert [round(p["pct_apurado"], 1) for p in a["pontos"]] == [60.0, 55.0,
                                                                 50.0]
    assert a["pontos"][0]["pct_projetado"] == 50.0


def test_linha_pela_metade_fica_para_depois(tmp_path):
    d = _diario(tmp_path)
    inteira = _linha("2026-10-04T20:00:00+00:00", 100, {"a": 60, "b": 40})
    d.write_text(inteira + inteira[:40], encoding="utf-8")
    assert SA.sincronizar(tmp_path, "teste") == 1


def test_segundo_turno_nao_herda_o_primeiro(tmp_path):
    d = _diario(tmp_path)
    d.write_text(
        "".join(_linha(f"2026-10-04T20:{m:02d}:00+00:00", 100 + m,
                       {"a": 60, "b": 40, "c": 10}) for m in range(5))
        + _linha("2026-10-25T20:00:00+00:00", 50, {"a": 20, "b": 30},
                 turno="2"), encoding="utf-8")
    SA.sincronizar(tmp_path, "teste")
    s = SA.consultar(tmp_path, "teste", "1", "br")
    assert (s["turno"], s["execucoes"]) == ("2", 1)
    assert [c["sqcand"] for c in s["candidatos"]] == ["b", "a"]


def test_registro_avulso_de_outro_modelo_nao_toma_a_serie(tmp_path):
    d = _diario(tmp_path)
    d.write_text(
        "".join(_linha(f"2026-10-04T20:{m:02d}:00+00:00", 100 + m,
                       {"a": 60, "b": 40}, modelo="m3") for m in range(5))
        + _linha("2026-10-07T11:00:00+00:00", 999, {"a": 60, "b": 40}),
        encoding="utf-8")
    SA.sincronizar(tmp_path, "teste")
    s = SA.consultar(tmp_path, "teste", "1", "br")
    assert (s["modelo"], s["execucoes"]) == ("m3", 5)


def test_diario_trocado_recomeca(tmp_path):
    d = _diario(tmp_path)
    d.write_text("".join(
        _linha(f"2026-10-04T20:{m:02d}:00+00:00", 100, {"a": 60, "b": 40})
        for m in range(4)), encoding="utf-8")
    SA.sincronizar(tmp_path, "teste")
    d.write_text(_linha("2026-10-05T10:00:00+00:00", 500, {"a": 1, "b": 2}),
                 encoding="utf-8")
    assert SA.sincronizar(tmp_path, "teste") == 1
    assert SA.consultar(tmp_path, "teste", "1", "br")["execucoes"] == 1


def test_sem_diario_e_sem_disputa(tmp_path):
    assert SA.sincronizar(tmp_path, "teste") == 0
    assert SA.consultar(tmp_path, "teste", "1", "br") is None
    _diario(tmp_path).write_text(
        _linha("2026-10-04T20:00:00+00:00", 100, {"a": 60, "b": 40}),
        encoding="utf-8")
    SA.sincronizar(tmp_path, "teste")
    assert SA.consultar(tmp_path, "teste", "3", "sp") is None


def test_faixa_publicada_sobe_de_nivel_e_trava_a_largura(tmp_path):
    """80% abaixo de 15% apurado, 90% dali em diante; acima de 95% a faixa
    não fica mais estreita que a do último registro abaixo disso."""
    _diario(tmp_path).write_text(
        _linha("2026-10-04T20:00:00+00:00", 100, {"a": 60, "b": 40})
        + _linha("2026-10-04T21:00:00+00:00", 500, {"a": 60, "b": 40},
                 largura=4.0)
        + _linha("2026-10-04T22:00:00+00:00", 940, {"a": 60, "b": 40},
                 largura=1.0)
        + _linha("2026-10-04T23:00:00+00:00", 990, {"a": 60, "b": 40},
                 largura=0.1)
        + _linha("2026-10-05T00:00:00+00:00", 1000, {"a": 60, "b": 40},
                 largura=0.0), encoding="utf-8")
    SA.sincronizar(tmp_path, "teste")
    p = SA.consultar(tmp_path, "teste", "1", "br")["candidatos"][0]["pontos"]
    assert [(x["faixa_nivel"], x["faixa_min"], x["faixa_max"]) for x in p] == [
        (80, 40.0, 60.0),      # 10% apurado: tendência, 80%
        (90, 42.0, 58.0),      # 50%: 90%, o dobro da largura do teste
        (90, 48.0, 52.0),      # 94%: último antes da trava
        (90, 48.0, 52.0),      # 99%: o modelo diz ±0,2; publica-se ±2
        (90, 50.0, 50.0),      # encerrada: sem trava, é o resultado
    ]
