"""Primitivas compartilhadas pelos parsers do TSE.

Três armadilhas reais observadas nos payloads do simulado de 2026:

1. Todo número vem como *string*, e os percentuais vêm em formato brasileiro
   (`"85,15"`, `"99,999810947"`). Converter percentual com `float()` direto
   levanta `ValueError`; converter trocando vírgula por ponto sem pensar é pior,
   porque passa e produz número errado se algum campo trouxer separador de
   milhar. Aqui a conversão é explícita e recusa o que não reconhece.

2. Nomes de município no EA12 vêm com entidade HTML: `OLHO D&APOS;AGUA` em vez
   de `OLHO D'AGUA`. Sem unescape, 45 municípios brasileiros deixam de parear
   com qualquer catálogo externo.

3. Códigos têm zeros à esquerda significativos. Nada neste módulo converte
   código para int.

Percentuais NÃO são usados para cálculo. O motor recalcula tudo a partir dos
inteiros e usa os percentuais do TSE apenas para conferência (§11.1: "nenhum
percentual agregado por média simples").
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, time, timezone, timedelta

#: America/Sao_Paulo. O Brasil não observa horário de verão desde 2019; se isso
#: mudar, esta constante vira lookup por data e o teste correspondente quebra.
BRT = timezone(timedelta(hours=-3), name="America/Sao_Paulo")

_RE_INT = re.compile(r"^-?\d+$")
_RE_DEC_BR = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")


class ParseError(ValueError):
    """Payload fora do contrato. Nunca é silenciado."""


def as_int(value, field: str = "?") -> int:
    """Inteiro de 64 bits a partir de string do TSE.

    Recusa vazio, `None` e float. Voto é contagem: se veio com decimal, o
    contrato mudou e isso precisa aparecer, não ser arredondado.
    """
    if value is None:
        raise ParseError(f"{field}: inteiro ausente")
    s = str(value).strip()
    if not s:
        raise ParseError(f"{field}: inteiro vazio")
    if not _RE_INT.match(s):
        raise ParseError(f"{field}: esperado inteiro, veio {value!r}")
    return int(s)


def as_int_opt(value, field: str = "?") -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return as_int(value, field)


def as_pct(value, field: str = "?") -> float | None:
    """Percentual em formato brasileiro -> float. Só para conferência."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if not _RE_DEC_BR.match(s):
        raise ParseError(f"{field}: percentual fora do formato BR: {value!r}")
    return float(s.replace(".", "").replace(",", "."))


def as_code(value, field: str = "?") -> str:
    """Código do TSE: string, sem strip de zeros, sem conversão numérica."""
    if value is None:
        raise ParseError(f"{field}: codigo ausente")
    s = str(value).strip()
    if not s:
        raise ParseError(f"{field}: codigo vazio")
    return s


def as_text(value) -> str:
    """Texto externo: unescape de entidade HTML + normalização de espaços.

    O unescape resolve `&APOS;`; o `html.unescape` é case-insensitive para
    entidades nomeadas em maiúsculas? Não é — por isso a normalização prévia.
    Nenhuma remoção de acento acontece aqui: o nome de exibição preserva o
    original. A forma sem acento é responsabilidade de `fold()`.
    """
    if value is None:
        return ""
    s = str(value)
    # html.unescape não reconhece &APOS; (maiúsculo); &apos; sim.
    s = re.sub(r"&APOS;", "&apos;", s)
    s = html.unescape(s)
    return " ".join(s.split())


def fold(value: str) -> str:
    """Chave de comparação: sem acento, maiúscula, espaços colapsados.

    Usada SOMENTE para procurar correspondência candidata. A associação final
    é decidida por código oficial (§2.1: proibida associação aproximada
    silenciosa).
    """
    s = as_text(value)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.upper().split())


def parse_dt(dg: str | None, hg: str | None, field: str = "?") -> datetime | None:
    """`dg`='18/09/2026' + `hg`='16:59:57' -> datetime aware em BRT.

    O horário do TSE não traz fuso. Tratá-lo como UTC atrasaria tudo em 3h e
    faria a série temporal mentir. Guardamos aware em BRT; a persistência
    converte para UTC e o horário original fica preservado no bruto.
    """
    if not dg or not hg:
        return None
    dg, hg = str(dg).strip(), str(hg).strip()
    if not dg or not hg:
        return None
    try:
        d = datetime.strptime(dg, "%d/%m/%Y").date()
    except ValueError as exc:
        raise ParseError(f"{field}: data invalida {dg!r}") from exc
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(hg, fmt).time()
            break
        except ValueError:
            continue
    else:
        raise ParseError(f"{field}: hora invalida {hg!r}")
    return datetime.combine(d, t, tzinfo=BRT)


def to_utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.astimezone(timezone.utc)


def require(payload: dict, *keys: str, ctx: str = "") -> None:
    """Falha cedo se o contrato mudou. Melhor recusar do que ingerir torto."""
    missing = [k for k in keys if k not in payload]
    if missing:
        raise ParseError(f"{ctx}: campos ausentes no payload: {missing}")
