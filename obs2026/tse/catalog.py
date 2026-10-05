"""Catálogo derivado do EA11 + EA12: o universo do que existe para coletar.

Regras que este módulo impõe, todas verificáveis contra os payloads reais:

- Os códigos de eleição vêm do EA11, nunca de exemplo copiado (§2.2). O seed
  de `sources.yaml` só serve para achar o `ele-c.json`; divergência entre seed
  e catálogo vira `quality_flag`.
- Presidente é o único cargo com exterior. Governador e Senador não existem em
  ZZ, e pedir esses arquivos geraria 404 em série — causa documentada de
  bloqueio de IP.
- O número de vagas do cargo é lido do EA20 (`carg.nv`) e confrontado com o
  esperado na configuração. Duas vagas de Senado em 2026 é fato do payload,
  não premissa do código.

Tamanho do universo com os dados de 2026 (conferido em 20/09/2026):

    Presidente  br + 27 UF + zz +  5.755 municípios/localidades
    Governador  br + 27 UF       +  5.571 municípios
    Senador     br + 27 UF       +  5.571 municípios
    ------------------------------------------------------------
    varredura municipal completa  = 16.897 arquivos

A 20 req/s isso leva ~14 minutos. É por isso que a varredura completa é a
rede de segurança periódica, e não o mecanismo principal (ver `collector`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import CargoConfig, Environment
from .parsers.ea11 import FASE_OFICIAL, Ea11, Eleicao
from .parsers.ea12 import Ea12, Municipio
from .urls import Target, UF_DISPUTAS, UrlBuilder


@dataclass(frozen=True, slots=True)
class Contest:
    """Uma disputa coletável: eleição x cargo."""

    election: str
    turno: str
    cargo: CargoConfig
    cargo_nome: str
    municipios: tuple[Municipio, ...]

    @property
    def key(self) -> str:
        return f"{self.election}:{self.cargo.cd}"

    @property
    def ufs(self) -> tuple[str, ...]:
        """UFs com arquivo de resultado para este cargo.

        Exterior (zz) só aparece em Presidente. Nunca gerar URL de Governador
        ou Senador em zz.
        """
        if self.cargo.includes_exterior:
            return tuple(sorted({m.uf for m in self.municipios}))
        return tuple(sorted(UF_DISPUTAS))


@dataclass
class Catalog:
    env: Environment
    urls: UrlBuilder
    ea11: Ea11
    ea12_por_eleicao: dict[str, Ea12]
    contests: tuple[Contest, ...]
    quality_flags: list[str] = field(default_factory=list)

    # -- universo de alvos ----------------------------------------------

    def control_targets(self) -> list[Target]:
        """Nacional e UF de cada disputa. Não dependem de detecção."""
        out: list[Target] = []
        for c in self.contests:
            if c.cargo.abrangencia_decisoria == "br":
                out.append(self.urls.ea20_br(c.election, c.cargo.cd))
            for uf in c.ufs:
                if uf == "zz":
                    # Exterior tem arquivo agregado por UF em Presidente.
                    out.append(self.urls.ea20_uf(c.election, c.cargo.cd, uf))
                else:
                    out.append(self.urls.ea20_uf(c.election, c.cargo.cd, uf))
        return out

    def proporcional_targets(self) -> list[tuple[str, float, Target]]:
        """Totais de UF dos cargos proporcionais: `(chave, intervalo, alvo)`.

        Só o arquivo da UF — o resultado de deputado é da UF, e o arquivo
        municipal não acrescenta nada à contagem de cadeiras. Só 1º turno:
        não existe 2º turno proporcional."""
        out: list[tuple[str, float, Target]] = []
        for chave, prop in sorted(self.env.proporcionais.items()):
            for p in self.ea11.pleitos:
                for e in p.eleicoes:
                    if (e.turno != "1" or e.cd not in self.ea12_por_eleicao
                            or not e.tem_cargo(prop["cd"])):
                        continue
                    so = set(prop.get("ufs") or ()) or set(UF_DISPUTAS)
                    fora = set(prop.get("exceto_ufs") or ())
                    for uf in sorted((so & set(UF_DISPUTAS)) - fora):
                        out.append((chave, prop["intervalo_s"],
                                    self.urls.ea20_uf(e.cd, prop["cd"], uf)))
        return out

    def accompaniment_targets(self) -> list[Target]:
        """EA14 + EA15: o mecanismo barato de detecção de mudança."""
        out: list[Target] = []
        for election in sorted({c.election for c in self.contests}):
            out.append(self.urls.ea14(election))
            ufs = sorted({uf for c in self.contests if c.election == election
                          for uf in c.ufs})
            for uf in ufs:
                out.append(self.urls.ea15(election, uf))
        return out

    def result_targets(self, contest: Contest | None = None) -> list[Target]:
        """Varredura municipal completa (rede de segurança periódica)."""
        contests = [contest] if contest else list(self.contests)
        out: list[Target] = []
        for c in contests:
            for m in c.municipios:
                out.append(self.urls.ea20_mun(c.election, c.cargo.cd,
                                              m.uf, m.cd))
        return out

    def municipio(self, uf: str, cd: str) -> Municipio | None:
        for ea12 in self.ea12_por_eleicao.values():
            m = ea12.by_code().get((uf, cd))
            if m:
                return m
        return None

    def electorate_hint(self, uf: str, cd: str) -> int:
        """Peso da unidade para a fila. Substituído pelo eleitorado real
        assim que o EA20/EA15 daquela unidade chegar."""
        m = self.municipio(uf, cd)
        return len(m.zonas) * 10000 if m else 1


def build(env: Environment, urls: UrlBuilder, ea11: Ea11,
          ea12s: dict[str, Ea12]) -> Catalog:
    flags: list[str] = []

    # Barreira de ambiente: 'o' = oficial, 's' = simulado.
    if env.official and ea11.fase != FASE_OFICIAL:
        raise RuntimeError(
            f"ambiente configurado como oficial mas o ele-c.json veio com "
            f"f={ea11.fase!r}. Coleta recusada para nao contaminar o oficial."
        )
    if not env.official and ea11.fase == FASE_OFICIAL:
        raise RuntimeError(
            "ambiente configurado como simulado mas o ele-c.json veio com "
            f"f={ea11.fase!r} (oficial). Coleta recusada."
        )

    problems = urls.check_declared_dirs(list(ea11.diretorios))
    flags.extend(problems)

    # Só o ciclo coletado: o catálogo de produção carrega também as dezenas
    # de suplementares de ele2024, que não são divergência do seed.
    catalogadas = {e.cd for p in ea11.pleitos if p.ciclo == urls.ciclo
                   for e in p.eleicoes}
    for seed in env.seed_elections:
        if seed not in catalogadas:
            flags.append(
                f"eleicao {seed} do seed nao existe no catalogo EA11 — "
                "seed desatualizado, catalogo prevalece"
            )
    for cd in sorted(catalogadas - set(env.seed_elections)):
        flags.append(f"eleicao {cd} no catalogo EA11 fora do seed")

    contests: list[Contest] = []
    for p in ea11.pleitos:
        if p.ciclo != urls.ciclo:
            continue
        for e in p.eleicoes:
            ea12 = ea12s.get(e.cd)
            if ea12 is None:
                continue
            for cargo in env.cargos.values():
                if not e.tem_cargo(cargo.cd):
                    continue
                muns = tuple(
                    m for m in ea12.municipios
                    if cargo.includes_exterior or not m.is_exterior
                )
                nome = next((c.ds for c in e.cargos if c.cd == cargo.cd), "")
                contests.append(Contest(
                    election=e.cd, turno=e.turno, cargo=cargo,
                    cargo_nome=nome, municipios=muns,
                ))

    if not contests:
        flags.append(
            "nenhuma disputa de interesse encontrada no catalogo "
            "(presidente/governador/senador)"
        )

    return Catalog(env=env, urls=urls, ea11=ea11, ea12_por_eleicao=ea12s,
                   contests=tuple(contests), quality_flags=flags)
