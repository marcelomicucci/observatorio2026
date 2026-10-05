"""Câmara dos Deputados: só o resultado que o TSE divulga, sem projeção.

Deputado federal ficou fora do motor de projeção de propósito (§3). O que
este módulo faz é o que foi pedido em 21/09/2026: **baixar o resultado** e
contar as cadeiras por partido — 513, distribuídas por UF.

## De onde vem

O coletor busca só os 27 arquivos de UF do cargo 6 (`{uf}-c0006-e...-u.json`)
numa cadência própria e lenta — nenhum arquivo municipal, que seriam 5.570
buscas a mais disputando a cota com os totais de presidente, governador e
senador. Os arquivos vão para o arquivo bruto como qualquer outro; este
módulo lê o manifesto de forma incremental e guarda o mais recente de cada UF.

Nada disto passa pelo banco nem pelo motor: deputado não tem projeção, não
tem "matematicamente definido" à moda majoritária, e misturá-lo nas
disputas faria o motor tentar projetá-lo.

## O que conta como cadeira

Conta a situação oficial do candidato (`st`) quando começa por "Eleito"
("Eleito por QP", "Eleito por média"). Não se calcula quociente aqui: a
distribuição de sobras tem regra própria e disputas judiciais recentes, e
refazer a conta do TSE seria trocar o resultado oficial por uma estimativa
nossa. Enquanto a UF não fecha, a situação pode mudar, e a tela diz isso.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..storage.raw import RawArchive, SnapshotRef
from ..tse.parsers.common import fold, parse_dt

CARGO_DEP_FEDERAL = "6"
#: Assembleias legislativas (05/10/2026): deputado estadual é o cargo 7; no
#: DF a casa é a Câmara Legislativa e o cargo é o 8, deputado distrital.
CARGOS_ASSEMBLEIA = ("7", "8")
TOTAL_CADEIRAS = 513


def _int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0


def eleito(st: str | None) -> bool:
    return fold(st or "").startswith("ELEITO")


def resumir_uf(payload: dict) -> dict:
    """Uma UF: vagas, andamento e eleitos por partido, literal do TSE."""
    carg = (payload.get("carg") or [{}])[0]
    e = payload.get("e") or {}
    s = payload.get("s") or {}
    te, est = _int(e.get("te")), _int(e.get("est"))
    eleitos: list[dict] = []
    vag_agr = 0
    for agr in carg.get("agr") or []:
        vag_agr += _int(agr.get("vag"))
        for par in agr.get("par") or []:
            for c in par.get("cand") or []:
                if not eleito(c.get("st")):
                    continue
                eleitos.append({
                    "nome": c.get("nmu") or c.get("nm"),
                    "numero": str(c.get("n") or ""),
                    "situacao": c.get("st"),
                    "votos": _int(c.get("vap")),
                    "partido_numero": str(par.get("n") or ""),
                    "partido_sigla": par.get("sg") or "",
                    "agremiacao": agr.get("nm") or None,
                })
    # Listas (partido isolado, federação ou coligação) com as vagas que o
    # próprio TSE atribui durante a apuração (`agr.vag`). Quem ocupa essas
    # vagas agora — os mais votados da lista — é leitura provisória da regra
    # da lista aberta, marcada como tal; o eleito é o que o TSE marcar.
    qe = _int(carg.get("qe"))
    listas = []
    for agr in carg.get("agr") or []:
        cands = []
        legenda = 0
        partidos = []
        for par in agr.get("par") or []:
            legenda += _int(par.get("tvtl"))
            partidos.append({"numero": str(par.get("n") or ""),
                             "sigla": par.get("sg") or ""})
            for c in par.get("cand") or []:
                if fold(c.get("dvt") or "Válido").startswith("ANULADO"):
                    continue
                cands.append({
                    "nome": c.get("nmu") or c.get("nm"),
                    "numero": str(c.get("n") or ""),
                    "votos": _int(c.get("vap")),
                    "situacao": c.get("st") or None,
                    "eleito_tse": eleito(c.get("st")),
                    "partido_numero": str(par.get("n") or ""),
                    "partido_sigla": par.get("sg") or "",
                })
        cands.sort(key=lambda x: -x["votos"])
        vag = _int(agr.get("vag"))
        for i, c in enumerate(cands):
            c["posicao"] = i + 1
            c["dentro_das_vagas"] = i < vag
            # Cláusula individual: para ocupar vaga, o candidato precisa de
            # ao menos 10% do quociente eleitoral.
            c["abaixo_10pct_qe"] = bool(qe) and c["votos"] < 0.1 * qe
        nominais = sum(c["votos"] for c in cands)
        listas.append({
            "nome": agr.get("nm") or "",
            "tipo": {"i": "partido", "f": "federação",
                     "c": "coligação"}.get(agr.get("tp"), agr.get("tp")),
            "vagas_tse": vag, "votos_nominais": nominais,
            "votos_legenda": legenda, "votos": nominais + legenda,
            "partidos": partidos,
            # Os que estão nas vagas e os dois seguintes da fila.
            "candidatos": cands[:vag + 2],
        })
    listas.sort(key=lambda x: (-x["vagas_tse"], -x["votos"]))
    gerado = parse_dt(payload.get("dg"), payload.get("hg"))
    return {
        "listas": listas,
        "uf": str(payload.get("cdabr") or "").lower(),
        "vagas": _int(carg.get("nv")),
        "quociente_eleitoral": _int(carg.get("qe")) or None,
        "andamento": payload.get("and"),
        "totalizacao_final": payload.get("tf") == "s",
        "apurado_pct": (100.0 * est / te) if te else None,
        "secoes_pct": ((100.0 * _int(s.get("st")) / _int(s.get("ts")))
                       if _int(s.get("ts")) else None),
        "eleitos": eleitos,
        # Vagas distribuídas pelo TSE às agremiações, para conferência com
        # a contagem por candidato. Divergência vira aviso, não correção.
        "vagas_agremiacoes": vag_agr,
        "gerado_em": gerado.isoformat() if gerado else None,
    }


@dataclass
class LeitorCamara:
    """Lê o manifesto incrementalmente e guarda o arquivo mais novo por UF.

    O manifesto cresce a noite toda; reler do começo a cada pedido do telão
    seria ler dezenas de milhares de linhas para achar 27. Guarda o
    deslocamento de cada dia e continua de onde parou."""

    archive: RawArchive
    #: Cargos lidos. O padrão é a Câmara; `CARGOS_ASSEMBLEIA` lê as 27
    #: assembleias com o mesmo leitor — o arquivo tem o mesmo formato.
    cargos: tuple[str, ...] = (CARGO_DEP_FEDERAL,)
    _pos: dict[str, int] = field(default_factory=dict)
    _ultimo: dict[str, SnapshotRef] = field(default_factory=dict)
    _resumo: dict[str, tuple[str, dict]] = field(default_factory=dict)
    _trava: threading.Lock = field(default_factory=threading.Lock)

    def _avancar(self) -> None:
        for dia in self.archive.days():
            for ref, fim in self.archive.manifest_desde(
                    dia, self._pos.get(dia, 0)):
                self._pos[dia] = fim
                if (ref.kind != "ea20" or ref.cargo not in self.cargos
                        or not ref.uf or ref.mun or not ref.path
                        or ref.status != 200):
                    continue
                antes = self._ultimo.get(ref.uf)
                chave = ref.source_generated_at or ref.captured_at
                if antes is None or chave >= (antes.source_generated_at
                                              or antes.captured_at):
                    self._ultimo[ref.uf] = ref

    def por_uf(self) -> dict[str, dict]:
        with self._trava:
            self._avancar()
            out = {}
            for uf, ref in self._ultimo.items():
                memo = self._resumo.get(uf)
                if memo and memo[0] == ref.sha256:
                    out[uf] = memo[1]
                    continue
                try:
                    r = resumir_uf(self.archive.load(ref))
                except Exception:              # noqa: BLE001
                    continue
                r["capturado_em"] = ref.captured_at
                self._resumo[uf] = (ref.sha256 or "", r)
                out[uf] = r
            return out


def composicao(por_uf: dict[str, dict], resolver, ideologia) -> dict:
    """Soma nacional. `resolver(numero, sigla)` dá o partido do cadastro;
    `ideologia(partido)` dá a classificação ou None."""
    partidos: dict[str, dict] = {}
    ufs = []
    eleitos_total = 0
    for uf in sorted(por_uf):
        r = por_uf[uf]
        ufs.append({k: r[k] for k in (
            "uf", "vagas", "andamento", "totalizacao_final", "apurado_pct",
            "vagas_agremiacoes", "gerado_em", "capturado_em")
            if k in r} | {"eleitos": len(r["eleitos"])})
        for c in r["eleitos"]:
            p = resolver(c["partido_numero"], c["partido_sigla"])
            k = p.numero if p.cadastrado else f"?{p.sigla}"
            d = partidos.setdefault(k, {"partido": p.as_dict(), "cadeiras": 0,
                                        "ideologia": ideologia(p),
                                        "por_uf": {}})
            d["cadeiras"] += 1
            d["por_uf"][uf] = d["por_uf"].get(uf, 0) + 1
            eleitos_total += 1
    ult = [r.get("gerado_em") for r in por_uf.values() if r.get("gerado_em")]
    return {
        "em_formacao": em_formacao(por_uf, resolver, ideologia),
        "total": TOTAL_CADEIRAS,
        "eleitos": eleitos_total,
        "sem_eleito": TOTAL_CADEIRAS - eleitos_total,
        "ufs_com_dado": len(por_uf),
        "ufs_encerradas": sum(1 for r in por_uf.values()
                              if r.get("andamento") == "f"),
        "partidos": sorted(partidos.values(),
                           key=lambda d: (-d["cadeiras"],
                                          d["partido"]["sigla"])),
        "ufs": ufs,
        "fonte_gerada_em": max(ult) if ult else None,
        "vagas_divergentes": [u["uf"] for u in ufs
                              if u.get("andamento") == "f"
                              and u["eleitos"] != u["vagas"]],
    }


def em_formacao(por_uf: dict[str, dict], resolver, ideologia) -> dict:
    """A Câmara se formando: onde o TSE já marcou eleitos, eles; onde ainda
    não marcou, as vagas que o TSE já distribuiu às listas (`agr.vag`),
    ocupadas pelos mais votados de cada lista. É leitura PROVISÓRIA — sem
    projeção: a distribuição é a do TSE com os votos contados até agora, e
    muda a cada boletim. O partido é o do candidato que ocupa a vaga (numa
    federação, a vaga é da federação; quem a leva é o partido do mais
    votado)."""
    partidos: dict[str, dict] = {}
    n_eleito = n_prov = 0
    ufs_prov = []
    for uf in sorted(por_uf):
        r = por_uf[uf]
        if r["eleitos"]:
            ocupantes = [(c, "eleito") for c in r["eleitos"]]
        else:
            ocupantes = [(c, "provisorio") for x in r.get("listas") or []
                         for c in x["candidatos"] if c["dentro_das_vagas"]]
            if ocupantes:
                ufs_prov.append(uf)
        for c, tipo in ocupantes:
            p = resolver(c["partido_numero"], c["partido_sigla"])
            k = p.numero if p.cadastrado else f"?{p.sigla}"
            d = partidos.setdefault(k, {"partido": p.as_dict(),
                                        "ideologia": ideologia(p),
                                        "eleito": 0, "provisorio": 0,
                                        "total": 0})
            d[tipo] += 1
            d["total"] += 1
            if tipo == "eleito":
                n_eleito += 1
            else:
                n_prov += 1
    return {
        "eleito": n_eleito, "provisorio": n_prov,
        "sem_dado": max(0, TOTAL_CADEIRAS - n_eleito - n_prov),
        # Soma das vagas que os arquivos das UFs declaram (`nv`). Diferente
        # de 513 só em dado de teste — o simulado do TSE soma 532 — e vira
        # aviso na tela.
        "vagas_declaradas": sum(r.get("vagas") or 0 for r in por_uf.values()),
        "ufs_provisorias": ufs_prov,
        "partidos": sorted(partidos.values(),
                           key=lambda d: (-d["total"], -d["eleito"],
                                          d["partido"]["sigla"])),
        "nota": ("Onde o TSE ainda não marcou eleitos, as vagas já "
                 "distribuídas pelo TSE às listas com os votos contados até "
                 "agora, ocupadas pelos mais votados de cada lista. "
                 "Provisório: muda a cada boletim. Não é projeção."),
    }


# ------------------------------------------------------------ assembleias ---

BLOCOS = ("esquerda", "centro", "direita", "sem_classificacao")


def ocupantes(r: dict) -> list[tuple[dict, str]]:
    """Quem ocupa as cadeiras de uma casa numa UF: os eleitos do TSE ou,
    enquanto ele não os marca, os mais votados de cada lista dentro das
    vagas que o próprio TSE distribuiu — provisório, sem projeção."""
    if r["eleitos"]:
        return [(c, "eleito") for c in r["eleitos"]]
    return [(c, "provisorio") for x in r.get("listas") or []
            for c in x["candidatos"]
            if c["dentro_das_vagas"] and c["votos"] > 0]


def assembleia(r: dict, resolver, ideologia) -> dict:
    """Composição de UMA assembleia: cadeiras (para o hemiciclo), bancadas e
    blocos. `ideologia(partido)` devolve a classificação ou None."""
    vagas = r.get("vagas") or 0
    partidos: dict[str, dict] = {}
    blocos = {b: {"total": 0} for b in BLOCOS}
    cadeiras = []
    for c, tipo in sorted(ocupantes(r), key=lambda x: -x[0]["votos"]):
        p = resolver(c["partido_numero"], c["partido_sigla"])
        ideo = ideologia(p)
        k = p.numero if p.cadastrado else f"?{p.sigla}"
        d = partidos.setdefault(k, {"partido": p.as_dict(), "ideologia": ideo,
                                    "eleito": 0, "provisorio": 0, "total": 0})
        d[tipo] += 1
        d["total"] += 1
        blocos[(ideo or {}).get("bloco") or "sem_classificacao"]["total"] += 1
        cadeiras.append({
            "partido": p.as_dict(), "ideologia": ideo,
            "estado": "eleito" if tipo == "eleito" else "liderando",
            "nome": c["nome"], "votos": c["votos"]})
    ocupadas = len(cadeiras)
    for _ in range(max(0, vagas - ocupadas)):
        cadeiras.append({"partido": None, "ideologia": None,
                         "estado": "aguardando"})
    return {
        "uf": r.get("uf"), "vagas": vagas,
        "maioria": vagas // 2 + 1 if vagas else None,
        "eleitos": sum(d["eleito"] for d in partidos.values()),
        "provisorios": sum(d["provisorio"] for d in partidos.values()),
        "andamento": r.get("andamento"),
        "totalizacao_final": r.get("totalizacao_final"),
        "apurado_pct": r.get("apurado_pct"),
        "gerado_em": r.get("gerado_em"),
        "cadeiras": cadeiras[:vagas] if vagas else cadeiras,
        "por_bloco": blocos,
        "partidos": sorted(partidos.values(),
                           key=lambda d: (-d["total"], d["partido"]["sigla"])),
    }


def assembleias(por_uf: dict[str, dict], resolver, ideologia) -> dict:
    """As 27 assembleias: o quadro de cada uma (sem os nomes) e a soma do
    país por partido e por bloco."""
    ufs = []
    partidos: dict[str, dict] = {}
    blocos = {b: {"total": 0} for b in BLOCOS}
    for uf in sorted(por_uf):
        a = assembleia(por_uf[uf], resolver, ideologia)
        for b in BLOCOS:
            blocos[b]["total"] += a["por_bloco"][b]["total"]
        for d in a["partidos"]:
            k = d["partido"]["numero"] or d["partido"]["sigla"]
            t = partidos.setdefault(k, {"partido": d["partido"],
                                        "ideologia": d["ideologia"],
                                        "eleito": 0, "provisorio": 0,
                                        "total": 0, "assembleias": 0})
            for campo in ("eleito", "provisorio", "total"):
                t[campo] += d[campo]
            t["assembleias"] += 1
        maior = max((b for b in ("esquerda", "centro", "direita")),
                    key=lambda b: a["por_bloco"][b]["total"])
        ufs.append({k: a[k] for k in (
            "uf", "vagas", "maioria", "eleitos", "provisorios", "andamento",
            "apurado_pct", "por_bloco")} | {
            "maior_bancada": a["partidos"][0] if a["partidos"] else None,
            "bloco_maior": maior if a["por_bloco"][maior]["total"] else None,
            "bloco_com_maioria": next(
                (b for b in ("esquerda", "centro", "direita")
                 if a["maioria"] and a["por_bloco"][b]["total"]
                 >= a["maioria"]), None)})
    ult = [r.get("gerado_em") for r in por_uf.values() if r.get("gerado_em")]
    return {
        "total": sum(u["vagas"] for u in ufs),
        "eleitos": sum(u["eleitos"] for u in ufs),
        "provisorios": sum(u["provisorios"] for u in ufs),
        "ufs_com_dado": len(ufs),
        "ufs_encerradas": sum(1 for u in ufs if u["andamento"] == "f"),
        "por_bloco": blocos,
        "partidos": sorted(partidos.values(),
                           key=lambda d: (-d["total"], d["partido"]["sigla"])),
        "ufs": ufs,
        "fonte_gerada_em": max(ult) if ult else None,
    }
