"""Recuperação depois de interrupção ou de arquivo recusado (22/09/2026).

No simulado de 22/09 dois defeitos apareceram juntos: o leitor recusou os
arquivos "não recebidos" (candidato sem `dvt`) e a carga seguiu adiante sem
eles; e o horário do TSE, em Brasília, foi gravado como se fosse UTC. Estes
testes fixam o comportamento corrigido.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from obs2026.storage import carga as CG
from obs2026.storage.db import create_all, make_engine, make_session_factory
from obs2026.storage.ingest import _utc
from obs2026.storage.models import CargaPendente
from obs2026.storage.raw import RawArchive
from obs2026.tse.parsers import ea20
from obs2026.quality.checks import check_ea20

BRT = timezone(timedelta(hours=-3))


def _arquivo_nao_recebido() -> dict:
    """Formato real de 22/09: and='n', tudo zero, candidatos sem `dvt`."""
    zero = "0"
    return {
        "ele": "21270", "t": "1", "f": "s", "sup": "n", "tpabr": "br",
        "cdabr": "br", "dg": "22/09/2026", "hg": "09:03:22", "idg": "1",
        "dt": "", "ht": "", "dv": "s", "tf": "n", "and": "n", "esae": "n",
        "mnae": [],
        "carg": [{"cd": "1", "nmn": "Presidente", "nv": "1", "agr": [{
            "n": "", "nm": "", "tp": "", "com": "", "par": [{
                "n": "60", "sg": "P 9998", "nm": "P", "cand": [
                    {"n": "60", "sqcand": "1", "nm": "A", "nmu": "A",
                     "e": "n", "st": "", "vap": zero, "pvap": "0,00",
                     "pvapn": "0"}]}]}]}],
        "s": {"ts": "6196", "st": zero, "snt": "6196", "si": zero,
              "sni": zero, "sa": zero, "sna": zero},
        "e": {"te": "1746104", "est": zero, "esnt": "1746104", "esi": zero,
              "esni": zero, "esa": zero, "esna": zero, "c": zero, "a": zero},
        "v": {k: zero for k in ("tv", "vvc", "vv", "vnom", "van", "vansj",
                                "vb", "tvn", "vn", "vnt", "vscv")},
    }


def test_arquivo_nao_recebido_e_lido_e_nao_acusa_divergencia():
    r = ea20.parse(_arquivo_nao_recebido())
    c = r.cargo.candidatos[0]
    assert c.dvt == ea20.DVT_SEM and not c.conta_como_valido
    criticos = [f.code for f in check_ea20(r) if f.severity == "critical"]
    assert criticos == []


def test_voto_sem_destinacao_e_divergencia_critica():
    p = _arquivo_nao_recebido()
    p["carg"][0]["agr"][0]["par"][0]["cand"][0]["vap"] = "10"
    codigos = {f.code for f in check_ea20(ea20.parse(p))}
    assert "DVT_AUSENTE_COM_VOTOS" in codigos


def test_horario_do_tse_vai_para_o_banco_em_utc():
    brt = datetime(2026, 9, 22, 9, 3, 22, tzinfo=BRT)
    assert _utc(brt) == datetime(2026, 9, 22, 12, 3, 22, tzinfo=timezone.utc)
    assert _utc(None) is None


def test_arquivo_recusado_fica_pendente_e_volta_depois(tmp_path, monkeypatch):
    arq = RawArchive(tmp_path / "raw", "teste")
    corpo = json.dumps({"quebrado": True}).encode()
    ref = arq.store(key="ea20:21270:1:-:-", kind="ea20", url="u", status=200,
                    body=corpo, sha256=hashlib.sha256(corpo).hexdigest(),
                    captured_at=datetime.now(timezone.utc),
                    source_generated_at=None, idg=None, etag=None,
                    last_modified=None, parser_version="t",
                    election="21270", cargo="1")
    eng = make_engine(sqlite_path=tmp_path / "db.sqlite")
    create_all(eng)
    Sess = make_session_factory(eng)
    env = SimpleNamespace(name="teste", proporcionais={})

    with Sess() as s:
        tot = CG.carregar_novos(s, env, arq)
        assert tot["pendentes"] == 1
        assert s.query(CargaPendente).count() == 1
        # A carga seguinte não duplica o pendente.
        CG.carregar_novos(s, env, arq, completa=True)
        assert s.query(CargaPendente).count() == 1

    # "Corrige o leitor": agora a leitura passa.
    gravados = []
    monkeypatch.setattr(CG.ea20_parser, "parse", lambda payload: "ok")
    monkeypatch.setattr(CG, "ingest_ea20",
                        lambda s, env, r, x: gravados.append(r.key))
    with Sess() as s:
        out = CG.reler_pendentes(s, env, arq)
        assert out == {"tentados": 1, "gravados": 1, "ainda_falham": 0}
        assert s.query(CargaPendente).count() == 0
    assert gravados == [ref.key]


# ------------------------------------------------------------ homologação ---

def _evidencia(tmp_path, passa_erro=True, cob=(79.0, 81.0), parcial=False):
    import json as _j
    f = tmp_path / "gate.json"
    f.write_text(_j.dumps({
        "disputas": 55, "passa_erro_mediano": passa_erro,
        "derrotas_m2": [] if passa_erro else [{"rodada": 1}],
        "cobertura80_m2_pct": {"real": cob[0], "aleatoria": cob[1]},
        "tolerancia_pp": 5.0,
        "passa_cobertura": all(abs(c - 80) <= 5 for c in cob),
        "gerado_em": "2026-09-22T12:00:00",
        "parametros": {"parcial": parcial}}), encoding="utf-8")
    return f


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _modelo_m2(monkeypatch):
    """A evidência de teste é só do M2: os testes não podem depender do
    modelo escolhido hoje na aba Configuração (desde 22/09, o M3)."""
    from obs2026 import operacao
    monkeypatch.setattr(operacao, "modelo_ativo", lambda: "m2")


def _thresholds(tmp_path):
    import shutil as _s
    from obs2026.config import CONFIG_DIR
    f = tmp_path / "thresholds.yaml"
    _s.copy(CONFIG_DIR / "thresholds.yaml", f)
    return f


def test_homologacao_recusa_sem_vencer_o_m0(tmp_path):
    import pytest
    from obs2026 import homologacao as H
    th = _thresholds(tmp_path)
    antes = th.read_text(encoding="utf-8")
    with pytest.raises(H.HomologacaoError, match="M0"):
        H.homologar("Fulano", caminho=th,
                    evidencia=_evidencia(tmp_path, passa_erro=False))
    assert th.read_text(encoding="utf-8") == antes


def test_homologacao_recusa_teste_parcial(tmp_path):
    import pytest
    from obs2026 import homologacao as H
    with pytest.raises(H.HomologacaoError, match="parcial"):
        H.homologar("Fulano", caminho=_thresholds(tmp_path),
                    evidencia=_evidencia(tmp_path, parcial=True))


def test_homologar_e_revogar(tmp_path):
    from obs2026 import homologacao as H
    from obs2026.config import load_thresholds
    th = _thresholds(tmp_path)
    H.homologar("Fulano de Tal", caminho=th, evidencia=_evidencia(tmp_path))
    t = load_thresholds(tmp_path)
    assert t.homologated and t.signed_by == "Fulano de Tal"
    assert "evidencia:" in th.read_text(encoding="utf-8")
    H.revogar("teste", caminho=th)
    assert not load_thresholds(tmp_path).homologated
