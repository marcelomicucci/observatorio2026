"""Cache local das fotos de candidato.

Sem isto, cada navegador que abre o painel busca as fotos direto em
`eleicoes.osidosos.com.br`. Três problemas, e o terceiro é o que importa na
noite da apuração:

1. **Quebra o §8.** O navegador deve falar só com a nossa API. Mil leitores
   não podem virar mil requisições no servidor de outra pessoa.
2. **Vaza o IP do leitor** para um terceiro que ele não escolheu.
3. **O telão fica refém da internet.** Se a rede oscilar — e numa noite de
   apuração ela oscila — as fotos somem da projeção, no meio do evento.

A foto é baixada uma vez, guardada em disco e servida daqui em diante. Se a
origem não responder, a rota devolve 404 e a interface cai no círculo com o
número do candidato, que já é o comportamento quando não há foto. Foto é
enfeite; a apuração não depende dela.

O cache não expira: a foto de um candidato não muda durante a eleição, e
revalidar toda noite seria gastar rede para confirmar o que já se sabe.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse

#: Só imagens, e só destes tipos. Servir o que veio de fora sem restringir o
#: tipo é como um servidor vira vetor de conteúdo arbitrário.
TIPOS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}
#: Teto por arquivo. Foto de perfil não passa disso; o que passar é anomalia.
TETO_BYTES = 3 * 1024 * 1024
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def register(app) -> None:
    from . import app as M
    from ..candidates import osidosos as OS
    from ..config import DATA_DIR

    raiz = DATA_DIR / "cache" / "fotos"

    def _arquivo(slug: str) -> Path | None:
        for ext in (".jpg", ".png", ".webp", ".gif"):
            p = raiz / f"{slug}{ext}"
            if p.exists():
                return p
        return None

    def _origem_do_perfil(slug: str) -> str | None:
        """URL real da foto, lida do enriquecimento ja em cache."""
        cache = OS.Cache(DATA_DIR / "cache" / "enriquecimento")
        for cargo in ("1", "3", "5"):
            perfis, _ = OS.buscar(cargo, cache=cache)
            for p in perfis:
                if p.slug == slug and p.foto_url:
                    return p.foto_url
        return None

    def _baixar(slug: str, origem: str) -> Path | None:
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers=OS.CABECALHOS) as c:
                r = c.get(origem)
                r.raise_for_status()
                tipo = (r.headers.get("content-type") or "").split(";")[0]
                ext = TIPOS.get(tipo.strip().lower())
                if ext is None or len(r.content) > TETO_BYTES:
                    return None
                raiz.mkdir(parents=True, exist_ok=True)
                # Grava por nome temporário e renomeia: nunca existe arquivo
                # truncado com o nome final, nem se o processo cair no meio.
                tmp = raiz / f".{hashlib.sha256(slug.encode()).hexdigest()}.tmp"
                tmp.write_bytes(r.content)
                destino = raiz / f"{slug}{ext}"
                tmp.replace(destino)
                return destino
        except (httpx.HTTPError, OSError):
            return None

    @app.get("/api/v1/foto/{slug}", include_in_schema=False)
    def foto(slug: str):
        """Foto do candidato, servida do nosso disco."""
        slug = slug.strip().lower()
        if not _SLUG.match(slug):
            raise HTTPException(400, "slug invalido")

        local = _arquivo(slug)
        if local is None:
            # A origem vem do perfil ja coletado, nao de um palpite sobre o
            # nome do arquivo: a extensao varia, e adivinhar produz 404 em
            # candidatos que tem foto.
            origem = _origem_do_perfil(slug)
            if origem is None:
                raise HTTPException(404, "candidato sem foto conhecida")
            local = _baixar(slug, origem)
        if local is None:
            # A interface cai no círculo com o número. Foto é enfeite.
            raise HTTPException(404, "foto indisponivel")

        return FileResponse(
            local,
            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/api/v1/fotos/estado")
    def estado_das_fotos():
        """Quantas fotos já estão em disco. Útil antes de subir ao telão."""
        arquivos = list(raiz.glob("*")) if raiz.exists() else []
        imagens = [p for p in arquivos if p.suffix in
                   (".jpg", ".png", ".webp", ".gif")]
        return M.envelope({
            "fotos_em_disco": len(imagens),
            "bytes": sum(p.stat().st_size for p in imagens),
            "diretorio": str(raiz),
            "nota": ("O navegador busca as fotos aqui, nunca no servidor de "
                     "terceiro (§8). Sem foto, a interface mostra o numero "
                     "do candidato."),
        }, snapshots=[], estado="operacional")
