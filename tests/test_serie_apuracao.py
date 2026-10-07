"""Tabela pronta do gráfico "ao longo da apuração" (engine/serie_apuracao)."""

import json

from obs2026.engine import serie_apuracao as SA


def _linha(momento, apurado, votos, *, turno="1", modelo="m33",
           cargo="1", unidade="br"):
    total = sum(votos.values())
    return json.dumps({
        "eleicao": "6257", "turno": turno, "cargo": cargo,
        "unidade": unidade, "modelo": modelo, "momento": momento,
        "eleitorado_total": 1000, "eleitorado_apurado": apurado,
        "pendente_max": 1000 - apurado, "concorrentes_observados": total,
        "candidatos": [{
            "sqcand": sq, "nome": sq.upper(), "votos_observados": v,
            "pct_concorrentes_q100": 40.0, "pct_concorrentes_q500": 50.0,
            "pct_concorrentes_q900": 60.0} for sq, v in votos.items()],
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
