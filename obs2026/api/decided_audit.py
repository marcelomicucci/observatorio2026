"""Rotas de definição matemática e de auditoria das projeções.

Separadas do módulo principal porque respondem a perguntas de natureza
diferente das outras rotas:

    /api/v1/decided          o que já não depende de modelo nenhum
    /api/v1/forecast-audit   o que dissemos antes, e se bateu

A primeira nunca devolve probabilidade: ou a aritmética fecha, ou não fecha.
A segunda nunca devolve projeção nova: ela só lê o que já foi registrado.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import Depends


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment
    from ..engine import audit as A
    from ..engine import decided as DC
    from ..engine.denominators import Base as DenBase
    from ..storage.models import (Candidate, Contest, Geography,
                                  ObservedResult, SourceSnapshot)

    @app.get("/api/v1/decided")
    def definicao_matematica(cargo: str, eleicao: str | None = None,
                             uf: str | None = None,
                             municipio: str | None = None,
                             s: Session = Depends(M.get_session)):
        """O que já está matematicamente definido, sem depender de modelo.

        Rótulo distinto de "projetado" e de "eleito segundo TSE" (§4.7).

        Com `municipio`, a conta é feita sobre o recorte municipal — e o
        resultado NÃO é "eleito pelo município": é a aritmética local, que
        serve para ler o território, não para atribuir mandato (§3).
        """
        ct = M._contest_or_404(s, cargo, eleicao)
        abr = "mu" if municipio else ct.abrangencia_decisoria
        gq = select(Geography.id).where(
            Geography.environment == M._ENV_NAME, Geography.tipo == abr)
        if uf and abr in ("uf", "mu"):
            gq = gq.where(Geography.uf == uf.lower())
        if municipio:
            gq = gq.where(M._filtro_municipio(municipio))
        geo_ids = [r[0] for r in s.execute(gq).all()]
        totais = M._latest_snapshot_ids(s, ct.id, geo_ids)
        if not totais:
            return M.envelope([], snapshots=[], estado="aguardando",
                              motivo="nenhum resultado capturado ainda")

        geos = {g.id: g for g in s.execute(select(Geography).where(
            Geography.id.in_(geo_ids))).scalars().all()}
        cands = {c.id: c for c in s.execute(select(Candidate).where(
            Candidate.contest_id == ct.id)).scalars().all()}
        por_sq = {c.sqcand: c for c in cands.values()}
        snaps = s.execute(select(SourceSnapshot).where(
            SourceSnapshot.id.in_([t.snapshot_id for t in totais]))
        ).scalars().all()

        saida = []
        for t in totais:
            rows = s.execute(select(ObservedResult).where(
                ObservedResult.snapshot_id == t.snapshot_id,
                ObservedResult.geography_id == t.geography_id,
                ObservedResult.dvt == "Válido",
            )).scalars().all()
            if not rows:
                continue
            votos = {cands[r.candidate_id].sqcand: r.votos
                     for r in rows if r.candidate_id in cands}
            d = DC.avaliar(
                votos, vagas=ct.vagas,
                concorrentes_observados=t.validos_computados,
                pendente_max=t.pendente_max,
                sub_judice=t.anulados_sub_judice,
                encerrada=(t.andamento == "f"),
                unidade=geos[t.geography_id].nome,
                # Presidente e governador têm segundo turno; senador não.
                tem_segundo_turno=(ct.vagas == 1),
            )
            # Num recorte municipal a aritmética local é válida, mas o
            # resultado dela NÃO atribui mandato: quem decide é o Brasil
            # (presidente) ou a UF (governador, senador). O §3 é explícito.
            recorte_nao_decisorio = (abr != ct.abrangencia_decisoria)
            g = geos[t.geography_id]
            saida.append({
                "geografia": {"tipo": g.tipo, "uf": g.uf, "nome": g.nome},
                "recorte_nao_decisorio": recorte_nao_decisorio,
                "aviso_recorte": (
                    f"Recorte de {g.nome}. A disputa e decidida "
                    + ("no total do Brasil" if ct.abrangencia_decisoria == "br"
                       else f"no total de {g.uf.upper()}")
                    + "; liderar aqui nao e ser eleito por aqui."
                    if recorte_nao_decisorio else None),
                "vagas": ct.vagas,
                "tem_segundo_turno": ct.vagas == 1,
                "resto_maximo_total": d.resto_maximo_total,
                "resto_maximo_por_candidato": d.resto_maximo_por_candidato,
                "sub_judice_em_jogo": d.sub_judice_em_jogo,
                "encerrada": d.encerrada,
                "disputa_matematicamente_definida": d.disputa_definida,
                "segundo_turno_matematicamente_definido":
                    d.segundo_turno_definido,
                "avisos": list(d.avisos),
                "candidatos": [{
                    "sqcand": x.sqcand,
                    "numero": (por_sq[x.sqcand].numero
                               if x.sqcand in por_sq else None),
                    "nome_urna": (por_sq[x.sqcand].nome_urna
                                  if x.sqcand in por_sq else x.sqcand),
                    "votos": x.votos,
                    "estado": x.estado.value,
                    "rotulo": DC.ROTULOS[x.estado],
                    "custo_para_perder_a_vaga": x.custo_para_perder_a_vaga,
                    "precisa_para_alcancar": x.precisa_para_alcancar,
                    "motivo": x.motivo,
                } for x in d.situacoes],
            })

        return M.envelope(
            saida, snapshots=snaps, estado="definicao_matematica",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            extra={"nota": (
                "Definicao matematica NAO e proclamacao. Ela afirma que "
                "nenhuma distribuicao possivel dos votos restantes muda o "
                "resultado. A proclamacao e do TSE, e aparece em "
                "situacao_oficial.")})

    @app.get("/api/v1/forecast-audit")
    def auditoria_de_previsoes(serie: str | None = None):
        """Histórico das projeções feitas e a coerência delas (§7, §11.2)."""
        env = load_environment(M._ENV_NAME)
        d = A.Diario(DATA_DIR / "previsoes", env.name)
        series = d.series()
        saida = []
        for chave, regs in sorted(series.items()):
            if serie and serie not in chave:
                continue
            saida.append({
                "serie": chave,
                "execucoes": len(regs),
                "primeira": regs[0].momento,
                "ultima": regs[-1].momento,
                "registros": [{
                    "registro_id": r.registro_id,
                    "momento": r.momento,
                    "cobertura_eleitorado_pct": r.cobertura_pct,
                    "cobertura_secoes_pct": r.cobertura_secoes_pct,
                    "estado_publicacao": r.estado_publicacao,
                    "modelo": r.modelo,
                    "model_version": r.model_version,
                    "semente": r.semente,
                    "n_sims": r.n_sims,
                    "thresholds_hash": r.thresholds_hash,
                    "erro_monte_carlo_pp": r.erro_monte_carlo_pp,
                    "disputa_definida": r.disputa_matematicamente_definida,
                    "candidatos": [{
                        "nome": c.nome,
                        "votos_observados": c.votos_observados,
                        "pct_q100": c.pct_concorrentes_q100,
                        "pct_q500": c.pct_concorrentes_q500,
                        "pct_q900": c.pct_concorrentes_q900,
                        "prob_dentro_vagas": c.prob_dentro_vagas,
                        "estado_matematico": c.estado_matematico,
                    } for c in sorted(r.candidatos,
                                      key=lambda x: -x.votos_q500)[:8]],
                } for r in regs[-40:]],
            })
        return M.envelope(
            saida, snapshots=[], estado="auditoria",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            extra={"nota": (
                "Cada projecao foi registrada no instante em que foi feita, "
                "com a cobertura daquele momento. Reanalise com modelo novo "
                "e outra serie (§5.2), nunca uma correcao desta.")})
