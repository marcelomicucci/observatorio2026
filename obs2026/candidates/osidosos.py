"""Enriquecimento de candidatos a partir da plataforma Eleições 2026.

Fonte: https://eleicoes.osidosos.com.br — plataforma cívica de dados abertos,
licença MIT, alimentada pelo DivulgaCandContas do TSE.
Código do projeto: https://github.com/JorgeMello/eleicoes-2026

## Por que isto existe

O EA20 traz do candidato apenas o essencial para a apuração: `sqcand`,
número, nome de urna, partido, destinação do voto e situação. Não traz foto,
vice, patrimônio, plano de governo nem situação do registro. Sem isso, a tela
mostra `CANDIDATO 9987` — um código, não uma pessoa.

## Regras que este módulo respeita

**A junção é por código oficial.** O bloco `tse` da plataforma expõe
`sq_candidato`, que é a mesma chave `sqcand` do EA20. Nada é pareado por nome.
Quando o `sq_candidato` não vem, número + partido + UF + cargo entram como
*candidato a correspondência*, marcado como tal — nunca como correspondência
confirmada (§2.1 proíbe associação aproximada silenciosa).

**Fonte de terceiro nunca se mistura com resultado oficial.** Cada campo
enriquecido carrega `fonte` e `coletado_em` próprios. Voto vem do TSE; foto e
patrimônio vêm daqui; e a tela diz qual é qual.

**O navegador não fala com esta API.** Ele fala só com a nossa (§8). Este
módulo busca no servidor, guarda em cache e serve a partir do disco — assim
mil leitores não viram mil requisições ao serviço de outra pessoa.

**Indisponibilidade não derruba a apuração.** Se a plataforma não responder,
o enriquecimento fica vazio e o produto continua funcionando com os dados
oficiais. Enriquecimento é acessório; resultado é essencial.

## Cobertura, medida em 20/09/2026

    presidente    13 candidaturas
    governador   192 candidaturas, 27 UFs
    senador        0 candidaturas  <- ainda não carregado na origem

Senador vir vazio não é erro nosso nem deles: é o estado da fonte. O produto
mostra "sem enriquecimento disponível" em vez de inventar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://eleicoes.osidosos.com.br/backend/public/index.php/api"
SITE = "https://eleicoes.osidosos.com.br"
#: Os arquivos enviados ficam sob o mesmo prefixo da API, nao na raiz do
#: site. Montar a URL a partir da raiz devolve 422 "Invalid source image" —
#: verificado, nao suposto.
UPLOADS = f"{SITE}/backend/public"
FONTE = "eleicoes.osidosos.com.br"
LICENCA = "MIT — dados do TSE/DivulgaCandContas"

#: Cargos da plataforma, mapeados para os códigos do EA11/EA20.
CARGO_POR_CD = {"1": "presidente", "3": "governador", "5": "senador"}

#: Validade do cache. Dado cadastral muda em dias, não em segundos; e na noite
#: da apuração ninguém quer o coletor disputando banda com uma API de perfil.
TTL_SEGUNDOS = 6 * 3600

TIMEOUT = httpx.Timeout(20.0, connect=6.0)

#: Identificacao explicita. Consumir a API de outra pessoa sem dizer quem se
#: e nao e so descortes: e o que faz o servidor responder 403 a um cliente
#: anonimo, como aconteceu na primeira tentativa.
CABECALHOS = {
    "User-Agent": ("Observatorio-Eleitoral-2026/0.1 "
                   "(+https://github.com/marcelomicucci/observatorio2026)"),
    "Accept": "application/json",
}


class EnriquecimentoIndisponivel(RuntimeError):
    """A fonte não respondeu. Não é motivo para derrubar nada."""


@dataclass(frozen=True, slots=True)
class Perfil:
    """Dados cadastrais de um candidato, de fonte de terceiro."""

    sqcand: str | None
    numero: str
    partido: str
    cargo: str
    uf: str
    nome: str
    slug: str
    foto_url: str | None
    vice_nome: str | None
    vice_partido: str | None
    profissao: str | None
    grau_instrucao: str | None
    patrimonio_total: float | None
    situacao_registro: str | None
    status_geral: str | None
    plano_governo_url: str | None
    perfil_url: str
    fonte: str = FONTE
    coletado_em: str = ""
    #: Como a correspondência com o dado oficial foi feita.
    #: 'sqcand' = código oficial; 'numero+partido' = candidata, a confirmar.
    chave_juncao: str = "sqcand"

    @property
    def confirmado(self) -> bool:
        return self.chave_juncao == "sqcand" and bool(self.sqcand)


@dataclass
class Cache:
    """Cache em disco. O bruto fica como veio, para auditoria."""

    raiz: Path
    ttl: int = TTL_SEGUNDOS

    def caminho(self, chave: str) -> Path:
        return self.raiz / f"{chave}.json"

    def valido(self, chave: str) -> bool:
        p = self.caminho(chave)
        return p.exists() and (time.time() - p.stat().st_mtime) < self.ttl

    def ler(self, chave: str):
        return json.loads(self.caminho(chave).read_text(encoding="utf-8"))

    def gravar(self, chave: str, dados) -> None:
        self.raiz.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho(chave).with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.caminho(chave))

    def idade_s(self, chave: str) -> float | None:
        p = self.caminho(chave)
        return (time.time() - p.stat().st_mtime) if p.exists() else None


def _num(v) -> float | None:
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _txt(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _perfil(item: dict, cargo: str, agora: str) -> Perfil:
    sq = _txt((item.get("tse") or {}).get("sq_candidato")) \
        if isinstance(item.get("tse"), dict) else None
    sq = sq or _txt(item.get("sq_candidato"))
    foto = _txt(item.get("foto_local"))
    if foto and foto.startswith("/"):
        foto = UPLOADS + foto
    return Perfil(
        sqcand=sq,
        numero=str(item.get("numero") or "").strip(),
        partido=(_txt(item.get("partido")) or "").upper(),
        cargo=cargo,
        uf=(_txt(item.get("uf")) or "").lower(),
        nome=_txt(item.get("nome")) or "",
        slug=_txt(item.get("slug")) or "",
        foto_url=foto or _txt(item.get("foto_url_original")),
        vice_nome=_txt(item.get("vice_nome")),
        vice_partido=_txt(item.get("vice_partido")),
        profissao=_txt(item.get("profissao")),
        grau_instrucao=_txt(item.get("grau_instrucao")),
        patrimonio_total=_num(item.get("patrimonio_total")),
        situacao_registro=_txt(item.get("tse_situacao")),
        status_geral=_txt(item.get("tse_status")),
        plano_governo_url=_txt(item.get("plano_governo_url")),
        perfil_url=f"{SITE}/candidato/{item.get('slug')}",
        coletado_em=agora,
        chave_juncao="sqcand" if sq else "numero+partido",
    )


#: Ate quantos perfis vale buscar o detalhe individual para obter o
#: `sq_candidato`. A listagem nao traz esse campo; so o detalhe traz. Com 13
#: candidatos a presidente o custo e trivial e a juncao passa a ser por
#: codigo oficial. Com 192 a governador, nao compensa numa requisicao de
#: tela — ali a juncao fica por numero+partido, marcada como candidata.
TETO_APROFUNDAR = 30


def aprofundar(perfis: list[Perfil], *, cache: Cache) -> list[Perfil]:
    """Busca o detalhe de cada perfil para obter o `sq_candidato`.

    O `sq_candidato` e a mesma chave do EA20, e e o que transforma uma
    correspondencia candidata em correspondencia oficial. So vale a pena
    quando a lista e curta.
    """
    from dataclasses import replace
    saida = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                      headers=CABECALHOS) as c:
        for p in perfis:
            if p.sqcand or not p.slug:
                saida.append(p)
                continue
            chave = f"detalhe-{p.slug}"
            try:
                if cache.valido(chave):
                    d = cache.ler(chave)
                else:
                    r = c.get(f"{BASE}/candidatos/{p.slug}")
                    r.raise_for_status()
                    d = r.json()
                    cache.gravar(chave, d)
                sq = _txt((d.get("tse") or {}).get("sq_candidato"))
            except (httpx.HTTPError, ValueError, OSError):
                sq = None          # detalhe indisponivel nao quebra nada
            saida.append(replace(p, sqcand=sq,
                                 chave_juncao="sqcand" if sq
                                 else "numero+partido")
                         if sq else p)
    return saida


def buscar(cargo_cd: str, *, cache: Cache, forcar: bool = False,
           detalhar: bool | None = None) -> tuple[list[Perfil], dict]:
    """Busca perfis de um cargo. Devolve `(perfis, meta)`.

    Nunca levanta por indisponibilidade da fonte: devolve lista vazia e um
    `meta` que explica o porquê. O produto tem de continuar de pé.
    """
    cargo = CARGO_POR_CD.get(str(cargo_cd))
    if cargo is None:
        return [], {"erro": f"cargo {cargo_cd} sem equivalente na fonte"}

    chave = f"candidatos-{cargo}"
    meta = {"fonte": FONTE, "licenca": LICENCA, "cargo": cargo,
            "do_cache": False, "idade_s": cache.idade_s(chave)}

    if not forcar and cache.valido(chave):
        bruto = cache.ler(chave)
        meta["do_cache"] = True
    else:
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers=CABECALHOS) as c:
                r = c.get(f"{BASE}/candidatos", params={"cargo": cargo})
                r.raise_for_status()
                bruto = r.json()
            cache.gravar(chave, bruto)
            meta["idade_s"] = 0.0
        except (httpx.HTTPError, ValueError) as exc:
            # Cai para o cache vencido: dado velho identificado é melhor que
            # tela sem identidade nenhuma.
            if cache.caminho(chave).exists():
                bruto = cache.ler(chave)
                meta["do_cache"] = True
                meta["aviso"] = (
                    f"fonte indisponivel ({exc.__class__.__name__}); "
                    "usando cache vencido")
            else:
                return [], {**meta, "erro": f"fonte indisponivel: {exc!r}"}

    agora = datetime.now(timezone.utc).isoformat()
    if not isinstance(bruto, list):
        return [], {**meta, "erro": "formato inesperado na resposta"}

    perfis = [_perfil(x, cargo, agora) for x in bruto if isinstance(x, dict)]
    quer = (len(perfis) <= TETO_APROFUNDAR) if detalhar is None else detalhar
    if quer and perfis and not any(p.sqcand for p in perfis):
        perfis = aprofundar(perfis, cache=cache)
        meta["aprofundado"] = True
    meta["total"] = len(perfis)
    meta["com_sqcand"] = sum(1 for p in perfis if p.sqcand)
    if not perfis:
        meta["aviso"] = (
            f"a fonte ainda nao publicou candidaturas de {cargo}; "
            "o produto segue com os dados oficiais do TSE")
    return perfis, meta


@dataclass
class Indice:
    """Índice de junção, com a chave oficial em primeiro lugar."""

    por_sqcand: dict[str, Perfil] = field(default_factory=dict)
    por_numero: dict[tuple[str, str, str], Perfil] = field(
        default_factory=dict)

    @classmethod
    def de(cls, perfis: list[Perfil]) -> "Indice":
        ix = cls()
        for p in perfis:
            if p.sqcand:
                ix.por_sqcand[p.sqcand] = p
            ix.por_numero.setdefault((p.uf, p.numero, p.partido), p)
        return ix

    def casar(self, sqcand: str, numero: str, partido: str,
              uf: str) -> tuple[Perfil | None, str]:
        """Encontra o perfil. Devolve `(perfil, como)`.

        `como` é 'sqcand' (oficial, confiável), 'numero+partido' (candidata,
        a confirmar) ou 'nenhuma'. O chamador é obrigado a lidar com a
        diferença: uma correspondência por número não pode ser exibida como
        se fosse oficial.
        """
        p = self.por_sqcand.get(str(sqcand))
        if p is not None:
            return p, "sqcand"
        p = self.por_numero.get((uf.lower(), str(numero),
                                 (partido or "").upper()))
        if p is not None:
            return p, "numero+partido"
        return None, "nenhuma"
