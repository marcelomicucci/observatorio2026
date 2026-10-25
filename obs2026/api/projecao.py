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
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session


#: Uma trava por disputa para o cálculo de exibição.
_TRAVAS: dict[tuple, threading.Lock] = {}
#: Última resposta "aguardando" de cada disputa no modo exibição, e por
#: quanto tempo ela vale antes de tentar calcular de novo.
_SEM_PROJECAO: dict[tuple, tuple[float, dict]] = {}
SEM_PROJECAO_S = 45.0


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment, load_thresholds
    from ..engine import audit as A
    from ..engine import decided as DC
    from ..engine import nivel as NV
    from ..engine import runner as R
    from ..engine.denominators import Base as DenBase
    from ..storage.raw import RawArchive

    def _regra_probabilidade(apurado: float, cargo: str) -> dict:
        """Por que a probabilidade saiu ou não, para a tela dizer."""
        return {"publicada": NV.publica_probabilidade(apurado, cargo),
                "apurado_pct": apurado,
                "apurado_min_pct": NV.piso_probabilidade(cargo)}

    def _chamada_de(r, ct) -> dict:
        """A mesma chamada, quando a resposta vem de um registro do diário."""
        from ..config import load_environment
        env = load_environment(M.env_atual())
        nomes = {c.sqcand: c.nome for c in r.candidatos}
        return _chamada(env, ct, None if r.unidade == "br" else r.unidade,
                        r.modelo, nomes)

    def _chamada(env, ct, uf, modelo, nomes: dict) -> dict:
        """Vencedor projetado, com a régua dura do §4.7
        (`engine/chamada.py`): 99%, estável em 3 registros e 20% apurado."""
        from ..engine import chamada as CH
        d = A.Diario(DATA_DIR / "previsoes", env.name)
        d.ultimos()
        chave = (ct.cargo_cd, (uf.lower() if uf else "br"), modelo)
        ch = CH.avaliar(d.recentes(chave), vagas=ct.vagas)
        return {"apta": ch.apta, "motivo": ch.motivo, "tipo": ch.tipo,
                "candidatos": [{"sqcand": c, "nome": nomes.get(c, c)}
                               for c in ch.sqcands],
                "probabilidade_minima": ch.p_min_observada,
                "registros_estaveis": ch.registros_estaveis,
                "regra": {"p_min": CH.P_MIN, "registros": CH.ESTAVEL_N,
                          "apurado_min_pct": CH.APURADO_MIN},
                "nota": ("Vencedor projetado é probabilidade, não conta: "
                         "diferente de 'matematicamente definido'.")}

    def _quantis(c) -> dict:
        """Quantis de um candidato do diário, nos nomes de `NV.CHAVES`."""
        return {"q500": c.pct_concorrentes_q500,
                "q100": c.pct_concorrentes_q100,
                "q900": c.pct_concorrentes_q900,
                "q050": c.pct_concorrentes_q050,
                "q950": c.pct_concorrentes_q950,
                "q025": c.pct_concorrentes_q025,
                "q975": c.pct_concorrentes_q975}

    def _referencia_trava(ambiente: str, chave: tuple, apurado: float,
                          pendente_max: int) -> dict:
        """`{sqcand: quantis}` do último registro abaixo de 95% apurado,
        quando a disputa já passou disso e ainda falta voto. Vazio fora
        desse trecho: a faixa publicada é a do modelo."""
        if apurado < NV.TRAVA_LARGURA_APURADO or not pendente_max:
            return {}
        d = A.Diario(DATA_DIR / "previsoes", ambiente)
        d.ultimos()
        r = d.referencia_da_trava(chave)
        return {c.sqcand: _quantis(c) for c in r.candidatos} if r else {}

    def _faixa(qs: dict, nivel: int, rotulo: str) -> dict:
        lo, hi = NV.faixa(qs, nivel)
        # Registro antigo, sem os quantis largos: publica 80% e diz isso.
        real = nivel if (qs.get(NV.CHAVES[nivel][0]) is not None) else 80
        return {"nivel": real, "rotulo": rotulo if real == nivel
                else "tendência", "pct_min": lo, "pct_max": hi}

    def _de_registro(r, ct, idade: float) -> dict:
        """Monta a resposta a partir de um registro do diário, no mesmo
        formato do cálculo ao vivo — quem consome não precisa saber."""
        publicavel = r.estado_publicacao == "projecao_disponivel"
        com_prob = publicavel and NV.publica_probabilidade(r.cobertura_pct,
                                                    r.cargo)
        ref = _referencia_trava(r.ambiente, (r.cargo, r.unidade, r.modelo),
                                r.cobertura_pct, r.pendente_max)
        linhas = [{
            "sqcand": c.sqcand,
            "nome": c.nome,
            "votos_observados": c.votos_observados,
            "projecao": {
                "votos_mediana": c.votos_q500,
                "pct_mediana": c.pct_concorrentes_q500,
                "pct_q100": c.pct_concorrentes_q100,
                "pct_q900": c.pct_concorrentes_q900,
                "faixa": _faixa(NV.travar_largura(
                    _quantis(c), ref.get(c.sqcand)),
                    *NV.nivel_por_apurado(r.cobertura_secoes_pct)),
                "base": DenBase.VALIDOS_COMPUTADOS.value,
            },
            "probabilidades": ({
                "lideranca": c.prob_lideranca,
                "dentro_das_vagas": c.prob_dentro_vagas,
                "maioria_absoluta": c.prob_maioria_absoluta,
            } if com_prob else None),
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
                "probabilidade": _regra_probabilidade(r.cobertura_pct,
                                                      r.cargo),
                "faixa_travada": bool(ref),
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
                "chamada": _chamada_de(r, ct),
                "definicao": {
                    "disputa_definida": r.disputa_matematicamente_definida,
                    "segundo_turno_definido":
                        r.segundo_turno_matematicamente_definido,
                },
            })

    @app.get("/api/v1/projection")
    def projecao(cargo: str, uf: str | None = None,
                 modelo: str | None = None,
                 sims: int | None = None, registrar: bool = True,
                 # Idade aceitável do último registro antes de recalcular.
                 # Eram 300 s, menos do que o coletor leva para dar a volta
                 # nas 55 disputas (6 a 10 min com a apuração em andamento):
                 # quase todo pedido de presidente recalculava do zero, e
                 # recalcular com o coletor trabalhando levou 56 s no
                 # simulado de 23/09. Servir o registro de 8 minutos atrás,
                 # com a hora dele na tela, é melhor do que travar o telão
                 # por um número um pouco mais novo.
                 max_idade_s: int = 900,
                 s: Session = Depends(M.get_session)):
        """Roda a projeção da disputa e devolve quantis por candidato."""
        env = load_environment(M.env_atual())
        thr = load_thresholds()
        if modelo is None:
            # Sem pedido explícito, o modelo em produção (aba Configuração).
            # Resolvido aqui, antes do cache, para a chave do diário ser a
            # do modelo que de fato roda.
            from ..operacao import modelo_ativo
            modelo = modelo_ativo()
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
                # Sem o que projetar (antes do primeiro voto: "nenhum
                # candidato valido"), o cálculo lê a disputa inteira para
                # dizer que não há projeção — 8 s para presidente em
                # 04/10/2026 — e não deixa registro. Sem guardar essa
                # resposta, cada tela do telão pagava os 8 s de novo.
                memo = _SEM_PROJECAO.get(chave)
                if memo and time.monotonic() - memo[0] < SEM_PROJECAO_S:
                    return memo[1]
                resp = _calcular(env, thr, ct, uf, modelo, sims,
                                 registrar=True)
                if resp.get("status") == "aguardando":
                    _SEM_PROJECAO[chave] = (time.monotonic(), resp)
                else:
                    _SEM_PROJECAO.pop(chave, None)
                return resp
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
                      if reg else thr.vale_para(modelo))
        q = fc.quantis((0.025, 0.05, 0.10, 0.50, 0.90, 0.95, 0.975))
        nivel, rotulo = NV.nivel_por_apurado(
            100.0 * ctx.secoes_totalizadas / ctx.secoes_total
            if ctx.secoes_total else 0.0)
        lider = fc.prob_lideranca()
        vagas = fc.prob_dentro_das_vagas()
        maioria = fc.prob_maioria_absoluta()
        apurado = (100.0 * ctx.eleitorado_apurado / ctx.eleitorado_total
                   if ctx.eleitorado_total else 0.0)
        com_prob = publicavel and NV.publica_probabilidade(apurado,
                                                            ct.cargo_cd)
        ref = _referencia_trava(
            env.name, (ct.cargo_cd, uf.lower() if uf else "br", modelo),
            apurado, ctx.pendente_max)
        estados = {x.sqcand: x for x in dfn.situacoes}
        # Cenário de cassação dos votos sub judice: vai junto, nunca no
        # lugar do outro. Duas informações persistentes (pedido de 23/09).
        cassados = {x.sqcand: x for x in (dfn.situacoes_cassado or ())}

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
                    # 80% sempre: é a série que o gráfico de
                    # convergência compara ao longo da noite.
                    "pct_q100": q[c]["pct_concorrentes"]["q100"],
                    "pct_q900": q[c]["pct_concorrentes"]["q900"],
                    # Faixa publicada: o nível sobe com o apurado.
                    "faixa": _faixa(NV.travar_largura(
                        q[c]["pct_concorrentes"], ref.get(c)), nivel, rotulo),
                    "base": DenBase.VALIDOS_COMPUTADOS.value,
                },
                # §4.7: probabilidade só depois de o gate passar. `null` com
                # motivo, nunca um número que pareça homologado.
                "probabilidades": ({
                    "lideranca": lider.get(c),
                    "dentro_das_vagas": vagas.get(c),
                    "maioria_absoluta": maioria.get(c),
                } if com_prob else None),
                "estado_matematico": e.estado.value if e else "em_aberto",
                "estado_matematico_se_cassados": (
                    (cassados[c].estado.value if c in cassados
                     else "fora_da_disputa")
                    if dfn.situacoes_cassado is not None else None),
            })
        linhas.sort(key=lambda x: -x["projecao"]["votos_mediana"])

        return M.envelope(
            linhas, snapshots=[],
            estado=(reg.estado_publicacao if reg
                    else "cenario_exploratorio"),
            denominador=DenBase.VALIDOS_COMPUTADOS,
            motivo=(None if publicavel else
                    "config/thresholds.yaml não homologado: os intervalos "
                    "abaixo são cenário identificado, não previsão "
                    "publicada, e nenhuma probabilidade de vitória é "
                    "divulgada (§4.7)."),
            extra={
                "publicavel": publicavel,
                "probabilidade": _regra_probabilidade(apurado, ct.cargo_cd),
                "faixa_travada": bool(ref),
                "nivel_intervalo": nivel,
                "chamada": _chamada(env, ct, uf, modelo, ctx.nomes),
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
                    "cenarios_divergem": dfn.sub_judice_em_jogo,
                    "cenario_base": "votos sub judice validados",
                    "sub_judice_em_jogo": dfn.sub_judice_em_jogo,
                },
            })
