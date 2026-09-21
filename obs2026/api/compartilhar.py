"""Compartilhar no WhatsApp, a partir do painel.

Até aqui isto só existia como comando de terminal (`obs2026 compartilhar`),
e na noite ninguém vai lembrar do comando com o grupo pedindo. Agora é um
botão: o painel pede, o servidor fotografa as telas com o mesmo código do
comando, e o navegador recebe imagens e legenda prontas.

O caminho principal é curto: as imagens vão **todas juntas** para a área de
transferência do Windows (o servidor roda na máquina de quem opera), e no
WhatsApp um Ctrl+V anexa todas; depois, "copiar legenda" e outro Ctrl+V no
campo de legenda. Onde o navegador oferece a janela de compartilhar do
sistema, ela também aparece.

O envio continua sendo de quem opera, pelas mesmas razões de sempre (ver
`obs2026/captura.py`): a API oficial não entrega para grupos, e projeção
eleitoral não sai de um laço automático sem alguém ter olhado.

Duas rotas:

    POST /api/v1/compartilhar              fotografa e devolve o pacote
    GET  /api/v1/compartilhar/imagem/...   serve uma imagem já fotografada

A primeira roda um navegador sem cabeça nesta máquina, então fica atrás da
mesma trava da gravação do roteiro (`local.py`): só da própria máquina, só do
próprio painel.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

#: Uma captura por vez. Cada uma abre um Chrome sem cabeça por tela; dois
#: cliques seguidos no botão não podem virar dois navegadores disputando o
#: processador com a coleta, no pico da noite.
_UMA_POR_VEZ = asyncio.Lock()

_NOME = re.compile(r"^[0-9]{6}-[0-9]{1,3}-[a-z0-9-]{1,60}\.png$")
_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def register(app) -> None:
    from .. import captura as C
    from ..config import DATA_DIR
    from .local import somente_local

    raiz = DATA_DIR / "telao"

    @app.post("/api/v1/compartilhar")
    async def compartilhar(req: Request):
        """Fotografa as telas pedidas e devolve imagens e legenda.

        Corpo: `{"telas": [0, 2]}` para telas do roteiro, `{"telas": null}`
        para o roteiro inteiro, ou `{"recorte": {"cargo": "3", "uf": "pa"}}`
        para um recorte que não está no roteiro.
        """
        somente_local(req, "preparar imagens")
        try:
            corpo = await req.json()
        except ValueError as exc:
            raise HTTPException(400, "corpo não é JSON válido") from exc
        if not isinstance(corpo, dict):
            raise HTTPException(400, "esperado um objeto JSON")

        recorte = corpo.get("recorte")
        telas = corpo.get("telas")
        if recorte is not None:
            if not isinstance(recorte, dict) or not recorte.get("cargo"):
                raise HTTPException(400, "recorte precisa de `cargo`")
            recorte = {"cargo": str(recorte["cargo"]),
                       "uf": (str(recorte["uf"]).lower()[:2]
                              if recorte.get("uf") else None),
                       "municipio": (str(recorte["municipio"])[:7]
                                     if recorte.get("municipio") else None),
                       "camada": str(recorte.get("camada") or "lider")}
            telas = None
        elif telas is not None:
            if (not isinstance(telas, list) or len(telas) > 60
                    or not all(isinstance(i, int) for i in telas)):
                raise HTTPException(400, "`telas` é uma lista de índices")

        # O navegador sem cabeça vai abrir o telão neste mesmo servidor.
        api = str(req.base_url).rstrip("/")

        if _UMA_POR_VEZ.locked():
            raise HTTPException(
                409, "já há uma captura em andamento; aguarde ela terminar")
        async with _UMA_POR_VEZ:
            try:
                # Em thread: o navegador sem cabeça bloqueia, e o servidor
                # precisa continuar atendendo — inclusive a própria página
                # do telão que ele está fotografando.
                pacote = await asyncio.to_thread(
                    C.capturar, api=api, telas=telas, recorte=recorte,
                    destino=raiz)
            except C.CapturaError as exc:
                raise HTTPException(422, str(exc)) from exc

        # Todas as imagens de uma vez na área de transferência do Windows —
        # o servidor roda na máquina de quem opera, então pode. O navegador
        # só consegue pôr UMA imagem por vez; com cinco telas eram cinco
        # idas e voltas ao WhatsApp. Assim é um Ctrl+V só, e o WhatsApp
        # anexa todas.
        copiadas = False
        if corpo.get("copiar", True) and pacote.fotos:
            copiadas = await asyncio.to_thread(
                C.para_area_de_transferencia,
                [f.imagem for f in pacote.fotos])

        dia = pacote.pasta.name
        return {
            "na_area_de_transferencia": copiadas,
            "fotos": [{
                "indice": f.indice,
                "titulo": f.titulo,
                "url": f"/api/v1/compartilhar/imagem/{dia}/{f.imagem.name}",
                "arquivo": f.imagem.name,
                "legenda": f.legenda,
            } for f in pacote.fotos],
            "puladas": [{"indice": i, "titulo": t, "motivo": m}
                        for i, t, m in pacote.puladas],
            "legenda": pacote.texto,
            "pasta": str(pacote.pasta),
        }

    @app.get("/api/v1/compartilhar/imagem/{dia}/{nome}",
             include_in_schema=False)
    def imagem(dia: str, nome: str):
        """Uma imagem já fotografada. Nome e dia validados por padrão fixo:
        nada fora de `data/telao/` pode ser servido por aqui."""
        if not _DIA.match(dia) or not _NOME.match(nome):
            raise HTTPException(400, "nome de imagem inválido")
        p = raiz / dia / nome
        if not p.is_file():
            raise HTTPException(404, "imagem não encontrada")
        return FileResponse(p, media_type="image/png",
                            headers={"Cache-Control": "no-store"})
