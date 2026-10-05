"""Composição do Senado: as 27 cadeiras que não estão em disputa em 2026.

O §6 pede o Senado completo: 54 vagas em renovação **mais** os 27 mandatos
remanescentes, com data da composição, partido e substituições. As 54 saem
da apuração; as 27 remanescentes são dos senadores eleitos em 2022, com
mandato até 31/01/2031, e vêm dos dados abertos do Senado Federal.

## Por que em disco, e baixado antes

A lista é buscada por `obs2026 preparar-senado` (também chamada por
`obs2026 preparar`) e gravada em `data/institucional/`. Na noite da apuração
nada aqui toca a rede: fora o TSE, o painel não depende de terceiro
([[observatorio-2026-roda-local]]).

## Partido: o de hoje, não o da eleição

O partido registrado é o que o Senado informa **na data da consulta**, que
fica gravada. Senador pode trocar de partido durante o mandato, então o
partido de 2022 diria menos sobre o Senado que toma posse em 2027. A data
aparece na tela junto com a composição.

## Suplente em exercício

Quem ocupa a cadeira hoje pode ser suplente (titular licenciado para ser
ministro, por exemplo). Conta o ocupante, com nota: é ele quem vota hoje, e
o titular pode voltar — a tela diz isso em vez de esconder.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR

FONTE = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
PASTA = DATA_DIR / "institucional"
ARQUIVO = PASTA / "senado-atual.json"


class SenadoError(RuntimeError):
    pass


def _ano(data: str | None) -> int | None:
    try:
        return int(str(data)[:4])
    except (TypeError, ValueError):
        return None


def normalizar(bruto: dict) -> list[dict]:
    """Lista de ocupantes a partir do JSON do Senado. Literal: não deduz
    partido, não resolve sigla — isso é do cadastro de partidos."""
    try:
        ps = bruto["ListaParlamentarEmExercicio"]["Parlamentares"][
            "Parlamentar"]
    except (KeyError, TypeError) as exc:
        raise SenadoError("formato inesperado da lista do Senado") from exc
    if isinstance(ps, dict):
        ps = [ps]
    out = []
    for p in ps:
        ident = p.get("IdentificacaoParlamentar") or {}
        mand = p.get("Mandato") or {}
        l1 = mand.get("PrimeiraLegislaturaDoMandato") or {}
        l2 = mand.get("SegundaLegislaturaDoMandato") or {}
        participacao = str(mand.get("DescricaoParticipacao") or "").strip()
        out.append({
            "codigo": str(ident.get("CodigoParlamentar") or ""),
            "nome": str(ident.get("NomeParlamentar") or "").strip(),
            "nome_completo": str(ident.get("NomeCompletoParlamentar")
                                 or "").strip(),
            "uf": str(ident.get("UfParlamentar") or mand.get("UfParlamentar")
                      or "").strip().lower(),
            "partido_sigla": str(ident.get("SiglaPartidoParlamentar")
                                 or "").strip(),
            "participacao": participacao or None,
            "suplente_em_exercicio": bool(participacao)
                                     and "SUPLENTE" in participacao.upper(),
            "mandato_inicio": _ano(l1.get("DataInicio")),
            "mandato_fim": _ano(l2.get("DataFim") or l1.get("DataFim")),
        })
    return out


def baixar(timeout: float = 20.0) -> dict:
    """Busca a lista atual e grava em disco. Só para o preparo, nunca na
    noite da apuração."""
    import httpx

    r = httpx.get(FONTE, timeout=timeout,
                  headers={"Accept": "application/json",
                           "User-Agent": "observatorio2026 (preparo local)"})
    r.raise_for_status()
    bruto = r.json()
    ocupantes = normalizar(bruto)
    if len(ocupantes) != 81:
        raise SenadoError(
            f"o Senado listou {len(ocupantes)} senadores em exercício, não "
            "81; nada gravado — composição incompleta seria pior que a "
            "anterior")
    PASTA.mkdir(parents=True, exist_ok=True)
    corpo = r.content
    doc = {
        "fonte": FONTE,
        "obtido_em": datetime.now(timezone.utc).isoformat(),
        "sha256_bruto": hashlib.sha256(corpo).hexdigest(),
        "ocupantes": ocupantes,
    }
    (PASTA / "senado-atual.bruto.json").write_bytes(corpo)
    tmp = ARQUIVO.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(ARQUIVO)
    return doc


def carregar(caminho: Path | None = None) -> dict | None:
    p = caminho or ARQUIVO
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


#: Padrão de `remanescentes`: ler do disco. `None` explícito quer dizer
#: "não há composição" — não "vá procurar".
_DO_DISCO = object()


def remanescentes(ano_eleicao: int, doc=_DO_DISCO) -> dict:
    """Cadeiras cujo mandato continua depois da eleição.

    Em 2026 são as de mandato até 2031. O critério é o fim do mandato, não
    uma lista fixa: `mandato_fim > ano_eleicao + 1` (quem termina em
    janeiro de 2027 está em renovação)."""
    if doc is _DO_DISCO:
        doc = carregar()
    if doc is None:
        return {"estado": "indisponivel", "cadeiras": [],
                "motivo": ("composição atual do Senado não foi baixada; "
                           "rode `obs2026 preparar-senado` antes da noite"),
                "obtido_em": None, "fonte": FONTE}
    cad = [o for o in doc["ocupantes"]
           if (o.get("mandato_fim") or 0) > ano_eleicao + 1]
    return {"estado": "ok", "cadeiras": cad,
            "obtido_em": doc.get("obtido_em"), "fonte": doc.get("fonte"),
            "motivo": None}
