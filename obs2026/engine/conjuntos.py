"""Refazer e conferir o conjunto de capturas de cada projeção registrada.

O diário diz o que foi projetado. Para refazer uma projeção é preciso saber
também **com que arquivos** — e durante a apuração isso muda a cada segundo.
Duas anotações resolvem, sem guardar a lista inteira a cada registro (seriam
5.757 hashes por projeção presidencial, a cada dois minutos):

- `snapshot_set_id`, no registro: o hash do conjunto de capturas lidas
  (`runner.id_do_conjunto`);
- `manifesto_ate`, em `conjuntos-<ambiente>.jsonl`: até que byte de cada
  manifesto diário o motor tinha lido.

O manifesto só cresce, então "a captura mais recente de cada município entre
as linhas até este byte" é uma conta que dá sempre o mesmo resultado. Este
módulo a refaz e compara o hash com o que ficou no registro. Se bater, o
conjunto refeito é exatamente o que o motor leu; se não bater, alguma coisa
entre o manifesto e a projeção não foi como se supõe, e o relatório diz qual
registro.

Não lê nenhum payload: só o manifesto. A regra de escolha é a mesma dos dois
carregadores (`runner.carregar_com_posicao` e `incremental`): EA20 municipal
com conteúdo, do cargo da disputa, e vence a captura de `captured_at` maior.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..storage.raw import RawArchive


def _id(hashes) -> str:
    h = hashlib.sha256()
    for s in sorted(hashes):
        h.update(s.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def ler_anotacoes(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    saida = []
    with open(caminho, encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if linha:
                saida.append(json.loads(linha))
    return saida


def conferir(archive: RawArchive, anotacoes: list[dict]) -> dict:
    """Refaz o conjunto de cada anotação e compara com o `snapshot_set_id`.

    Devolve as contagens e a lista das divergentes (com o que foi refeito).
    Percorre o manifesto uma vez só, na ordem em que foi escrito.
    """
    dias = archive.days()
    # Os conjuntos são prefixos uns dos outros: ordenar pela soma dos
    # deslocamentos é ordenar por "quanto do manifesto já tinha sido lido".
    pendentes = sorted(
        anotacoes, key=lambda a: sum(a["manifesto_ate"].values()))
    # cargo -> uf -> mun -> (captured_at, sha256)
    estado: dict[str, dict[str, dict[str, tuple[str, str]]]] = {}
    leitores = {d: archive.manifest_desde(d) for d in dias}
    lido = {d: 0 for d in dias}

    def avancar(dia: str, ate: int) -> None:
        while lido[dia] < ate:
            try:
                ref, fim = next(leitores[dia])
            except StopIteration:
                return
            lido[dia] = fim
            if ref.kind != "ea20" or not ref.path or not ref.mun:
                continue
            ufs = estado.setdefault(ref.cargo, {})
            muns = ufs.setdefault(ref.uf or "", {})
            anterior = muns.get(ref.mun)
            if anterior is None or ref.captured_at > anterior[0]:
                muns[ref.mun] = (ref.captured_at, ref.sha256)

    iguais = 0
    divergentes = []
    for a in pendentes:
        for dia in dias:
            avancar(dia, a["manifesto_ate"].get(dia, 0))
        incompleto = [d for d, ate in a["manifesto_ate"].items()
                      if d not in lido or lido[d] < ate]
        ufs = estado.get(a["cargo"], {})
        unidade = a["unidade"]
        grupos = (ufs.values() if unidade == "br"
                  else [ufs.get(unidade, {})])
        hashes = [sha for muns in grupos for (_cap, sha) in muns.values()]
        refeito = _id(hashes) if hashes and all(hashes) else None
        if refeito == a["snapshot_set_id"] and not incompleto:
            iguais += 1
        else:
            divergentes.append({
                "registro_id": a["registro_id"],
                "esperado": a["snapshot_set_id"], "refeito": refeito,
                "unidades_registradas": a.get("unidades"),
                "unidades_refeitas": len(hashes),
                "manifesto_incompleto": incompleto,
            })
    return {"anotacoes": len(anotacoes), "iguais": iguais,
            "divergentes": divergentes}
