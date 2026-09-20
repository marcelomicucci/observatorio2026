"""Execução de uma projeção, do arquivo bruto até o registro de auditoria.

Junta as peças na ordem correta e garante que **toda** projeção produzida
seja registrada antes de poder ser vista. Não existe caminho no código que
gere um número de projeção sem gravar o que o motor sabia no momento — se
existisse, a auditoria teria buracos justamente nos instantes interessantes.

Lê do arquivo bruto, não do banco. O bruto é a fonte de verdade (§8.1) e
carrega o horário de geração no TSE, que é o que datar uma projeção exige.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Environment, Thresholds
from ..storage.raw import RawArchive
from ..tse.parsers import ea20 as ea20_parser
from . import audit, decided, models as M, pending as pend, simulate as S


@dataclass(frozen=True, slots=True)
class Contexto:
    """O que se sabia no instante da execução."""

    eleicao: str
    turno: str
    cargo: str
    abrangencia: str
    unidade: str
    vagas: int
    eleitorado_total: int
    eleitorado_apurado: int
    secoes_totalizadas: int
    secoes_total: int
    pendente_min: int
    pendente_max: int
    concorrentes_observados: int
    sub_judice: int
    encerrada: bool
    fonte_gerada_em: str | None
    snapshot_set_id: str | None
    unidades: int
    nomes: dict[str, str]


def carregar_observacoes(archive: RawArchive, cargo: str,
                         uf: str | None = None) -> list[M.UnitObservation]:
    """Últimas observações municipais de uma disputa, do arquivo bruto.

    Mantém apenas a captura mais recente por município: duas capturas da
    mesma unidade somadas seriam dupla contagem.
    """
    ultimos: dict[tuple[str, str], tuple[str, object]] = {}
    for dia in archive.days():
        for ref in archive.manifest(dia):
            if ref.kind != "ea20" or not ref.path or not ref.mun:
                continue
            if ref.cargo != cargo:
                continue
            if uf and ref.uf != uf:
                continue
            chave = (ref.uf or "", ref.mun)
            anterior = ultimos.get(chave)
            if anterior is None or ref.captured_at > anterior[0]:
                ultimos[chave] = (ref.captured_at, ref)

    saida = []
    for (uf_ref, _mun), (_cap, ref) in ultimos.items():
        try:
            r = ea20_parser.parse(archive.load(ref))
        except Exception:                     # noqa: BLE001
            continue
        saida.append(M.UnitObservation(
            unit=f"{uf_ref}:{r.cdabr}", uf=uf_ref,
            stratum=M.estrato(uf_ref, r.electorate.te), result=r,
        ))
    return saida


def montar_contexto(obs: list[M.UnitObservation], *, cargo: str,
                    abrangencia: str, unidade: str) -> Contexto:
    if not obs:
        raise ValueError("nenhuma observacao para esta disputa")
    r0 = obs[0].result
    te = sum(o.result.electorate.te for o in obs)
    esa = sum(o.result.electorate.esa for o in obs)
    st = sum(o.result.sections.st for o in obs)
    ts = sum(o.result.sections.ts for o in obs)
    p_min = sum(o.pending.lower for o in obs)
    p_max = sum(o.pending.upper for o in obs)
    vvc = sum(o.result.votes.vvc for o in obs)
    sj = sum(o.result.votes.vansj for o in obs)
    geradas = [o.result.generated_at for o in obs if o.result.generated_at]
    nomes = {c.sqcand: c.nome_urna for o in obs
             for c in o.result.cargo.candidatos}
    return Contexto(
        eleicao=r0.election, turno=r0.turno, cargo=cargo,
        abrangencia=abrangencia, unidade=unidade, vagas=r0.cargo.vagas,
        eleitorado_total=te, eleitorado_apurado=esa,
        secoes_totalizadas=st, secoes_total=ts,
        pendente_min=p_min, pendente_max=p_max,
        concorrentes_observados=vvc, sub_judice=sj,
        # A disputa só está encerrada se TODAS as unidades estiverem.
        encerrada=all(o.result.is_final for o in obs),
        fonte_gerada_em=(max(geradas).isoformat() if geradas else None),
        snapshot_set_id=None, unidades=len(obs), nomes=nomes,
    )


def executar(env: Environment, thr: Thresholds, *, cargo: str,
             uf: str | None = None, modelo: str = "m1",
             n_sims: int | None = None, model_version: str = "m1-0.1",
             archive: RawArchive | None = None,
             diario: audit.Diario | None = None,
             registrar: bool = True):
    """Roda a projeção de uma disputa e registra o resultado.

    Devolve `(forecast, definicao, contexto, registro)`.
    """
    archive = archive or RawArchive(Path("data/raw"), env.name)
    obs = carregar_observacoes(archive, cargo, uf)
    abrangencia = "br" if uf is None else "uf"
    unidade = uf or "br"
    ctx = montar_contexto(obs, cargo=cargo, abrangencia=abrangencia,
                          unidade=unidade)

    candidatos = sorted({
        c.sqcand for o in obs for c in o.result.cargo.candidatos
        if c.conta_como_valido
    })
    if not candidatos:
        raise ValueError("nenhum candidato valido na disputa")

    build = M.build_m0 if modelo == "m0" else M.build_m1
    units = build(obs, candidatos, seats=ctx.vagas)
    cfg = S.SimConfig(
        n_sims=n_sims or int(thr.monte_carlo.get("n_sims", 5000)),
        seed=int(thr.monte_carlo.get("seed", 20261004)),
    )
    fc = S.run(units, candidatos, cfg, seats=ctx.vagas,
               senado=ctx.vagas > 1)

    votos_obs = {c: fc.observed.get(c, 0) for c in candidatos}
    dfn = decided.avaliar(
        votos_obs, vagas=ctx.vagas,
        concorrentes_observados=ctx.concorrentes_observados,
        pendente_max=ctx.pendente_max, sub_judice=ctx.sub_judice,
        encerrada=ctx.encerrada, unidade=unidade,
    )

    # Estado de publicação (§4.7). O gate manda; a projeção existe de todo
    # jeito e fica registrada, mas não vira probabilidade publicada.
    if ctx.encerrada:
        estado = "resultado_oficial"
    elif not thr.homologated:
        estado = "cenario_exploratorio"
    else:
        estado = "projecao_disponivel"

    registro = None
    if registrar:
        diario = diario or audit.Diario(Path("data/previsoes"), env.name)
        registro = diario.registrar(audit.capturar(
            forecast=fc, definicao=dfn, ambiente=env.name,
            eleicao=ctx.eleicao, turno=ctx.turno, cargo=cargo,
            abrangencia=abrangencia, unidade=unidade,
            snapshot_set_id=ctx.snapshot_set_id,
            fonte_gerada_em=ctx.fonte_gerada_em,
            eleitorado_total=ctx.eleitorado_total,
            eleitorado_apurado=ctx.eleitorado_apurado,
            secoes_totalizadas=ctx.secoes_totalizadas,
            secoes_total=ctx.secoes_total,
            pendente_min=ctx.pendente_min, pendente_max=ctx.pendente_max,
            modelo=modelo, model_version=model_version,
            thresholds_hash=thr.config_hash,
            thresholds_homologados=thr.homologated,
            estado_publicacao=estado, nomes=ctx.nomes,
        ))
    return fc, dfn, ctx, registro
