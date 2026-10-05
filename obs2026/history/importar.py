"""Importa uma eleição passada para o banco histórico.

Lê os dois zips do TSE (votação por candidato e detalhe da votação), agrega
zona → município → UF → Brasil e grava. Roda uma vez por ano de eleição; é
idempotente, porque apaga o que já existe daquele ano antes de gravar.

## Decisões que valem a pena saber

**Presidente vem do arquivo nacional.** No zip do TSE, a votação
presidencial não está nos arquivos por UF: fica num membro `_BR`, com uma
linha por município e a UF em cada linha. Ler presidente por UF devolve zero
— foi assim que este importador nasceu errado e o teste do Amapá pegou.

**Voto em trânsito entra nos totais e sai do território.** A linha existe e
conta na disputa, mas o eleitor não mora ali. Na soma da UF e do Brasil ele
entra; na do município, não — senão o mapa mostra voto onde ninguém votou.

**Deputado federal só no nível da UF, e só eleito.** O que se compara ano a
ano é a bancada; guardar o proporcional por município seriam milhões de
linhas para uma pergunta que ninguém faz duas vezes.

**Senado tem duas vagas em alguns anos e uma em outros.** Nada aqui
normaliza isso: a tabela guarda o que houve, e quem compara composição usa
as 81 cadeiras, não as vagas em disputa daquele ano (ver docs).

**Situação vem do TSE.** `DS_SIT_TOT_TURNO` ("ELEITO", "ELEITO POR QP",
"2º TURNO", "NÃO ELEITO"). O campo `eleito` é derivado dela, e só no nível
que decide a disputa — município não elege ninguém (§3).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..tse.parsers.common import fold
from . import tse_odsele as H
from .banco import HistTotal, HistVoto, abrir

#: Cargos majoritários, guardados até o município.
CARGOS_MAJ = ("1", "3", "5")
#: Deputado federal: só UF, só eleitos (bancada).
CARGO_DEP = "6"
#: `SG_UF` que não são território (voto em trânsito, exterior agregado).
NAO_TERRITORIAL = frozenset({"vt"})


@dataclass
class Resumo:
    ano: int
    totais: int = 0
    votos: int = 0
    ufs: int = 0
    avisos: tuple[str, ...] = ()


def _eleito(situacao: str) -> bool:
    s = fold(situacao)
    return s.startswith("ELEITO") or s == "MEDIA"


def importar(ano: int, *, turnos: tuple[str, ...] = ("1", "2"),
             cargos: tuple[str, ...] = CARGOS_MAJ,
             com_deputados: bool = True, caminho=None,
             progresso=None) -> Resumo:
    """Importa um ano inteiro. `progresso(texto)` recebe o andamento."""
    zv = H.arquivo(f"votacao_candidato_munzona_{ano}.zip", ano)
    zd = H.arquivo(f"detalhe_votacao_munzona_{ano}.zip", ano)
    engine, Session = abrir(caminho)
    avisar = progresso or (lambda _t: None)
    ufs = sorted({m.split("_")[-1].split(".")[0].lower()
                  for m in H.membros(zv)} - {"brasil", "br"})
    ufs = [u for u in ufs if len(u) == 2 and u not in NAO_TERRITORIAL]

    with Session() as s:
        # Idempotente: reimportar um ano substitui, nunca duplica.
        for modelo in (HistTotal, HistVoto):
            s.query(modelo).filter(modelo.ano == ano).delete()
        s.commit()

        resumo = Resumo(ano=ano, ufs=len(ufs))
        for turno in turnos:
            if "1" in cargos:
                n_t, n_v = _importar_presidente(s, ano, turno, zv, zd)
                resumo.totais += n_t
                resumo.votos += n_v
                avisar(f"{ano} turno {turno} presidente: {n_t} totais, "
                       f"{n_v} votos")
                s.commit()
            estaduais = tuple(c for c in cargos if c != "1")
            for uf in ufs:
                n_t, n_v = _importar_uf(s, ano, turno, uf, zv, zd, estaduais,
                                        com_deputados)
                resumo.totais += n_t
                resumo.votos += n_v
                if n_t or n_v:
                    avisar(f"{ano} turno {turno} {uf.upper()}: "
                           f"{n_t} totais, {n_v} votos")
            s.commit()
            _agregar_brasil(s, ano, turno, cargos)
            s.commit()
    if "5" in cargos:
        n = importar_suplementares_senado(ano, caminho=caminho)
        avisar(f"{ano}: {n} senador(es) eleito(s) em suplementar posterior")
    return resumo


def importar_suplementares_senado(ano: int, *, caminho=None) -> int:
    """Eleitos ao Senado em eleição SUPLEMENTAR que o zip do ano traz.

    O zip de 2018 tem a suplementar de 2020 em Mato Grosso (a eleita de
    2018 foi cassada). Ela não é resultado de 2018 — o leitor a exclui por
    padrão —, mas é quem ocupou a cadeira: a composição do Senado de 2022
    precisa dela (decisão de 24/09/2026). Grava só os eleitos, no nível da
    UF, com o ANO DA SUPLEMENTAR (de `DT_ELEICAO`), para nunca se misturar
    com "eleitos em 2018". Idempotente.
    """
    zv = H.arquivo(f"votacao_candidato_munzona_{ano}.zip", ano)
    _, Session = abrir(caminho)
    exigidas = H.COLS_CANDIDATO + ("DT_ELEICAO",)
    votos: dict[tuple, int] = defaultdict(int)
    ficha: dict[tuple, dict] = {}
    for membro in H.membros(zv):
        for r in H._linhas(zv, membro, exigidas):
            if r["CD_CARGO"] != "5":
                continue
            ords = H.ordinarias(ano, r["NR_TURNO"])
            if ords is None or r["CD_ELEICAO"] in ords:
                continue
            k = (r["SG_UF"].lower(), r["SQ_CANDIDATO"], r["DT_ELEICAO"])
            votos[k] += H.num(r["QT_VOTOS_NOMINAIS"]) or 0
            ficha.setdefault(k, r)
    n = 0
    with Session() as s:
        for (uf, sq, dt), total in votos.items():
            r = ficha[(uf, sq, dt)]
            if not str(r["DS_SIT_TOT_TURNO"]).upper().startswith("ELEITO"):
                continue
            ano_sup = int(str(dt)[-4:])
            s.query(HistVoto).filter(
                HistVoto.ano == ano_sup, HistVoto.cargo == "5",
                HistVoto.tipo == "uf", HistVoto.uf == uf,
                HistVoto.sqcand == sq).delete()
            s.add(HistVoto(
                ano=ano_sup, turno=r["NR_TURNO"], cargo="5", tipo="uf",
                uf=uf, cd_municipio="", sqcand=sq,
                numero=r["NR_CANDIDATO"], nome_urna=r["NM_URNA_CANDIDATO"],
                partido_numero=r["NR_PARTIDO"],
                partido_sigla=r["SG_PARTIDO"], votos=total,
                situacao=f"ELEITO EM SUPLEMENTAR ({dt})", eleito=True,
                destinacao="Válido"))
            n += 1
        s.commit()
    return n


def _importar_presidente(s, ano, turno, zv, zd):
    """Presidente: um passo só, a partir do membro nacional do zip.

    Cada linha traz o município e a UF dele, então daqui saem as três
    abrangências: município, UF e Brasil (este último em `_agregar_brasil`).
    """
    campos = ("aptos", "comparecimento", "abstencoes", "total_votos",
              "concorrentes", "validos", "anulados", "anulados_sub_judice",
              "brancos", "nulos", "secoes_total")
    por_mun: dict[tuple, dict] = {}
    por_uf: dict[str, dict] = {}
    for d in H.ler_detalhe(zd, "br", cargos=("1",), turno=turno,
                             eleicoes=H.ordinarias(ano, turno)):
        if d.uf in NAO_TERRITORIAL:
            continue
        alvo_uf = por_uf.setdefault(d.uf, dict.fromkeys(campos, 0))
        for c in campos:
            alvo_uf[c] += getattr(d, c)
        if d.em_transito:
            continue
        alvo = por_mun.setdefault((d.uf, d.cd_municipio),
                                  dict.fromkeys(campos, 0))
        for c in campos:
            alvo[c] += getattr(d, c)
    n_t = 0
    for (uf, mun), v in por_mun.items():
        s.add(HistTotal(ano=ano, turno=turno, cargo="1", tipo="mu", uf=uf,
                        cd_municipio=mun, **v))
        n_t += 1
    for uf, v in por_uf.items():
        s.add(HistTotal(ano=ano, turno=turno, cargo="1", tipo="uf", uf=uf,
                        cd_municipio="", **v))
        n_t += 1

    mun_votos: dict[tuple, int] = defaultdict(int)
    uf_votos: dict[tuple, int] = defaultdict(int)
    ficha: dict[str, object] = {}
    nomes: dict[tuple, str] = {}
    for v in H.ler_votos(zv, "br", cargos=("1",), turno=turno,
                           eleicoes=H.ordinarias(ano, turno)):
        if v.uf in NAO_TERRITORIAL:
            continue
        ficha.setdefault(v.sq_candidato, v)
        uf_votos[(v.uf, v.sq_candidato)] += v.votos
        if v.em_transito:
            continue
        mun_votos[(v.uf, v.cd_municipio, v.sq_candidato)] += v.votos
        nomes.setdefault((v.uf, v.cd_municipio), v.nm_municipio)
    n_v = 0
    for (uf, mun, sq), votos in mun_votos.items():
        f = ficha[sq]
        s.add(HistVoto(ano=ano, turno=turno, cargo="1", tipo="mu", uf=uf,
                       cd_municipio=mun, sqcand=sq, numero=f.nr_candidato,
                       nome_urna=f.nm_urna, partido_numero=f.nr_partido,
                       partido_sigla=f.sg_partido, votos=votos,
                       destinacao=f.destinacao))
        n_v += 1
    for (uf, sq), votos in uf_votos.items():
        f = ficha[sq]
        s.add(HistVoto(ano=ano, turno=turno, cargo="1", tipo="uf", uf=uf,
                       cd_municipio="", sqcand=sq, numero=f.nr_candidato,
                       nome_urna=f.nm_urna, partido_numero=f.nr_partido,
                       partido_sigla=f.sg_partido, votos=votos,
                       situacao=f.situacao, eleito=_eleito(f.situacao),
                       destinacao=f.destinacao))
        n_v += 1
    for (uf, mun), nome in nomes.items():
        for t in s.query(HistTotal).filter(
                HistTotal.ano == ano, HistTotal.turno == turno,
                HistTotal.cargo == "1", HistTotal.tipo == "mu",
                HistTotal.uf == uf, HistTotal.cd_municipio == mun):
            t.nome = nome
    return n_t, n_v


def _importar_uf(s, ano, turno, uf, zv, zd, cargos, com_deputados):
    """Uma UF de um turno: municípios, o total da UF e os votos."""
    # --- totais (detalhe): zona → município → UF
    por_mun: dict[tuple, dict] = {}
    por_uf: dict[str, dict] = {}
    campos = ("aptos", "comparecimento", "abstencoes", "total_votos",
              "concorrentes", "validos", "anulados", "anulados_sub_judice",
              "brancos", "nulos", "secoes_total")
    for d in H.ler_detalhe(zd, uf, cargos=cargos, turno=turno,
                             eleicoes=H.ordinarias(ano, turno)):
        alvo_uf = por_uf.setdefault(d.cargo, dict.fromkeys(campos, 0))
        for c in campos:
            alvo_uf[c] += getattr(d, c)
        if d.em_transito:
            continue          # conta na UF, não pertence a município nenhum
        k = (d.cargo, d.cd_municipio)
        alvo = por_mun.setdefault(k, dict.fromkeys(campos, 0))
        for c in campos:
            alvo[c] += getattr(d, c)

    n_t = 0
    for (cargo, mun), v in por_mun.items():
        s.add(HistTotal(ano=ano, turno=turno, cargo=cargo, tipo="mu", uf=uf,
                        cd_municipio=mun, **v))
        n_t += 1
    for cargo, v in por_uf.items():
        s.add(HistTotal(ano=ano, turno=turno, cargo=cargo, tipo="uf", uf=uf,
                        cd_municipio="", **v))
        n_t += 1

    # --- votos por candidato
    cargos_voto = tuple(cargos) + ((CARGO_DEP,) if com_deputados else ())
    mun_votos: dict[tuple, int] = defaultdict(int)
    uf_votos: dict[tuple, int] = defaultdict(int)
    ficha: dict[tuple, H.VotoHistorico] = {}
    nomes_mun: dict[str, str] = {}
    for v in H.ler_votos(zv, uf, cargos=cargos_voto, turno=turno,
                           eleicoes=H.ordinarias(ano, turno)):
        chave = (v.cargo, v.sq_candidato)
        ficha.setdefault(chave, v)
        uf_votos[chave] += v.votos
        if v.em_transito or v.cargo == CARGO_DEP:
            continue
        mun_votos[(v.cargo, v.cd_municipio, v.sq_candidato)] += v.votos
        nomes_mun.setdefault(v.cd_municipio, v.nm_municipio)

    n_v = 0
    for (cargo, mun, sq), votos in mun_votos.items():
        f = ficha[(cargo, sq)]
        s.add(HistVoto(ano=ano, turno=turno, cargo=cargo, tipo="mu", uf=uf,
                       cd_municipio=mun, sqcand=sq, numero=f.nr_candidato,
                       nome_urna=f.nm_urna, partido_numero=f.nr_partido,
                       partido_sigla=f.sg_partido, votos=votos,
                       destinacao=f.destinacao))
        n_v += 1
    for (cargo, sq), votos in uf_votos.items():
        f = ficha[(cargo, sq)]
        # Deputado federal: só quem se elegeu (a bancada é o que se compara).
        if cargo == CARGO_DEP and not _eleito(f.situacao):
            continue
        s.add(HistVoto(ano=ano, turno=turno, cargo=cargo, tipo="uf", uf=uf,
                       cd_municipio="", sqcand=sq, numero=f.nr_candidato,
                       nome_urna=f.nm_urna, partido_numero=f.nr_partido,
                       partido_sigla=f.sg_partido, votos=votos,
                       situacao=f.situacao, eleito=_eleito(f.situacao),
                       destinacao=f.destinacao))
        n_v += 1
    # Nome do município, para a tela não depender do banco ao vivo.
    for mun, nome in nomes_mun.items():
        for t in s.query(HistTotal).filter(
                HistTotal.ano == ano, HistTotal.turno == turno,
                HistTotal.tipo == "mu", HistTotal.uf == uf,
                HistTotal.cd_municipio == mun):
            t.nome = nome
    return n_t, n_v


def _agregar_brasil(s, ano, turno, cargos):
    """Soma as UFs. Só presidente tem disputa nacional, mas o total do país
    de governador e senador é útil para participação — e é soma, não
    afirmação sobre mandato."""
    from sqlalchemy import select

    campos = ("aptos", "comparecimento", "abstencoes", "total_votos",
              "concorrentes", "validos", "anulados", "anulados_sub_judice",
              "brancos", "nulos", "secoes_total")
    for cargo in cargos:
        linhas = s.execute(select(HistTotal).where(
            HistTotal.ano == ano, HistTotal.turno == turno,
            HistTotal.cargo == cargo, HistTotal.tipo == "uf")).scalars().all()
        if not linhas:
            continue
        soma = {c: sum(getattr(x, c) for x in linhas) for c in campos}
        s.add(HistTotal(ano=ano, turno=turno, cargo=cargo, tipo="br",
                        uf="br", cd_municipio="", nome="Brasil", **soma))
        if cargo != "1":
            continue                 # só presidente se decide no Brasil
        votos = defaultdict(int)
        fichas = {}
        for v in s.execute(select(HistVoto).where(
                HistVoto.ano == ano, HistVoto.turno == turno,
                HistVoto.cargo == cargo, HistVoto.tipo == "uf")).scalars():
            votos[v.sqcand] += v.votos
            fichas.setdefault(v.sqcand, v)
        for sq, total in votos.items():
            f = fichas[sq]
            s.add(HistVoto(ano=ano, turno=turno, cargo=cargo, tipo="br",
                           uf="br", cd_municipio="", sqcand=sq,
                           numero=f.numero, nome_urna=f.nome_urna,
                           partido_numero=f.partido_numero,
                           partido_sigla=f.partido_sigla, votos=total,
                           situacao=f.situacao, eleito=f.eleito,
                           destinacao=f.destinacao))
