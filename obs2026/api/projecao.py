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

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment, load_thresholds
    from ..engine import audit as A
    from ..engine import decided as DC
    from ..engine import runner as R
    from ..engine.denominators import Base as DenBase
    from ..storage.raw import RawArchive

    @app.get("/api/v1/projection")
    def projecao(cargo: str, uf: str | None = None, modelo: str = "m1",
                 sims: int | None = None, registrar: bool = True,
                 s: Session = Depends(M.get_session)):
        """Roda a projeção da disputa e devolve quantis por candidato."""
        env = load_environment(M._ENV_NAME)
        thr = load_thresholds()
        ct = M._contest_or_404(s, cargo, None)
        if ct.abrangencia_decisoria == "uf" and not uf:
            raise HTTPException(
                400, f"{ct.cargo_nome} é decidido por UF; informe uf=")

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
