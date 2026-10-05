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
from ..tse.parsers.ea20 import DVT_ANULADO_SJ
from . import audit, decided, models as M, pending as pend, simulate as S
from . import territorio


#: Modelo de produção. M2 desde 21/09/2026: no ensaio com a ordem real de
#: 2022 (`scripts/ensaio2022.py`), calibrado em 2018 e testado em 2022, a
#: cobertura do intervalo de 80% foi de 54-59% (M1) para 77-82% (M2), e no
#: presidente a maior chance dada ao perdedor caiu de 91% para 56%. O M1
#: continua disponível (`modelo="m1"`) para comparação e para voltar atrás.
#:
#: Desde 21/09/2026 este é só o padrão de fábrica: o modelo em produção é
#: escolhido na aba Configuração e fica em `config/operacao.yaml`
#: (`operacao.modelo_ativo()`), relido a cada projeção.
MODELO_PADRAO = "m2"
VERSOES = {"m0": "m0-0.1", "m1": "m1-0.1", "m2": "m2-0.1", "m3": "m3-0.1",
           "m31": "m31-0.1", "m32": "m32-0.1", "m33": "m33-0.1"}


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
    #: Votos sub judice por candidato — permite separar "há voto sub
    #: judice" de "o voto sub judice decide" (engine/decided.py).
    sub_judice_por_candidato: dict[str, int]
    encerrada: bool
    fonte_gerada_em: str | None
    #: Horario da fonte MAIS ANTIGA entre as unidades lidas. Durante a
    #: apuracao os 5.570 municipios nunca estao simultaneos: cada um
    #: totaliza no seu tempo. Nao ha instante consistente pelo qual esperar,
    #: entao o §8.2 manda registrar a DISPERSAO em vez de fingir que ela nao
    #: existe. A diferenca entre esta e `fonte_gerada_em` e o quanto o
    #: retrato esta borrado.
    fonte_mais_antiga: str | None
    #: Unidades que ainda nao produziram nenhum dado, e o eleitorado delas.
    #: Sao o buraco do retrato; o modelo trata como pendente, mas quem le a
    #: projecao precisa saber o tamanho do buraco.
    unidades_sem_dado: int
    eleitorado_sem_dado: int
    snapshot_set_id: str | None
    unidades: int
    nomes: dict[str, str]

    @property
    def dispersao_fonte_s(self) -> float | None:
        """Segundos entre a fonte mais antiga e a mais recente."""
        if not (self.fonte_gerada_em and self.fonte_mais_antiga):
            return None
        from datetime import datetime
        a = datetime.fromisoformat(self.fonte_mais_antiga)
        b = datetime.fromisoformat(self.fonte_gerada_em)
        return (b - a).total_seconds()


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
            # Só o M2 lê a marca; o estrato do M1 continua igual.
            capital=territorio.e_capital(uf_ref, r.cdabr),
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
    sj_cand: dict[str, int] = {}
    for o in obs:
        for c in o.result.cargo.candidatos:
            if c.dvt == DVT_ANULADO_SJ:
                sj_cand[c.sqcand] = sj_cand.get(c.sqcand, 0) + c.votos
    geradas = [o.result.generated_at for o in obs if o.result.generated_at]
    # Unidade sem dado: nenhuma secao apurada ainda. Nao e erro — e o comeco
    # da noite — mas precisa aparecer no registro.
    sem_dado = [o for o in obs if o.result.electorate.esa == 0]
    nomes = {c.sqcand: c.nome_urna for o in obs
             for c in o.result.cargo.candidatos}
    return Contexto(
        eleicao=r0.election, turno=r0.turno, cargo=cargo,
        abrangencia=abrangencia, unidade=unidade, vagas=r0.cargo.vagas,
        eleitorado_total=te, eleitorado_apurado=esa,
        secoes_totalizadas=st, secoes_total=ts,
        pendente_min=p_min, pendente_max=p_max,
        concorrentes_observados=vvc, sub_judice=sj,
        sub_judice_por_candidato=sj_cand,
        # A disputa só está encerrada se TODAS as unidades estiverem.
        encerrada=all(o.result.is_final for o in obs),
        fonte_gerada_em=(max(geradas).isoformat() if geradas else None),
        fonte_mais_antiga=(min(geradas).isoformat() if geradas else None),
        unidades_sem_dado=len(sem_dado),
        eleitorado_sem_dado=sum(o.result.electorate.te for o in sem_dado),
        snapshot_set_id=None, unidades=len(obs), nomes=nomes,
    )


def executar(env: Environment, thr: Thresholds, *, cargo: str,
             uf: str | None = None, modelo: str | None = None,
             n_sims: int | None = None, model_version: str | None = None,
             archive: RawArchive | None = None,
             diario: audit.Diario | None = None,
             registrar: bool = True,
             observacoes: list[M.UnitObservation] | None = None):
    """Roda a projeção de uma disputa e registra o resultado.

    Devolve `(forecast, definicao, contexto, registro)`.

    `observacoes` permite reaproveitar uma carga incremental: durante a
    apuração, reler o bruto inteiro a cada projeção custaria segundos que
    crescem com o manifesto (ver `incremental.py`).
    """
    archive = archive or RawArchive(Path("data/raw"), env.name)
    obs = (observacoes if observacoes is not None
           else carregar_observacoes(archive, cargo, uf))
    abrangencia = "br" if uf is None else "uf"
    unidade = uf or "br"
    ctx = montar_contexto(obs, cargo=cargo, abrangencia=abrangencia,
                          unidade=unidade)

    # Válidos e sub judice (ver `pending.observed_of`): o sub judice é
    # projetado como "se os votos forem validados".
    candidatos = sorted({
        c.sqcand for o in obs for c in o.result.cargo.candidatos
        if c.conta_como_valido or c.dvt == ea20_parser.DVT_ANULADO_SJ
    })
    if not candidatos:
        raise ValueError("nenhum candidato valido na disputa")

    if modelo is None:
        # O modelo de produção é chave de operação, não constante: vale o
        # que estiver escolhido na aba Configuração neste instante.
        from ..operacao import modelo_ativo
        modelo = modelo_ativo()
    if modelo not in VERSOES:
        raise ValueError(f"modelo desconhecido: {modelo}")
    model_version = model_version or VERSOES[modelo]
    build = {"m0": M.build_m0, "m1": M.build_m1, "m2": M.build_m2,
             "m3": M.build_m3, "m31": M.build_m31,
             "m32": M.build_m32, "m33": M.build_m33}[modelo]
    units = build(obs, candidatos, seats=ctx.vagas)
    cfg = S.SimConfig(
        n_sims=n_sims or int(thr.monte_carlo.get("n_sims", 5000)),
        seed=int(thr.monte_carlo.get("seed", 20261004)),
    )
    if modelo == "m33":
        cfg = S.estreitar(cfg, M.FATOR_M33)
    fc = S.run(units, candidatos, cfg, seats=ctx.vagas,
               senado=ctx.vagas > 1)

    votos_obs = {c: fc.observed.get(c, 0) for c in candidatos}
    dfn = decided.avaliar(
        votos_obs, vagas=ctx.vagas,
        concorrentes_observados=ctx.concorrentes_observados,
        pendente_max=ctx.pendente_max, sub_judice=ctx.sub_judice,
        sub_judice_por_candidato=ctx.sub_judice_por_candidato,
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
            concorrentes_observados=ctx.concorrentes_observados,
            fonte_mais_antiga=ctx.fonte_mais_antiga,
            unidades_sem_dado=ctx.unidades_sem_dado,
            eleitorado_sem_dado=ctx.eleitorado_sem_dado,
        ))
    return fc, dfn, ctx, registro
