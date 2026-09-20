"""Parser do EA20 — arquivo de resultado unificado.

Estruturas confirmadas contra payloads reais do simulado (20/09/2026):
`br-c0001-e021270-u.json`, `ac-c0003-e021272-u.json`,
`ac01120-c0005-e021272-u.json`.

O parser é deliberadamente literal. Ele não calcula percentual, não deduz
vencedor e não normaliza destinação de voto. Tudo o que o TSE mandou é
preservado; a interpretação fica no motor, que pode mudar sem reprocessar o
bruto.

As identidades contábeis abaixo foram verificadas nos três payloads e viram
invariante de qualidade (ver `obs2026.quality.checks`):

    vvc == vv + van + vansj          válidos computados = válidos + anulados
                                     + anulados sub judice
    tv  == vvc + vb + tvn            total de votos = computados + brancos
                                     + nulos
    vv  == soma de vap com dvt='Válido'
    c + a == esa                     comparecimento e abstenção se medem
                                     contra o eleitorado das seções APURADAS
    te == esi + esni                 total = instaladas + não instaladas
    esi == esa + esna                instaladas = apuradas + não apuradas
    te == est + esnt                 total = totalizadas + não totalizadas
    c <= tv <= nv * c                no Senado (nv=2) tv pode chegar a 2x o
                                     comparecimento; nunca dividir por 2

Atenção a `soma(vap) <= vvc`: no arquivo nacional de Presidente a soma dos
`vap` ficou 143.627 votos abaixo de `vvc`, e a diferença é exatamente
`van - soma(vap com dvt='Anulado')`. Ou seja, existem votos anulados que não
estão atribuídos a nenhum candidato listado. Portanto `vvc` NÃO pode ser
reconstruído somando candidatos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .common import (
    ParseError, as_code, as_int, as_int_opt, as_pct, as_text, fold, parse_dt,
    require,
)

#: Destinações de voto observadas. Lista aberta de propósito: uma destinação
#: nova não pode derrubar a coleta, mas tem de aparecer como quality_flag.
DVT_VALIDO = "Válido"
DVT_ANULADO = "Anulado"
DVT_ANULADO_SJ = "Anulado sub judice"
DVT_CONHECIDAS = frozenset({DVT_VALIDO, DVT_ANULADO, DVT_ANULADO_SJ})

#: Andamento da totalização: não recebido / parcial / finalizado.
AND_NAO_RECEBIDO, AND_PARCIAL, AND_FINAL = "n", "p", "f"


@dataclass(frozen=True, slots=True)
class SectionCounts:
    ts: int      # total de seções
    st: int      # totalizadas
    snt: int     # não totalizadas
    si: int      # instaladas
    sni: int     # não instaladas
    sa: int      # apuradas
    sna: int     # não apuradas


@dataclass(frozen=True, slots=True)
class ElectorateCounts:
    te: int      # eleitorado total
    est: int     # eleitorado de seções totalizadas
    esnt: int    # ... não totalizadas
    esi: int     # ... instaladas
    esni: int    # ... não instaladas
    esa: int     # ... apuradas
    esna: int    # ... não apuradas
    c: int       # comparecimento
    a: int       # abstenção


@dataclass(frozen=True, slots=True)
class VoteCounts:
    tv: int      # total de votos (NÃO é eleitores: no Senado vai até 2x)
    vvc: int     # válidos computados
    vv: int      # válidos
    vnom: int    # nominais
    van: int     # anulados
    vansj: int   # anulados sub judice
    vb: int      # brancos
    tvn: int     # total de nulos
    vn: int      # nulos
    vnt: int | None       # nulos técnicos
    vscv: int | None      # sem conversão de voto
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class Candidate:
    numero: str          # número de urna — string, zeros à esquerda importam
    sqcand: str          # identificador oficial sequencial
    nome: str
    nome_urna: str
    seq: str | None
    dvt: str             # destinação do voto — NUNCA normalizar fora daqui
    #: `e` do payload ('s'/'n'). NÃO significa "eleito". No arquivo nacional
    #: real de Presidente, os dois candidatos com `e='s'` têm `st='2º turno'`:
    #: a marca é de CLASSIFICAÇÃO (o candidato se qualifica no cargo), e ler
    #: isso como "eleito" produziria o erro de manchete que o §4.7 proíbe.
    #: A situação oficial é `st`, e é ela que vai à tela.
    classificado_flag: str
    situacao: str        # 'st': 'Eleito', '2º turno', 'Não eleito', ...
    votos: int           # vap
    pct_tse: float | None      # pvapn — só conferência
    partido_numero: str
    partido_sigla: str
    partido_nome: str
    agr_numero: str            # federação/coligação
    agr_nome: str
    agr_tipo: str              # 'f' federação, 'c' coligação, ...
    agr_composicao: str
    vice: tuple[dict, ...] = ()

    @property
    def conta_como_valido(self) -> bool:
        """Só `dvt='Válido'` entra em `vv`. Verificado nos três payloads."""
        return self.dvt == DVT_VALIDO

    @property
    def eleito_segundo_tse(self) -> bool:
        """Eleito de verdade, segundo a SITUAÇÃO oficial — não segundo `e`.

        O §4.7 exige rótulos distintos para "projetado", "matematicamente
        definido pelo TSE" e "eleito segundo TSE". Esta propriedade só
        devolve True para o último caso.
        """
        return fold(self.situacao) == "ELEITO"

    @property
    def vai_para_segundo_turno(self) -> bool:
        return "TURNO" in fold(self.situacao)


@dataclass(frozen=True, slots=True)
class CargoResult:
    cd: str
    nome: str
    vagas: int           # `nv` — 2 para Senador em 2026, lido do payload
    candidatos: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class Ea20:
    election: str
    turno: str
    fase: str            # 'f' = oficial, 's' = simulado — barreira de ambiente
    suplementar: bool    # 'sup'
    tpabr: str           # br | uf | mu
    cdabr: str           # 'br' | 'ac' | '01120'
    generated_at: datetime | None    # dg + hg (geração do arquivo)
    totalized_at: datetime | None    # dt + ht (totalização)
    idg: str | None
    andamento: str       # 'and': n | p | f
    divulga_votos: bool  # 'dv'
    totalizacao_final: bool  # 'tf'
    esae: str | None
    mnae: tuple[str, ...]
    cargo: CargoResult
    sections: SectionCounts
    electorate: ElectorateCounts
    votes: VoteCounts

    @property
    def is_final(self) -> bool:
        return self.andamento == AND_FINAL

    @property
    def unit_key(self) -> str:
        """Chave da unidade territorial na partição (§4.1)."""
        return f"{self.tpabr}:{self.cdabr}"


def _sections(b: dict) -> SectionCounts:
    require(b, "ts", "st", "snt", "si", "sni", "sa", "sna", ctx="EA20.s")
    g = lambda k: as_int(b[k], f"s.{k}")  # noqa: E731
    return SectionCounts(g("ts"), g("st"), g("snt"), g("si"), g("sni"),
                         g("sa"), g("sna"))


def _electorate(b: dict) -> ElectorateCounts:
    require(b, "te", "est", "esnt", "esi", "esni", "esa", "esna", "c", "a",
            ctx="EA20.e")
    g = lambda k: as_int(b[k], f"e.{k}")  # noqa: E731
    return ElectorateCounts(g("te"), g("est"), g("esnt"), g("esi"), g("esni"),
                            g("esa"), g("esna"), g("c"), g("a"))


def _votes(b: dict) -> VoteCounts:
    require(b, "tv", "vvc", "vv", "vnom", "van", "vansj", "vb", "tvn", "vn",
            ctx="EA20.v")
    g = lambda k: as_int(b[k], f"v.{k}")  # noqa: E731
    return VoteCounts(
        tv=g("tv"), vvc=g("vvc"), vv=g("vv"), vnom=g("vnom"), van=g("van"),
        vansj=g("vansj"), vb=g("vb"), tvn=g("tvn"), vn=g("vn"),
        vnt=as_int_opt(b.get("vnt"), "v.vnt"),
        vscv=as_int_opt(b.get("vscv"), "v.vscv"),
        raw=dict(b),
    )


def _candidates(cargo: dict) -> tuple[Candidate, ...]:
    out: list[Candidate] = []
    for agr in cargo.get("agr", []):
        agr_n = as_code(agr.get("n", ""), "agr.n") if agr.get("n") else ""
        agr_nm = as_text(agr.get("nm"))
        agr_tp = str(agr.get("tp", "") or "")
        agr_com = as_text(agr.get("com"))
        for par in agr.get("par", []):
            p_n = as_code(par.get("n", ""), "par.n") if par.get("n") else ""
            p_sg = as_text(par.get("sg"))
            p_nm = as_text(par.get("nm"))
            for c in par.get("cand", []):
                require(c, "n", "sqcand", "nm", "dvt", "vap", ctx="EA20.cand")
                out.append(Candidate(
                    numero=as_code(c["n"], "cand.n"),
                    sqcand=as_code(c["sqcand"], "cand.sqcand"),
                    nome=as_text(c["nm"]),
                    nome_urna=as_text(c.get("nmu") or c["nm"]),
                    seq=str(c.get("seq") or "") or None,
                    dvt=as_text(c["dvt"]),
                    classificado_flag=str(c.get("e", "") or ""),
                    situacao=as_text(c.get("st")),
                    votos=as_int(c["vap"], "cand.vap"),
                    pct_tse=as_pct(c.get("pvapn"), "cand.pvapn"),
                    partido_numero=p_n, partido_sigla=p_sg, partido_nome=p_nm,
                    agr_numero=agr_n, agr_nome=agr_nm, agr_tipo=agr_tp,
                    agr_composicao=agr_com,
                    vice=tuple(dict(v) for v in c.get("vs", []) or ()),
                ))
    return tuple(out)


def parse(payload: dict) -> Ea20:
    """Converte o JSON bruto do EA20 em estrutura tipada.

    Levanta `ParseError` se o contrato mudou. Recusar é melhor que ingerir
    torto: um campo ausente que vira zero contamina a projeção inteira.
    """
    require(payload, "ele", "t", "f", "tpabr", "cdabr", "carg", "s", "e", "v",
            ctx="EA20")
    cargos = payload["carg"]
    if len(cargos) != 1:
        raise ParseError(
            f"EA20 com {len(cargos)} cargos; o contrato unificado prevê 1 "
            "por arquivo. Coleta recusada até revisão do contrato."
        )
    cg = cargos[0]
    require(cg, "cd", "nv", ctx="EA20.carg")

    return Ea20(
        election=as_code(payload["ele"], "ele"),
        turno=as_code(payload["t"], "t"),
        fase=str(payload["f"]),
        suplementar=str(payload.get("sup", "n")).lower() == "s",
        tpabr=str(payload["tpabr"]).lower(),
        cdabr=as_code(payload["cdabr"], "cdabr"),
        generated_at=parse_dt(payload.get("dg"), payload.get("hg"), "dg/hg"),
        totalized_at=parse_dt(payload.get("dt"), payload.get("ht"), "dt/ht"),
        idg=str(payload.get("idg") or "") or None,
        andamento=str(payload.get("and", "") or ""),
        divulga_votos=str(payload.get("dv", "n")).lower() == "s",
        totalizacao_final=str(payload.get("tf", "n")).lower() == "s",
        esae=str(payload.get("esae") or "") or None,
        mnae=tuple(str(m) for m in payload.get("mnae", []) or ()),
        cargo=CargoResult(
            cd=as_code(cg["cd"], "carg.cd"),
            nome=as_text(cg.get("nmn") or cg.get("nmm")),
            vagas=as_int(cg["nv"], "carg.nv"),
            candidatos=_candidates(cg),
        ),
        sections=_sections(payload["s"]),
        electorate=_electorate(payload["e"]),
        votes=_votes(payload["v"]),
    )
