"""Apontar vencedor ("chamada"), com critério mais duro que a projeção.

Três coisas diferentes convivem na tela e nunca se somam (§4.7):

1. **Contagem** — o que o TSE já contou.
2. **Matematicamente definido** — conta, não probabilidade: o que falta não
   alcança. Não passa por aqui.
3. **Vencedor projetado** — probabilidade. É o que este módulo decide.

Mostrar "vencedor projetado" é a afirmação mais cara do produto: se errar
uma vez, contamina tudo o que o Observatório disser depois. Por isso a
régua é bem mais alta que a do intervalo publicado, e três condições têm de
valer ao mesmo tempo:

- **probabilidade ≥ 99%** (`P_MIN`) de vencer, ou de ficar dentro das vagas
  no Senado;
- **estabilidade**: a mesma pessoa acima do limite em `ESTAVEL_N` registros
  seguidos — no simulado, um registro a cada 2 minutos. Uma única rodada em
  99% pode ser um retrato enviesado da ordem de chegada; três seguidas, não;
- **apuração mínima** (`APURADO_MIN`), porque com pouquíssimo voto contado o
  modelo pode estar confiante pelo motivo errado.

A evidência que fixou a régua (ensaio, modelo M3): em 2022, nas 55 disputas
× 2 ordens × 9 rodadas, nenhuma chance ≥ 90% foi dada a quem perdeu. Em
2018, houve 3 casos ≥ 90%, 2 ≥ 95% e 1 ≥ 99% — todos no Senado com ordem de
chegada aleatória, onde há duas vagas e a segunda se decide no fio. Daí o
limite de 99% e a exigência de estabilidade, que nenhum desses casos
cumpria por três registros seguidos.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Probabilidade mínima para apontar vencedor.
P_MIN = 0.99
#: Registros seguidos com o mesmo nome acima do limite.
ESTAVEL_N = 3
#: Eleitorado apurado mínimo, em %.
APURADO_MIN = 20.0
#: Estados de publicação em que a chamada pode sair.
PUBLICAVEIS = ("projecao_disponivel", "resultado_oficial")
TIPO_VENCEDOR = "vencedor"
TIPO_SEGUNDO_TURNO = "segundo_turno"


@dataclass(frozen=True, slots=True)
class Chamada:
    """O que a tela pode dizer sobre uma disputa."""

    sqcands: tuple[str, ...]          # quem foi apontado (vagas do cargo)
    apta: bool
    motivo: str | None = None         # por que ainda não, quando não apta
    p_min_observada: float | None = None
    registros_estaveis: int = 0
    #: O que se está apontando: vencedor(es) ou os dois do 2º turno.
    tipo: str | None = None


def _q(c, *nomes) -> float | None:
    for n in nomes:
        v = getattr(c, n, None)
        if v is not None:
            return v
    return None


def _dois_do_segundo_turno(cands) -> tuple[str, ...]:
    """Os dois nomes do 2º turno, só quando o terceiro não os alcança: o
    teto da faixa mais larga do 3º fica abaixo do piso da do 2º."""
    ordem = sorted((c for c in cands
                    if _q(c, "pct_concorrentes_q500") is not None),
                   key=lambda c: -c.pct_concorrentes_q500)
    if len(ordem) < 2:
        return ()
    if len(ordem) > 2:
        piso2 = _q(ordem[1], "pct_concorrentes_q025",
                   "pct_concorrentes_q050", "pct_concorrentes_q100")
        teto3 = _q(ordem[2], "pct_concorrentes_q975",
                   "pct_concorrentes_q950", "pct_concorrentes_q900")
        if piso2 is None or teto3 is None or teto3 >= piso2:
            return ()
    return (ordem[0].sqcand, ordem[1].sqcand)


def _acima(reg, vagas: int) -> tuple[tuple[str, ...], float | None,
                                     str | None]:
    """Quem está acima do limite num registro, a menor probabilidade entre
    eles e o tipo da chamada. Senado usa a chance de ficar dentro das vagas.

    Com uma vaga (presidente, governador), **liderar não é vencer**: quem
    termina em 1º sem maioria absoluta vai ao 2º turno. Em 04/10/2026 o
    telão deu "vencedor projetado" a quem tinha 99% de chance de liderar e
    projeção de 47,2%. Por isso, com uma vaga:

    - vencedor: chance de MAIORIA ABSOLUTA ≥ limite;
    - 2º turno: chance de ninguém ter maioria ≥ limite, e os dois nomes
      separados do terceiro.
    """
    if vagas == 1:
        mai = [(c.sqcand, c.prob_maioria_absoluta) for c in reg.candidatos
               if getattr(c, "prob_maioria_absoluta", None) is not None]
        if mai:
            mai.sort(key=lambda x: -x[1])
            if mai[0][1] >= P_MIN:
                return (mai[0][0],), mai[0][1], TIPO_VENCEDOR
            sem_maioria = 1.0 - sum(p for _, p in mai)
            if sem_maioria >= P_MIN:
                dois = _dois_do_segundo_turno(reg.candidatos)
                if dois:
                    return dois, sem_maioria, TIPO_SEGUNDO_TURNO
            return (), max(mai[0][1], sem_maioria), None
        # Registro sem a chance de maioria (anterior a ela): régua antiga.
    ps = []
    for c in reg.candidatos:
        p = (c.prob_dentro_vagas if vagas > 1 else c.prob_lideranca)
        if p is not None:
            ps.append((c.sqcand, p))
    ps.sort(key=lambda x: -x[1])
    topo = [x for x in ps[:vagas] if x[1] >= P_MIN]
    if len(topo) != vagas:
        return (), (ps[vagas - 1][1] if len(ps) >= vagas else None), None
    return (tuple(x[0] for x in topo), min(x[1] for x in topo),
            TIPO_VENCEDOR)


def avaliar(registros: list, vagas: int = 1) -> Chamada:
    """`registros` são os últimos do diário da disputa, do mais novo para o
    mais antigo (como `Diario.ultimos` devolve por série)."""
    if not registros:
        return Chamada((), False, "sem projeção registrada")
    atual = registros[0]
    # Uso interno: a disputa encerrada continua mostrando a chamada, para
    # conferir no fim se o "vencedor projetado" bateu com o resultado. O que
    # não entra é cenário exploratório — ali não há probabilidade publicável.
    if atual.estado_publicacao not in PUBLICAVEIS:
        return Chamada((), False, "projeção não publicável "
                                  f"({atual.estado_publicacao})")
    apurado = atual.cobertura_pct
    nomes, pmin, tipo = _acima(atual, vagas)
    if not nomes:
        return Chamada((), False, "probabilidade abaixo de "
                       f"{P_MIN:.0%}", pmin)
    if apurado < APURADO_MIN:
        return Chamada(nomes, False,
                       f"apuração em {apurado:.1f}% (mínimo "
                       f"{APURADO_MIN:.0f}%)", pmin, tipo=tipo)
    estaveis = 0
    for r in registros[:ESTAVEL_N]:
        outros, _, tipo_r = _acima(r, vagas)
        if set(outros) != set(nomes) or tipo_r != tipo:
            break
        estaveis += 1
    if estaveis < ESTAVEL_N:
        return Chamada(nomes, False,
                       f"estável em {estaveis} de {ESTAVEL_N} registros",
                       pmin, estaveis, tipo=tipo)
    return Chamada(nomes, True, None, pmin, estaveis, tipo=tipo)
