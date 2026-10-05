"""Homologação do gate estatístico: o ato que libera probabilidade de vitória.

`config/thresholds.yaml` com `homologated: true` faz o motor publicar
probabilidades (§4.7). Isso só pode acontecer com evidência: o gate do §11.2
rodado sobre eleição real (`scripts/gate2022.py`) e aprovado nos dois
critérios — vencer o M0 no erro mediano em toda rodada e cobrir o intervalo
de 80% dentro da tolerância.

Não há atalho de propósito: se o gate não passou, `homologar` recusa, e o
produto segue em cenário exploratório — com os intervalos no ar e sem
probabilidade. O que se assina é a evidência, que fica gravada no próprio
arquivo, com nome e data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CONFIG_DIR, DATA_DIR

BRT = ZoneInfo("America/Sao_Paulo")
ARQUIVO = CONFIG_DIR / "thresholds.yaml"
EVIDENCIA = DATA_DIR / "exports" / "ensaio2022" / "gate2022.json"


class HomologacaoError(RuntimeError):
    pass


def ler_evidencia(caminho: Path | None = None,
                  modelo: str | None = None) -> dict:
    """Evidência do gate para o modelo em produção (ou o pedido): o gate
    compara vários modelos contra o M0 e homologa-se o que vai ao ar."""
    p = caminho or EVIDENCIA
    if not p.exists():
        raise HomologacaoError(
            f"sem evidência: {p} não existe. Rode `python scripts/"
            "gate2022.py` antes de homologar.")
    d = json.loads(p.read_text(encoding="utf-8"))
    if modelo is None:
        from .operacao import modelo_ativo
        modelo = modelo_ativo()
    por = d.get("por_modelo")
    if por is not None:
        if modelo not in por:
            raise HomologacaoError(
                f"o gate não avaliou o modelo em produção ({modelo}); rode "
                f"`python scripts/gate2022.py --modelos {modelo}`")
        d = por[modelo]
    elif modelo != "m2":
        raise HomologacaoError(f"a evidência é só do M2; o modelo em "
                               f"produção é {modelo}")
    if (d.get("parametros") or {}).get("parcial"):
        raise HomologacaoError("a evidência é de um teste parcial (--so-uf); "
                               "rode o gate completo")
    return d


def motivos_de_recusa(ev: dict) -> list[str]:
    m = []
    if not ev.get("passa_erro_mediano"):
        n = len(ev.get("derrotas_m2") or [])
        mod = str(ev.get("modelo", "m2")).upper()
        m.append(f"o {mod} não vence o M0 no erro mediano em {n} rodada(s)")
    if not ev.get("passa_cobertura"):
        m.append("cobertura do intervalo de 80% fora da tolerância: "
                 f"{ev.get('cobertura80_m2_pct')} "
                 f"(±{ev.get('tolerancia_pp')} pp)")
    return m


def _bloco_evidencia(ev: dict, assinante: str, quando: datetime) -> str:
    cob = ev["cobertura80_m2_pct"]
    return (
        "\n# --- evidência da homologação (gerado por `obs2026 homologar`) ---\n"
        "evidencia:\n"
        f"  relatorio: data/exports/ensaio2022/gate2022.md\n"
        f"  gerado_em: \"{ev.get('gerado_em')}\"\n"
        f"  disputas: {ev.get('disputas')}\n"
        f"  modelo: {ev.get('modelo', 'm2')}\n"
        f"  erro_mediano_vence_m0_em_todas_as_rodadas: "
        f"{str(ev.get('passa_erro_mediano')).lower()}\n"
        f"  cobertura80_ordem_real_pct: {cob.get('real')}\n"
        f"  cobertura80_ordem_aleatoria_pct: {cob.get('aleatoria')}\n"
        f"  assinado_por: \"{assinante}\"\n"
        f"  assinado_em: \"{quando.isoformat()}\"\n")


def homologar(assinante: str, *, caminho: Path | None = None,
              evidencia: Path | None = None,
              quando: datetime | None = None) -> dict:
    assinante = " ".join((assinante or "").split())
    if len(assinante) < 3:
        raise HomologacaoError("assinante precisa de nome identificável")
    ev = ler_evidencia(evidencia)
    recusa = motivos_de_recusa(ev)
    if recusa:
        raise HomologacaoError("homologação recusada: " + "; ".join(recusa)
                               + ". O produto segue em cenário exploratório.")
    p = caminho or ARQUIVO
    texto = p.read_text(encoding="utf-8")
    agora = quando or datetime.now(BRT)
    nome = '"' + assinante.replace('"', "'") + '"'
    novo = re.sub(r"(?m)^homologated:.*$", "homologated: true", texto, 1)
    novo = re.sub(r"(?m)^signed_by:.*$", f"signed_by: {nome}", novo, 1)
    novo = re.sub(r"(?m)^signed_at:.*$",
                  f'signed_at: "{agora.isoformat()}"', novo, 1)
    novo = re.sub(r"(?s)\n# --- evidência da homologação.*\Z", "", novo)
    novo = novo.rstrip("\n") + "\n" + _bloco_evidencia(ev, assinante, agora)
    _gravar(p, novo, agora)
    return {"assinante": assinante, "assinado_em": agora.isoformat(),
            "cobertura": ev["cobertura80_m2_pct"]}


def revogar(motivo: str, *, caminho: Path | None = None) -> dict:
    """Volta ao cenário exploratório — para a noite, se algo parecer
    errado. A evidência fica no arquivo; só a chave desliga."""
    p = caminho or ARQUIVO
    agora = datetime.now(BRT)
    texto = p.read_text(encoding="utf-8")
    novo = re.sub(r"(?m)^homologated:.*$", "homologated: false", texto, 1)
    novo = novo.rstrip("\n") + (
        f"\n# revogada em {agora:%d/%m/%Y %H:%M}: "
        f"{' '.join((motivo or 'sem motivo informado').split())}\n")
    _gravar(p, novo, agora)
    return {"revogada_em": agora.isoformat()}


def _gravar(p: Path, texto: str, agora: datetime) -> None:
    pasta = p.parent / "thresholds.anterior"
    pasta.mkdir(exist_ok=True)
    shutil.copy2(p, pasta / f"thresholds-{agora:%Y%m%d-%H%M%S}.yaml")
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, p)
