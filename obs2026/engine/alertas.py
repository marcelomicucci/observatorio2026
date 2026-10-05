"""Alertas de resultado: as novidades da noite, na ordem em que acontecem.

Pedido de 05/10/2026: uma faixa no telão com as últimas novidades —
matematicamente resolvido, 2º turno, vencedor projetado, apuração encerrada.

Tudo sai do diário de projeções (`engine/audit.py`), que já guarda, a cada
dois minutos e por disputa, os votos contados, o estado matemático de cada
candidato e as probabilidades. Um alerta é uma **transição** entre dois
registros seguidos da mesma disputa: o que passou a valer agora e não valia
no registro anterior. Por isso o módulo não busca nada e não tem relógio —
recebe registros em ordem e devolve o que mudou. Reaplicado ao diário de uma
noite inteira, devolve a mesma lista, sempre.

Os rótulos seguem a hierarquia do §4.7 e nunca se misturam:

- **conta** — "matematicamente eleito", "2º turno definido": o que falta
  apurar não alcança;
- **probabilidade** — "vencedor projetado", "2º turno projetado": a régua
  dura de `engine/chamada.py`;
- **contagem** — "100% apurado", "assume a liderança".

"Eleito", sem adjetivo, é palavra do TSE e não sai daqui.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import chamada as CH

CARGOS = {"1": "Presidente", "3": "Governador", "5": "Senador"}
#: A liderança da contagem só vira notícia depois disto: com menos voto, a
#: ordem de chegada troca o líder várias vezes sem significar nada.
VIRADA_APURADO_MIN = 10.0
#: Peso do alerta, para a tela escolher o que destacar.
PESO = {"matematico": 3, "encerrada": 3, "segundo_turno": 3,
        "projetado": 2, "virada": 1}


@dataclass(frozen=True, slots=True)
class Alerta:
    id: str
    momento: str            # ISO UTC do registro que revelou a novidade
    cargo: str
    unidade: str            # 'br' ou a UF
    tipo: str               # matematico | segundo_turno | projetado | ...
    natureza: str           # conta | probabilidade | contagem
    titulo: str             # "Governador · SP"
    texto: str              # a frase da faixa
    apurado_pct: float
    peso: int

    def as_dict(self) -> dict:
        return asdict(self)


def _pct(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + "%"


def _nomes(ns: list[str]) -> str:
    return ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " e " + ns[-1]


@dataclass
class _Disputa:
    """O que já se disse de uma disputa."""

    ditos: set = field(default_factory=set)
    lideres: tuple = ()
    recentes: list = field(default_factory=list)   # mais novo primeiro
    n: int = 0


class Acompanhador:
    """Recebe registros em ordem cronológica e devolve os alertas novos."""

    def __init__(self) -> None:
        self._d: dict[tuple, _Disputa] = {}

    def alimentar(self, r) -> list[Alerta]:
        chave = (r.cargo, r.unidade, r.modelo)
        d = self._d.setdefault(chave, _Disputa())
        vagas = 2 if r.cargo == "5" else 1
        apurado = r.cobertura_pct
        C = r.concorrentes_observados or sum(
            c.votos_observados for c in r.candidatos)
        cs = sorted(r.candidatos, key=lambda c: -c.votos_observados)
        if not cs or not C or cs[0].votos_observados <= 0:
            return []
        fatia = {c.sqcand: 100.0 * c.votos_observados / C for c in cs}
        nome = {c.sqcand: c.nome.title() for c in cs}
        titulo = CARGOS.get(r.cargo, r.cargo) + (
            "" if r.unidade == "br" else f" · {r.unidade.upper()}")
        out: list[Alerta] = []

        def diz(tipo: str, natureza: str, texto: str, marca=None) -> None:
            marca = marca or tipo
            if marca in d.ditos:
                return
            d.ditos.add(marca)
            d.n += 1
            out.append(Alerta(
                id=f"{r.cargo}:{r.unidade}:{d.n}", momento=r.momento,
                cargo=r.cargo, unidade=r.unidade, tipo=tipo,
                natureza=natureza, titulo=titulo, texto=texto,
                apurado_pct=round(apurado, 2), peso=PESO[tipo]))

        com = f"com {apurado:.0f}% apurado".replace(".", ",")
        encerrada = bool(r.secoes_total) and \
            r.secoes_totalizadas >= r.secoes_total

        # ---- conta: eleitos e 2º turno
        eleitos = [c for c in cs
                   if c.estado_matematico == "matematicamente_eleito"]
        no2t = [c for c in cs if c.estado_matematico
                == "matematicamente_no_segundo_turno"]
        maioria_possivel = any(
            2 * c.votos_observados + r.pendente_max > C for c in cs)
        if not encerrada:
            if vagas == 1 and eleitos:
                c = eleitos[0]
                diz("matematico", "conta",
                    f"{nome[c.sqcand]} está matematicamente eleito no 1º "
                    f"turno — {_pct(fatia[c.sqcand])} {com}")
            elif vagas > 1:
                for c in eleitos:
                    diz("matematico", "conta",
                        f"{nome[c.sqcand]} garante matematicamente uma das "
                        f"{vagas} vagas — {_pct(fatia[c.sqcand])} {com}",
                        marca=("matematico", c.sqcand))
            if vagas == 1 and len(no2t) >= 2 and not maioria_possivel:
                a, b = no2t[0], no2t[1]
                diz("segundo_turno", "conta",
                    f"2º turno matematicamente definido: {nome[a.sqcand]} × "
                    f"{nome[b.sqcand]} — {com}")

        # ---- probabilidade: a chamada, com a régua de engine/chamada.py
        d.recentes.insert(0, r)
        del d.recentes[CH.ESTAVEL_N + 2:]
        if not encerrada and "matematico" not in d.ditos \
                and "segundo_turno" not in d.ditos:
            ch = CH.avaliar(d.recentes, vagas=vagas)
            if ch.apta and ch.tipo == CH.TIPO_SEGUNDO_TURNO:
                diz("projetado", "probabilidade",
                    "2º turno projetado: "
                    + " × ".join(nome.get(s, s) for s in ch.sqcands)
                    + f" — {com}", marca="projetado_2t")
            elif ch.apta:
                ns = [nome.get(s, s) for s in ch.sqcands]
                diz("projetado", "probabilidade",
                    (f"{ns[0]} é o vencedor projetado" if vagas == 1
                     else f"{_nomes(ns)} são os eleitos projetados")
                    + f" — {com}", marca="projetado_v")

        # ---- contagem: liderança e encerramento
        lideres = tuple(c.sqcand for c in cs[:vagas])
        if (d.lideres and set(lideres) != set(d.lideres)
                and apurado >= VIRADA_APURADO_MIN and not encerrada):
            novo = [s for s in lideres if s not in d.lideres][0]
            saiu = [s for s in d.lideres if s not in lideres][0]
            d.n += 1
            out.append(Alerta(
                id=f"{r.cargo}:{r.unidade}:{d.n}", momento=r.momento,
                cargo=r.cargo, unidade=r.unidade, tipo="virada",
                natureza="contagem", titulo=titulo,
                texto=(f"{nome[novo]} passa {nome.get(saiu, saiu)} e "
                       + ("assume a liderança" if vagas == 1
                          else f"entra nas {vagas} vagas")
                       + f" — {_pct(fatia[novo])} {com}"),
                apurado_pct=round(apurado, 2), peso=PESO["virada"]))
        d.lideres = lideres

        if encerrada:
            if vagas > 1:
                txt = ("100% apurado: " + _nomes(
                    [f"{nome[c.sqcand]} ({_pct(fatia[c.sqcand])})"
                     for c in cs[:vagas]]) + f" ficam com as {vagas} vagas")
            elif 2 * cs[0].votos_observados > C:
                txt = (f"100% apurado: {nome[cs[0].sqcand]} vence no 1º "
                       f"turno com {_pct(fatia[cs[0].sqcand])}")
            elif len(cs) > 1:
                txt = ("100% apurado: 2º turno entre "
                       f"{nome[cs[0].sqcand]} ({_pct(fatia[cs[0].sqcand])}) e "
                       f"{nome[cs[1].sqcand]} ({_pct(fatia[cs[1].sqcand])})")
            else:
                txt = f"100% apurado: {nome[cs[0].sqcand]}"
            diz("encerrada", "contagem", txt)
        return out
