"""Convergência: o apurado e o projetado ao longo da apuração.

A pergunta que este gráfico responde é a única que importa sobre uma
projeção: **ela estava certa antes de o resultado aparecer?**

Duas linhas por candidato, no mesmo eixo e no mesmo denominador:

    apurado     o percentual que estava contado naquele instante. Começa
                longe do final, porque os primeiros territórios a chegar não
                são amostra aleatória, e caminha até o resultado.
    projetado   a mediana que o modelo dizia naquele instante, com a faixa
                de 80%.

Se o modelo presta, a linha **projetada nasce perto do resultado final e
fica lá**, enquanto a apurada sobe ou desce até encontrá-la. Se o modelo não
presta, as duas passeiam juntas — o que significa que a projeção não estava
antecipando nada, apenas repetindo o parcial.

O eixo horizontal é a **cobertura do eleitorado apurado**, não o relógio.
Cobertura é a variável que de fato governa o erro, e permite comparar
disputas que apuraram em ritmos diferentes.

Tudo sai do diário de auditoria, que grava cada projeção no instante em que
foi feita — incluindo `concorrentes_observados`, o denominador daquele
momento. Sem esse campo, a linha do apurado e a do projetado teriam
denominadores diferentes, e comparar duas linhas assim é pior do que não
comparar.

A rota **não roda o motor**. Ela só lê o que já foi registrado: um gráfico de
convergência que recalculasse o passado com o modelo de agora não mostraria
o que sabíamos, mostraria o que gostaríamos de ter sabido (§5.2).
"""

from __future__ import annotations


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment
    from ..engine import audit as A
    from ..engine.denominators import Base as DenBase

    @app.get("/api/v1/convergencia")
    def convergencia(serie: str | None = None, ambiente: str | None = None,
                     top: int = 6):
        """Séries de apurado e projetado por candidato, por cobertura."""
        env = load_environment(M._ENV_NAME)
        nome_diario = ambiente or env.name
        d = A.Diario(DATA_DIR / "previsoes", nome_diario)
        series = d.series()
        if not series:
            return M.envelope(
                [], snapshots=[], estado="sem_registros",
                motivo=(f"nenhuma projeção registrada em {nome_diario}. "
                        "Rode `obs2026 prever` durante a apuração, ou "
                        "`obs2026 ensaiar-auditoria` para um ensaio."))

        saida = []
        for chave, regs in sorted(series.items()):
            if serie and serie not in chave:
                continue
            regs = sorted(regs, key=lambda r: (r.cobertura_pct, r.momento))

            # Último registro define quem são os candidatos relevantes e, se
            # a disputa terminou, qual foi o resultado.
            ultimo = regs[-1]
            ordenados = sorted(ultimo.candidatos,
                               key=lambda c: -c.votos_observados)[:max(1, top)]
            escolhidos = [c.sqcand for c in ordenados]
            encerrada = ultimo.pendente_max == 0

            linhas = []
            for sq in escolhidos:
                pontos = []
                for r in regs:
                    c = next((x for x in r.candidatos if x.sqcand == sq), None)
                    if c is None:
                        continue
                    den = r.concorrentes_observados
                    pontos.append({
                        "momento": r.momento,
                        "cobertura_pct": r.cobertura_pct,
                        # Apurado: null quando nada foi contado ainda. Zero
                        # seria uma afirmação diferente (§9).
                        "pct_apurado": (100.0 * c.votos_observados / den
                                        if den > 0 else None),
                        "votos_apurados": c.votos_observados,
                        "pct_projetado": c.pct_concorrentes_q500,
                        "pct_q100": c.pct_concorrentes_q100,
                        "pct_q900": c.pct_concorrentes_q900,
                        "estado_matematico": c.estado_matematico,
                        "estado_publicacao": r.estado_publicacao,
                    })
                nome = next((x.nome for x in ultimo.candidatos
                             if x.sqcand == sq), sq)
                final = None
                if encerrada and ultimo.concorrentes_observados > 0:
                    v = next((x.votos_observados for x in ultimo.candidatos
                              if x.sqcand == sq), 0)
                    final = 100.0 * v / ultimo.concorrentes_observados
                # Distância entre as duas linhas no último ponto: é o
                # resumo honesto do gráfico em um número.
                ult = pontos[-1] if pontos else None
                gap = (abs(ult["pct_projetado"] - ult["pct_apurado"])
                       if ult and ult["pct_apurado"] is not None else None)
                linhas.append({
                    "sqcand": sq, "nome": nome,
                    "pct_final": final,
                    "distancia_final_pp": gap,
                    "pontos": pontos,
                })

            saida.append({
                "serie": chave,
                "cargo": ultimo.cargo,
                "unidade": ultimo.unidade,
                "modelo": ultimo.modelo,
                "model_version": ultimo.model_version,
                "execucoes": len(regs),
                "encerrada": encerrada,
                "cobertura_min": regs[0].cobertura_pct,
                "cobertura_max": regs[-1].cobertura_pct,
                "candidatos": linhas,
            })

        return M.envelope(
            saida, snapshots=[], estado="convergencia",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            extra={
                "diario": nome_diario,
                "eixo_x": "cobertura_pct",
                "nota": (
                    "Se o modelo presta, a linha projetada nasce perto do "
                    "resultado final e fica la, e a apurada caminha ate "
                    "encontra-la. Se as duas passeiam juntas, a projecao nao "
                    "estava antecipando nada — so repetindo o parcial."),
                "aviso": (
                    "Nada aqui e recalculado: sao os numeros como foram "
                    "registrados no momento. Refazer o passado com o modelo "
                    "de agora mostraria o que gostariamos de ter sabido, nao "
                    "o que sabiamos (§5.2)."),
            })
