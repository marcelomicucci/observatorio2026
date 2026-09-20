"""Carga do arquivo bruto para o banco.

O banco é derivado. Este módulo é a única ponte, e ele é **reexecutável**:
rodar duas vezes sobre o mesmo bruto produz o mesmo estado, sem duplicar
snapshot nem voto. Isso é o que torna verdadeira a afirmação do §11.3 de que
o teste de recuperação "comprova restauração sem duplicar snapshots".

A ordem importa: catálogo primeiro (eleições, disputas, geografias), resultado
depois. Um EA20 de um município que o EA12 não conhece **não** cria geografia
nova em silêncio — vai para quality_flags, porque território inventado na
carga é a forma mais fácil de produzir dupla contagem.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import pending as pend
from ..quality.checks import check_ea20
from ..tse.catalog import Catalog
from ..tse.parsers.ea12 import Ea12
from ..tse.parsers.ea20 import Ea20
from .models import (Candidate, Contest, Election, Geography, ObservedResult,
                     SourceSnapshot, UnitTotals)
from .raw import SnapshotRef


def _get_or_create(s: Session, model, defaults: dict | None = None, **keys):
    obj = s.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if obj is not None:
        return obj, False
    obj = model(**keys, **(defaults or {}))
    s.add(obj)
    s.flush()
    return obj, True


def sync_catalog(s: Session, cat: Catalog) -> dict:
    """Grava eleições, disputas e geografias a partir do catálogo oficial."""
    env = cat.env
    stats = {"elections": 0, "contests": 0, "geographies": 0}

    for p in cat.ea11.pleitos:
        for e in p.eleicoes:
            if e.cd not in cat.ea12_por_eleicao:
                continue
            el, novo = _get_or_create(
                s, Election,
                defaults={
                    "official": env.official, "ciclo": p.ciclo,
                    "cd_pleito": p.cd, "cd_eleicao_turno2": e.cd_turno2,
                    "nome": e.nome,
                    "data": (datetime.combine(p.data, datetime.min.time(),
                                              tzinfo=timezone.utc)
                             if p.data else None),
                },
                environment=env.name, cd_eleicao=e.cd, turno=e.turno,
            )
            stats["elections"] += int(novo)

            for c in cat.contests:
                if c.election != e.cd:
                    continue
                _, novo = _get_or_create(
                    s, Contest,
                    defaults={
                        "cargo_nome": c.cargo_nome,
                        "abrangencia_decisoria": c.cargo.abrangencia_decisoria,
                        # Valor de partida; sobrescrito pelo `carg.nv` real
                        # do EA20 na primeira ingestão.
                        "vagas": c.cargo.expected_seats,
                        "inclui_exterior": c.cargo.includes_exterior,
                    },
                    election_id=el.id, cargo_cd=c.cargo.cd,
                )
                stats["contests"] += int(novo)

    ea12: Ea12 = next(iter(cat.ea12_por_eleicao.values()))
    _get_or_create(s, Geography,
                   defaults={"nome": "Brasil", "nome_fold": "BRASIL",
                             "cd_ibge": None},
                   environment=env.name, tipo="br", uf="br", cd_tse="br")
    stats["geographies"] += 1
    for uf in sorted({m.uf for m in ea12.municipios}):
        _get_or_create(s, Geography,
                       defaults={"nome": uf.upper(), "nome_fold": uf.upper(),
                                 "exterior": uf == "zz"},
                       environment=env.name, tipo="uf", uf=uf, cd_tse=uf)
        stats["geographies"] += 1
    for m in ea12.municipios:
        _, novo = _get_or_create(
            s, Geography,
            defaults={"cd_ibge": m.cd_ibge, "nome": m.nome,
                      "nome_fold": m.nome_fold, "capital": m.capital,
                      "exterior": m.is_exterior},
            environment=env.name, tipo="mu", uf=m.uf, cd_tse=m.cd,
        )
        stats["geographies"] += int(novo)

    s.commit()
    return stats


def _geography_for(s: Session, env_name: str, r: Ea20,
                   uf_hint: str | None) -> Geography | None:
    if r.tpabr == "br":
        keys = {"tipo": "br", "uf": "br", "cd_tse": "br"}
    elif r.tpabr == "uf":
        keys = {"tipo": "uf", "uf": r.cdabr.lower(), "cd_tse": r.cdabr.lower()}
    else:
        if not uf_hint:
            return None
        keys = {"tipo": "mu", "uf": uf_hint.lower(), "cd_tse": r.cdabr}
    return s.execute(
        select(Geography).filter_by(environment=env_name, **keys)
    ).scalar_one_or_none()


def ingest_ea20(s: Session, env, ref: SnapshotRef, r: Ea20) -> dict:
    """Ingere um EA20. Idempotente pela chave (sha256, url, captured_at)."""
    out = {"snapshot": 0, "candidates": 0, "results": 0, "flags": []}

    el = s.execute(select(Election).filter_by(
        environment=env.name, cd_eleicao=r.election, turno=r.turno
    )).scalar_one_or_none()
    if el is None:
        out["flags"].append(f"eleicao {r.election}/{r.turno} fora do catalogo")
        return out

    contest = s.execute(select(Contest).filter_by(
        election_id=el.id, cargo_cd=r.cargo.cd
    )).scalar_one_or_none()
    if contest is None:
        out["flags"].append(f"cargo {r.cargo.cd} fora do catalogo")
        return out
    # O número de vagas é do payload, não da configuração (§3).
    if contest.vagas != r.cargo.vagas:
        out["flags"].append(
            f"vagas divergentes: catalogo={contest.vagas} "
            f"payload={r.cargo.vagas} — payload prevalece"
        )
        contest.vagas = r.cargo.vagas

    geo = _geography_for(s, env.name, r, ref.uf)
    if geo is None:
        # Território desconhecido NÃO é criado aqui (§11.1: nenhuma dupla
        # contagem geográfica). Vai para quarentena.
        out["flags"].append(
            f"geografia desconhecida {r.tpabr}:{r.cdabr} (uf={ref.uf})"
        )
        return out

    snap = s.execute(select(SourceSnapshot).filter_by(
        environment=env.name, source_key=ref.key, sha256=ref.sha256,
        captured_at=datetime.fromisoformat(ref.captured_at),
    )).scalar_one_or_none()
    if snap is None:
        snap = SourceSnapshot(
            environment=env.name, kind=ref.kind, source_key=ref.key,
            url=ref.url, status=ref.status, sha256=ref.sha256,
            bytes_len=ref.bytes_len,
            source_generated_at=(
                datetime.fromisoformat(ref.source_generated_at)
                if ref.source_generated_at else None),
            captured_at=datetime.fromisoformat(ref.captured_at),
            idg=ref.idg, etag=ref.etag, parser_version=ref.parser_version,
            blob_path=ref.path,
        )
        s.add(snap)
        s.flush()
        out["snapshot"] = 1

    cand_ids: dict[str, int] = {}
    for c in r.cargo.candidatos:
        obj, novo = _get_or_create(
            s, Candidate,
            defaults={
                "numero": c.numero, "nome": c.nome, "nome_urna": c.nome_urna,
                "partido_numero": c.partido_numero,
                "partido_sigla": c.partido_sigla, "agr_numero": c.agr_numero,
                "agr_nome": c.agr_nome, "agr_tipo": c.agr_tipo,
                "agr_composicao": c.agr_composicao, "dvt": c.dvt,
                "situacao": c.situacao,
                "eleito": c.eleito_segundo_tse,
            },
            contest_id=contest.id, sqcand=c.sqcand,
        )
        out["candidates"] += int(novo)
        cand_ids[c.sqcand] = obj.id
        # Situação e destinação podem mudar (sub judice, substituição).
        if not novo:
            obj.dvt, obj.situacao = c.dvt, c.situacao
            # `eleito` só vale no arquivo da abrangência que decide (§3): o
            # arquivo municipal replica o status da UF e não pode defini-lo.
            if r.tpabr == contest.abrangencia_decisoria:
                obj.eleito = c.eleito_segundo_tse

    findings = check_ea20(r, expected_seats=contest.vagas,
                          official_env=env.official)
    p = pend.of(r)
    nominal = sum(c.votos for c in r.cargo.candidatos)

    existente = s.execute(select(UnitTotals).filter_by(
        snapshot_id=snap.id, contest_id=contest.id, geography_id=geo.id
    )).scalar_one_or_none()
    if existente is None:
        e, sec, v = r.electorate, r.sections, r.votes
        s.add(UnitTotals(
            snapshot_id=snap.id, contest_id=contest.id, geography_id=geo.id,
            andamento=r.andamento, totalizado_em=r.totalized_at,
            secoes_total=sec.ts, secoes_totalizadas=sec.st,
            secoes_instaladas=sec.si, secoes_apuradas=sec.sa,
            eleitorado_total=e.te, eleitorado_totalizado=e.est,
            eleitorado_nao_totalizado=e.esnt, eleitorado_nao_instalado=e.esni,
            eleitorado_apurado=e.esa, comparecimento=e.c, abstencao=e.a,
            total_votos=v.tv, validos=v.vv, validos_computados=v.vvc,
            nominal_tse=nominal, anulados=v.van, anulados_sub_judice=v.vansj,
            brancos=v.vb, nulos=v.tvn,
            pendente_min=p.lower, pendente_max=p.upper,
            quality_flags=json.dumps(
                [{"code": f.code, "sev": f.severity, "obs": f.observed}
                 for f in findings], ensure_ascii=False) or None,
        ))
        for c in r.cargo.candidatos:
            s.add(ObservedResult(
                snapshot_id=snap.id, contest_id=contest.id,
                geography_id=geo.id, candidate_id=cand_ids[c.sqcand],
                votos=c.votos, dvt=c.dvt,
            ))
            out["results"] += 1

    out["flags"].extend(f"{f.severity}:{f.code}" for f in findings
                        if f.severity == "critical")
    return out
