"""Nível do intervalo publicado, conforme o apurado (decisão de 22/09/2026).

Subir o nível não torna a projeção mais precisa: alarga a faixa. O que muda
com a apuração é o preço da garantia. Com 5% apurado, uma faixa de 90% é
larga demais para o telão; com 30%, a incerteza já caiu e a mesma garantia
cabe na tela. Por isso o nível sobe com o apurado, e a tela sempre diz qual
está valendo — faixa sem nível declarado não significa nada.

A calibração do gate (§11.2) continua sendo medida em 80%: é o teste mais
exigente. Medido no ensaio de 2018 (54 disputas, as duas ordens de chegada):
a faixa de 80% conteve o resultado em 81–83% dos casos, a de 90% em 89% e a
de 95% em apenas 92% — por isso o nível publicado para de subir em 90%.
"""

from __future__ import annotations

#: (apurado mínimo, nível, rótulo). Ordem decrescente de apuração.
#:
#: O degrau de 95% foi testado no ensaio de 2018 e NÃO entrou: a faixa de
#: 95% conteve o resultado em 92% dos casos (governador e senador, as duas
#: ordens de chegada), enquanto a de 90% entregou 89% — de 1 pp, aceitável;
#: de 3 pp, é prometer mais do que se cumpre. A cauda da simulação é um
#: pouco fina demais para o nível de 95%. Se um dia o modelo melhorar ali,
#: o degrau volta com a medida refeita, nunca antes.
ESCADA = (
    (15.0, 90, "projeção"),
    (0.0, 80, "tendência"),
)

#: Quantis de cada nível, como o `Forecast.quantis` os nomeia.
CHAVES = {80: ("q100", "q900"), 90: ("q050", "q950"), 95: ("q025", "q975")}


def nivel_por_apurado(apurado_pct: float | None) -> tuple[int, str]:
    """Nível e rótulo para o percentual apurado da disputa."""
    p = apurado_pct or 0.0
    for minimo, nivel, rotulo in ESCADA:
        if p >= minimo:
            return nivel, rotulo
    return 80, "tendência"


def faixa(d: dict, nivel: int) -> tuple[float | None, float | None]:
    """Limites da faixa num dicionário de quantis, com queda para 80% quando
    o registro é antigo e não tem os quantis novos."""
    lo, hi = CHAVES.get(nivel, CHAVES[80])
    if d.get(lo) is None or d.get(hi) is None:
        lo, hi = CHAVES[80]
    return d.get(lo), d.get(hi)
