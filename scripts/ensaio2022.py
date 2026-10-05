"""Ensaio das projeções com eleição real, na ordem real de chegada (§11.2).

    python scripts/ensaio2022.py presidente
    python scripts/ensaio2022.py governador --uf sp
    python scripts/ensaio2022.py senador --uf rj --ano 2018
    python scripts/ensaio2022.py presidente --rodadas 20 --sims 2000
    python scripts/ensaio2022.py governador --uf mg --sem-fatiar --modelos m1

Diferente de `backtest.py` e de `obs2026 ensaiar-auditoria`, aqui a ordem de
chegada **não é construída por nós**: é a hora em que cada município terminou
de totalizar no 1º turno de 2022, lida do campo `dt`+`ht` dos arquivos de
acompanhamento do TSE (eleição 544, federal), baixados em
`data/raw/ensaio2022/544/`. A mesma sequência roda embaralhada, como régua:
se a projeção só acerta na ordem aleatória, ela não está pronta para a noite
real.

Como funciona:

- **cidades grandes entram aos poucos.** Município do grupo capital
  (capital + arranjo, `obs2026/engine/territorio.py`) ou com 200 mil eleitores
  ou mais é fatiado pelas **zonas eleitorais**; cada zona recebe uma hora
  sorteada entre o primeiro resultado da noite e a hora em que o município
  terminou, e a última zona fecha na hora real. Zonas de uma capital votam
  diferente entre si, então o modelo enfrenta o mesmo que na noite real: um
  parcial da capital que não representa a capital inteira;
- as rodadas têm o **mesmo número de municípios-equivalentes** (uma zona vale
  1/nº de zonas do município);
- em cada rodada, M1 e M2 projetam sobre **o mesmo** retrato, e cada um vai
  para um diário separado (`data/previsoes/ensaio2022-<ordem>-<modelo>-
  <disputa>.jsonl`), o que os põe no gráfico de convergência sem tocar na
  auditoria do simulado;
- o relatório por rodada mostra contado, projeção com intervalo de 80%,
  resultado final, se o intervalo contém o final e se o líder está certo.

O percentual é o do TSE: votos do candidato sobre votos válidos (sem brancos
e nulos). No Senado de duas vagas cada eleitor dá dois votos e os dois entram
nos válidos.

Limitações que o relatório repete:

1. A ordem **entre as zonas** de uma cidade é sorteada: só se conhece a hora
   em que o município terminou. Municípios não fatiados entram inteiros.
2. **Governador e Senado usam a ordem da eleição federal (544).** A
   estadual (546) foi re-totalizada pelo TSE em set/2026 e perdeu as horas
   de 2022. Mesma urna, mesmo horário.
3. **2018 usa a ordem de 2022.** A ordem de 2018 não está mais publicada.
4. Municípios sem hora (41 do exterior) entram por último.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR, load_thresholds                # noqa: E402
from obs2026.engine import (audit, backtest as B, decided,          # noqa: E402
                            models as M, runner as R, simulate as S,
                            territorio as T)
from obs2026.history import tse_odsele as H                        # noqa: E402
from obs2026.tse.parsers.ea20 import (Candidate, CargoResult,      # noqa: E402
                                      DVT_VALIDO, Ea20,
                                      ElectorateCounts, SectionCounts,
                                      VoteCounts)

RAIZ = Path(__file__).resolve().parent.parent
HIST = RAIZ.parent
ORDEM_DIR = DATA_DIR / "raw" / "ensaio2022" / "544"
SAIDA = DATA_DIR / "exports" / "ensaio2022"

CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}
#: Vagas por cargo e ano. Senado alterna: 2018 elegeu dois, 2022 um.
VAGAS = {("5", 2018): 2}
ELEICAO = {2022: "544", 2018: "295"}
#: Município a partir deste eleitorado entra fatiado por zona mesmo fora do
#: grupo capital (Campinas, Uberlândia, Juiz de Fora...).
FATIAR_A_PARTIR = 200_000
#: Ano da eleição sendo ensaiada. `carregar_historico` o fixa; o M3 usa a
#: inclinação da eleição anterior a ele (2022 → 2018; 2018 → 2014), como
#: 2026 usará a de 2022.
ANO_ALVO = 2022


def _m3(obs, cands, seats=1, **kw):
    return M.build_m3(obs, cands, seats=seats, ano_referencia=ANO_ALVO - 4,
                      **kw)


def _m31(obs, cands, seats=1, **kw):
    return M.build_m31(obs, cands, seats=seats, ano_referencia=ANO_ALVO - 4,
                       **kw)


def _m32(obs, cands, seats=1, **kw):
    return M.build_m32(obs, cands, seats=seats, ano_referencia=ANO_ALVO - 4,
                       **kw)


MODELOS = {"m0": M.build_m0, "m1": M.build_m1, "m2": M.build_m2,
           "m3": _m3, "m31": _m31, "m32": _m32}


def modelo(nome: str):
    """`m1`, `m2`, `m3`, ou uma variante para calibrar: `m2@0.3` ou
    `m3@0.3` (sigma do grupo vazio), `m2@0.3^1` (expoente da curva) e
    `m3@0.2k20000` (sigma e K_INCLINACAO)."""
    if nome in MODELOS:
        return MODELOS[nome]
    base, _, resto = nome.partition("@")
    if base == "m32":
        # m32@30 = M0_CONFORMAL; m32@30g0.5 acrescenta GAMMA_CONFORMAL.
        m0_, _, g = resto.partition("g")
        kw = {"m0_conformal": float(m0_)}
        if g:
            kw["gamma_conformal"] = float(g)
        return lambda obs, cands, seats=1: _m32(obs, cands, seats=seats,
                                                **kw)
    if base not in ("m2", "m3") or not resto:
        raise ValueError(f"modelo desconhecido: {nome}")
    resto, _, k = resto.partition("k")
    sig, _, exp = resto.partition("^")
    kw = {"sigma_grupo_vazio": float(sig)}
    if exp:
        kw["expoente"] = float(exp)
    if base == "m3":
        if k:
            kw["k_inclinacao"] = float(k)
        return lambda obs, cands, seats=1: _m3(obs, cands, seats=seats, **kw)
    return lambda obs, cands, seats=1: M.build_m2(obs, cands, seats=seats,
                                                   **kw)

CAMPOS = ("te", "c", "a", "tv", "vvc", "vv", "van", "vansj", "vb", "tvn",
          "ts", "sni")


# ---------------------------------------------------------------- ordem ---

def carregar_ordem() -> dict[tuple[str, str], datetime]:
    """(uf, municipio) -> hora de totalização em 2022 (eleição 544)."""
    if not ORDEM_DIR.exists():
        sys.exit(f"faltam os arquivos de ordem em {ORDEM_DIR}")
    horas = {}
    for f in sorted(ORDEM_DIR.glob("*-e000544-ab.json")):
        uf = f.name[:2]
        if uf == "br":
            continue
        for a in json.loads(f.read_text(encoding="utf-8"))["abr"]:
            if a.get("tpabr", "").lower() not in ("mu", "mun"):
                continue
            if a.get("dt") and a.get("ht"):
                horas[(uf, a["cdabr"])] = datetime.strptime(
                    f"{a['dt']} {a['ht']}", "%d/%m/%Y %H:%M:%S")
    return horas


# ------------------------------------------------------------ histórico ---

@dataclass
class Municipio:
    uf: str
    mun: str
    #: zona -> contagens (CAMPOS)
    det: dict
    #: zona -> sqcand -> votos
    votos: dict
    #: sqcand -> metadados
    meta: dict
    capital: bool = False

    @property
    def zonas(self) -> list[str]:
        return sorted(self.det)

    @property
    def te(self) -> int:
        return sum(d["te"] for d in self.det.values())


def carregar_historico(ano: int, cargo: str,
                       uf: str | None) -> tuple[list[Municipio], int, str]:
    """Resultado final por município E zona, dos zips de dados abertos.

    O código do município é completado com zeros à esquerda: em
    `votacao_candidato_munzona_2022_BR.csv` ele vem sem eles em parte das
    linhas (`1007` em vez de `01007`), e sem a correção 519 municípios
    perdiam os votos por candidato. `SG_UF='VT'` não é território e sai.
    """
    global ANO_ALVO
    ANO_ALVO = ano
    alvo = "br" if cargo == "1" else uf
    vagas = VAGAS.get((cargo, ano), 1)
    zv = H.arquivo(f"votacao_candidato_munzona_{ano}.zip", ano)
    zd = H.arquivo(f"detalhe_votacao_munzona_{ano}.zip", ano)

    det = defaultdict(lambda: defaultdict(lambda: dict.fromkeys(CAMPOS, 0)))
    for d in H.ler_detalhe(zd, alvo, cargos=(cargo,)):
        if d.uf.upper() in H.UF_NAO_TERRITORIAL:
            continue
        z = det[(d.uf, d.cd_municipio.zfill(5))][d.zona]
        for campo, v in (("te", d.aptos), ("c", d.comparecimento),
                         ("a", d.abstencoes), ("tv", d.total_votos),
                         ("vvc", d.concorrentes), ("vv", d.validos),
                         ("van", d.anulados),
                         ("vansj", d.anulados_sub_judice),
                         ("vb", d.brancos), ("tvn", d.nulos),
                         ("ts", d.secoes_total),
                         ("sni", d.secoes_nao_instaladas)):
            z[campo] += v

    votos = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    meta = defaultdict(dict)
    for v in H.ler_votos(zv, alvo, cargos=(cargo,)):
        if not v.territorial:
            continue
        k = (v.uf, v.cd_municipio.zfill(5))
        votos[k][v.zona][v.sq_candidato] += v.votos
        meta[k].setdefault(v.sq_candidato, {
            "nr": v.nr_candidato, "nome": v.nm_urna, "nrp": v.nr_partido,
            "sgp": v.sg_partido,
            "dvt": DVT_VALIDO if v.destinacao == DVT_VALIDO else v.destinacao,
        })

    muns = [Municipio(uf=u, mun=m, det={z: dict(d) for z, d in zs.items()},
                      votos={z: dict(c) for z, c in votos[(u, m)].items()},
                      meta=meta[(u, m)], capital=T.e_capital(u, m))
            for (u, m), zs in det.items()]
    return muns, vagas, ELEICAO[ano]


def _soma(dets) -> dict:
    s = dict.fromkeys(CAMPOS, 0)
    for d in dets:
        for k in CAMPOS:
            s[k] += d[k]
    return s


def montar(m: Municipio, contadas: set[str], cargo: str, vagas: int,
           eleicao: str) -> Ea20:
    """O município como o coletor o veria com `contadas` zonas totalizadas.

    Todas as zonas: encerrado. Nenhuma: não apurado. Parte: em andamento,
    com o eleitorado das zonas que faltam como pendente — é o estado que o
    EA20 mostra de um município grande no meio da noite.
    """
    tot = _soma(m.det.values())
    cont = _soma(m.det[z] for z in contadas)
    final = len(contadas) == len(m.det)
    votos = defaultdict(int)
    for z in contadas:
        for sq, v in m.votos.get(z, {}).items():
            votos[sq] += v
    candidatos = tuple(
        Candidate(
            numero=c["nr"], sqcand=sq, nome=c["nome"], nome_urna=c["nome"],
            seq=None, dvt=c["dvt"], classificado_flag="n", situacao="",
            votos=votos.get(sq, 0), pct_tse=None, partido_numero=c["nrp"],
            partido_sigla=c["sgp"], partido_nome=c["sgp"], agr_numero="",
            agr_nome="", agr_tipo="", agr_composicao="")
        for sq, c in sorted(m.meta.items()))
    esa_tot = tot["c"] + tot["a"]
    te = max(tot["te"], esa_tot)
    esa = cont["c"] + cont["a"]
    if final:
        el = ElectorateCounts(te=te, est=te, esnt=0, esi=esa, esni=te - esa,
                              esa=esa, esna=0, c=cont["c"], a=cont["a"])
        sec = SectionCounts(ts=tot["ts"], st=tot["ts"], snt=0,
                            si=tot["ts"] - tot["sni"], sni=tot["sni"],
                            sa=tot["ts"] - tot["sni"], sna=0)
    else:
        el = ElectorateCounts(te=te, est=esa, esnt=te - esa, esi=te, esni=0,
                              esa=esa, esna=te - esa, c=cont["c"],
                              a=cont["a"])
        sec = SectionCounts(ts=tot["ts"], st=cont["ts"],
                            snt=tot["ts"] - cont["ts"], si=tot["ts"], sni=0,
                            sa=cont["ts"], sna=tot["ts"] - cont["ts"])
    return Ea20(
        election=eleicao, turno="1", fase="o", suplementar=False,
        tpabr="mu", cdabr=m.mun, generated_at=None, totalized_at=None,
        idg=None, andamento="f" if final else ("p" if contadas else "n"),
        divulga_votos=True, totalizacao_final=final, esae=None, mnae=(),
        cargo=CargoResult(cd=cargo, nome="", vagas=vagas,
                          candidatos=candidatos),
        sections=sec, electorate=el,
        votes=VoteCounts(tv=cont["tv"], vvc=cont["vvc"], vv=cont["vv"],
                         vnom=cont["vv"], van=cont["van"],
                         vansj=cont["vansj"], vb=cont["vb"], tvn=cont["tvn"],
                         vn=cont["tvn"], vnt=None, vscv=None),
    )


def observar(m: Municipio, contadas, cargo, vagas, eleicao):
    r = montar(m, contadas, cargo, vagas, eleicao)
    return M.UnitObservation(
        unit=f"{m.uf}:{m.mun}", uf=m.uf,
        # O estrato do M1 é o de produção, sem a marca de capital: assim o
        # M1 do ensaio é o M1 que roda de verdade.
        stratum=M.estrato(m.uf, r.electorate.te), result=r,
        capital=m.capital)


# -------------------------------------------------------------- eventos ---

def eventos(muns: list[Municipio], horas, ordem: str, seed: int,
            fatiar: bool):
    """Sequência de chegadas: (atrasado, instante, municipio, zona|None, peso).

    Peso em municípios-equivalentes: o município inteiro vale 1, cada zona
    de um município fatiado vale 1/nº de zonas.
    """
    rng = np.random.default_rng(seed)
    t0 = min(horas.values())
    fim = max(horas.values())
    ev = []
    for m in muns:
        h = horas.get((m.uf, m.mun))
        chave = h if h is not None else fim
        atrasado = h is None
        zs = m.zonas
        if fatiar and len(zs) > 1 and (m.capital or m.te >= FATIAR_A_PARTIR):
            span = (chave - t0).total_seconds()
            ts = sorted(rng.uniform(0, span, len(zs) - 1).tolist()) + [span]
            ordem_z = rng.permutation(len(zs))
            for t, i in zip(ts, ordem_z):
                ev.append((atrasado, t0.timestamp() + t, m, zs[i],
                           1.0 / len(zs)))
        else:
            ev.append((atrasado, chave.timestamp(), m, None, 1.0))
    if ordem == "real":
        ev.sort(key=lambda e: (e[0], e[1], e[2].uf, e[2].mun))
    else:
        ev = [ev[i] for i in np.random.default_rng(seed + 1).permutation(
            len(ev))]
    return ev


# ------------------------------------------------------------- rodadas ---

def rodar(muns, vagas, eleicao, horas, *, ordem, rodadas, sims, seed, cargo,
          disputa, thr, modelos=("m1", "m2"), fatiar=True, registrar=True):
    ev = eventos(muns, horas, ordem, seed, fatiar)
    peso_total = sum(e[4] for e in ev)
    n_mun = len(muns)
    finais = [observar(m, set(m.zonas), cargo, vagas, eleicao) for m in muns]
    cands = sorted({c.sqcand for o in finais for c in o.result.cargo.candidatos
                    if c.conta_como_valido})
    real = B.verdade(finais, cands)
    total_el = sum(o.result.electorate.te for o in finais)
    nomes = {c.sqcand: c.nome_urna for o in finais
             for c in o.result.cargo.candidatos}
    topo_real = sorted(cands, key=lambda c: -real["votos"][c])
    eleitos = set(topo_real[:vagas])
    mostrar = topo_real[:max(3, vagas + 2)]
    unidade = "br" if cargo == "1" else disputa.split("-")[-1]

    diarios = {}
    if registrar:
        for mod in modelos:
            d = audit.Diario(DATA_DIR / "previsoes",
                             f"ensaio2022-{ordem}-{mod}-{disputa}")
            if d.caminho.exists():
                d.caminho.unlink()           # cada ensaio recomeça limpo
            diarios[mod] = d

    linhas = {mod: [] for mod in modelos}
    comuns = []
    contadas = defaultdict(set)
    i, acum = 0, 0.0
    for r in range(1, rodadas + 1):
        alvo = peso_total * r / rodadas
        while i < len(ev) and (acum + ev[i][4] <= alvo + 1e-9
                               or r == rodadas):
            _, t, m, z, w = ev[i]
            contadas[(m.uf, m.mun)].update(m.zonas if z is None else {z})
            acum += w
            i += 1
        ultimo = datetime.fromtimestamp(ev[i - 1][1]) if i else None
        obs = [observar(m, contadas[(m.uf, m.mun)], cargo, vagas, eleicao)
               for m in muns]
        contado = {c: sum(o.obs.votos_por_candidato.get(c, 0) for o in obs)
                   for c in cands}
        val_cont = sum(o.obs.validos for o in obs)
        el_cont = sum(o.result.electorate.esa for o in obs)
        topo_cont = sorted(cands, key=lambda c: -contado[c])
        comuns.append({
            "rodada": r, "municipios_eq": round(acum, 1),
            "municipios_total": n_mun,
            "eleitorado_pct": 100.0 * el_cont / total_el,
            "validos_pct": 100.0 * val_cont / real["validos"],
            "hora_ultimo": (ultimo.isoformat(" ") if ordem == "real"
                            and ultimo else None),
            "contado_pct": {c: (100.0 * contado[c] / val_cont
                                if val_cont else None) for c in mostrar},
            "lider_contado_certo": set(topo_cont[:vagas]) == eleitos,
        })
        ctx = R.montar_contexto(obs, cargo=cargo,
                                abrangencia="br" if cargo == "1" else "uf",
                                unidade=unidade)
        for mod in modelos:
            units = modelo(mod)(obs, cands, seats=vagas)
            fc = S.run(units, cands, S.SimConfig(n_sims=sims, seed=20261004),
                       seats=vagas, senado=vagas > 1)
            linhas[mod].append(_medir(fc, cands, real, nomes, mostrar,
                                      eleitos, vagas))
            if mod in diarios:
                _registrar(diarios[mod], fc, ctx, cands, cargo, ordem, mod,
                           r, thr)

    return {"ordem": ordem, "seed": seed if ordem != "real" else None,
            "vagas": vagas, "fatiado": fatiar, "comum": comuns,
            "modelos": {mod: linhas[mod] for mod in modelos},
            "diarios": {mod: str(d.caminho) for mod, d in diarios.items()}}


def _medir(fc, cands, real, nomes, mostrar, eleitos, vagas) -> dict:
    q = fc.quantis((0.025, 0.05, 0.10, 0.50, 0.90, 0.95, 0.975))
    plid = fc.prob_lideranca()
    # Com uma vaga, a chance que vai à tela é a de liderar; com duas
    # (Senado 2018), a de ficar dentro das vagas.
    pvaga = plid if vagas == 1 else fc.prob_dentro_das_vagas()
    topo_proj = sorted(cands, key=lambda c: -q[c]["votos_mediana"])
    dentro = [q[c]["pct_validos"]["q100"] <= real["pct"][c]
              <= q[c]["pct_validos"]["q900"] for c in cands]
    # Cobertura também em 90% e 95%: o nível publicado sobe com o apurado
    # (engine/nivel.py) e cada nível precisa cumprir a própria promessa.
    def _cob(lo, hi):
        return sum(q[c]["pct_validos"][lo] <= real["pct"][c]
                   <= q[c]["pct_validos"][hi] for c in cands) / len(cands)
    return {
        "candidatos": [{
            "sqcand": c, "nome": nomes.get(c, c),
            "proj_q10": q[c]["pct_validos"]["q100"],
            "proj_q50": q[c]["pct_validos"]["q500"],
            "proj_q90": q[c]["pct_validos"]["q900"],
            "final_pct": real["pct"][c],
            "dentro_80": (q[c]["pct_validos"]["q100"] <= real["pct"][c]
                          <= q[c]["pct_validos"]["q900"]),
            "prob_vaga": pvaga.get(c),
        } for c in mostrar],
        "lider_projetado_certo": set(topo_proj[:vagas]) == eleitos,
        "cobertura_80_todos": sum(dentro) / len(dentro),
        "cobertura_90_todos": _cob("q050", "q950"),
        "cobertura_95_todos": _cob("q025", "q975"),
        #: Maior chance (de liderar ou de ficar nas vagas) dada a quem NÃO
        #: venceu. É o número que envergonharia no telão.
        "confianca_errada": max((p for c, p in pvaga.items()
                                 if c not in eleitos), default=0.0),
        "erro_pp_mediano": float(np.median(
            [abs(q[c]["pct_validos"]["q500"] - real["pct"][c])
             for c in mostrar])),
    }


def _registrar(diario, fc, ctx, cands, cargo, ordem, mod, r, thr):
    dfn = decided.avaliar(
        {c: fc.observed.get(c, 0) for c in cands}, vagas=ctx.vagas,
        concorrentes_observados=ctx.concorrentes_observados,
        pendente_max=ctx.pendente_max, sub_judice=ctx.sub_judice,
        encerrada=False, unidade=ctx.unidade)
    diario.registrar(audit.capturar(
        forecast=fc, definicao=dfn, ambiente=f"ensaio2022-{ordem}-{mod}",
        eleicao=ctx.eleicao, turno=ctx.turno, cargo=cargo,
        abrangencia=ctx.abrangencia, unidade=ctx.unidade,
        snapshot_set_id=f"rodada-{r:02d}", fonte_gerada_em=None,
        eleitorado_total=ctx.eleitorado_total,
        eleitorado_apurado=ctx.eleitorado_apurado,
        secoes_totalizadas=ctx.secoes_totalizadas,
        secoes_total=ctx.secoes_total,
        pendente_min=ctx.pendente_min, pendente_max=ctx.pendente_max,
        modelo=mod, model_version=f"{mod}-ensaio2022",
        thresholds_hash=thr.config_hash,
        thresholds_homologados=thr.homologated,
        estado_publicacao="cenario_exploratorio", nomes=ctx.nomes,
        concorrentes_observados=ctx.concorrentes_observados,
        unidades_sem_dado=ctx.unidades_sem_dado,
        eleitorado_sem_dado=ctx.eleitorado_sem_dado))


def estavel(seq: list[bool]) -> int | None:
    """Primeira rodada (a partir de 1) desde a qual tudo é True."""
    ult = None
    for i in range(len(seq), 0, -1):
        if not seq[i - 1]:
            break
        ult = i
    return ult


#: Erro máximo, em pontos percentuais, para chamar o número de "estável".
TOLERANCIA_PP = 1.0


def _pct_em(comum, rodada):
    return None if rodada is None else comum[rodada - 1]["validos_pct"]


def resumo_modelo(ls: list[dict], comum: list[dict] | None = None,
                  vagas: int = 1) -> dict:
    """Resumo de um modelo numa ordem.

    `vencedor_pct` e `numero_pct` respondem "com quanto apurado o número é
    confiável": o % dos válidos apurados a partir do qual a projeção aponta
    o vencedor certo e não muda mais, e a partir do qual o percentual dos
    principais (vagas + 1) fica a menos de `TOLERANCIA_PP` do final e não
    sai mais.
    """
    parciais = ls[:-1]
    acerta = estavel([l["lider_projetado_certo"] for l in ls])
    perto = estavel([
        max(abs(c["proj_q50"] - c["final_pct"])
            for c in l["candidatos"][:vagas + 1]) <= TOLERANCIA_PP
        for l in ls])
    extra = {}
    if comum is not None:
        extra = {"vencedor_pct": _pct_em(comum, acerta),
                 "numero_pct": _pct_em(comum, perto),
                 "contagem_pct": _pct_em(comum, estavel(
                     [c["lider_contado_certo"] for c in comum]))}
    return {
        **extra,
        "numero_rodada": perto,
        "acerta": acerta,
        "cob80": sum(l["cobertura_80_todos"] for l in parciais)
        / len(parciais),
        # Cada nível publicado tem de cumprir a própria promessa
        # (engine/nivel.py): 80% no começo, 90% e 95% conforme apura.
        "cob90": sum(l.get("cobertura_90_todos", 0.0) for l in parciais)
        / len(parciais),
        "cob95": sum(l.get("cobertura_95_todos", 0.0) for l in parciais)
        / len(parciais),
        "errada": max(l["confianca_errada"] for l in parciais),
        "erro_r1": ls[0]["erro_pp_mediano"],
    }


# ------------------------------------------------------------ relatório ---

def _f(p):
    return "—" if p is None else f"{p:.0f}%"


def imprimir(titulo, res, n_top):
    mods = list(res["modelos"])
    print(f"\n=== {titulo} — ordem {res['ordem'].upper()}"
          f"{' (semente %d)' % res['seed'] if res['seed'] is not None else ''}"
          f"{'' if res['fatiado'] else ' — sem fatiar'} ===")
    for k, c in enumerate(res["comum"]):
        hora = f"  até {c['hora_ultimo'][11:16]}" if c["hora_ultimo"] else ""
        print(f"\nrodada {c['rodada']:>2}: {c['municipios_eq']:.0f}/"
              f"{c['municipios_total']} mun.-eq., eleitorado "
              f"{c['eleitorado_pct']:.1f}%, válidos {c['validos_pct']:.1f}%"
              f"{hora}")
        base = res["modelos"][mods[0]][k]["candidatos"][:n_top]
        for j, cb in enumerate(base):
            cont = c["contado_pct"].get(cb["sqcand"])
            partes = [f"  {cb['nome'][:18]:<18} cont "
                      f"{cont if cont is not None else 0:5.2f}"]
            for mod in mods:
                x = res["modelos"][mod][k]["candidatos"][j]
                partes.append(
                    f"{mod} {x['proj_q50']:5.2f} [{x['proj_q10']:5.2f}–"
                    f"{x['proj_q90']:5.2f}]{' ' if x['dentro_80'] else '*'}"
                    f" {x['prob_vaga'] or 0:4.0%}")
            partes.append(f"final {cb['final_pct']:5.2f}")
            print("  ".join(partes))
        print("  líder: contagem " + ("certa" if c["lider_contado_certo"]
                                      else "errada") + "".join(
            f" | {mod} "
            f"{'certo' if res['modelos'][mod][k]['lider_projetado_certo'] else 'ERRADO'}"
            f" cob80 {res['modelos'][mod][k]['cobertura_80_todos']:.0%}"
            for mod in mods))
    print("\n  (* = final fora do intervalo de 80%)")
    print(f"  contagem acerta de vez na rodada "
          f"{estavel([c['lider_contado_certo'] for c in res['comum']])}")
    for mod in mods:
        s = resumo_modelo(res["modelos"][mod], res["comum"], res["vagas"])
        print(f"  {mod}: vencedor estável com {_f(s['vencedor_pct'])} "
              f"apurado; número a menos de {TOLERANCIA_PP:.0f} pp com "
              f"{_f(s['numero_pct'])} (contagem: {_f(s['contagem_pct'])})")
        print(f"  {mod}: acerta de vez na rodada {s['acerta']}; "
              f"maior chance num perdedor {s['errada']:.0%}; "
              f"cobertura 80% média {s['cob80']:.0%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cargo", choices=CARGOS)
    ap.add_argument("--uf")
    ap.add_argument("--ano", type=int, default=2022, choices=(2018, 2022))
    ap.add_argument("--rodadas", type=int, default=10)
    ap.add_argument("--sims", type=int, default=1500)
    ap.add_argument("--semente", type=int, default=7,
                    help="semente do sorteio (zonas e ordem aleatória)")
    ap.add_argument("--modelos", default="m1,m2")
    ap.add_argument("--sem-fatiar", action="store_true",
                    help="municípios entram inteiros (como no 1º ensaio)")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()

    cargo = CARGOS[a.cargo]
    if cargo != "1" and not a.uf:
        sys.exit("governador e senador precisam de --uf")
    if not T.disponivel():
        sys.exit("falta config/arranjos_capitais.json — rode "
                 "scripts/gerar_arranjos.py")
    uf = a.uf.lower() if a.uf else None
    disputa = f"{a.ano}-{a.cargo}-{uf or 'br'}"
    modelos = tuple(m.strip() for m in a.modelos.split(","))

    horas = carregar_ordem()
    muns, vagas, eleicao = carregar_historico(a.ano, cargo, uf)
    if not muns:
        sys.exit(f"sem dados históricos para {disputa}")
    thr = load_thresholds()

    titulo = f"{a.cargo.upper()} {(uf or 'br').upper()} {a.ano}"
    print(f"{titulo}: {len(muns)} municípios, {a.rodadas} rodadas, "
          f"{a.sims} simulações, modelos {', '.join(modelos)}")
    if a.ano == 2018:
        print("AVISO: ordem de chegada de 2022 aplicada a 2018.")

    resultados = []
    for ordem in ("real", "aleatoria"):
        res = rodar(muns, vagas, eleicao, horas, ordem=ordem,
                    rodadas=a.rodadas, sims=a.sims, seed=a.semente,
                    cargo=cargo, disputa=disputa, thr=thr, modelos=modelos,
                    fatiar=not a.sem_fatiar)
        imprimir(titulo, res, a.top)
        resultados.append(res)

    SAIDA.mkdir(parents=True, exist_ok=True)
    arq = SAIDA / f"{disputa}.json"
    arq.write_text(json.dumps({
        "disputa": disputa, "gerado_em": datetime.now().isoformat(),
        "rodadas": a.rodadas, "sims": a.sims,
        "ordem_de_chegada": ("2022 (eleição 544)" if a.ano == 2022
                             else "2022 aplicada a 2018"),
        "resultados": resultados}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nrelatório em {arq}")


if __name__ == "__main__":
    main()
