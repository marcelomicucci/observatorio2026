"""API de leitura (§9).

Regras do contrato que o código impõe, não apenas documenta:

- **Todo envelope declara o denominador.** Sem isso, `12,84%` é um número sem
  significado: pode ser sobre válidos, sobre votos a concorrentes, sobre
  nominais do TSE ou sobre o total de votos, e os quatro diferem (ver
  `engine/denominators.py`). O padrão é `validos_computados`, que é a base da
  maioria absoluta.
- **Ausência é `null` com motivo, nunca zero.** Município sem voto contado
  tem percentual indefinido, não 0%.
- **Um lote por resposta.** Todos os cards de uma resposta referenciam o mesmo
  `snapshot_set_id`, para não misturar horários (§8.2).
- **Observado e projetado nunca se misturam** no mesmo campo.
- **Probabilidade só sai se o gate permitir.** Enquanto `thresholds.yaml` não
  estiver homologado, `/forecasts` responde com estado
  `cenario_exploratorio` e sem probabilidade — e diz por quê.

O navegador fala só com esta API. Nenhuma página consulta o TSE diretamente
(§8), de modo que mil leitores simultâneos não viram mil requisições ao TSE.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, load_environment, load_thresholds
from ..engine.denominators import Base as DenBase
from ..engine.denominators import LABELS as DEN_LABELS
from ..storage.db import make_engine, make_session_factory
from ..storage.models import (Candidate, Contest, Election, Geography,
                              ObservedResult, SourceSnapshot, UnitTotals)

API_VERSION = "v1"
MODEL_VERSION = "m2-0.1-nao-homologado"
CLASSIFICATION_VERSION = "2026.0-rascunho"

app = FastAPI(title="Observatório Eleitoral 2026", version="0.1.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")
# O mapa municipal do Brasil tem 3,5 MB de JSON e o telão o pede a cada
# troca de tela. Comprimido cai para cerca de um décimo: menos tempo de
# transferência e de leitura no navegador do projetor.
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
    allow_headers=["*"],
)

#: Ambiente servido. `OBS2026_ENV` escolhe sem editar código — é assim que
#: `obs2026 servir --env ...` troca para o oficial ou para a demonstração.
_ENV_NAME = os.environ.get("OBS2026_ENV", "simulado2026")
_engine = None
_Session = None


def configure(env_name: str = "simulado2026") -> None:
    global _ENV_NAME, _engine, _Session
    _ENV_NAME = env_name
    env = load_environment(env_name)
    _engine = make_engine(sqlite_path=env.db_path)
    _Session = make_session_factory(_engine)


#: Ambiente pedido por ESTE pedido (`?eleicao_passada=tse2022t2`), para o seletor de
#: anos: as mesmas telas da noite lendo uma eleição passada. Só ambientes
#: congelados e só leitura — ver `EleicaoPorPedido`. Fora disso, vale o
#: ambiente servido.
_ENV_PEDIDO: ContextVar[str | None] = ContextVar("obs2026_env_pedido",
                                                 default=None)
_fabricas_congeladas: dict[str, object] = {}
_congelados: dict[str, bool] = {}


def env_atual() -> str:
    """O ambiente que as consultas deste pedido devem ler."""
    return _ENV_PEDIDO.get() or _ENV_NAME


def ambiente_congelado(nome: str) -> bool:
    """Existe, é congelado e tem banco montado."""
    if nome not in _congelados:
        try:
            env = load_environment(nome)
            _congelados[nome] = bool(env.congelado and env.db_path.exists())
        except Exception:                               # noqa: BLE001
            _congelados[nome] = False
    return _congelados[nome]


def get_session() -> Session:
    pedido = _ENV_PEDIDO.get()
    if pedido:
        fab = _fabricas_congeladas.get(pedido)
        if fab is None:
            env = load_environment(pedido)
            fab = _fabricas_congeladas[pedido] = make_session_factory(
                make_engine(sqlite_path=env.db_path))
        with fab() as s:           # type: ignore[operator]
            yield s
        return
    if _Session is None:
        configure(_ENV_NAME)
    with _Session() as s:          # type: ignore[misc]
        yield s


_metas: dict[str, tuple[float, dict | None]] = {}


def _meta_congelado(env) -> dict | None:
    """Metadados que o `montar-ano` grava ao lado do banco (lidos uma vez
    por versão do arquivo)."""
    from ..history.congelado import caminho_meta
    p = caminho_meta(env.db_path)
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    memo = _metas.get(env.name)
    if memo is None or memo[0] != mt:
        memo = _metas[env.name] = (mt, json.loads(
            p.read_text(encoding="utf-8")))
    return memo[1]


class EleicaoPorPedido:
    """`?eleicao_passada=<ambiente>` troca o banco lido NESTE pedido.

    O nome é longo de propósito: `eleicao` já é parâmetro de rotas como
    `/results` (o código da eleição no TSE), e a colisão fazia a disputa
    "sumir" (visto em 24/09).

    Middleware ASGI puro (não o `BaseHTTPMiddleware`), para o ContextVar
    chegar intacto à rota e às dependências. Recusa, em vez de ignorar:
    - ambiente que não é congelado — o painel nunca deve ler o simulado ou
      o oficial por esta porta, e ignorar mostraria 2026 com rótulo de 2018;
    - método que não é GET/HEAD — eleição passada não se escreve.
    """

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from urllib.parse import parse_qs
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        nome = (qs.get("eleicao_passada") or [""])[0].strip()
        if not nome:
            return await self.app(scope, receive, send)
        erro = None
        if scope.get("method") not in ("GET", "HEAD"):
            erro = "eleição passada é só leitura"
        elif not ambiente_congelado(nome):
            erro = (f"eleição {nome!r} não disponível: monte com "
                    "`obs2026 montar-ano`")
        if erro:
            corpo = json.dumps({"detail": erro},
                               ensure_ascii=False).encode("utf-8")
            await send({"type": "http.response.start", "status": 400,
                        "headers": [(b"content-type",
                                     b"application/json; charset=utf-8")]})
            await send({"type": "http.response.body", "body": corpo})
            return
        from ..contexto import ANO_ELEICAO
        meta = _meta_congelado(load_environment(nome)) or {}
        token = _ENV_PEDIDO.set(nome)
        token_ano = ANO_ELEICAO.set(meta.get("ano"))
        try:
            await self.app(scope, receive, send)
        finally:
            ANO_ELEICAO.reset(token_ano)
            _ENV_PEDIDO.reset(token)


app.add_middleware(EleicaoPorPedido)


# --------------------------------------------------------------------------
# envelope
# --------------------------------------------------------------------------

def aware(dt: datetime | None) -> datetime | None:
    """Reanexa UTC ao datetime lido do banco.

    O SQLite não guarda fuso. O §9 manda operar em UTC internamente e exibir
    em America/Sao_Paulo, e tudo que é gravado já está em UTC — então a
    reanexação é fiel, não um chute. Centralizar aqui evita que uma
    subtração ingênua entre "naive" e "aware" derrube uma rota, que foi
    exatamente o que aconteceu na primeira execução.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    a = aware(dt)
    return a.isoformat() if a else None


def envelope(dados: Any, *, snapshots: list[SourceSnapshot],
             estado: str, denominador: DenBase | None = None,
             quality_flags: list[str] | None = None,
             motivo: str | None = None, extra: dict | None = None) -> dict:
    """Monta a resposta com os metadados obrigatórios do §9."""
    gerados = [aware(s.source_generated_at) for s in snapshots
               if s.source_generated_at]
    capturados = [aware(s.captured_at) for s in snapshots if s.captured_at]
    ids = sorted({str(s.id) for s in snapshots})
    set_id = hashlib.sha256("|".join(ids).encode()).hexdigest()[:16] \
        if ids else None

    env = load_environment(env_atual())
    thr = load_thresholds()
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source_generated_at": (max(gerados).isoformat() if gerados else None),
        "source_generated_at_min": (min(gerados).isoformat()
                                    if gerados else None),
        "captured_at": (max(capturados).isoformat() if capturados else None),
        "snapshot_set_id": set_id,
        "snapshot_count": len(snapshots),
        "status": estado,
        "status_motivo": motivo,
        "ambiente": {
            "nome": env.name,
            "oficial": env.official,
            # Barreira visível: nenhum consumidor pode confundir simulado com
            # resultado oficial por descuido de leitura.
            "aviso": (None if env.official else env.aviso or
                      "AMBIENTE DE SIMULADO — dados de teste do TSE, "
                      "não são resultado eleitoral"),
            # Eleição passada (seletor de anos): ano, turno e as UFs cujo
            # governador do "resultado final" é o do 1º turno.
            "eleicao": _meta_congelado(env) if env.congelado else None,
        },
        "denominador": ({"base": denominador.value,
                         "rotulo": DEN_LABELS[denominador]}
                        if denominador else None),
        "quality_flags": quality_flags or [],
        "model_version": MODEL_VERSION,
        "classification_version": CLASSIFICATION_VERSION,
        "thresholds_homologados": thr.homologated,
        "thresholds_hash": thr.config_hash,
        "dados": dados,
        **(extra or {}),
    }


def _latest_snapshot_ids(s: Session, contest_id: int,
                         geography_ids: list[int] | None = None):
    """Último snapshot por (contest, geography). Nunca mistura horários.

    Uma consulta só, com janela. A versão anterior fazia uma consulta por
    território — 645 para o mapa de São Paulo, 5.755 para o do Brasil —, e
    o custo não estava no banco, estava em montar cada consulta e cada
    objeto. `ROW_NUMBER` existe no SQLite (3.25+) e no PostgreSQL.
    """
    ordem = func.row_number().over(
        partition_by=UnitTotals.geography_id,
        order_by=(SourceSnapshot.captured_at.desc(), UnitTotals.id.desc()),
    ).label("ordem")
    base = (select(UnitTotals.id, ordem)
            .join(SourceSnapshot, SourceSnapshot.id == UnitTotals.snapshot_id)
            .where(UnitTotals.contest_id == contest_id))
    if geography_ids:
        base = base.where(UnitTotals.geography_id.in_(geography_ids))
    sub = base.subquery()
    ids = select(sub.c.id).where(sub.c.ordem == 1)
    return list(s.execute(
        select(UnitTotals).where(UnitTotals.id.in_(ids))).scalars().all())


#: Bases cujo denominador contém apenas votos com destinação 'Válido'.
#: Para candidatos anulados ou sub judice, o percentual nessas bases é
#: indefinido, não zero e não calculável.
_BASES_SO_VALIDOS = {DenBase.VALIDOS}


def _pct_motivo(den: int, base: DenBase, dvt: str) -> str | None:
    if base in _BASES_SO_VALIDOS and dvt != "Válido":
        return (f"candidato com destinação {dvt!r} não compõe o denominador "
                f"{base.value!r}; use base=nominal_tse ou "
                "base=validos_computados para comparar")
    if den <= 0:
        return "denominador zero: nenhuma votação computada nesta unidade"
    return None


def _pct_coerente(votos: int, den: int, base: DenBase,
                  dvt: str) -> float | None:
    if _pct_motivo(den, base, dvt) is not None:
        return None
    return 100.0 * votos / den


def _filtro_municipio(codigo: str):
    """Aceita codigo TSE (5 digitos) ou IBGE (7 digitos).

    Sao duas numeracoes diferentes para o mesmo lugar, e quem monta um
    roteiro de telao vai ter uma ou outra na mao. Nenhuma busca por nome:
    nome de municipio nao e chave (PAU D'ARCO existe em PA e em TO).
    """
    c = str(codigo).strip()
    if len(c) == 7 and c.isdigit():
        return Geography.cd_ibge == c
    return Geography.cd_tse == c.zfill(5)


def _abrangencia_do_recorte(ct: Contest, uf: str | None,
                            municipio: str | None) -> str:
    """Em que nível ler, dado o recorte pedido.

    A disputa tem uma abrangência que decide — Brasil para presidente, UF
    para governador e senador. Pedir um recorte **menor** que ela é legítimo
    e comum ("presidente em Belém", "presidente no PA"): não é a disputa, é
    o voto daquele território, e quem responde precisa avisar isso (§3).

    Esta função existe porque a regra estava escrita duas vezes, e as duas
    cópias tinham o mesmo furo:

        if uf and abr in ("uf", "mu"): filtra por uf

    Para presidente `abr` é `"br"`, então a condição era falsa e o `uf` era
    **descartado em silêncio**: `/candidates?cargo=1&uf=pa` devolvia o
    número do Brasil inteiro com a cara de resultado do Pará — 10,5 milhões
    de votos num estado cujo total é 4,6 milhões. Filtro que não pode ser
    aplicado tem que mudar a pergunta ou recusá-la, nunca responder outra.
    """
    if municipio:
        return "mu"
    if uf:
        return "uf"
    return ct.abrangencia_decisoria


def tem_segundo_turno(s: Session, ct: Contest | None) -> bool:
    """A disputa ainda pode ir a 2º turno?

    Presidente e governador no 1º turno. NÃO é "vaga única": o Senado de
    2022 teve uma vaga por UF e nenhum 2º turno, e o resultado de um 2º
    turno não tem outro depois dele — o seletor de anos mostrava "2º turno
    garantido" no Haddad de 2018 (visto em 24/09)."""
    if ct is None or ct.cargo_cd not in ("1", "3"):
        return False
    turno = s.execute(select(Election.turno).where(
        Election.id == ct.election_id)).scalar()
    return str(turno or "1") == "1"


def _contest_or_404(s: Session, cargo: str, eleicao: str | None) -> Contest:
    q = (select(Contest).join(Election, Election.id == Contest.election_id)
         .where(Election.environment == env_atual(), Contest.cargo_cd == cargo))
    if eleicao:
        q = q.where(Election.cd_eleicao == eleicao)
    c = s.execute(q).scalars().first()
    if c is None:
        raise HTTPException(404, f"disputa nao encontrada: cargo={cargo}")
    return c


# --------------------------------------------------------------------------
# rotas
# --------------------------------------------------------------------------

@app.get("/api/v1/elections")
def elections(s: Session = Depends(get_session)):
    """Eleições, turnos e disputas disponíveis."""
    linhas = s.execute(
        select(Election, Contest)
        .join(Contest, Contest.election_id == Election.id)
        .where(Election.environment == env_atual())
    ).all()
    por_eleicao: dict[int, dict] = {}
    for el, ct in linhas:
        d = por_eleicao.setdefault(el.id, {
            "cd_eleicao": el.cd_eleicao, "turno": el.turno, "nome": el.nome,
            "ciclo": el.ciclo, "cd_pleito": el.cd_pleito,
            "cd_eleicao_turno2": el.cd_eleicao_turno2,
            "oficial": el.official, "disputas": [],
        })
        d["disputas"].append({
            "cargo_cd": ct.cargo_cd, "cargo": ct.cargo_nome,
            "abrangencia_decisoria": ct.abrangencia_decisoria,
            "vagas": ct.vagas, "inclui_exterior": ct.inclui_exterior,
            # §3: o município é recorte do voto, não quem elege.
            "nota": ("O município é recorte do voto. Liderança municipal "
                     "nunca significa eleição pelo município."),
        })
    return envelope(list(por_eleicao.values()), snapshots=[],
                    estado="catalogo")


@app.get("/api/v1/results")
def results(
    cargo: str = Query(..., description="cd do cargo: 1, 3 ou 5"),
    eleicao: str | None = None,
    abrangencia: str = Query("br", pattern="^(br|uf|mu)$"),
    uf: str | None = None,
    #: Codigo TSE do municipio (5 digitos, com zeros a esquerda) ou codigo
    #: IBGE (7 digitos). Recorte municipal e RECORTE DO VOTO, nunca a
    #: unidade que elege (§3).
    municipio: str | None = None,
    # Padrão é a base que decide o primeiro turno (§3).
    base: DenBase = DenBase.VALIDOS_COMPUTADOS,
    s: Session = Depends(get_session),
):
    """Resultado oficial observado. Nunca mistura projeção."""
    ct = _contest_or_404(s, cargo, eleicao)

    gq = select(Geography.id).where(Geography.environment == env_atual(),
                                    Geography.tipo == abrangencia)
    if uf:
        gq = gq.where(Geography.uf == uf.lower())
    if municipio:
        gq = gq.where(_filtro_municipio(municipio))
    geo_ids = [r[0] for r in s.execute(gq).all()]
    if not geo_ids:
        return envelope([], snapshots=[], estado="aguardando",
                        denominador=base,
                        motivo="nenhuma geografia para o filtro pedido")

    totais = _latest_snapshot_ids(s, ct.id, geo_ids)
    if not totais:
        return envelope([], snapshots=[], estado="aguardando",
                        denominador=base,
                        motivo="nenhum resultado capturado ainda")

    snaps = s.execute(select(SourceSnapshot).where(
        SourceSnapshot.id.in_([t.snapshot_id for t in totais]))).scalars().all()
    geos = {g.id: g for g in s.execute(select(Geography).where(
        Geography.id.in_(geo_ids))).scalars().all()}
    cands = {c.id: c for c in s.execute(select(Candidate).where(
        Candidate.contest_id == ct.id)).scalars().all()}

    flags: set[str] = set()
    saida = []
    for t in totais:
        rows = s.execute(select(ObservedResult).where(
            ObservedResult.snapshot_id == t.snapshot_id,
            ObservedResult.contest_id == ct.id,
            ObservedResult.geography_id == t.geography_id,
        )).scalars().all()

        den = {
            DenBase.VALIDOS: t.validos,
            DenBase.NOMINAL_TSE: t.nominal_tse,
            DenBase.VALIDOS_COMPUTADOS: t.validos_computados,
            DenBase.TOTAL_VOTOS: t.total_votos,
            DenBase.COMPARECIMENTO: t.comparecimento,
        }[base]

        if t.quality_flags:
            for f in json.loads(t.quality_flags):
                if f["sev"] in ("critical", "warning"):
                    flags.add(f"{f['sev']}:{f['code']}")

        g = geos[t.geography_id]
        saida.append({
            "geografia": {
                "tipo": g.tipo, "cd_tse": g.cd_tse, "cd_ibge": g.cd_ibge,
                "uf": g.uf, "nome": g.nome, "capital": g.capital,
                "exterior": g.exterior,
            },
            "andamento": t.andamento,
            "totalizado_em": iso(t.totalizado_em),
            "secoes": {"total": t.secoes_total,
                       "totalizadas": t.secoes_totalizadas,
                       "instaladas": t.secoes_instaladas,
                       "apuradas": t.secoes_apuradas},
            "eleitorado": {
                "total": t.eleitorado_total,
                "apurado": t.eleitorado_apurado,
                "nao_totalizado": t.eleitorado_nao_totalizado,
                "nao_instalado": t.eleitorado_nao_instalado,
                "comparecimento": t.comparecimento,
                "abstencao": t.abstencao,
                # §4.3: sobre o eleitorado das seções APURADAS.
                "comparecimento_pct": (
                    100.0 * t.comparecimento / t.eleitorado_apurado
                    if t.eleitorado_apurado else None),
                "comparecimento_denominador": "eleitorado_secoes_apuradas",
            },
            "votos": {
                "total": t.total_votos, "validos": t.validos,
                "validos_computados": t.validos_computados,
                "nominal_tse": t.nominal_tse,
                "anulados": t.anulados,
                "anulados_sub_judice": t.anulados_sub_judice,
                "brancos": t.brancos, "nulos": t.nulos,
            },
            "pendente": {"min": t.pendente_min, "max": t.pendente_max,
                         "ambiguo": t.pendente_max != t.pendente_min},
            "candidatos": sorted([
                {
                    "sqcand": cands[r.candidate_id].sqcand,
                    "numero": cands[r.candidate_id].numero,
                    "nome_urna": cands[r.candidate_id].nome_urna,
                    "partido": cands[r.candidate_id].partido_sigla,
                    "votos": r.votos,
                    "destinacao_voto": r.dvt,
                    # Percentual só existe quando numerador e denominador
                    # pertencem ao mesmo universo. Um candidato com
                    # destinação 'Anulado sub judice' NÃO compõe `vv`;
                    # dividir os votos dele por `vv` produz uma razão que não
                    # significa nada (e que, no arquivo nacional real, o
                    # colocaria em primeiro lugar com 10,40%). Aqui isso vira
                    # `null` com motivo, como manda o §9.
                    "pct": _pct_coerente(r.votos, den, base, r.dvt),
                    "pct_indisponivel_motivo": _pct_motivo(den, base, r.dvt),
                    # A SITUAÇÃO oficial do TSE, literal. §4.7 exige
                    # rótulos distintos para "projetado", "matematicamente
                    # definido" e "eleito segundo TSE" — então nada aqui
                    # resume as três em um booleano de conveniência.
                    "situacao_oficial": cands[r.candidate_id].situacao,
                    # True SÓ quando a situação oficial diz "Eleito", e só no
                    # arquivo da abrangência que decide (§3). Em qualquer
                    # outro caso é None, nunca False — porque "não sabemos
                    # aqui" é diferente de "não foi eleito".
                    "eleito_segundo_tse": (
                        cands[r.candidate_id].eleito
                        if abrangencia == ct.abrangencia_decisoria else None),
                    "situacao_definida_nesta_abrangencia":
                        abrangencia == ct.abrangencia_decisoria,
                }
                for r in rows if r.candidate_id in cands
            ], key=lambda x: -x["votos"]),
        })

    saida.sort(key=lambda x: -(x["votos"]["validos"]))
    estado = "resultado_oficial" if all(
        t.andamento == "f" for t in totais) else "apuracao_em_andamento"
    return envelope(saida, snapshots=snaps, estado=estado, denominador=base,
                    quality_flags=sorted(flags),
                    extra={"disputa": {"cargo_cd": ct.cargo_cd,
                                       "cargo": ct.cargo_nome,
                                       "vagas": ct.vagas,
                                       "abrangencia_decisoria":
                                           ct.abrangencia_decisoria}})


@app.get("/api/v1/forecasts")
def forecasts(cargo: str, eleicao: str | None = None,
              s: Session = Depends(get_session)):
    """Projeção. Recusa publicar probabilidade sem homologação (§4.7)."""
    ct = _contest_or_404(s, cargo, eleicao)
    thr = load_thresholds()
    if not thr.homologated:
        return envelope(
            None, snapshots=[], estado="cenario_exploratorio",
            motivo=(
                "config/thresholds.yaml não está homologado. O gate do §11.2 "
                "exige superar o baseline M0 no erro mediano e demonstrar "
                "cobertura de intervalo compatível com o nominal, em "
                "backtests sobre eleições encerradas reais. Enquanto isso, a "
                "apuração oficial está disponível em /api/v1/results e "
                "nenhuma probabilidade de vitória é publicada."
            ),
            extra={"disputa": {"cargo": ct.cargo_nome, "vagas": ct.vagas},
                   "gate": thr.gates},
        )
    # Homologado: a projeção por disputa, com probabilidades, sai em
    # /api/v1/projection (a mesma que o telão usa). Antes daqui saía um 501
    # — que ninguém tinha visto porque o gate nunca tinha passado.
    return envelope(
        None, snapshots=[], estado="projecao_disponivel",
        motivo=None,
        extra={"disputa": {"cargo": ct.cargo_nome, "vagas": ct.vagas},
               "gate": thr.gates, "assinado_por": thr.signed_by,
               "assinado_em": thr.signed_at,
               "rota": "/api/v1/projection?cargo=<cd>&uf=<uf>"})


@app.get("/api/v1/map")
def mapa(cargo: str, eleicao: str | None = None,
         uf: str | None = None, s: Session = Depends(get_session)):
    """Dados compactos do mapa. Não repete geometria (§9)."""
    ct = _contest_or_404(s, cargo, eleicao)
    gq = select(Geography.id).where(Geography.environment == env_atual(),
                                    Geography.tipo == "mu")
    if uf:
        gq = gq.where(Geography.uf == uf.lower())
    geo_ids = [r[0] for r in s.execute(gq).all()]
    totais = _latest_snapshot_ids(s, ct.id, geo_ids)
    geos = {g.id: g for g in s.execute(select(Geography).where(
        Geography.id.in_([t.geography_id for t in totais]))).scalars().all()}
    cands = {c.id: c for c in s.execute(select(Candidate).where(
        Candidate.contest_id == ct.id)).scalars().all()}
    snaps = s.execute(select(SourceSnapshot).where(
        SourceSnapshot.id.in_([t.snapshot_id for t in totais]))).scalars().all()

    linhas = []
    for t in totais:
        rows = s.execute(select(ObservedResult).where(
            ObservedResult.snapshot_id == t.snapshot_id,
            ObservedResult.geography_id == t.geography_id,
            ObservedResult.dvt == "Válido",
        )).scalars().all()
        if not rows:
            continue
        rows.sort(key=lambda r: -r.votos)
        lider = cands.get(rows[0].candidate_id)
        segundo = rows[1].votos if len(rows) > 1 else 0
        g = geos[t.geography_id]
        linhas.append({
            "cd_ibge": g.cd_ibge, "cd_tse": g.cd_tse, "uf": g.uf,
            "nome": g.nome,
            "lider_sqcand": lider.sqcand if lider else None,
            "lider_partido": lider.partido_sigla if lider else None,
            # Intensidade = margem, como manda o §6.
            "margem_pp": ((100.0 * (rows[0].votos - segundo) / t.validos)
                          if t.validos else None),
            "andamento": t.andamento,
            "eleitorado": t.eleitorado_total,
            "cobertura_pct": (100.0 * t.eleitorado_apurado /
                              t.eleitorado_total) if t.eleitorado_total else None,
        })
    return envelope(linhas, snapshots=snaps, estado="observado",
                    denominador=DenBase.VALIDOS,
                    extra={"aviso_cartografico": (
                        "Área geográfica não é eleitorado. Acompanhar sempre "
                        "de barras ponderadas por votos (§6).")})


@app.get("/api/v1/timeline")
def timeline(cargo: str, eleicao: str | None = None,
             abrangencia: str = "br", uf: str | None = None,
             s: Session = Depends(get_session)):
    """Série para replay. Mostra só o que se sabia em cada instante (§5.2)."""
    ct = _contest_or_404(s, cargo, eleicao)
    gq = select(Geography.id).where(Geography.environment == env_atual(),
                                    Geography.tipo == abrangencia)
    if uf:
        gq = gq.where(Geography.uf == uf.lower())
    geo_ids = [r[0] for r in s.execute(gq).all()]

    linhas = s.execute(
        select(UnitTotals, SourceSnapshot)
        .join(SourceSnapshot, SourceSnapshot.id == UnitTotals.snapshot_id)
        .where(UnitTotals.contest_id == ct.id,
               UnitTotals.geography_id.in_(geo_ids))
        .order_by(SourceSnapshot.captured_at)
    ).all()
    serie = [{
        "captured_at": iso(sn.captured_at),
        "source_generated_at": iso(sn.source_generated_at),
        # Latência da fonte é medida separadamente da fila (§10).
        "latencia_fonte_s": ((aware(sn.captured_at)
                              - aware(sn.source_generated_at)).total_seconds()
                             if sn.source_generated_at else None),
        "andamento": t.andamento,
        "secoes_totalizadas": t.secoes_totalizadas,
        "secoes_total": t.secoes_total,
        "validos": t.validos,
        "comparecimento": t.comparecimento,
        "eleitorado_apurado": t.eleitorado_apurado,
    } for t, sn in linhas]
    snaps = [sn for _, sn in linhas]
    return envelope(serie, snapshots=snaps, estado="observado",
                    denominador=DenBase.VALIDOS)


@app.get("/api/v1/quality")
def quality(s: Session = Depends(get_session)):
    """Atrasos, cobertura e divergências (§9)."""
    total_snaps = s.execute(
        select(func.count()).select_from(SourceSnapshot)
        .where(SourceSnapshot.environment == env_atual())).scalar_one()
    ultimo = s.execute(
        select(func.max(SourceSnapshot.captured_at))
        .where(SourceSnapshot.environment == env_atual())).scalar_one()

    flags: dict[str, int] = {}
    for (qf,) in s.execute(select(UnitTotals.quality_flags)
                           .where(UnitTotals.quality_flags.is_not(None))).all():
        for f in json.loads(qf):
            k = f"{f['sev']}:{f['code']}"
            flags[k] = flags.get(k, 0) + 1

    por_disputa = []
    for ct in s.execute(select(Contest).join(
            Election, Election.id == Contest.election_id)
            .where(Election.environment == env_atual())).scalars().all():
        tot = s.execute(
            select(func.count(func.distinct(UnitTotals.geography_id)))
            .where(UnitTotals.contest_id == ct.id)).scalar_one()
        por_disputa.append({
            "cargo": ct.cargo_nome, "cargo_cd": ct.cargo_cd,
            "vagas": ct.vagas, "unidades_com_dado": tot,
        })

    criticos = sum(v for k, v in flags.items() if k.startswith("critical"))
    ultimo = aware(ultimo)
    return envelope({
        "snapshots": total_snaps,
        "ultima_captura": iso(ultimo),
        "idade_s": ((datetime.now(timezone.utc) - ultimo).total_seconds()
                    if ultimo else None),
        "flags": flags,
        "divergencias_criticas": criticos,
        "projecao_bloqueada_por_qualidade": criticos > 0,
        "disputas": por_disputa,
    }, snapshots=[], estado="operacional")


@app.get("/api/v1/classifications")
def classifications(s: Session = Depends(get_session)):
    """Metodologia e versão da classificação política (§6)."""
    from ..politics.classification import metodologia
    return envelope(metodologia(), snapshots=[], estado="metodologia")


@app.get("/api/v1/exports")
def exports(cargo: str, eleicao: str | None = None, limite: int = 5000,
            s: Session = Depends(get_session)):
    """Exportação limitada por escopo (§9). Paginação obrigatória."""
    if limite > 20000:
        raise HTTPException(400, "limite máximo de exportação é 20000 linhas")
    r = results(cargo=cargo, eleicao=eleicao, abrangencia="mu",
                base=DenBase.VALIDOS_COMPUTADOS, s=s)
    r["dados"] = r["dados"][:limite]
    r["export_limite"] = limite
    r["export_truncado"] = len(r["dados"]) >= limite
    return r


@app.get("/api/health")
def health():
    return {"ok": True, "ambiente": env_atual(),
            "api_version": API_VERSION}

# --------------------------------------------------------------------------
# painel estático
# --------------------------------------------------------------------------

_WEB = Path(__file__).resolve().parent.parent.parent / "web"


def _html_com_versao(arquivo: Path) -> HTMLResponse:
    """Serve o HTML com `mapa.js` versionado pela data do arquivo.

    Cabeçalho de cache resolve o próximo carregamento; não resolve o que já
    está guardado. Um navegador que cacheou `/mapa.js` sem instrução
    aplica prazo heurístico e segue servindo a versão antiga — o painel
    mostrava camadas de mapa que não existiam mais no código.

    Versionar o endereço torna a questão inexistente: arquivo novo, URL
    nova, nada a invalidar. O carimbo sai da própria mtime, então ninguém
    precisa lembrar de incrementar um número.
    """
    html = arquivo.read_text(encoding="utf-8")
    try:
        v = int((_WEB / "mapa.js").stat().st_mtime)
    except OSError:
        v = 0
    html = html.replace('src="/mapa.js"', f'src="/mapa.js?v={v}"')
    try:
        v2 = int((_WEB / "politico.js").stat().st_mtime)
    except OSError:
        v2 = 0
    html = html.replace('src="/politico.js"', f'src="/politico.js?v={v2}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def painel():
    """Serve o painel. O navegador só fala com esta API, nunca com o TSE."""
    return _html_com_versao(_WEB / "index.html")


# Registrado por último, quando os auxiliares acima já existem.
from . import decided_audit as _decided_audit  # noqa: E402
from . import geo as _geo  # noqa: E402
from . import candidatos as _candidatos  # noqa: E402
from . import projecao as _projecao  # noqa: E402
from . import telao as _telao  # noqa: E402
from . import convergencia as _convergencia  # noqa: E402
from . import fotos as _fotos  # noqa: E402
from . import compartilhar as _compartilhar  # noqa: E402
from . import politico as _politico  # noqa: E402
from . import configuracao as _configuracao  # noqa: E402
from . import historico as _historico  # noqa: E402

_decided_audit.register(app)
_geo.register(app)
_candidatos.register(app)
_projecao.register(app)
_telao.register(app)
_convergencia.register(app)
_fotos.register(app)
_compartilhar.register(app)
_politico.register(app)
_configuracao.register(app)
_historico.register(app)
from . import alertas as _alertas  # noqa: E402
_alertas.register(app)
