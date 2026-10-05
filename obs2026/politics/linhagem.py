"""Linhagem partidária: ligar a mesma legenda através das eleições.

Comparar "o PL de 2018 com o PL de 2022" exige uma regra explícita, porque
sigla e número mudam: o PR virou PL, o PSL e o DEM viraram União Brasil, o
PMDB virou MDB. A decisão de 23/09/2026 foi **seguir a linhagem, com nota**
— e, como toda afirmação editorial do produto, ela é assinada (§6).

Sem assinatura, `serie()` devolve cada partido nele mesmo: a tela mostra o
nome da época e as séries não se ligam. É a escolha conservadora, e a
interface diz que a linhagem está pendente em vez de fingir continuidade.

O que este módulo NÃO faz:

- **Federação não é sucessão.** PT/PCdoB/PV disputaram 2022 juntos e
  continuam três partidos, com bancadas próprias. Somar por federação é
  outra operação, feita na tela quando o usuário pedir.
- **Não herda classificação ideológica.** A ideologia de cada ano vem do
  arquivo daquele ano (`classificacao_AAAA.yaml`). Um partido pode mudar de
  lado sem mudar de nome, e a linhagem não tem opinião sobre isso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from datetime import date
from pathlib import Path

import yaml

from ..config import CONFIG_DIR
from ..tse.parsers.common import fold

ARQUIVO = "linhagem_partidos.yaml"


class LinhagemError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Sucessao:
    sigla: str
    numero: str
    ano: int
    sucessor_sigla: str | None
    sucessor_numero: str | None
    tipo: str
    desde: int
    nota: str
    fonte: str


@dataclass(frozen=True, slots=True)
class Registro:
    versao: str | None
    curador: str | None
    publicada_em: date | None
    sucessoes: tuple[Sucessao, ...]

    @property
    def pendente(self) -> bool:
        return not self.curador


def caminho_padrao() -> Path:
    env = os.environ.get("OBS2026_LINHAGEM")
    return Path(env) if env else CONFIG_DIR / ARQUIVO


def load(path: Path | None = None) -> Registro:
    p = path or caminho_padrao()
    if not p.exists():
        return Registro(None, None, None, ())
    return _load_cache(str(p), p.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_cache(caminho: str, _mtime: int) -> Registro:
    """Uma leitura por versão do arquivo (mesma razão da classificação:
    `serie()` é chamada por cadeira e por partido). Registro imutável."""
    d = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    itens = []
    for e in d.get("sucessoes") or []:
        suc = e.get("sucessor") or {}
        itens.append(Sucessao(
            sigla=str(e.get("sigla", "")).strip(),
            numero=str(e.get("numero", "")).strip(),
            ano=int(e.get("ano") or 0),
            sucessor_sigla=(str(suc.get("sigla")).strip()
                            if suc.get("sigla") else None),
            sucessor_numero=(str(suc.get("numero")).strip()
                             if suc.get("numero") else None),
            tipo=str(e.get("tipo", "")).strip(),
            desde=int(e.get("desde") or 0),
            nota=str(e.get("nota", "")).strip(),
            fonte=str(e.get("fonte", "")).strip()))
    pub = d.get("publicada_em")
    return Registro(versao=d.get("versao"), curador=d.get("curador"),
                    publicada_em=pub if isinstance(pub, date) else None,
                    sucessoes=tuple(itens))


def _sig(sigla: str | None) -> str:
    """Sigla comparável: o TSE grava "PC do B" num arquivo e o cadastro diz
    "PCdoB"; sem tirar os espaços, o PCdoB viraria duas séries."""
    return fold(sigla or "").replace(" ", "")


def _casa(x: Sucessao, sigla: str, numero: str, ano: int) -> bool:
    """A entrada `x` descreve este partido nesta eleição?

    Pela sigla, como sempre. E também pelo NÚMERO dentro da janela em que a
    legenda antiga existia (de `x.ano` até antes de `x.desde`): o TSE abrevia
    a mesma legenda de jeitos diferentes em anos diferentes — o 51 é "PATRI"
    em 2018 e "PATRIOTA" em 2022 —, e sem isso o Patriota de 2022 ficava
    fora da série que a linhagem assinada liga ao PRD. Fora da janela o
    número não vale: ele é reusado (o 25 era o DEM e é o PRD).
    """
    if x.numero and numero and x.numero != numero:
        return False
    if _sig(x.sigla) == _sig(sigla):
        return True
    return bool(x.numero and numero and x.ano <= ano < x.desde)


def serie(sigla: str | None, numero: str | None, ano: int,
          reg: Registro | None = None, *,
          ate: int | None = None) -> tuple[str, str | None]:
    """Chave da série a que este partido pertence, e a nota da troca.

    A chave é a do partido MAIS RECENTE da cadeia — é ele que aparece no
    rótulo, com a nota ("União Brasil (ex-PSL)"). Sem assinatura, cada
    partido é sua própria série.

    `ate` para a cadeia num ano: "qual era esta legenda em 2018?" — o PMDB
    de 2014 é o MDB de 2018, mas o PR de 2014 ainda era PR em 2018 (virou
    PL em 2019). Serve para classificar quem se elegeu num ano sem
    classificação própria pela régua de outro ano.
    """
    reg = reg if reg is not None else load()
    atual_sigla = (sigla or "").strip()
    atual_numero = (numero or "").strip()
    chave = f"{atual_numero}|{_sig(atual_sigla)}"
    if reg.pendente:
        return chave, None
    nota = None
    vistos = set()
    # Depois de cada troca, a cadeia segue no ano em que o sucessor passou a
    # valer: o PRD que sucede o Patriota é o 25 de 2026, e procurá-lo como
    # "25 em 2018" achava o DEM.
    era = ano
    while True:
        alvo = None
        for x in reg.sucessoes:
            if not _casa(x, atual_sigla, atual_numero, era):
                continue
            if era >= x.desde:
                continue          # a troca ainda não tinha acontecido
            if ate is not None and x.desde > ate:
                continue          # a troca ainda não tinha acontecido ATÉ lá
            alvo = x
            break
        if alvo is None or alvo.sucessor_sigla is None:
            break
        if (alvo.sucessor_sigla, alvo.sucessor_numero) in vistos:
            raise LinhagemError(
                f"ciclo na linhagem em {alvo.sucessor_sigla}")
        vistos.add((alvo.sucessor_sigla, alvo.sucessor_numero))
        atual_sigla = alvo.sucessor_sigla
        atual_numero = alvo.sucessor_numero or atual_numero
        era = max(era, alvo.desde)
        nota = alvo.nota or nota
    return f"{atual_numero}|{_sig(atual_sigla)}", nota


def rotulo(sigla: str | None, numero: str | None, ano: int,
           reg: Registro | None = None) -> dict:
    """Como a tela deve escrever este partido numa série de vários anos."""
    reg = reg if reg is not None else load()
    chave, nota = serie(sigla, numero, ano, reg)
    numero_atual, _, sigla_atual = chave.partition("|")
    return {"serie": chave, "sigla_hoje": sigla_atual.upper(),
            "numero_hoje": numero_atual, "sigla_na_epoca": sigla,
            "nota": nota, "linhagem_ativa": not reg.pendente}


def validar(reg: Registro) -> list[str]:
    problemas = []
    for x in reg.sucessoes:
        if not x.sigla or not x.ano:
            problemas.append(f"entrada sem sigla ou ano: {x}")
        if x.sucessor_sigla and not x.desde:
            problemas.append(f"{x.sigla}: sucessor sem ano `desde`")
        if not x.fonte.strip():
            problemas.append(f"{x.sigla}: fonte vazia")
        if x.tipo not in ("fusao", "incorporacao", "renomeacao", "fim",
                          "federacao"):
            problemas.append(f"{x.sigla}: tipo {x.tipo!r} desconhecido")
    # Ciclo: seguir cada cadeia até o fim detecta e levanta.
    for x in reg.sucessoes:
        try:
            serie(x.sigla, x.numero, x.ano, reg)
        except LinhagemError as e:
            problemas.append(str(e))
    return problemas


def caminho_proposta(path: Path | None = None) -> Path:
    """Onde mora uma versão nova ainda não assinada.

    Fica ao lado da oficial, e não no lugar dela: enquanto a proposta espera
    a assinatura, a versão anterior continua valendo e as séries continuam
    ligadas. Escrever a proposta por cima da oficial desligaria a
    comparação inteira até alguém assinar."""
    p = path or caminho_padrao()
    return p.with_name(f"{p.stem}.proposta{p.suffix}")


def _proxima_versao(anterior: str | None, ano: int) -> str:
    """2026.1 → 2026.2; primeira do ano → AAAA.1."""
    if anterior and str(anterior).startswith(f"{ano}."):
        try:
            return f"{ano}.{int(str(anterior).split('.', 1)[1]) + 1}"
        except ValueError:
            pass
    return f"{ano}.1"


def assinar(curador: str, path: Path | None = None,
            quando: date | None = None) -> Registro:
    """Assina a linhagem. Sem isso, as séries não se ligam.

    Havendo proposta (`linhagem_partidos.proposta.yaml`), é ela que se
    assina: vira a oficial com a versão seguinte, e a oficial anterior fica
    guardada ao lado com o número dela (`linhagem_partidos.2026.1.yaml`),
    para se saber o que valia em cada data."""
    import re

    p = path or caminho_padrao()
    curador = " ".join((curador or "").split())
    if len(curador) < 3:
        raise LinhagemError("curador precisa de nome identificável")
    proposta = caminho_proposta(p)
    alvo = proposta if proposta.exists() else p
    reg = load(alvo)
    if not reg.pendente:
        raise LinhagemError(f"{alvo.name} já está assinada; para mudar, "
                            f"escreva {proposta.name}")
    problemas = validar(reg)
    if problemas:
        raise LinhagemError("linhagem com problemas: "
                            + "; ".join(problemas[:5]))
    anterior = load(p).versao if alvo is proposta and p.exists() else None
    texto = alvo.read_text(encoding="utf-8")
    hoje = quando or date.today()
    versao = _proxima_versao(anterior, hoje.year)
    texto = texto.replace("versao: null", f'versao: "{versao}"', 1)
    texto = texto.replace("curador: null", f'curador: "{curador}"', 1)
    texto = texto.replace("publicada_em: null",
                          f"publicada_em: {hoje.isoformat()}", 1)
    texto = re.sub(
        r"# PROPOSTA, redigida em \d{2}/\d{2}/\d{4}\.",
        f"# ASSINADA por {curador} em {hoje.strftime('%d/%m/%Y')} "
        f"(versão {versao}).", texto, count=1)
    if alvo is proposta:
        if p.exists() and anterior:
            arquivo = p.with_name(f"{p.stem}.{anterior}{p.suffix}")
            arquivo.write_bytes(p.read_bytes())
        p.write_text(texto, encoding="utf-8")
        proposta.unlink()
    else:
        p.write_text(texto, encoding="utf-8")
    return load(p)
