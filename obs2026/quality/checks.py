"""Invariantes contábeis do EA20.

Todas foram verificadas contra os três payloads reais do simulado de 2026
(Presidente/Brasil, Governador/AC, Senador/Acrelândia). São regras do
contrato, não heurísticas: uma violação significa que o payload mudou ou que
a coleta corrompeu algo, e nos dois casos a projeção precisa ser suspensa
antes de ir ao ar (§4.7, §11.1).

Severidade:
    critical  divergência contábil — suspende projeção
    warning   inconsistência tolerável com registro
    info      observação para a tela de qualidade
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tse.parsers.ea11 import FASE_OFICIAL
from ..tse.parsers.ea20 import DVT_CONHECIDAS, DVT_SEM, DVT_VALIDO, Ea20

CRITICAL, WARNING, INFO = "critical", "warning", "info"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    message: str
    unit: str
    observed: str = ""

    @property
    def blocks_forecast(self) -> bool:
        return self.severity == CRITICAL


def check_ea20(r: Ea20, *, expected_seats: int | None = None,
               official_env: bool | None = None) -> list[Finding]:
    f: list[Finding] = []
    u = f"{r.election}/{r.cargo.cd}/{r.unit_key}"
    v, e, s = r.votes, r.electorate, r.sections

    def bad(code: str, sev: str, msg: str, obs: str = "") -> None:
        f.append(Finding(code, sev, msg, u, obs))

    # -- contabilidade de votos ------------------------------------------
    if v.vvc != v.vv + v.van + v.vansj:
        bad("VOTOS_VVC", CRITICAL,
            "vvc != vv + van + vansj",
            f"{v.vvc} != {v.vv}+{v.van}+{v.vansj}={v.vv+v.van+v.vansj}")
    if v.tv != v.vvc + v.vb + v.tvn:
        bad("VOTOS_TV", CRITICAL,
            "tv != vvc + vb + tvn",
            f"{v.tv} != {v.vvc}+{v.vb}+{v.tvn}={v.vvc+v.vb+v.tvn}")

    soma_validos = sum(c.votos for c in r.cargo.candidatos
                       if c.dvt == DVT_VALIDO)
    if soma_validos != v.vv:
        bad("VOTOS_SOMA_CAND", CRITICAL,
            "soma dos votos de candidatos com dvt='Valido' != vv",
            f"{soma_validos} != {v.vv}")

    soma_todos = sum(c.votos for c in r.cargo.candidatos)
    if soma_todos > v.vvc:
        bad("VOTOS_SOMA_EXCEDE", CRITICAL,
            "soma de vap excede vvc", f"{soma_todos} > {v.vvc}")
    elif soma_todos < v.vvc:
        # Observado de verdade no arquivo nacional: existem votos anulados
        # nao atribuidos a candidato listado. Nao e erro; e razao para nunca
        # reconstruir vvc somando candidatos.
        bad("VOTOS_ANULADOS_SEM_CAND", INFO,
            "vvc acima da soma de vap: ha votos anulados sem candidato listado",
            f"vvc-soma={v.vvc - soma_todos}")

    # -- destinacao do voto ----------------------------------------------
    # Sem destinação e sem voto é o arquivo ainda não recebido: normal no
    # começo da noite. Sem destinação COM voto, não se sabe se o voto é
    # válido — a soma de `vv` deixa de fechar, e isso bloqueia a projeção.
    com_voto_sem_dvt = [c for c in r.cargo.candidatos
                        if c.dvt == DVT_SEM and c.votos > 0]
    if com_voto_sem_dvt:
        bad("DVT_AUSENTE_COM_VOTOS", CRITICAL,
            "candidato com votos e sem destinacao do voto (dvt)",
            f"{len(com_voto_sem_dvt)} candidato(s)")
    novas = ({c.dvt for c in r.cargo.candidatos} - DVT_CONHECIDAS
             - {DVT_SEM})
    if novas:
        bad("DVT_DESCONHECIDA", WARNING,
            "destinacao de voto fora do conjunto conhecido", str(sorted(novas)))

    # -- eleitorado e secoes ---------------------------------------------
    # Instalação só é conhecida nas seções já TOTALIZADAS. No simulado de
    # 22/09 o arquivo "não recebido" veio com te=1.746.104 e esi=esni=0; a
    # regra antiga (te = esi + esni) só valia com 100% totalizado, que era o
    # único estado visto até então. Com tudo totalizado, est = te e as duas
    # formas coincidem.
    if e.est != e.esi + e.esni:
        bad("ELEITORADO_INSTALADAS", CRITICAL, "est != esi + esni",
            f"{e.est} != {e.esi}+{e.esni}")
    if e.esi != e.esa + e.esna:
        bad("ELEITORADO_APURADAS", CRITICAL, "esi != esa + esna",
            f"{e.esi} != {e.esa}+{e.esna}")
    if e.te != e.est + e.esnt:
        bad("ELEITORADO_TOTALIZADAS", CRITICAL, "te != est + esnt",
            f"{e.te} != {e.est}+{e.esnt}")
    if e.c + e.a != e.esa:
        # Esta e a invariante que fundamenta o §4.3: comparecimento e
        # abstencao se medem contra o eleitorado das secoes APURADAS, nunca
        # contra o eleitorado cadastrado inteiro.
        bad("COMPARECIMENTO_DENOMINADOR", CRITICAL,
            "c + a != esa (comparecimento nao bate com o eleitorado apurado)",
            f"{e.c}+{e.a}={e.c+e.a} != {e.esa}")
    if s.st != s.si + s.sni:
        # Mesma razão de ELEITORADO_INSTALADAS: instalação se conhece nas
        # seções totalizadas, não no total.
        bad("SECOES_INSTALADAS", CRITICAL, "st != si + sni",
            f"{s.st} != {s.si}+{s.sni}")
    if s.ts != s.st + s.snt:
        bad("SECOES_TOTALIZADAS", CRITICAL, "ts != st + snt",
            f"{s.ts} != {s.st}+{s.snt}")
    if s.si != s.sa + s.sna:
        bad("SECOES_APURADAS", CRITICAL, "si != sa + sna",
            f"{s.si} != {s.sa}+{s.sna}")

    # -- vagas e escolhas por eleitor -------------------------------------
    nv = r.cargo.vagas
    if expected_seats is not None and nv != expected_seats:
        bad("VAGAS_DIVERGENTES", CRITICAL,
            "numero de vagas do payload difere do configurado",
            f"carg.nv={nv}, esperado={expected_seats}")
    if e.c > 0:
        if v.tv < e.c:
            bad("VOTOS_ABAIXO_COMPARECIMENTO", CRITICAL,
                "total de votos menor que o comparecimento",
                f"tv={v.tv} < c={e.c}")
        if v.tv > nv * e.c:
            bad("VOTOS_ACIMA_DO_TETO", CRITICAL,
                f"total de votos acima de nv({nv}) x comparecimento",
                f"tv={v.tv} > {nv}x{e.c}={nv*e.c}")
        # Senado: media de escolhas validas por comparecente, entre 0 e nv.
        if nv > 1:
            q = v.vv / e.c
            if not (0.0 <= q <= nv + 1e-9):
                bad("SENADO_Q_FORA_FAIXA", CRITICAL,
                    "escolhas validas por comparecente fora de [0, nv]",
                    f"q={q:.4f}, nv={nv}")
    elif v.tv > 0:
        bad("VOTOS_SEM_COMPARECIMENTO", CRITICAL,
            "ha votos com comparecimento zero", f"tv={v.tv}, c=0")

    # -- barreira de ambiente ---------------------------------------------
    if official_env is not None:
        if official_env and r.fase != FASE_OFICIAL:
            bad("AMBIENTE_CONTAMINADO", CRITICAL,
                "payload de simulado no ambiente oficial", f"f={r.fase!r}")
        if not official_env and r.fase == FASE_OFICIAL:
            bad("AMBIENTE_CONTAMINADO", CRITICAL,
                "payload oficial no ambiente de simulado", f"f={r.fase!r}")

    # -- coerencia com os percentuais do TSE ------------------------------
    # Nao usamos os percentuais do TSE para calcular; usamos para conferir que
    # nosso entendimento do denominador esta certo (§11.1).
    #
    # O denominador de `pvap` e a SOMA DOS VOTOS DOS CANDIDATOS, incluindo os
    # anulados e os anulados sub judice — nao `vv` e nao `vvc`. Verificado com
    # erro exatamente zero nos tres payloads reais; contra `vv` o erro chega a
    # 1,69 pp e contra `vvc` a 0,0104 pp no arquivo nacional.
    #
    # Consequencia que o produto precisa respeitar: o percentual que o TSE
    # exibe NAO e o percentual que decide o primeiro turno. A maioria absoluta
    # se apura sobre votos validos (`vv`). Confundir os dois muda quem "passa
    # em primeiro turno" na tela.
    if soma_todos > 0:
        for c in r.cargo.candidatos:
            if c.pct_tse is None:
                continue
            nosso = 100.0 * c.votos / soma_todos
            if abs(nosso - c.pct_tse) > 1e-6:
                bad("PCT_DENOMINADOR", WARNING,
                    f"percentual do candidato {c.numero} nao bate com a soma "
                    "dos votos de candidatos",
                    f"nosso={nosso:.9f} tse={c.pct_tse:.9f}")
                break

    # -- estado oficial propagado -----------------------------------------
    if r.tpabr == "mu":
        eleitos = [c.numero for c in r.cargo.candidatos
                   if c.eleito_segundo_tse]
        if eleitos:
            # Observado no simulado: o arquivo MUNICIPAL de Senador traz
            # st='Eleito'. O status e da disputa na UF, nao do municipio.
            # A UI nunca pode ler isso como "eleito pelo municipio" (§3).
            bad("STATUS_UF_EM_ARQUIVO_MUNICIPAL", INFO,
                "arquivo municipal traz situacao de eleito definida na UF; "
                "nao exibir como 'eleito pelo municipio'",
                f"candidatos={eleitos}")

    return f


def summarize(findings: list[Finding]) -> dict:
    by = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for f in findings:
        by[f.severity] = by.get(f.severity, 0) + 1
    return {
        "counts": by,
        "blocks_forecast": any(f.blocks_forecast for f in findings),
        "codes": sorted({f.code for f in findings}),
    }
