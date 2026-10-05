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


# ---------------------------------------------------------------------------
# Duas regras de publicação decididas depois do 1º turno de 2026 (05/10/2026)
# pela auditoria das 6.617 projeções da noite. As duas medem o apurado como a
# tela mede: eleitorado apurado sobre o eleitorado total. Valem só para o que
# se PUBLICA — o diário guarda sempre o número que o modelo produziu.

#: Abaixo disto a probabilidade não sai. Com 0,0% apurado o modelo deu 97,6%
#: de chance de maioria a quem foi ao 2º turno (governo do RJ) e 93% a quem
#: perdeu (AL); o primeiro registro do presidente dava 78% ao segundo
#: colocado. Mediana e faixa continuam saindo.
PROB_APURADO_MIN = 6.0

#: A partir daqui a faixa para de encolher: mantém a largura que tinha no
#: último registro abaixo do limite. Acima de 95% a faixa caía para 0,1 pp e
#: deixava de conter o resultado (81% de cobertura entre 95% e 99% apurado,
#: 64% acima de 99%), porque o que falta entrar não se parece com o que já
#: entrou.
TRAVA_LARGURA_APURADO = 95.0


def publica_probabilidade(apurado_pct: float | None) -> bool:
    return (apurado_pct or 0.0) >= PROB_APURADO_MIN


def travar_largura(atual: dict, referencia: dict | None) -> dict:
    """Quantis de `atual` com, no mínimo, a largura de `referencia`.

    Os dois são dicionários `q025 … q975` de um candidato. A mediana é a de
    agora; cada lado da faixa fica com a maior distância entre a de agora e
    a do registro de referência. Sem referência, devolve `atual`."""
    if not referencia or atual.get("q500") is None             or referencia.get("q500") is None:
        return atual
    out = dict(atual)
    m, mr = atual["q500"], referencia["q500"]
    for lo, hi in CHAVES.values():
        if atual.get(lo) is not None and referencia.get(lo) is not None:
            out[lo] = max(0.0, m - max(m - atual[lo], mr - referencia[lo]))
        if atual.get(hi) is not None and referencia.get(hi) is not None:
            out[hi] = min(100.0, m + max(atual[hi] - m, referencia[hi] - mr))
    return out
