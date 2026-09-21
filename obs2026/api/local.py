"""Trava das rotas que mexem na máquina: só da própria máquina, só do painel.

Duas rotas fazem mais do que ler: gravar o roteiro do telão (escreve um
arquivo de configuração) e preparar as imagens para o WhatsApp (roda um
navegador sem cabeça). As duas ficam atrás da mesma trava:

- **loopback.** O pedido tem de vir da própria máquina. Se um dia o servidor
  for publicado, estas rotas continuam recusando quem vem de fora.
- **JSON, e da mesma origem.** Loopback não basta: o navegador de quem opera
  também está em loopback, e qualquer página aberta nele pode disparar um POST
  para `localhost`. Um formulário de outro site chega como `text/plain`, sem
  a pré-verificação do CORS; exigir `application/json` e conferir o `Origin`
  fecha essa porta.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

#: `testclient` é o endereço que o cliente de teste do Starlette informa; ele
#: só existe dentro do processo, nunca na rede.
LOCAIS = {"127.0.0.1", "::1", "localhost", "testclient"}


def somente_local(req: Request, acao: str) -> None:
    """Levanta 403/415 se o pedido não vier do painel, nesta máquina."""
    host = req.client.host if req.client else ""
    if host not in LOCAIS:
        raise HTTPException(403, f"{acao}: só da própria máquina")
    tipo = (req.headers.get("content-type") or "").split(";")[0].strip()
    if tipo != "application/json":
        raise HTTPException(415, f"{acao}: envie como application/json")
    origem = req.headers.get("origin")
    if origem:
        o = urlsplit(origem)
        if (o.hostname not in LOCAIS
                or (o.port or 80) != (req.url.port or 80)):
            raise HTTPException(403, f"{acao}: origem {origem!r} recusada")
