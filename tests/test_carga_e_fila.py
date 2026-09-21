"""Carga incremental, prioridade dos totais e retomada do coletor.

As três existem pelo mesmo motivo: na noite da apuração o número que a sala
olha é o total do estado e do país, e ele tem de chegar à tela antes de
qualquer detalhe — e continuar chegando depois de um reinício.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obs2026.storage.raw import RawArchive
from obs2026.tse.collector import NIVEL_TOTAIS, PriorityQueue
from obs2026.tse.urls import Target


def _ref(**kw) -> dict:
    base = {"key": "k", "kind": "ea11", "url": "u", "status": 200,
            "sha256": None, "bytes_len": 0, "captured_at": "2026-10-04T20:00:00",
            "source_generated_at": None, "idg": None, "etag": None,
            "last_modified": None, "environment": "t", "parser_version": "1",
            "path": None, "dedup": False}
    base.update(kw)
    return base


def _manifesto(raiz: Path, dia: str, linhas: list[dict], fim=b"\n") -> Path:
    p = raiz / "t" / "manifest" / f"{dia}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    corpo = b"\n".join(json.dumps(x).encode() for x in linhas) + fim
    p.write_bytes(corpo)
    return p


# ------------------------------------------------------- manifest_desde ---

def test_manifesto_retoma_do_byte_exato(tmp_path):
    _manifesto(tmp_path, "2026-10-04",
               [_ref(key="a"), _ref(key="b"), _ref(key="c")])
    arc = RawArchive(tmp_path, "t")
    primeira = list(arc.manifest_desde("2026-10-04", 0))
    assert [r.key for r, _ in primeira] == ["a", "b", "c"]
    # Retomar do fim da segunda linha devolve só a terceira.
    meio = primeira[1][1]
    assert [r.key for r, _ in arc.manifest_desde("2026-10-04", meio)] == ["c"]
    # Do fim, nada.
    assert list(arc.manifest_desde("2026-10-04", primeira[-1][1])) == []


def test_linha_sendo_escrita_nao_e_lida_nem_pulada(tmp_path):
    """A última linha sem quebra é a que o coletor ainda está escrevendo.

    Lê-la daria JSON truncado; avançar além dela a perderia para sempre. O
    carregador anterior, em modo texto, levantava erro exatamente aqui.
    """
    p = _manifesto(tmp_path, "2026-10-04", [_ref(key="a")])
    with open(p, "ab") as fh:
        fh.write(b'{"key": "b", "kind": "ea1')        # meia linha
    arc = RawArchive(tmp_path, "t")
    lidas = list(arc.manifest_desde("2026-10-04", 0))
    assert [r.key for r, _ in lidas] == ["a"]
    retomar = lidas[-1][1]

    # O coletor termina a linha; a próxima leitura a pega inteira.
    _manifesto(tmp_path, "2026-10-04", [_ref(key="a"), _ref(key="b")])
    novas = list(arc.manifest_desde("2026-10-04", retomar))
    assert [r.key for r, _ in novas] == ["b"]


# --------------------------------------------------------- carregar_novos ---

def test_carga_incremental_e_totais_primeiro(tmp_path, monkeypatch):
    """Segunda passada não relê nada; em cada janela, Brasil e UF antes."""
    from obs2026.storage import carga as C
    from obs2026.storage.db import create_all, make_engine, make_session_factory

    linhas = [
        _ref(key="m1", kind="ea20", path="x", mun="00001", uf="pa"),
        _ref(key="br", kind="ea20", path="x"),
        _ref(key="m2", kind="ea20", path="x", mun="00002", uf="pa"),
        _ref(key="uf", kind="ea20", path="x", uf="pa"),
        _ref(key="cfg", kind="ea11"),
    ]
    _manifesto(tmp_path, "2026-10-04", linhas)
    arc = RawArchive(tmp_path, "t")

    ordem: list[str] = []
    monkeypatch.setattr(C.ea20_parser, "parse", lambda payload: payload)
    monkeypatch.setattr(RawArchive, "load", lambda self, ref: ref.key)
    monkeypatch.setattr(
        C, "ingest_ea20",
        lambda s, env, ref, r: ordem.append(ref.key) or
        {"snapshot": 1, "candidates": 0, "results": 0, "flags": []})

    eng = make_engine(sqlite_path=tmp_path / "t.db")
    create_all(eng)
    Sess = make_session_factory(eng)
    with Sess() as s:
        tot = C.carregar_novos(s, env=None, archive=arc)
    assert tot["linhas"] == 5 and tot["ea20"] == 4 and tot["grandes"] == 2
    # Os números grandes primeiro, na ordem em que chegaram; depois o resto.
    assert ordem == ["br", "uf", "m1", "m2"]

    ordem.clear()
    with Sess() as s:
        tot = C.carregar_novos(s, env=None, archive=arc)
    assert tot["linhas"] == 0 and ordem == []

    # Chegou uma linha nova: só ela é lida.
    with open(tmp_path / "t" / "manifest" / "2026-10-04.jsonl", "ab") as fh:
        fh.write(json.dumps(_ref(key="m3", kind="ea20", path="x",
                                 mun="00003", uf="pa")).encode() + b"\n")
    with Sess() as s:
        tot = C.carregar_novos(s, env=None, archive=arc)
    assert tot["linhas"] == 1 and ordem == ["m3"]


# ------------------------------------------------------------------ fila ---

def test_total_nacional_sai_antes_da_maior_cidade():
    """São Paulo tem 9,3 milhões de eleitores; o peso fixo dos totais era
    1.000.000. Por peso, a capital passava à frente do total do Brasil."""
    q = PriorityQueue()
    for i, peso in enumerate((9_300_000, 5_000_000, 2_400_000)):
        q.push(Target("ea20", f"https://x/mu{i}", election="1", cargo="1",
                      uf="sp", mun=f"{i:05d}", abr="mu"), peso)
    q.push(Target("ea20", "https://x/br", election="1", cargo="1",
                  abr="br"), 1_000_000, nivel=NIVEL_TOTAIS)
    assert q.pop().target.url == "https://x/br"


def test_envelhecimento_nao_faz_municipio_passar_o_total():
    q = PriorityQueue(aging_boost_per_minute=1e9)
    q.push(Target("ea20", "https://x/mu", election="1", cargo="1",
                  uf="sp", mun="00001", abr="mu"), 9_300_000)
    q.push(Target("ea20", "https://x/uf", election="1", cargo="1",
                  uf="sp", abr="uf"), 1, nivel=NIVEL_TOTAIS)
    q._heap[0].enqueued_at -= 3600           # uma hora na fila
    q._heap[1].enqueued_at -= 3600
    q.age()
    assert q.pop().target.url == "https://x/uf"


def test_reenfileirar_como_total_promove_o_item():
    """A varredura pode ter posto o arquivo da UF na fila como comum; quando
    o controle o pede, ele sobe de nível sem virar requisição duplicada."""
    q = PriorityQueue()
    t = Target("ea20", "https://x/uf", election="1", cargo="1", uf="pa",
               abr="uf")
    q.push(Target("ea20", "https://x/mu", election="1", cargo="1",
                  uf="pa", mun="00001", abr="mu"), 9_000_000)
    assert q.push(t, 1) is True
    assert q.push(t, 1, nivel=NIVEL_TOTAIS) is False
    assert len(q) == 2
    assert q.pop().target.url == "https://x/uf"


# -------------------------------------------------------------- retomada ---

def test_retomada_contra_o_arquivo_real():
    """Com o arquivo do simulado: a retomada reconhece quase tudo como em
    dia, em segundos, sem ir ao TSE."""
    from obs2026.cli import _client
    from obs2026.config import DATA_DIR, load_collector, load_environment
    from obs2026.tse import catalog as catalog_mod
    from obs2026.tse.collector import Collector
    from obs2026.tse.parsers import ea11 as p11, ea12 as p12
    from obs2026.tse.urls import UrlBuilder

    env, cfg = load_environment("simulado2026"), load_collector()
    arc = RawArchive(DATA_DIR / "raw", env.name)
    if not arc.days():
        pytest.skip("arquivo bruto do simulado ausente")
    ult = {}
    for d in arc.days():
        for r in arc.manifest(d):
            if r.kind in ("ea11", "ea12") and r.path:
                ult[r.key] = r
    if "ea11:-:-:-:-" not in ult:
        pytest.skip("sem EA11 arquivado")
    cat = catalog_mod.build(
        env, UrlBuilder(env.base, env.ambiente, env.ciclo, env.allowed_hosts),
        p11.parse(arc.load(ult["ea11:-:-:-:-"])),
        {r.election: p12.parse(arc.load(r), r.election)
         for r in ult.values() if r.kind == "ea12"})
    col = Collector(cat, _client(env, cfg), arc, cfg)
    r = col.restaurar_do_arquivo()
    if not r["tokens"] and not r["pendentes"]:
        pytest.skip("arquivo sem EA15")
    assert r["etags"] > 1000
    assert r["tokens"] / (r["tokens"] + r["pendentes"]) > 0.9
    # O que foi restaurado virou estado de verdade no cliente.
    assert sum(1 for s in col.client.state.values() if s.etag) == r["etags"]


# ------------------------------------------------------------ finalizados ---

def _coletor_minimo(pular: bool = True):
    """Coletor com um catálogo de mentira: um total de UF e um município."""
    import asyncio
    from types import SimpleNamespace
    from obs2026.tse.collector import Collector

    uf = Target("ea20", "https://x/pa-c0003", election="21272", cargo="3",
                uf="pa", abr="uf")
    mu = Target("ea20", "https://x/pa04278-c0003", election="21272",
                cargo="3", uf="pa", mun="04278", abr="mu")
    cat = SimpleNamespace(control_targets=lambda: [uf],
                          result_targets=lambda: [mu], contests=[])
    cfg = SimpleNamespace(aging_boost_per_minute=0.15, control_weight=1e6,
                          reconcile_interval_s=5.0, pular_finalizados=pular)
    col = Collector(cat, client=None, archive=None, cfg=cfg)
    return col, uf, mu, asyncio


def test_finalizado_sai_do_controle_e_da_varredura():
    col, uf, mu, asyncio = _coletor_minimo()
    col.finalizados |= {uf.key, mu.key}
    asyncio.run(col.cycle_control())
    asyncio.run(col.cycle_reconcile(tick_s=5.0))
    assert len(col.queue) == 0
    assert col.stats.finalizados_pulados == 2


def test_re_totalizacao_reabre_o_total():
    """Finalizado não é imutável: se a assinatura da UF muda no EA14/EA15, o
    total volta a ser buscado."""
    col, uf, mu, asyncio = _coletor_minimo()
    col.finalizados.add(uf.key)
    col._reabrir_totais("21272", "21272:uf:pa")
    assert uf.key not in col.finalizados
    asyncio.run(col.cycle_control())
    assert len(col.queue) == 1
    assert col.stats.finalizados_reabertos == 1


def test_chave_desligada_busca_tudo():
    col, uf, mu, asyncio = _coletor_minimo(pular=False)
    col.finalizados |= {uf.key, mu.key}
    asyncio.run(col.cycle_control())
    asyncio.run(col.cycle_reconcile(tick_s=5.0))
    assert len(col.queue) == 2 and col.stats.finalizados_pulados == 0


def test_versao_antiga_nao_remarca_o_reaberto(monkeypatch):
    """O EA14/EA15 pode chegar antes do EA20 regenerado.

    Reaberto por uma totalização às 20h, o alvo busca o EA20 e recebe ainda
    a versão das 19h, que diz `and: f`. Ela não pode remarcá-lo como
    finalizado: a versão nova, quando saísse, nunca seria buscada. Só um
    EA20 gerado depois das 20h pode.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from obs2026.tse import collector as mod

    col, uf, mu, asyncio = _coletor_minimo()
    col.finalizados.add(mu.key)
    as20 = datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)
    col._reabrir(mu.key, as20)
    assert mu.key not in col.finalizados

    gerado = {"quando": datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)}

    async def capturar(t):
        return SimpleNamespace(), {"qualquer": 1}, None
    monkeypatch.setattr(col, "_capture", capturar)
    monkeypatch.setattr(mod.ea20_parser, "parse", lambda p: SimpleNamespace(
        andamento="f", generated_at=gerado["quando"]))

    asyncio.run(col._consumir(mu))              # versão antiga
    assert mu.key not in col.finalizados

    gerado["quando"] = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    asyncio.run(col._consumir(mu))              # versão nova
    assert mu.key in col.finalizados
