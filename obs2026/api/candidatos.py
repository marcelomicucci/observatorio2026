"""Rota de candidatos: identidade oficial + enriquecimento, separados.

O que vem do TSE e o que vem de terceiro nunca compartilham campo. Cada
candidato sai com dois blocos:

    oficial        do EA20: sqcand, numero, nome de urna, partido,
                   destinacao do voto, situacao, votos
    enriquecimento foto, vice, patrimonio, plano de governo — com `fonte`,
                   `coletado_em` e `juncao` proprios

`juncao` diz como a correspondencia foi feita. `sqcand` e o codigo oficial e
pode ser exibido sem ressalva; `numero+partido` e candidata e a interface
precisa marcar como tal; `nenhuma` significa que o candidato aparece so com
o dado oficial, que e o suficiente para apurar.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


def register(app) -> None:
    from . import app as M
    from ..candidates import osidosos as OS
    from ..config import DATA_DIR
    from ..engine.denominators import Base as DenBase
    from ..storage.models import (Candidate, Geography, ObservedResult)

    cache = OS.Cache(DATA_DIR / "cache" / "enriquecimento")

    @app.get("/api/v1/candidates")
    def candidatos(cargo: str, eleicao: str | None = None,
                   uf: str | None = None, enriquecer: bool = True,
                   municipio: str | None = None,
                   s: Session = Depends(M.get_session)):
        ct = M._contest_or_404(s, cargo, eleicao)
        abr = ct.abrangencia_decisoria

        # Recorte municipal: o mapa e o telao pedem "Presidente em Belem",
        # que e recorte do voto, nao a unidade que decide (§3).
        if municipio:
            abr = "mu"
        gq = select(Geography).where(Geography.environment == M._ENV_NAME,
                                     Geography.tipo == abr)
        if uf and abr in ("uf", "mu"):
            gq = gq.where(Geography.uf == uf.lower())
        if municipio:
            gq = gq.where(M._filtro_municipio(municipio))
        geos = {g.id: g for g in s.execute(gq).scalars().all()}
        totais = M._latest_snapshot_ids(s, ct.id, list(geos))
        if not totais:
            return M.envelope([], snapshots=[], estado="aguardando",
                              motivo="nenhum resultado capturado ainda")

        cands = {c.id: c for c in s.execute(select(Candidate).where(
            Candidate.contest_id == ct.id)).scalars().all()}
        from ..storage.models import SourceSnapshot
        snaps = s.execute(select(SourceSnapshot).where(
            SourceSnapshot.id.in_([t.snapshot_id for t in totais]))
        ).scalars().all()

        perfis, meta = ([], {"desligado": True}) if not enriquecer \
            else OS.buscar(ct.cargo_cd, cache=cache)
        indice = OS.Indice.de(perfis)

        saida = []
        for t in totais:
            g = geos[t.geography_id]
            rows = s.execute(select(ObservedResult).where(
                ObservedResult.snapshot_id == t.snapshot_id,
                ObservedResult.geography_id == t.geography_id,
            )).scalars().all()
            rows.sort(key=lambda r: -r.votos)

            lista = []
            for r in rows:
                c = cands.get(r.candidate_id)
                if c is None:
                    continue
                p, como = indice.casar(c.sqcand, c.numero,
                                       c.partido_sigla, g.uf)
                den = t.validos_computados
                lista.append({
                    "oficial": {
                        "sqcand": c.sqcand,
                        "numero": c.numero,
                        "nome_urna": c.nome_urna,
                        "partido": c.partido_sigla,
                        "federacao": c.agr_nome,
                        "destinacao_voto": r.dvt,
                        "situacao": c.situacao,
                        "eleito_segundo_tse": (
                            c.eleito if abr == ct.abrangencia_decisoria
                            else None),
                        "votos": r.votos,
                        "pct": M._pct_coerente(
                            r.votos, den, DenBase.VALIDOS_COMPUTADOS, r.dvt),
                        "pct_indisponivel_motivo": M._pct_motivo(
                            den, DenBase.VALIDOS_COMPUTADOS, r.dvt),
                        "fonte": "TSE/EA20",
                    },
                    "enriquecimento": (None if p is None else {
                        "nome": p.nome,
                        "foto_url": p.foto_url,
                        "vice_nome": p.vice_nome,
                        "vice_partido": p.vice_partido,
                        "profissao": p.profissao,
                        "grau_instrucao": p.grau_instrucao,
                        "patrimonio_total": p.patrimonio_total,
                        "situacao_registro": p.situacao_registro,
                        "status_geral": p.status_geral,
                        "plano_governo_url": p.plano_governo_url,
                        "perfil_url": p.perfil_url,
                        "fonte": p.fonte,
                        "coletado_em": p.coletado_em,
                        # A interface PRECISA distinguir: 'sqcand' e codigo
                        # oficial, 'numero+partido' e correspondencia
                        # candidata, a confirmar.
                        "juncao": como,
                        "juncao_confiavel": como == "sqcand",
                    }),
                })

            saida.append({
                "geografia": {"tipo": g.tipo, "uf": g.uf, "nome": g.nome},
                "vagas": ct.vagas,
                "tem_segundo_turno": ct.vagas == 1,
                "votos_a_concorrentes": t.validos_computados,
                "candidatos": lista,
            })

        casados = sum(1 for u in saida for c in u["candidatos"]
                      if c["enriquecimento"])
        total = sum(len(u["candidatos"]) for u in saida)
        por_sqcand = sum(1 for u in saida for c in u["candidatos"]
                         if c["enriquecimento"]
                         and c["enriquecimento"]["juncao"] == "sqcand")

        return M.envelope(
            saida, snapshots=snaps, estado="observado",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            extra={
                "disputa": {"cargo_cd": ct.cargo_cd, "cargo": ct.cargo_nome,
                            "vagas": ct.vagas},
                "enriquecimento": {
                    **meta,
                    "casados": casados,
                    "casados_por_codigo_oficial": por_sqcand,
                    "total_candidatos": total,
                    "nota": (
                        "Voto e situacao vem do TSE. Foto, vice, patrimonio e "
                        "plano de governo vem de fonte civica de terceiro, "
                        "com data propria. Os dois nunca se misturam no mesmo "
                        "campo."),
                },
            })
