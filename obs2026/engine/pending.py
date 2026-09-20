"""Eleitorado pendente — `A_pendente` do §4.2.

A especificação é explícita: `A_pendente` **não** será calculado como
"aptos × percentual de seções restantes". Ele tem de representar eleitores de
unidades cujo resultado ainda pode entrar na totalização.

Os payloads reais permitem derivá-lo dos estados oficiais. A árvore de
eleitorado do EA20 tem duas decomposições independentes, ambas verificadas
com igualdade exata:

    te = esi + esni          instaladas   + não instaladas
    esi = esa + esna         apuradas     + não apuradas   (dentro das instaladas)
    te = est + esnt          totalizadas  + não totalizadas

E a chave: `c + a = esa`. Comparecimento e abstenção se medem contra o
eleitorado das seções **apuradas** — nunca contra o eleitorado cadastrado.

Daí:

    Eleitores de seções NÃO INSTALADAS (`esni`) não são votos futuros. Uma
    seção que não foi instalada não vai produzir voto. O §4.2 diz isso com
    todas as letras: "eleitores de seções encerradas sem votação não são
    votos futuros".

    Eleitores de seções instaladas e ainda NÃO APURADAS (`esna`) são a massa
    que ainda pode votar — ou já votou e o resultado não chegou.

O contrato não expõe, porém, a interseção entre "não totalizada" e "não
instalada". Por isso este módulo devolve um **intervalo**, não um ponto:

    limite superior   esnt          tudo que falta totalizar
    limite inferior   esnt - esni   descontando o que não vai votar

Onde o contrato não resolve, a incerteza aumenta em vez de a estimativa
fingir precisão. No arquivo nacional real a folga é de 267 eleitores em 163
milhões (0,00016%); num município pequeno pode pesar, e é lá que importa.

`retotalizacao_detectada` existe porque correções podem **reduzir** votos
(§8.1). Isso não é erro de coleta: é revisão, e as duas versões ficam.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tse.parsers.ea20 import Ea20


@dataclass(frozen=True, slots=True)
class Pending:
    """Eleitorado que ainda pode entrar na totalização de uma unidade."""

    unit: str
    lower: int          # não totalizado, descontando seções não instaladas
    upper: int          # tudo que falta totalizar
    nao_instalados: int
    apurado: int        # eleitorado de seções apuradas (base do comparecimento)
    total: int
    andamento: str
    finalizado: bool

    @property
    def point(self) -> int:
        """Ponto usado no cenário-base: meio do intervalo, arredondado."""
        return (self.lower + self.upper) // 2

    @property
    def width(self) -> int:
        """Largura da ambiguidade do contrato. Entra na incerteza."""
        return self.upper - self.lower

    @property
    def share_of_total(self) -> float:
        return (self.point / self.total) if self.total else 0.0

    @property
    def is_resolved(self) -> bool:
        """Sem ambiguidade: o contrato determinou o pendente exatamente."""
        return self.width == 0


def of(r: Ea20) -> Pending:
    e = r.electorate
    upper = e.esnt
    lower = max(0, e.esnt - e.esni)
    if r.is_final:
        # Disputa concluída: nada pendente, independentemente de arredondamento.
        upper = lower = 0
    return Pending(
        unit=r.unit_key, lower=lower, upper=upper, nao_instalados=e.esni,
        apurado=e.esa, total=e.te, andamento=r.andamento,
        finalizado=r.is_final,
    )


@dataclass(frozen=True, slots=True)
class Observed:
    """O que já está contado numa unidade, com seu denominador."""

    unit: str
    votos_por_candidato: dict[str, int]      # sqcand -> vap
    validos: int
    nominal_tse: int
    #: `van + vansj`. Não entram na preferência (não são votos que a
    #: totalização vai aceitar para um candidato apto), mas ENTRAM no
    #: denominador da maioria absoluta: são "votos a concorrentes".
    anulados: int
    brancos: int
    nulos: int
    total_votos: int
    comparecimento: int
    apurado: int
    vagas: int

    @property
    def comparecimento_observado(self) -> float | None:
        """Comparecimento sobre o eleitorado das seções apuradas.

        Este é o ponto do §4.3 que o contrato confirma: usar o eleitorado do
        MESMO universo de seções, não o eleitorado cadastrado inteiro.
        """
        return (self.comparecimento / self.apurado) if self.apurado else None

    @property
    def taxa_valida(self) -> float | None:
        """Votos válidos sobre o total de votos daquela unidade."""
        return (self.validos / self.total_votos) if self.total_votos else None

    @property
    def concorrentes(self) -> int:
        """Votos a concorrentes: `vv + van + vansj` = `vvc`.

        Denominador da maioria absoluta (leiame do TSE, CF art. 77 §2º).
        """
        return self.validos + self.anulados

    @property
    def escolhas_por_comparecente(self) -> float | None:
        """`q` do §4.6. Entre 0 e `vagas`."""
        return (self.validos / self.comparecimento) if self.comparecimento \
            else None

    def preferencias(self) -> dict[str, float]:
        """Distribuição dos válidos entre candidatos. Soma 1 quando há voto."""
        if self.validos <= 0:
            return {}
        return {k: v / self.validos for k, v in self.votos_por_candidato.items()}


def observed_of(r: Ea20) -> Observed:
    """Extrai o observado usando apenas candidatos com destinação 'Válido'.

    Candidatos anulados e sub judice ficam fora da preferência: eles não
    compõem `vv`, e projetar preferência sobre eles produziria voto que a
    totalização não vai aceitar. Os votos deles continuam registrados no
    observado bruto e aparecem em cenário de sensibilidade (§4.7).
    """
    validos_por_cand = {
        c.sqcand: c.votos for c in r.cargo.candidatos if c.conta_como_valido
    }
    return Observed(
        unit=r.unit_key,
        votos_por_candidato=validos_por_cand,
        validos=r.votes.vv,
        nominal_tse=sum(c.votos for c in r.cargo.candidatos),
        anulados=r.votes.van + r.votes.vansj,
        brancos=r.votes.vb,
        nulos=r.votes.tvn,
        total_votos=r.votes.tv,
        comparecimento=r.electorate.c,
        apurado=r.electorate.esa,
        vagas=r.cargo.vagas,
    )


def retotalizacao_detectada(anterior: Observed, atual: Observed) -> list[str]:
    """Detecta redução de votos entre duas capturas da mesma unidade.

    Correção que reduz voto é legítima (§8.1). O que não pode é a redução
    passar despercebida: a versão anterior fica preservada e a nova é
    classificada como revisão.
    """
    avisos = []
    if atual.validos < anterior.validos:
        avisos.append(
            f"validos caiu de {anterior.validos} para {atual.validos}"
        )
    if atual.comparecimento < anterior.comparecimento:
        avisos.append(
            f"comparecimento caiu de {anterior.comparecimento} para "
            f"{atual.comparecimento}"
        )
    for k, v in anterior.votos_por_candidato.items():
        novo = atual.votos_por_candidato.get(k)
        if novo is None:
            avisos.append(f"candidato {k} desapareceu da apuracao")
        elif novo < v:
            avisos.append(f"candidato {k}: {v} -> {novo}")
    return avisos
