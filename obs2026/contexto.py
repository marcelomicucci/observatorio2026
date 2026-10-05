"""O ano da eleição que o pedido está lendo.

Com o seletor de anos (24/09/2026), a mesma rota pode servir 2026 ao vivo ou
uma eleição passada (`?eleicao=tse2018t1`, ver `api/app.py`). Algumas regras
dependem do ano, não do banco:

- **partido**: o 17 de 2018 é o PSL, que não existe no cadastro de 2026 —
  `politics/partidos.py` resolve pela linhagem assinada;
- **ideologia**: vale a classificação daquele ano
  (`classificacao_AAAA.yaml`), nunca a de 2026.

`None` é o ano ao vivo.
"""

from __future__ import annotations

from contextvars import ContextVar

ANO_ELEICAO: ContextVar[int | None] = ContextVar("obs2026_ano_eleicao",
                                                 default=None)


def ano_passado() -> int | None:
    """O ano do pedido, se for uma eleição passada; senão None."""
    return ANO_ELEICAO.get()
