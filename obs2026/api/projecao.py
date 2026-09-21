"""Rota de projeção ao vivo, para o painel e o telão.

Diferente de `/forecasts`, que só responde sobre o **estado de publicação**,
esta rota executa o motor e devolve os números — sempre com o estado junto.

A distinção que o §4.7 exige é mantida no payload, não na boa vontade de quem
consome:

    estado_publicacao   'projecao_disponivel' só quando o gate passou
    publicavel          booleano explícito
    probabilidades      `null` enquanto o gate não passou

Os quantis saem mesmo em `cenario_exploratorio`, porque cenário identificado é
exatamente o que o §4.7 permite exibir. O que não sai é probabilidade de
vitória, que é a afirmação forte.

Toda execução é registrada no diário de auditoria antes de virar resposta: não
existe caminho que produza número de projeção sem deixar rastro (§7).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session


#: Uma trava por disputa para o cálculo de exibição.
_TRAVAS: dict[tuple, threading.Lock] = {}


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment, load_thresholds
    from ..engine import audit as A
    from ..engine import decided as DC
    from ..engine import runner as R
    from ..engine.denominators import Base as DenBase
    from ..storage.raw import RawArchive

    def _de_registro(r, ct, idade: float) -> dict:
        """Monta a resposta a partir de um registro do diário, no mesmo
        formato do cálculo ao vivo — quem consome não precisa saber."""
        publicavel = r.estado_publicacao == "projecao_disponivel"
        linhas = [{
            "sqcand": c.sqcand,
            "nome": c.nome,
            "votos_observados": c.votos_observados,
            "projecao": {
                "votos_mediana": c.votos_q500,
                "pct_mediana": c.pct_concorrentes_q500,
                "pct_q100": c.pct_concorrentes_q100,
                "pct_q900": c.pct_concorrentes_q900,
                "base": DenBase.VALIDOS_COMPUTADOS.value,
            },
            "probabilidades": ({
                "lideranca": c.prob_lideranca,
                "dentro_das_vagas": c.prob_dentro_vagas,
                "maioria_absoluta": c.prob_maioria_absoluta,
            } if publicavel else None),
            "estado_matematico": c.estado_matematico,
        } for c in r.candidatos]
        linhas.sort(key=lambda x: -x["projecao"]["votos_mediana"])
        return M.envelope(
            linhas, snapshots=[], estado=r.estado_publicacao,
            denominador=DenBase.VALIDOS_COMPUTADOS,
            motivo=(None if publicavel else
                    "config/thresholds.yaml não homologado: os intervalos "
                    "abaixo são cenário identificado, não previsão "
                    "publicada, e nenhuma probabilidade de vitória é "
                    "divulgada (§4.7)."),
            extra={
                "publicavel": publicavel,
                "origem": "registro",
                "registrado_em": r.momento,
                "idade_s": round(idade, 1),
                "disputa": {"cargo": ct.cargo_nome, "vagas": ct.vagas,
                            "unidade": r.unidade},
                "modelo": r.modelo,
                "semente": r.semente,
                "n_sims": r.n_sims,
                "erro_monte_carlo_pp": r.erro_monte_carlo_pp,
                "cobertura_pct": (100.0 * r.eleitorado_apurado
                                  / r.eleitorado_total
                                  if r.eleitorado_total else None),
                "pendente_min": r.pendente_min,
                "pendente_max": r.pendente_max,
                "registro_id": r.registro_id,
                "definicao": {
                    "disputa_definida": r.disputa_matematicamente_definida,
                    "segundo_turno_definido":
                        r.segundo_turno_matematicamente_definido,
                },
            })

    @app.get("/api/v1/projection")
    def projecao(cargo: str, uf: str | None = None, modelo: str = "m1",
                 sims: int | None = None, registrar: bool = True,
                 max_idade_s: int = 300,
                 s: Session = Depends(M.get_session)):
        """Roda a projeção da disputa e devolve quantis por candidato."""
        env = load_environment(M._ENV_NAME)
        thr = load_thresholds()
        ct = M._contest_or_404(s, cargo, None)
        if ct.abrangencia_decisoria == "uf" and not uf:
            raise HTTPException(
                400, f"{ct.cargo_nome} é decidido por UF; informe uf=")

        # Exibição (telão, legenda do WhatsApp) — `registrar=false`.
        #
        # Serve a última projeção REGISTRADA, se for recente. Calcular na
        # hora custava 5,9 s por pedido para presidente, e o telão e a
        # legenda pediam de novo a cada tela, enquanto o coletor já registra
        # todas as disputas a cada 2 minutos.
        #
        # Sem registro recente (o coletor parado, por exemplo), calcula E
        # REGISTRA. Antes, o modo exibição calculava sem registrar — um
        # número mostrado na sala que não ficava no diário, contra a regra de
        # que toda projeção exibida é gravada. Agora não existe esse caminho:
        # o que a sala vê é sempre um registro. A trava por disputa impede
        # que o telão e a legenda, pedindo juntos, calculem duas vezes.
        if not registrar and max_idade_s > 0:
            unidade = uf.lower() if uf else "br"
            chave = (ct.cargo_cd, unidade, modelo)
            trava = _TRAVAS.setdefault(chave, threading.Lock())
            with trava:
                r = A.Diario(DATA_DIR / "previsoes",
                             env.name).ultimos().get(chave)
                if r is not None:
                    idade = (datetime.now(timezone.utc)
                             - datetime.fromisoformat(r.momento)
                             ).total_seconds()
                    if idade <= max_idade_s:
                        return _de_registro(r, ct, idade)
                return _calcular(env, thr, ct, uf, modelo, sims,
                                 registrar=True)
        return _calcular(env, thr, ct, uf, modelo, sims, registrar=registrar)

    def _calcular(env, thr, ct, uf, modelo, sims, *, registrar: bool):
        try:
            fc, dfn, ctx, reg = R.executar(
                env, thr, cargo=ct.cargo_cd,
                uf=(uf.lower() if uf else None), modelo=modelo,
                n_sims=sims or 2000,
                archive=RawArchive(DATA_DIR / "raw", env.name),
                diario=A.Diario(DATA_DIR / "previsoes", env.name),
                registrar=registrar,
            )
        except ValueError as exc:
            return M.envelope(None, snapshots=[], estado="aguardando",
                              motivo=str(exc))

        publicavel = (reg.estado_publicacao == "projecao_disponivel"
                      if reg else thr.homologated)
        q = fc.quantis((0.10, 0.50, 0.90))
        lider = fc.prob_lideranca()
        vagas = fc.prob_dentro_das_vagas()
        maioria = fc.prob_maioria_absoluta()
        estados = {x.sqcand: x for x in dfn.situacoes}

        linhas = []
        for c in fc.candidates:
            e = estados.get(c)
            linhas.append({
                "sqcand": c,
                "nome": ctx.nomes.get(c, c),
                "votos_observados": fc.observed.get(c, 0),
                "projecao": {
                    "votos_mediana": q[c]["votos"]["q500"],
                    "pct_mediana": q[c]["pct_concorrentes"]["q500"],
                    "pct_q100": q[c]["pct_concorrentes"]["q100"],
                    "pct_q900": q[c]["pct_concorrentes"]["q900"],
                    "base": DenBase.VALIDOS_COMPUTADOS.value,
                },
                # §4.7: probabilidade só depois de o gate passar. `null` com
                # motivo, nunca um número que pareça homologado.
                "probabilidades": ({
                    "lideranca": lider.get(c),
                    "dentro_das_vagas": vagas.get(c),
                    "maioria_absoluta": maioria.get(c),
                } if publicavel else None),
                "estado_matematico": e.estado.value if e else "em_aberto",
            })
        linhas.sort(key=lambda x: -x["projecao"]["votos_mediana"])

        return M.envelope(
            linhas, snapshots=[], estado=(reg.estado_publicacao if reg
                                          else "cenario_exploratorio"),
            denominador=DenBase.VALIDOS_COMPUTADOS,
            motivo=(None if publicavel else
                    "config/thresholds.yaml não homologado: os intervalos "
                    "abaixo são cenário identificado, não previsão "
                    "publicada, e nenhuma probabilidade de vitória é "
                    "divulgada (§4.7)."),
            extra={
                "publicavel": publicavel,
                "disputa": {"cargo": ct.cargo_nome, "vagas": ct.vagas,
                            "unidade": ctx.unidade},
                "modelo": modelo,
                "semente": fc.seed,
                "n_sims": fc.n_sims,
                "erro_monte_carlo_pp": fc.diagnostics.get(
                    "erro_monte_carlo_pp"),
                "cobertura_pct": (100.0 * ctx.eleitorado_apurado
                                  / ctx.eleitorado_total
                                  if ctx.eleitorado_total else None),
                "pendente_min": ctx.pendente_min,
                "pendente_max": ctx.pendente_max,
                "unidades": ctx.unidades,
                "registro_id": reg.registro_id if reg else None,
                "definicao": {
                    "disputa_definida": dfn.disputa_definida,
                    "segundo_turno_definido": dfn.segundo_turno_definido,
                    "sub_judice_em_jogo": dfn.sub_judice_em_jogo,
                },
            })
