"""Rota dos alertas de resultado (`engine/alertas.py`).

Lê o diário de projeções de forma incremental — o mesmo cuidado de
`Diario.ultimos`: o arquivo ganha dezenas de linhas por minuto a noite
toda, e relê-lo inteiro a cada pedido do telão trocaria um gargalo por
outro. O estado fica no módulo, por ambiente.
"""

from __future__ import annotations

import threading

from fastapi import Query
from fastapi.responses import FileResponse

from ..engine import alertas as AL
from ..engine import audit as A

#: Quantos alertas ficam na memória. A noite do 1º turno de 2026 gerou
#: algumas centenas; o teto existe para o processo não crescer sem limite.
MAX_ALERTAS = 2000

_ESTADO: dict[str, dict] = {}
_TRAVA = threading.Lock()


def _ler(caminho) -> list[AL.Alerta]:
    chave = str(caminho)
    with _TRAVA:
        est = _ESTADO.setdefault(chave, {"pos": 0, "ac": AL.Acompanhador(),
                                         "lista": []})
        if not caminho.exists():
            return []
        if caminho.stat().st_size < est["pos"]:      # arquivo trocado
            est.update(pos=0, ac=AL.Acompanhador(), lista=[])
        with open(caminho, "rb") as fh:
            fh.seek(est["pos"])
            while True:
                linha = fh.readline()
                if not linha or not linha.endswith(b"\n"):
                    break
                est["pos"] += len(linha)
                texto = linha.strip()
                if not texto:
                    continue
                try:
                    r = A.Diario._decodificar(texto.decode("utf-8"))
                except (ValueError, TypeError):
                    continue
                est["lista"].extend(est["ac"].alimentar(r))
        del est["lista"][:-MAX_ALERTAS]
        return list(est["lista"])


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR, load_environment
    from ..operacao import modelo_ativo

    @app.get("/telas.js", include_in_schema=False)
    def telas_js():
        return FileResponse(M._WEB / "telas.js",
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/alertas")
    def alertas(limite: int = Query(30, ge=1, le=500),
                peso_min: int = Query(1, ge=1, le=3),
                cargo: str | None = Query(None, pattern="^(1|3|5)$"),
                uf: str | None = Query(None, pattern="^[a-zA-Z]{2}$")):
        """As novidades mais recentes primeiro. `peso_min=2` deixa de fora
        as trocas de liderança da contagem; `3` fica só com conta e
        encerramento."""
        env = load_environment(M.env_atual())
        d = A.Diario(DATA_DIR / "previsoes", env.name)
        modelo = modelo_ativo()
        todos = [a for a in _ler(d.caminho)
                 if a.id and a.peso >= peso_min
                 and (cargo is None or a.cargo == cargo)
                 and (uf is None or a.unidade == uf.lower())]
        todos.sort(key=lambda a: a.momento, reverse=True)
        return M.envelope(
            [a.as_dict() for a in todos[:limite]], snapshots=[],
            estado="alertas" if todos else "aguardando",
            motivo=None if todos else "nenhuma novidade registrada ainda",
            extra={"total": len(todos), "modelo": modelo,
                   "source_generated_at": todos[0].momento if todos
                   else None})
