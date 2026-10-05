"""Recordes da noite: tela pronta para o telão (pedido de 22/09/2026).

Tudo sai do que já foi coletado — nenhum arquivo novo, nenhuma projeção:

- **Apuração mais rápida** (estado e município): o primeiro boletim em que a
  unidade chegou a 100% das seções, com o horário de totalização do TSE
  (`dt/ht`). Enquanto nenhuma fechou, mostra a mais adiantada agora.
  Referência: presidente, que tem arquivo de todas as UFs e municípios.
- **Votos absolutos e relativos** de governador, senador e deputado federal:
  último total de cada UF. Relativo = votos ÷ votos nominais da UF, a mesma
  base do percentual do TSE (no Senado cada eleitor vota duas vezes; o TSE
  divide pelo total de votos dados, e aqui também).
- **Menor diferença**: 1º × 2º colocado, em governador e em senador
  (decisão do usuário em 22/09: também no Senado, mesmo com duas vagas).
- Extras: maior e menor abstenção por UF e o município mais unânime para
  presidente.

Durante a apuração todo recorde é "até agora" e a tela diz isso; o que vem
de UF não encerrada leva `provisorio: true`.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..politics import partidos as PA

BRT = timezone(timedelta(hours=-3))
#: Horário unificado de Brasília: as urnas fecham às 17h em todo o país.
FECHAMENTO = time(17, 0)
#: Município "mais unânime" só entre os que têm votos para isso significar
#: algo — em 30 votos, 95% é acaso.
MIN_VALIDOS_UNANIME = 5000


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


def register(app, unidades, leitor_camara) -> None:
    """`unidades(s, contest, tipo)` e `leitor_camara()` vêm de politico.py,
    para ler os totais do mesmo jeito que as outras telas políticas."""
    from . import app as M
    from ..storage.models import (Election, Geography, SourceSnapshot,
                                  UnitTotals)

    def _partido(c) -> dict:
        return PA.resolver(c.partido_numero, c.partido_sigla).as_dict()

    def _cand(u: dict, c, votos: int, base: int) -> dict:
        g, t = u["geo"], u["t"]
        return {
            "nome": c.nome_urna or c.nome, "numero": c.numero,
            "partido": _partido(c), "uf": g.uf,
            "votos": votos, "pct": (100.0 * votos / base) if base else None,
            "eleito": bool(c.eleito),
            "provisorio": t.andamento != "f",
        }

    def _base(u: dict) -> int:
        # Σ vap da unidade: o denominador do pvap do TSE.
        return u["t"].nominal_tse or sum(v for _, v in u["rows"])

    def _majoritario(s: Session, cargo: str) -> dict | None:
        try:
            ct = M._contest_or_404(s, cargo, None)
        except Exception:                                    # noqa: BLE001
            return None
        us = [u for u in unidades(s, ct, "uf") if u["rows"]]
        if not us:
            return {"vagas": ct.vagas, "absoluto": None, "relativo": None,
                    "menor_diferenca": None}
        todos = [(u, c, v) for u in us for c, v in u["rows"]]
        a = max(todos, key=lambda x: x[2])
        r = max(todos, key=lambda x: (x[2] / _base(x[0])) if _base(x[0])
                else 0)
        # Menor diferença: sempre 1º × 2º, também no Senado (duas vagas).
        k = 1
        dif = []
        for u in us:
            if len(u["rows"]) <= k:
                continue
            (c1, v1), (c2, v2) = u["rows"][k - 1], u["rows"][k]
            b = _base(u)
            dif.append({
                "uf": u["geo"].uf,
                "dentro": _cand(u, c1, v1, b), "fora": _cand(u, c2, v2, b),
                "votos": v1 - v2,
                "pp": (100.0 * (v1 - v2) / b) if b else None,
                "posicoes": [k, k + 1],
                "apurado_pct": (100.0 * u["t"].secoes_totalizadas
                                / u["t"].secoes_total)
                if u["t"].secoes_total else None,
                "provisorio": u["t"].andamento != "f",
            })
        dif.sort(key=lambda d: d["votos"])
        return {
            "vagas": ct.vagas,
            "absoluto": _cand(a[0], a[1], a[2], _base(a[0])),
            "relativo": _cand(r[0], r[1], r[2], _base(r[0])),
            "menor_diferenca": dif[0] if dif else None,
            "menores_diferencas": dif[:5],
        }

    def _deputados() -> dict | None:
        por_uf = leitor_camara().por_uf()
        todos = []
        for uf, r in por_uf.items():
            base = sum(x["votos"] for x in r.get("listas") or [])
            for x in r.get("listas") or []:
                for c in x["candidatos"]:
                    todos.append((uf, r, c, base))
        # Antes do primeiro voto o arquivo já lista os candidatos, todos
        # com zero: não há recorde, e o primeiro da lista não é "o mais
        # votado".
        if not any(x[2]["votos"] > 0 for x in todos):
            return None

        def item(uf, r, c, base):
            return {"nome": c["nome"], "numero": c["numero"],
                    "partido": PA.resolver(c["partido_numero"],
                                           c["partido_sigla"]).as_dict(),
                    "uf": uf, "votos": c["votos"],
                    # Base: votos válidos da UF (nominais + legenda), como o
                    # TSE calcula o percentual de deputado.
                    "pct": (100.0 * c["votos"] / base) if base else None,
                    "eleito": c["eleito_tse"],
                    "provisorio": r.get("andamento") != "f"}
        a = max(todos, key=lambda x: x[2]["votos"])
        rel = max(todos, key=lambda x: x[2]["votos"] / x[3] if x[3] else 0)
        return {"absoluto": item(*a), "relativo": item(*rel)}

    def _mais_rapida(s: Session, ct, tipo: str, data_eleicao) -> dict:
        """Primeiro instante em que cada unidade teve 100% das seções,
        contado depois da última vez em que esteve incompleta. Sem isso, um
        ciclo antigo já fechado (o simulado de 18/09 veio todo a 100%)
        venceria qualquer apuração nova."""
        instante = func.coalesce(UnitTotals.totalizado_em,
                                 SourceSnapshot.source_generated_at,
                                 SourceSnapshot.captured_at)
        base = (select(UnitTotals.geography_id.label("g"),
                       instante.label("q"),
                       (UnitTotals.secoes_totalizadas
                        >= UnitTotals.secoes_total).label("ok"))
                .join(SourceSnapshot,
                      SourceSnapshot.id == UnitTotals.snapshot_id)
                .join(Geography, Geography.id == UnitTotals.geography_id)
                .where(UnitTotals.contest_id == ct.id, Geography.tipo == tipo,
                       Geography.exterior.is_(False),
                       UnitTotals.secoes_total > 0)).subquery()
        ultima_incompleta = (select(base.c.g, func.max(base.c.q).label("u"))
                             .where(base.c.ok.is_(False))
                             .group_by(base.c.g)).subquery()
        rows = s.execute(
            select(base.c.g.label("geography_id"), func.min(base.c.q)
                   .label("q"))
            .outerjoin(ultima_incompleta, ultima_incompleta.c.g == base.c.g)
            .where(base.c.ok.is_(True),
                   (ultima_incompleta.c.u.is_(None))
                   | (base.c.q > ultima_incompleta.c.u))
            .group_by(base.c.g)).all()
        # Início do ciclo atual: a primeira unidade incompleta do último dia
        # com unidade incompleta. Município que não foi recoletado neste
        # ciclo (ficou com o 100% de um ciclo antigo) não entra.
        incompletas = [q for (q,) in s.execute(
            select(base.c.q).where(base.c.ok.is_(False)))]
        if incompletas:
            dia = max(incompletas).date()
            inicio = min(q for q in incompletas if q.date() == dia)
            rows = [r for r in rows if r.q >= inicio]
        fech = (datetime.combine(data_eleicao.date(), FECHAMENTO, BRT)
                if data_eleicao else None)

        def desde(q):
            if q is None or fech is None:
                return None
            q = q if q.tzinfo else q.replace(tzinfo=timezone.utc)
            m = (q - fech).total_seconds() / 60
            # Fora da noite (simulado com data de abril, reprocessamento),
            # "minutos após o fechamento" não significa nada.
            return round(m) if 0 <= m <= 24 * 60 else None

        if rows:
            rows.sort(key=lambda r: r.q)
            q0 = rows[0].q
            empatados = [r for r in rows if r.q == q0]
            geos = {g.id: g for g in s.execute(select(Geography).where(
                Geography.id.in_([r.geography_id for r in rows[:10]]
                                 + [r.geography_id for r in empatados])))
                .scalars()}
            g = geos[rows[0].geography_id]
            return {
                "estado": "concluida", "uf": g.uf, "nome": g.nome,
                "cd_ibge": g.cd_ibge, "concluida_em": _iso(q0),
                "minutos_apos_fechamento": desde(q0),
                "empatados": len(empatados),
                "empatados_nomes": [f"{geos[r.geography_id].nome} "
                                    f"({geos[r.geography_id].uf.upper()})"
                                    for r in empatados[1:6]],
                "concluidas": len(rows),
                "seguintes": [{"uf": geos[r.geography_id].uf,
                               "nome": geos[r.geography_id].nome,
                               "concluida_em": _iso(r.q),
                               "minutos_apos_fechamento": desde(r.q)}
                              for r in rows[1:5]
                              if r.geography_id in geos],
            }
        # Nenhuma fechou: a mais adiantada agora.
        us = unidades(s, ct, tipo)
        us = [u for u in us if u["t"].secoes_total
              and not u["geo"].exterior]
        if not us:
            return {"estado": "aguardando"}
        u = max(us, key=lambda u: (u["t"].secoes_totalizadas
                                   / u["t"].secoes_total))
        if not u["t"].secoes_totalizadas:
            return {"estado": "aguardando"}
        return {"estado": "em_andamento", "uf": u["geo"].uf,
                "nome": u["geo"].nome,
                "apurado_pct": 100.0 * u["t"].secoes_totalizadas
                / u["t"].secoes_total}

    def _extras(s: Session, ct_pres) -> dict:
        out: dict = {}
        us = [u for u in unidades(s, ct_pres, "uf")
              if not u["geo"].exterior and u["t"].eleitorado_apurado]
        if us:
            def abst(u):
                t = u["t"]
                return 100.0 * t.abstencao / t.eleitorado_apurado
            hi, lo = max(us, key=abst), min(us, key=abst)
            out["abstencao"] = {
                "maior": {"uf": hi["geo"].uf, "pct": abst(hi),
                          "provisorio": hi["t"].andamento != "f"},
                "menor": {"uf": lo["geo"].uf, "pct": abst(lo),
                          "provisorio": lo["t"].andamento != "f"}}
        mus = [u for u in unidades(s, ct_pres, "mu")
               if u["rows"] and not u["geo"].exterior
               and _base(u) >= MIN_VALIDOS_UNANIME]
        if mus:
            u = max(mus, key=lambda u: u["rows"][0][1] / _base(u))
            c, v = u["rows"][0]
            out["municipio_unanime"] = {**_cand(u, c, v, _base(u)),
                                        "municipio": u["geo"].nome,
                                        "minimo_validos":
                                            MIN_VALIDOS_UNANIME}
        return out

    _memo: dict = {}

    @app.get("/api/v1/recordes")
    def recordes(s: Session = Depends(M.get_session)):
        # A conta varre os 5.570 municípios de presidente (~7 s): refeita no
        # máximo a cada 30 s, e só se chegou snapshot novo.
        ult_id = s.execute(select(func.max(SourceSnapshot.id)).where(
            SourceSnapshot.environment == M.env_atual())).scalar()
        agora = datetime.now(timezone.utc)
        m = _memo.get(M.env_atual())
        if m and (m[0] == ult_id or (agora - m[1]).total_seconds() < 30):
            return m[2]
        dados: dict = {"apuracao": {}, "extras": {}}
        try:
            ct_pres = M._contest_or_404(s, "1", None)
        except Exception:                                    # noqa: BLE001
            ct_pres = None
        if ct_pres is not None:
            el = s.get(Election, ct_pres.election_id)
            data = el.data if el else None
            dados["apuracao"] = {
                "estado": _mais_rapida(s, ct_pres, "uf", data),
                "municipio": _mais_rapida(s, ct_pres, "mu", data),
                "referencia": "presidente",
                "fechamento_urnas": (datetime.combine(
                    data.date(), FECHAMENTO, BRT).isoformat()
                    if data else None)}
            dados["extras"] = _extras(s, ct_pres)
        dados["governador"] = _majoritario(s, "3")
        dados["senador"] = _majoritario(s, "5")
        dados["deputado_federal"] = _deputados()
        ult = s.execute(select(func.max(SourceSnapshot.source_generated_at))
                        .where(SourceSnapshot.environment == M.env_atual())
                        ).scalar()
        dados["nota"] = ("Até agora: durante a apuração, cada recorde vale "
                         "para os votos contados. Sem projeção.")
        r = M.envelope(dados, snapshots=[], estado="observado",
                       extra={"source_generated_at": _iso(ult)})
        _memo[M.env_atual()] = (ult_id, agora, r)
        return r
