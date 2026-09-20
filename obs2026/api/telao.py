"""Roteiro do telão: a sequência de telas que o projetor exibe sozinho.

O roteiro vem de `config/telao.yaml`, versionado junto com o código. A janela
de controle pode reordenar e mudar tempos em cima da hora, mas o arquivo é a
base — assim o roteiro da noite pode ser preparado com antecedência, revisado
e reproduzido, em vez de existir só na memória de um navegador.

**Rota somente de leitura.** Não existe endpoint que grave o roteiro. O §10
manda separar administração autenticada de leitura pública, e um endpoint de
escrita sem autenticação seria exatamente o contrário disso. Ajuste
permanente se faz no arquivo; ajuste de momento fica no navegador de quem
opera.

## Resolução de município

Aceita código TSE (5 dígitos), código IBGE (7) ou nome + UF. Nome **não é
chave**: `PAU D'ARCO` existe em PA e em TO. Por isso:

  - nome sem UF é recusado;
  - nome ambíguo dentro da mesma UF é recusado com a lista de candidatos.

Nada é resolvido por adivinhação (§2.1). Uma tela que não resolve entra no
roteiro marcada com o erro, e o telão a pula — melhor pular do que projetar
o município errado.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}
CARGO_NOME = {v: k for k, v in CARGOS.items()}
ABRANGENCIA = {"1": "br", "3": "uf", "5": "uf"}
CAMADAS = ("definicao", "lider", "margem", "cobertura")


def register(app) -> None:
    from . import app as M
    from ..config import CONFIG_DIR
    from ..storage.models import Geography
    from ..tse.parsers.common import fold

    @app.get("/api/v1/telao/roteiro")
    def roteiro(s: Session = Depends(M.get_session)):
        """Roteiro resolvido e validado, pronto para o telão consumir."""
        caminho = Path(CONFIG_DIR) / "telao.yaml"
        if not caminho.exists():
            return M.envelope([], snapshots=[], estado="sem_roteiro",
                              motivo="config/telao.yaml não existe")
        cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        padrao = int(cfg.get("padrao_segundos", 20))
        camada_padrao = str(cfg.get("camada_padrao", "definicao"))

        munis = s.execute(select(Geography).where(
            Geography.environment == M._ENV_NAME,
            Geography.tipo == "mu")).scalars().all()
        por_codigo = {}
        por_nome: dict[tuple[str, str], list] = {}
        for g in munis:
            por_codigo[(g.uf, g.cd_tse)] = g
            if g.cd_ibge:
                por_codigo[("", g.cd_ibge)] = g
            por_nome.setdefault((g.uf, g.nome_fold), []).append(g)

        def resolver_municipio(valor, uf) -> tuple[object, str | None]:
            v = str(valor).strip()
            if v.isdigit() and len(v) == 7:
                g = por_codigo.get(("", v))
                return g, (None if g else f"código IBGE {v} desconhecido")
            if v.isdigit():
                if not uf:
                    return None, ("código TSE exige `uf` para desambiguar: "
                                  "o código é único por UF")
                g = por_codigo.get((uf, v.zfill(5)))
                return g, (None if g else
                           f"código TSE {v.zfill(5)} não existe em "
                           f"{uf.upper()}")
            if not uf:
                return None, (f"nome {v!r} sem `uf`: nome de município não é "
                              "chave única no país")
            achados = por_nome.get((uf, fold(v)), [])
            if len(achados) == 1:
                return achados[0], None
            if not achados:
                return None, f"município {v!r} não encontrado em {uf.upper()}"
            return None, (f"nome {v!r} ambíguo em {uf.upper()}: "
                          + ", ".join(f"{g.nome} ({g.cd_tse})"
                                      for g in achados))

        telas = []
        for i, item in enumerate(cfg.get("roteiro") or []):
            cargo_nome = str(item.get("cargo", "")).strip().lower()
            cd = CARGOS.get(cargo_nome)
            uf = (str(item.get("uf")).strip().lower()
                  if item.get("uf") else None)
            erro = None
            municipio_cd = None
            municipio_nome = None

            if cd is None:
                erro = (f"cargo {item.get('cargo')!r} desconhecido; "
                        f"use um de {sorted(CARGOS)}")
            elif ABRANGENCIA[cd] == "uf" and not uf:
                erro = (f"{cargo_nome} é decidido por UF; informe `uf`")

            if not erro and item.get("municipio"):
                g, e = resolver_municipio(item["municipio"], uf)
                if e:
                    erro = e
                else:
                    municipio_cd = g.cd_tse
                    municipio_nome = g.nome
                    uf = uf or g.uf

            camada = str(item.get("camada") or camada_padrao)
            if camada not in CAMADAS:
                erro = erro or (f"camada {camada!r} desconhecida; "
                                f"use uma de {list(CAMADAS)}")

            # Recorte que não decide a disputa: presidente num município,
            # governador num município. O aviso não é configurável (§3).
            nao_decisorio = bool(municipio_cd)

            if item.get("titulo"):
                titulo = str(item["titulo"])
            elif municipio_nome:
                titulo = (f"{cargo_nome.title()} — "
                          f"{municipio_nome}/{(uf or '').upper()}")
            elif uf:
                titulo = f"{cargo_nome.title()} — {uf.upper()}"
            else:
                titulo = f"{cargo_nome.title()} — Brasil"

            telas.append({
                "indice": i,
                "titulo": titulo,
                "cargo": cd,
                "cargo_nome": cargo_nome,
                "uf": uf,
                "municipio": municipio_cd,
                "municipio_nome": municipio_nome,
                "camada": camada,
                "segundos": int(item.get("segundos") or padrao),
                "recorte_nao_decisorio": nao_decisorio,
                "aviso_recorte": (
                    "Recorte do voto. A disputa é decidida "
                    + ("no total do Brasil"
                       if ABRANGENCIA.get(cd) == "br"
                       else f"no total de {(uf or '').upper()}")
                    + " — liderar aqui não é ser eleito por aqui."
                    if nao_decisorio else None),
                "erro": erro,
            })

        validas = [t for t in telas if not t["erro"]]
        return M.envelope(
            telas, snapshots=[], estado="roteiro",
            motivo=(None if validas else
                    "nenhuma tela válida no roteiro; o telão segue no "
                    "recorte manual"),
            extra={
                "arquivo": "config/telao.yaml",
                "telas_validas": len(validas),
                "telas_com_erro": len(telas) - len(validas),
                "duracao_total_s": sum(t["segundos"] for t in validas),
                "nota": (
                    "Telas com erro são puladas pelo telão. Projetar o "
                    "município errado é pior do que pular a tela."),
            })
