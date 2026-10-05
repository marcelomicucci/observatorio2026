"""Definição matemática — o que já não depende mais de modelo.

Esta é uma conta de limite, não uma projeção. Ela responde:

    "Existe alguma distribuição possível dos votos que ainda faltam que mude
    este resultado?"

Se não existe, o resultado está **matematicamente definido**. Nenhuma
premissa estatística entra: não há comparecimento estimado, preferência
estimada nem simulação. Só aritmética sobre o que o TSE já publicou.

Por isso o §4.7 exige rótulos distintos, e este módulo produz o do meio:

    projetado                  sai do modelo, tem incerteza, pode errar
    matematicamente definido   sai daqui, não pode errar se os dados estiverem
                               certos, mas NÃO é proclamação
    eleito segundo TSE         sai do campo `st` do payload oficial

Os três são coisas diferentes e nunca se fundem numa tela só.

## O limite

Seja `V_x` os votos já contados de um candidato e `R` o máximo de votos que
ainda podem entrar. `R` é derivado do **limite superior** de `A_pendente`
(ver `pending.py`) com comparecimento 100% e validade 100% — o cenário mais
generoso possível para quem está atrás. Usar o limite superior é deliberado:
o erro que importa evitar é declarar definido algo que não está.

    maioria absoluta garantida      2 · V_x  >  C_obs + R_total
    vaga garantida                  a soma dos `vagas` menores déficits
                                    necessários para ultrapassar x excede R
    eliminado                       nem com R_por_candidato x entra entre os
                                    `lugares_em_disputa` primeiros

## Quantos lugares estão em disputa

Não é sempre `vagas`. Presidente e governador têm **uma** vaga, mas quando
ninguém alcança maioria absoluta o primeiro turno seleciona **dois** nomes
para o segundo. Um candidato em segundo lugar não está eliminado por não
poder vencer no primeiro turno — ele está indo para o segundo.

Confundir as duas coisas apagaria da tela o segundo colocado de uma eleição
presidencial, que é exatamente o erro mais visível possível. Por isso:

    presidente, governador   lugares_em_disputa = 2   (há segundo turno)
    senador                  lugares_em_disputa = 2   (duas vagas, sem
                                                       segundo turno)

Coincidem em 2, mas por razões diferentes, e o código não pode tratar isso
como coincidência.

O denominador da maioria é o de **votos a concorrentes** (`vvc`), conforme
`denominators.py`.

## Por que o déficit conjunto, e não candidato a candidato

A conta ingênua pergunta, para cada rival, "ele consegue me ultrapassar?".
Isso é correto mas frouxo: os rivais disputam o **mesmo** estoque `R`, e não
podem todos recebê-lo inteiro. Para perder uma vaga entre `n`, é preciso que
`n` rivais passem ao mesmo tempo, e o jeito mais barato é escolher os `n`
rivais de menor déficit. Se a soma desses déficits já excede `R`, a vaga está
garantida.

O resultado é o mesmo tipo de certeza, alcançado mais cedo. No Senado, onde
são duas vagas, a diferença é grande.

## Senado

Duas restrições simultâneas, ambas do §4.6:

    um candidato recebe no máximo   comparecentes restantes
    todos juntos recebem no máximo  vagas × comparecentes restantes

Um eleitor não dá dois votos válidos ao mesmo candidato, então o teto
individual não é multiplicado pelas vagas.

## Quando a conta não vale

`sub_judice_em_jogo` desliga a afirmação. Votos anulados sub judice podem ser
revalidados ou cassados por decisão judicial, e isso muda a aritmética depois
do fato. O §4.7 manda tratar sensibilidade judicial em cenário separado — e
aqui a consequência é não afirmar definição matemática enquanto houver voto
sub judice capaz de alterar a ordem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Estado(str, Enum):
    ELEITO = "matematicamente_eleito"
    SEGUNDO_TURNO = "matematicamente_no_segundo_turno"
    ELIMINADO = "matematicamente_eliminado"
    EM_ABERTO = "em_aberto"


ROTULOS = {
    Estado.ELEITO: "matematicamente definido",
    Estado.SEGUNDO_TURNO: "vaga no 2º turno matematicamente definida",
    Estado.ELIMINADO: "matematicamente eliminado",
    Estado.EM_ABERTO: "em aberto",
}


@dataclass(frozen=True, slots=True)
class Situacao:
    sqcand: str
    votos: int
    estado: Estado
    #: Quantos rivais ainda podem, aritmeticamente, terminar à frente.
    rivais_que_podem_passar: int
    #: Quantos votos os rivais precisariam, JUNTOS, para tirar a vaga deste
    #: candidato. `None` quando é aritmeticamente impossível.
    custo_para_perder_a_vaga: int | None
    #: Votos que o candidato precisaria para alcançar a última vaga.
    #: `None` quando já está dentro dela.
    precisa_para_alcancar: int | None
    motivo: str = ""

    @property
    def definido(self) -> bool:
        return self.estado is not Estado.EM_ABERTO


@dataclass(frozen=True, slots=True)
class Definicao:
    """Leitura determinística de uma disputa num instante."""

    unidade: str
    vagas: int
    votos_concorrentes_observados: int
    resto_maximo_total: int
    resto_maximo_por_candidato: int
    situacoes: tuple[Situacao, ...]
    sub_judice_em_jogo: bool
    #: True quando a apuração acabou: aí não há resto e tudo está definido.
    encerrada: bool
    #: Situações no cenário em que a Justiça CASSA os votos sub judice. O
    #: cenário-base (`situacoes`) é o da validação, onde esses votos contam.
    #: `None` quando não há voto sub judice. Quando os dois divergem,
    #: `sub_judice_em_jogo` é True e a tela mostra os dois — esconder a
    #: definição matemática por causa do sub judice apagava a informação
    #: mais forte que existe (decisão de 23/09/2026).
    situacoes_cassado: tuple[Situacao, ...] | None = None
    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def eleitos(self) -> tuple[Situacao, ...]:
        return tuple(s for s in self.situacoes if s.estado is Estado.ELEITO)

    @property
    def no_segundo_turno(self) -> tuple[Situacao, ...]:
        return tuple(s for s in self.situacoes
                     if s.estado is Estado.SEGUNDO_TURNO)

    @property
    def disputa_definida(self) -> bool:
        """Todas as vagas atribuídas sem depender de modelo."""
        return len(self.eleitos) >= self.vagas

    @property
    def maioria_absoluta_possivel(self) -> bool:
        """Alguém ainda pode fechar o 1º turno com mais de 50%.

        Melhor caso do candidato: todo o resto vai para ele. Passa de
        metade se `(V + R) / (C + R) > 1/2`, isto é, `2·V + R > C`."""
        r, c = self.resto_maximo_total, self.votos_concorrentes_observados
        return any(2 * s.votos + r > c for s in self.situacoes)

    @property
    def segundo_turno_definido(self) -> bool:
        """Haverá segundo turno e os dois nomes já são certos.

        Dois candidatos garantidos entre os dois primeiros não bastam: o
        líder ainda pode ter maioria absoluta. Em 04/10/2026 o governo do
        PA apareceu como "2º turno garantido" com o líder em 52,6% e 18%
        do eleitorado por apurar."""
        return (self.vagas == 1 and len(self.no_segundo_turno) >= 2
                and not self.maioria_absoluta_possivel)


def _br(n: int) -> str:
    """Milhar com ponto. Estas mensagens vao para a tela do leitor."""
    return f"{n:,}".replace(",", ".")


def _deficits(votos: dict[str, int], alvo: str,
              teto_individual: int) -> list[int]:
    """Quanto falta a cada rival para ultrapassar `alvo`.

    Rival que nem com o teto individual alcança `alvo` recebe déficit
    infinito e nunca entra na conta conjunta.
    """
    v_alvo = votos[alvo]
    saida = []
    for k, v in votos.items():
        if k == alvo:
            continue
        precisa = v_alvo - v + 1          # tem de PASSAR, empate não basta
        if precisa <= 0:
            saida.append(0)               # já está à frente
        elif precisa > teto_individual:
            saida.append(10**18)          # impossível
        else:
            saida.append(precisa)
    return sorted(saida)


def avaliar(votos: dict[str, int], *, vagas: int,
            concorrentes_observados: int, pendente_max: int,
            sub_judice: int = 0, encerrada: bool = False,
            unidade: str = "", tem_segundo_turno: bool | None = None,
            sub_judice_por_candidato: dict[str, int] | None = None,
            _cenario: bool = False) -> Definicao:
    """Avalia uma disputa. `votos` são os votos já contados por candidato.

    `pendente_max` é o limite SUPERIOR do eleitorado que ainda pode votar.
    Deliberadamente o limite superior: declarar definido algo que não está é
    o erro caro; demorar a declarar é só conservador.

    `tem_segundo_turno` só importa quando `vagas == 1`: presidente e
    governador vão a segundo turno, senador não. Quando omitido, assume-se
    que uma vaga única admite segundo turno, que é o caso brasileiro para os
    dois cargos majoritários de vaga única.
    """
    if tem_segundo_turno is None:
        tem_segundo_turno = (vagas == 1)
    # Lugares que o primeiro turno seleciona. Ver o cabeçalho do módulo.
    lugares = 2 if (vagas == 1 and tem_segundo_turno) else vagas
    avisos: list[str] = []
    if encerrada:
        pendente_max = 0

    # Teto individual: ninguém recebe mais de um voto válido por eleitor,
    # nem no Senado (§4.6).
    teto_ind = pendente_max
    # Teto coletivo: cada eleitor pode distribuir até `vagas` escolhas.
    teto_total = pendente_max * max(1, vagas)

    # Sub judice: o voto pode ser validado ou cassado, e a conta muda depois
    # do fato. Com `sub_judice_por_candidato`, em vez de suspender sempre, o
    # cenário de cassação é calculado e comparado com o de validação (que é o
    # cenário-base, onde esses votos contam). Só suspende se o resultado
    # mudar entre os dois — "há voto sub judice" não é o mesmo que "o voto
    # sub judice decide". Sem o detalhe por candidato, mantém-se a regra
    # antiga, conservadora.
    sub_judice_em_jogo = sub_judice > 0
    cenario_cassado = None
    if sub_judice > 0 and not _cenario and sub_judice_por_candidato:
        cenario_cassado = avaliar(
            {k: v for k, v in votos.items()
             if k not in sub_judice_por_candidato},
            vagas=vagas, tem_segundo_turno=tem_segundo_turno,
            concorrentes_observados=max(
                0, concorrentes_observados - sub_judice),
            pendente_max=pendente_max, sub_judice=0, encerrada=encerrada,
            unidade=unidade, _cenario=True)
    if sub_judice > 0:
        if cenario_cassado is not None:
            # A suspensão fica em suspenso: as situações são calculadas
            # normalmente e o veredito sai da comparação dos dois cenários.
            sub_judice_em_jogo = False
        elif not encerrada:
            avisos.append(
                f"{_br(sub_judice)} votos anulados sub judice podem ser "
                "revalidados ou cassados por decisão judicial. Enquanto isso "
                "não se resolve, a definição matemática fica suspensa "
                "(§4.7).")
        else:
            avisos.append(
                f"{_br(sub_judice)} votos anulados sub judice seguem "
                "pendentes de decisão judicial, mesmo com a apuração "
                "encerrada.")

    if not votos:
        return Definicao(unidade, vagas, concorrentes_observados, teto_total,
                         teto_ind, (), sub_judice_em_jogo, encerrada,
                         tuple(avisos))

    ordenados = sorted(votos.items(), key=lambda kv: -kv[1])
    lider_votos = ordenados[0][1]
    # Fronteira da disputa: votos do `lugares`-esimo colocado. E contra ELE
    # que se mede eliminacao, nao contra o lider.
    corte_votos = (ordenados[min(lugares, len(ordenados)) - 1][1]
                   if ordenados else 0)
    total_max = concorrentes_observados + teto_total
    IMPOSSIVEL = 10**18

    situacoes: list[Situacao] = []
    for posicao, (sq, v) in enumerate(ordenados, start=1):
        defs = _deficits(votos, sq, teto_ind)

        # Quantos rivais podem, isoladamente, terminar à frente.
        podem = sum(1 for d in defs if d <= teto_ind)

        # Conta conjunta: para x perder a vaga, `vagas` rivais precisam
        # passar ao mesmo tempo, dividindo o MESMO estoque.
        custo_para_perder = (sum(defs[:vagas]) if len(defs) >= vagas
                             else IMPOSSIVEL)
        vaga_garantida = custo_para_perder > teto_total
        # Para o segundo turno, o que interessa e nao cair do 2o lugar: sao
        # `lugares` rivais que precisariam passar, nao `vagas`.
        custo_lugares = (sum(defs[:lugares]) if len(defs) >= lugares
                         else IMPOSSIVEL)
        garantido_entre_lugares = custo_lugares > teto_total

        # Maioria absoluta garantida, sobre votos a concorrentes.
        maioria_garantida = (2 * v) > total_max

        # Eliminado: nem com o teto individual entra entre as `vagas`
        # primeiras colocacoes. Basta contar quantos rivais permanecem acima
        # mesmo com x recebendo tudo — os rivais, no pior caso para x, nao
        # recebem nada.
        acima_mesmo_no_maximo = sum(
            1 for k, vy in votos.items() if k != sq and vy >= v + teto_ind
        )
        eliminado = acima_mesmo_no_maximo >= lugares

        if eliminado:
            alvo = ("as duas vagas do segundo turno"
                    if (vagas == 1 and tem_segundo_turno)
                    else f"as {lugares} vaga(s)")
            estado, motivo = Estado.ELIMINADO, (
                f"mesmo recebendo todos os {_br(teto_ind)} votos ainda "
                f"possíveis, segue atrás de {acima_mesmo_no_maximo} "
                f"rival(is) para {alvo}")
        elif vagas == 1 and maioria_garantida:
            estado, motivo = Estado.ELEITO, (
                f"tem mais de 50% dos votos a concorrentes mesmo que todos "
                f"os {_br(teto_total)} restantes vão para outros")
        elif vagas > 1 and vaga_garantida:
            quanto = ("mais votos do que ainda existem"
                      if custo_para_perder >= IMPOSSIVEL
                      else f"{_br(custo_para_perder)} votos")
            estado, motivo = Estado.ELEITO, (
                f"para perder a vaga, {vagas} rivais precisariam de {quanto}, "
                f"e só restam {_br(teto_total)}")
        elif vagas == 1 and tem_segundo_turno and garantido_entre_lugares:
            # Garante estar entre os dois, sem garantir maioria absoluta.
            estado, motivo = Estado.SEGUNDO_TURNO, (
                "não pode cair para fora dos dois primeiros, mas ainda não "
                "tem maioria absoluta garantida")
        else:
            estado, motivo = Estado.EM_ABERTO, ""

        # Sub judice suspende qualquer afirmação de definição.
        if sub_judice_em_jogo and estado is not Estado.EM_ABERTO:
            estado, motivo = Estado.EM_ABERTO, (
                "definição suspensa: há votos sub judice em jogo")

        custo = (None if custo_para_perder >= IMPOSSIVEL
                 else custo_para_perder)
        # Quanto falta para alcancar a ultima vaga. Quem ja esta dentro
        # dela nao precisa de nada — comparar o candidato do corte consigo
        # mesmo daria "precisa 1", que e absurdo.
        if posicao <= vagas:
            precisa = None
        else:
            dif = corte_votos - v + 1
            precisa = dif if dif > 0 else None

        situacoes.append(Situacao(
            sqcand=sq, votos=v, estado=estado,
            rivais_que_podem_passar=podem,
            custo_para_perder_a_vaga=custo, precisa_para_alcancar=precisa,
            motivo=motivo,
        ))

    # Sub judice decide ou não decide? Compara o cenário-base (votos sub
    # judice contando) com o de cassação. Se cada candidato que não é sub
    # judice tem a mesma situação nos dois, e nenhum sub judice ocupa lugar
    # decisivo, a decisão judicial não muda o resultado — e a definição
    # matemática pode ser afirmada, com o aviso de que os votos seguem
    # pendentes.
    if cenario_cassado is not None:
        sjs = set(sub_judice_por_candidato or ())
        b = {s.sqcand: s.estado for s in cenario_cassado.situacoes}
        decisivos = {Estado.ELEITO, Estado.SEGUNDO_TURNO}
        muda = any(s.estado in decisivos for s in situacoes
                   if s.sqcand in sjs)
        if not muda:
            muda = any(b.get(s.sqcand) is not s.estado for s in situacoes
                       if s.sqcand not in sjs)
        sub_judice_em_jogo = muda
        if muda:
            avisos.append(
                f"{_br(sub_judice)} votos anulados sub judice mudam quem se "
                "define: a tela mostra os dois cenários — o de validação "
                "(votos contam) e o de cassação — até a decisão judicial "
                "(§4.7).")
        else:
            avisos.append(
                f"{_br(sub_judice)} votos anulados sub judice seguem "
                "pendentes de decisão judicial, mas não alteram o resultado: "
                "validados ou cassados, os mesmos nomes se definem.")

    # Para o 2º turno com uma vaga: se exatamente dois candidatos não estão
    # eliminados, os dois nomes estão definidos.
    if vagas == 1 and tem_segundo_turno and not sub_judice_em_jogo:
        vivos = [s for s in situacoes if s.estado is not Estado.ELIMINADO]
        if len(vivos) == 2 and not any(s.estado is Estado.ELEITO
                                       for s in situacoes):
            situacoes = [
                Situacao(s.sqcand, s.votos, Estado.SEGUNDO_TURNO,
                         s.rivais_que_podem_passar,
                         s.custo_para_perder_a_vaga,
                         s.precisa_para_alcancar,
                         "só restam dois candidatos não eliminados")
                if s in vivos else s
                for s in situacoes
            ]

    return Definicao(
        unidade=unidade, vagas=vagas,
        votos_concorrentes_observados=concorrentes_observados,
        resto_maximo_total=teto_total, resto_maximo_por_candidato=teto_ind,
        situacoes=tuple(situacoes), sub_judice_em_jogo=sub_judice_em_jogo,
        encerrada=encerrada, avisos=tuple(avisos),
        situacoes_cassado=(cenario_cassado.situacoes
                           if cenario_cassado is not None else None),
    )


def de_ea20(r, pendente) -> Definicao:
    """Atalho: avalia direto a partir de um EA20 já parseado.

    Senador tem duas vagas e NAO tem segundo turno; presidente e governador
    tem uma vaga e tem.
    """
    votos = {c.sqcand: c.votos for c in r.cargo.candidatos
             if c.conta_como_valido}
    return avaliar(
        votos, vagas=r.cargo.vagas,
        tem_segundo_turno=(r.cargo.vagas == 1),
        concorrentes_observados=r.votes.vvc,
        pendente_max=pendente.upper,
        sub_judice=r.votes.vansj,
        encerrada=r.is_final,
        unidade=r.unit_key,
    )
