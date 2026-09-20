"""A captura é o que sai do sistema para um grupo de WhatsApp.

Uma imagem encaminhada perde o contexto no primeiro repasse. O que ela
afirma tem que estar certo dentro dela — e o que ela não pode afirmar não
pode aparecer nem por acidente.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs2026 import captura as C


def test_slug_nao_perde_o_titulo_por_causa_de_acento():
    """`fold` devolve maiúsculas; o slug precisa lidar com isso.

    Sem o `.lower()` todo arquivo se chamaria `tela.png`, e três telas
    capturadas no mesmo segundo se sobrescreveriam.
    """
    assert C._slug("Presidente — Brasil") == "presidente-brasil"
    assert C._slug("Presidente — recorte de Belém") == \
        "presidente-recorte-de-belem"
    assert C._slug("Governador — PA") == "governador-pa"
    assert C._slug("") == "tela"


def test_numero_sai_no_formato_brasileiro_e_nd_nao_vira_zero():
    """`None` é 'não medido'; zero é 'medido e deu zero'. §9."""
    assert C._num(1234.5) == "1.234,5"
    assert C._num(8.7012, 2) == "8,70"
    assert C._num(None) == "n/d"
    assert C._num(None, 2) == "n/d"


def test_recorte_municipal_carrega_o_aviso_do_paragrafo_3():
    """Liderar um município não é ser eleito por ele — e isso não é opção."""
    t = C._tela_avulsa("1", "pa", "04278", "definicao")
    assert t["aviso_recorte"] is not None
    assert "não é ser eleito por aqui" in t["aviso_recorte"]
    assert t["municipio"] == "04278"

    nacional = C._tela_avulsa("1", None, None, "definicao")
    assert nacional["aviso_recorte"] is None


def test_municipio_por_nome_e_recusado():
    """Nome de município não é chave: PAU D'ARCO existe em PA e em TO."""
    with pytest.raises(C.CapturaError, match="código TSE"):
        C._tela_avulsa("1", "pa", "Belém", "definicao")


def test_municipio_sem_uf_e_recusado():
    """O código TSE é único por UF, não no país."""
    with pytest.raises(C.CapturaError, match="exige --uf"):
        C._tela_avulsa("1", None, "04278", "definicao")


def test_cargo_de_uf_sem_uf_e_recusado():
    with pytest.raises(C.CapturaError, match="por UF"):
        C._tela_avulsa("3", None, None, "definicao")


def test_cargo_desconhecido_e_recusado():
    with pytest.raises(C.CapturaError, match="desconhecido"):
        C._tela_avulsa("9", "pa", None, "definicao")


def test_pagina_que_nao_ficou_pronta_nao_deixa_arquivo(tmp_path,
                                                       monkeypatch):
    """Meia imagem em disco é pior que nenhuma.

    O operador varre a pasta e manda o que estiver lá; um PNG truncado com
    nome plausível seria enviado sem ninguém perceber.
    """
    destino = tmp_path / "saida.png"
    destino.write_bytes(b"lixo de uma tentativa anterior")

    class Falsa:
        stdout = b'<html data-pronto="0"></html>'
        stderr = b""

    monkeypatch.setattr(C.subprocess, "run", lambda *a, **k: Falsa())
    with pytest.raises(C.CapturaError):
        C._disparar(Path("chrome.exe"), "http://x/telao", destino, 800, 600)
    assert not destino.exists()


def test_tela_com_erro_de_render_diz_o_motivo(tmp_path, monkeypatch):
    class Falsa:
        stdout = (b'<html data-pronto="erro"><div class="vazio">'
                  b'sem dados para este recorte</div></html>')
        stderr = b""

    monkeypatch.setattr(C.subprocess, "run", lambda *a, **k: Falsa())
    destino = tmp_path / "saida.png"
    with pytest.raises(C.CapturaError, match="sem dados para este recorte"):
        C._disparar(Path("chrome.exe"), "http://x/telao", destino, 800, 600)


def test_pronto_sem_imagem_tambem_falha(tmp_path, monkeypatch):
    """Dizer que está pronto e não gravar nada é falha, não sucesso."""
    class Falsa:
        stdout = b'<html data-pronto="1"></html>'
        stderr = b""

    monkeypatch.setattr(C.subprocess, "run", lambda *a, **k: Falsa())
    with pytest.raises(C.CapturaError, match="nenhuma imagem"):
        C._disparar(Path("chrome.exe"), "http://x/telao",
                    tmp_path / "nao-existe.png", 800, 600)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _Cli:
    """httpx.Client de mentira, devolvendo payloads por rota."""

    def __init__(self, rotas):
        self._r = rotas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, rota, params=None):
        return _Resp(self._r[rota])


def _rotas(publicavel: bool) -> dict:
    return {
        "/api/v1/results": {
            "dados": [{"eleitorado": {"total": 1000, "apurado": 400}}],
            "source_generated_at": "2026-10-04T19:40:00-03:00",
        },
        "/api/v1/candidates": {"dados": [{"candidatos": [
            {"oficial": {"sqcand": "a", "nome_urna": "FULANO",
                         "partido": "PX", "votos": 120, "pct": 60.0},
             "enriquecimento": None},
        ]}]},
        "/api/v1/projection": {
            "dados": [{"sqcand": "a", "projecao": {
                "pct_mediana": 58.4, "pct_q100": 56.1, "pct_q900": 60.7}}],
            "publicavel": publicavel, "modelo": "m0", "n_sims": 1200,
        },
    }


def test_legenda_nao_publica_projecao_sem_o_gate(monkeypatch):
    """O texto circula sozinho; 'cenário exploratório' some no repasse.

    Na tela o rótulo acompanha o número. Num grupo de WhatsApp a mensagem é
    encaminhada e o rótulo fica para trás — sobra o número, lido como
    previsão. Por isso, sem gate, número projetado não entra no texto.
    """
    tela = {"cargo": "1", "uf": None, "municipio": None,
            "titulo": "Presidente — Brasil", "aviso_recorte": None}

    monkeypatch.setattr(C.httpx, "Client",
                        lambda **k: _Cli(_rotas(publicavel=False)))
    txt = C._legenda("http://x", tela)
    assert "58,4" not in txt
    assert "gate estatístico não passou" in txt
    assert "60,00%" in txt          # o apurado continua lá

    monkeypatch.setattr(C.httpx, "Client",
                        lambda **k: _Cli(_rotas(publicavel=True)))
    txt = C._legenda("http://x", tela)
    assert "58,4" in txt
    assert "56,1–60,7" in txt


def test_legenda_sempre_diz_que_o_numero_nao_e_oficial(monkeypatch):
    monkeypatch.setattr(C.httpx, "Client",
                        lambda **k: _Cli(_rotas(publicavel=True)))
    txt = C._legenda("http://x", {
        "cargo": "1", "uf": None, "municipio": None,
        "titulo": "Presidente — Brasil", "aviso_recorte": None})
    assert "não oficial" in txt
    assert "TSE" in txt
