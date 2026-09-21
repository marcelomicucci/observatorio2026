"""Roteiro do telão: a sequência de telas que o projetor exibe sozinho.

O roteiro mora em `config/telao.yaml`. Pode ser preparado com antecedência no
arquivo e ajustado durante a noite pela aba Telão do painel, que grava no
mesmo arquivo — há uma fonte só, e é ela que o telão, a captura de imagens e
qualquer outra janela leem.

## Por que agora existe uma rota que grava

A primeira versão não tinha, com base no §10: administração tem que ser
separada da leitura pública, e escrita sem autenticação é o contrário disso.
O argumento vale para um servidor exposto. Este roda na máquina de quem
opera, e a alternativa que sobrava — copiar YAML do navegador e colar num
arquivo com o projetor ligado — não é operável numa noite de apuração.

Então a gravação existe, com as travas que substituem a autenticação:

- **só aceita pedido da própria máquina** (endereço de loopback). Se um dia
  o servidor for publicado, a rota continua recusando quem vem de fora;
- **só aceita JSON e só da mesma origem.** Estar em loopback não basta: o
  navegador de quem opera também está, e qualquer página aberta nele pode
  disparar um POST para `localhost`. Um formulário de outro site chega como
  `text/plain`, sem pré-verificação de CORS; exigir `application/json` e
  conferir o `Origin` fecha essa porta;
- **valida antes de gravar**, com o mesmo resolvedor da leitura. Um roteiro
  com tela quebrada não é gravado — é devolvido com o motivo de cada erro;
- **guarda a versão anterior** em `config/telao.anterior/`, e grava por
  arquivo temporário renomeado: nunca existe um `telao.yaml` pela metade.

## Resolução de município

Aceita código TSE (5 dígitos), código IBGE (7) ou nome + UF. Nome **não é
chave**: `PAU D'ARCO` existe em PA e em TO. Por isso:

  - nome sem UF é recusado;
  - nome ambíguo dentro da mesma UF é recusado com a lista de candidatos.

Nada é resolvido por adivinhação (§2.1). Na leitura, uma tela que não resolve
entra marcada com o erro, e o telão a pula — melhor pular do que projetar o
município errado. Na gravação, ela impede a gravação.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}
CARGO_NOME = {v: k for k, v in CARGOS.items()}
ABRANGENCIA = {"1": "br", "3": "uf", "5": "uf"}
CAMADAS = ("lider", "apurar")
#: Nomes de antes da fusão das camadas do mapa. Continuam aceitos para que
#: nenhum roteiro já escrito quebre, mas são traduzidos aqui — o telão
#: recebe sempre o nome atual. Sem isto, `cobertura` chegava ao mapa como
#: nome desconhecido e caía em silêncio no mapa de líderes.
APELIDOS_CAMADA = {"definicao": "lider", "margem": "lider",
                   "cobertura": "apurar"}

#: Quem pode gravar. `testclient` é o endereço que o cliente de teste do
#: Starlette informa; ele só existe dentro do processo, nunca na rede.
LOCAIS = {"127.0.0.1", "::1", "localhost", "testclient"}
#: Limites do que se aceita gravar. Um roteiro de noite de apuração tem
#: dezenas de telas, não milhares; o teto existe para um erro de clique não
#: virar um arquivo de megabytes.
MAX_TELAS = 60
SEG_MIN, SEG_MAX = 5, 300
MANTER_ANTERIORES = 30

BRT = ZoneInfo("America/Sao_Paulo")

CABECALHO = """\
# Roteiro do telão — a sequência de telas que o projetor exibe sozinho.
#
# Pode ser editado aqui ou pela aba Telão do painel, que grava neste mesmo
# arquivo e guarda a versão anterior em config/telao.anterior/.
#
# CAMPOS
#   cargo       presidente | governador | senador        (obrigatório)
#   uf          sigla, obrigatória para governador e senador
#   municipio   código TSE (5 dígitos) ou IBGE (7), OU nome + uf
#   camada      lider | apurar                           (opcional)
#                 lider   quem lidera: cor do 1o, tom pela margem sobre o
#                         2o, contorno amarelo se matematicamente definido
#                         e verde se eleito segundo o TSE
#                 apurar  onde falta apurar: quanto do eleitorado ainda
#                         nao entrou na conta (escuro = falta mais)
#               Os nomes antigos (definicao, margem, cobertura) continuam
#               aceitos: definicao e margem viram lider, cobertura vira
#               apurar.
#   segundos    tempo da tela, de 5 a 300; usa `padrao_segundos` se omitido
#   titulo      rótulo livre; se omitido, é montado a partir do recorte
#
# SOBRE RECORTE MUNICIPAL
# "Presidente em Belém" é recorte do VOTO, não a unidade que elege. O telão
# marca essas telas com aviso próprio, e isso não é configurável (§3).
#
# SOBRE NOME DE MUNICÍPIO
# Nome não é chave: PAU D'ARCO existe em PA e em TO. Nome exige `uf`, e
# ambiguidade é recusada em vez de resolvida por adivinhação (§2.1).
"""


def _normalizar_camada(nome) -> str:
    c = str(nome or "").strip().lower()
    return APELIDOS_CAMADA.get(c, c)


def register(app) -> None:
    from . import app as M
    from ..config import CONFIG_DIR
    from ..storage.models import Geography
    from ..tse.parsers.common import fold

    caminho = Path(CONFIG_DIR) / "telao.yaml"

    def resolver(cfg: dict, s: Session) -> list[dict]:
        """Transforma o roteiro cru em telas resolvidas, cada uma com erro
        ou sem. Usado pela leitura e pela gravação — a mesma régua nos dois
        sentidos, para que nada gravado deixe de ser lido."""
        padrao = int(cfg.get("padrao_segundos", 20))
        camada_padrao = _normalizar_camada(cfg.get("camada_padrao", "lider"))

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
            if not isinstance(item, dict):
                telas.append({"indice": i, "titulo": f"tela {i + 1}",
                              "erro": "item do roteiro não é um objeto"})
                continue
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

            camada = _normalizar_camada(item.get("camada") or camada_padrao)
            if camada not in CAMADAS:
                erro = erro or (f"camada {camada!r} desconhecida; "
                                f"use uma de {list(CAMADAS)}")

            try:
                segundos = int(item.get("segundos") or padrao)
            except (TypeError, ValueError):
                segundos = padrao
                erro = erro or f"segundos {item.get('segundos')!r} não é número"

            # Recorte que não decide a disputa: presidente num município,
            # governador num município. O aviso não é configurável (§3).
            nao_decisorio = bool(municipio_cd)

            titulo_livre = (str(item["titulo"]).strip()
                            if item.get("titulo") else None)
            if titulo_livre:
                titulo = titulo_livre
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
                # O título escrito à mão, separado do montado: sem isso, gravar
                # de volta congelaria no arquivo um título automático que
                # deixaria de acompanhar o recorte.
                "titulo_livre": titulo_livre,
                "cargo": cd,
                "cargo_nome": cargo_nome,
                "uf": uf,
                "municipio": municipio_cd,
                "municipio_nome": municipio_nome,
                "camada": camada,
                "segundos": segundos,
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
        return telas

    def resposta(telas: list[dict], gravado_em: str | None = None) -> dict:
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
                "gravado_em": gravado_em,
                "nota": (
                    "Telas com erro são puladas pelo telão. Projetar o "
                    "município errado é pior do que pular a tela."),
            })

    @app.get("/api/v1/telao/roteiro")
    def roteiro(s: Session = Depends(M.get_session)):
        """Roteiro resolvido e validado, pronto para o telão consumir."""
        if not caminho.exists():
            return M.envelope([], snapshots=[], estado="sem_roteiro",
                              motivo="config/telao.yaml não existe")
        cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        return resposta(resolver(cfg, s))

    def _conferir_origem(req: Request) -> None:
        host = req.client.host if req.client else ""
        if host not in LOCAIS:
            raise HTTPException(
                403, "o roteiro só pode ser gravado da própria máquina")
        tipo = (req.headers.get("content-type") or "").split(";")[0].strip()
        if tipo != "application/json":
            # Formulário de outra página chega como text/plain ou
            # x-www-form-urlencoded, sem passar pelo CORS.
            raise HTTPException(415, "envie o roteiro como application/json")
        origem = req.headers.get("origin")
        if origem:
            from urllib.parse import urlsplit
            o = urlsplit(origem)
            if (o.hostname not in LOCAIS
                    or (o.port or 80) != (req.url.port or 80)):
                raise HTTPException(
                    403, f"origem {origem!r} não pode gravar o roteiro")

    @app.post("/api/v1/telao/roteiro")
    async def gravar_roteiro(req: Request,
                             s: Session = Depends(M.get_session)):
        """Grava o roteiro em config/telao.yaml, se todas as telas resolvem."""
        _conferir_origem(req)
        try:
            corpo = await req.json()
        except ValueError as exc:
            raise HTTPException(400, "corpo não é JSON válido") from exc
        itens = corpo.get("roteiro") if isinstance(corpo, dict) else None
        if not isinstance(itens, list):
            raise HTTPException(400, "esperado {\"roteiro\": [...]}")
        if len(itens) > MAX_TELAS:
            raise HTTPException(
                400, f"{len(itens)} telas; o máximo é {MAX_TELAS}")

        # Só os campos conhecidos passam. O resto seria gravado no arquivo
        # sem que ninguém o lesse.
        limpos = []
        for i, it in enumerate(itens):
            if not isinstance(it, dict):
                raise HTTPException(400, f"tela {i + 1} não é um objeto")
            cargo = CARGO_NOME.get(str(it.get("cargo", "")),
                                   str(it.get("cargo", "")).strip().lower())
            d: dict = {"cargo": cargo}
            if it.get("uf"):
                d["uf"] = str(it["uf"]).strip().lower()[:2]
            if it.get("municipio"):
                d["municipio"] = str(it["municipio"]).strip()[:7]
            d["camada"] = _normalizar_camada(it.get("camada") or "lider")
            try:
                seg = int(it.get("segundos") or 20)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    400, f"tela {i + 1}: segundos não é número") from exc
            d["segundos"] = max(SEG_MIN, min(SEG_MAX, seg))
            if it.get("titulo"):
                d["titulo"] = str(it["titulo"]).strip()[:120]
            limpos.append(d)

        cfg = {"padrao_segundos": 20, "camada_padrao": "lider",
               "roteiro": limpos}
        telas = resolver(cfg, s)
        erros = [f"tela {t['indice'] + 1} ({t['titulo']}): {t['erro']}"
                 for t in telas if t["erro"]]
        if erros:
            # Nada gravado. Um roteiro com tela quebrada no arquivo seria
            # descoberto só quando o telão pulasse a tela, com o projetor
            # ligado.
            raise HTTPException(422, {"motivo": "roteiro não gravado",
                                      "erros": erros})

        agora = datetime.now(BRT)
        if caminho.exists():
            pasta = caminho.parent / "telao.anterior"
            pasta.mkdir(exist_ok=True)
            copia = pasta / f"telao-{agora:%Y%m%d-%H%M%S}.yaml"
            copia.write_bytes(caminho.read_bytes())
            antigas = sorted(pasta.glob("telao-*.yaml"))
            for velha in antigas[:-MANTER_ANTERIORES]:
                velha.unlink(missing_ok=True)

        texto = (CABECALHO
                 + f"#\n# Gravado pelo painel em {agora:%d/%m/%Y %H:%M:%S}.\n\n"
                 + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, width=100))
        tmp = caminho.with_suffix(".yaml.tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, caminho)

        return resposta(telas, gravado_em=agora.isoformat())
