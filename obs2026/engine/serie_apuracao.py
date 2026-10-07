"""Tabela pronta da série "ao longo da apuração".

O gráfico do placar mostra, para cada candidato, o apurado e o projetado a
cada rodada de projeção. Esses números já existem no diário de auditoria,
mas o diário é um JSONL que cresce a noite inteira — 171 MB no 1º turno de
2026 — e a rota `/convergencia` o relê do começo a cada pedido: 5 a 7 s,
medidos em 07/10/2026. Um gráfico de parede não pode custar isso.

Aqui fica uma tabela pequena, em SQLite, com uma linha por (rodada,
candidato) e só as colunas que o gráfico usa. Ela é **alimentada a cada
projeção registrada** (`Diario.registrar` chama `sincronizar`) e a rota só
faz uma consulta indexada.

Três decisões:

* **A tabela é derivada, o diário continua sendo a fonte.** `sincronizar`
  guarda até que byte do diário já leu e continua dali, como
  `Diario.ultimos`. Se a tabela for apagada, a próxima leitura a refaz
  inteira; se o diário for trocado por um menor, ela recomeça do zero.
* **Nada é recalculado** (§5.2): são os números como foram registrados.
* **Nunca atrapalha o registro.** Quem chama a partir do caminho de gravação
  usa espera curta e ignora falha: a rota põe a tabela em dia depois.

Arquivo à parte (`data/previsoes/serie-<ambiente>.sqlite`), não o banco do
ambiente: o coletor escreve naquele o tempo todo, e este aqui pode ser
apagado sem perder nada.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import nivel as NV

_ESQUEMA = """
create table if not exists ponto (
  eleicao text not null, turno text not null, cargo text not null,
  unidade text not null, modelo text not null, momento text not null,
  cobertura_pct real not null, pendente_max integer,
  concorrentes integer not null, sqcand text not null, nome text,
  votos integer not null, pct_projetado real, pct_q100 real, pct_q900 real,
  secoes_pct real, pct_q050 real, pct_q950 real, pct_q025 real, pct_q975 real
);
create index if not exists ix_ponto_disputa
  on ponto (cargo, unidade, eleicao, turno, modelo, momento);
create table if not exists leitura (
  id integer primary key check (id = 1), pos integer not null
);
"""


def caminho(raiz: Path, ambiente: str) -> Path:
    return Path(raiz) / f"serie-{ambiente}.sqlite"


def _abrir(raiz: Path, ambiente: str, espera_s: float) -> sqlite3.Connection:
    con = sqlite3.connect(caminho(raiz, ambiente), timeout=espera_s,
                          isolation_level=None)
    # Tabela de versão anterior (sem os quantis da faixa publicada): é
    # derivada do diário, então some e se refaz na próxima sincronização.
    colunas = {r[1] for r in con.execute("pragma table_info(ponto)")}
    if colunas and "pct_q050" not in colunas:
        con.executescript("drop table ponto; drop table if exists leitura;")
    con.executescript(_ESQUEMA)
    return con


def sincronizar(raiz: Path, ambiente: str, *, espera_s: float = 30.0) -> int:
    """Leva para a tabela as linhas do diário que ainda não estão lá.

    Devolve quantas rodadas entraram. Uma transação só, aberta com
    `begin immediate`: o servidor e o coletor podem chamar ao mesmo tempo, e
    quem chega depois encontra a posição já avançada em vez de inserir tudo
    de novo.
    """
    diario = Path(raiz) / f"previsoes-{ambiente}.jsonl"
    if not diario.exists():
        return 0
    con = _abrir(raiz, ambiente, espera_s)
    try:
        con.execute("begin immediate")
        linha = con.execute("select pos from leitura where id = 1").fetchone()
        pos = linha[0] if linha else 0
        if diario.stat().st_size < pos:        # diário trocado: recomeça
            con.execute("delete from ponto")
            pos = 0
        novas, rodadas = [], 0
        with open(diario, "rb") as fh:
            fh.seek(pos)
            while True:
                bruta = fh.readline()
                # Linha sem quebra final ainda está sendo escrita.
                if not bruta or not bruta.endswith(b"\n"):
                    break
                pos += len(bruta)
                if not bruta.strip():
                    continue
                try:
                    r = json.loads(bruta)
                    tot = r.get("eleitorado_total") or 0
                    cob = 100.0 * r["eleitorado_apurado"] / tot if tot else 0.0
                    sec = r.get("secoes_total") or 0
                    cob_sec = (100.0 * (r.get("secoes_totalizadas") or 0)
                               / sec if sec else 0.0)
                    base = (str(r["eleicao"]), str(r["turno"]),
                            str(r["cargo"]), str(r["unidade"]), r["modelo"],
                            r["momento"], cob, r.get("pendente_max"),
                            r.get("concorrentes_observados") or 0)
                    for c in r["candidatos"]:
                        novas.append(base + (
                            str(c["sqcand"]), c.get("nome"),
                            c["votos_observados"],
                            c.get("pct_concorrentes_q500"),
                            c.get("pct_concorrentes_q100"),
                            c.get("pct_concorrentes_q900"), cob_sec,
                            c.get("pct_concorrentes_q050"),
                            c.get("pct_concorrentes_q950"),
                            c.get("pct_concorrentes_q025"),
                            c.get("pct_concorrentes_q975")))
                    rodadas += 1
                except (ValueError, KeyError, TypeError):
                    continue                    # linha estragada: pula
        if novas:
            con.executemany(
                "insert into ponto values "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                novas)
        con.execute("insert into leitura (id, pos) values (1, ?) "
                    "on conflict (id) do update set pos = excluded.pos",
                    (pos,))
        con.execute("commit")
        return rodadas
    except BaseException:
        if con.in_transaction:
            con.execute("rollback")
        raise
    finally:
        con.close()


def consultar(raiz: Path, ambiente: str, cargo: str, unidade: str, *,
              top: int = 2) -> dict | None:
    """A série de uma disputa, pronta para o gráfico, ou `None` se não há.

    Qual série: a da eleição e turno do registro MAIS RECENTE daquela
    disputa — na noite do 2º turno o gráfico passa a ser o do 2º turno no
    primeiro registro, sem herdar as linhas do 1º. Entre os modelos dessa
    eleição, o que tem mais rodadas (modelo diferente é série diferente,
    §5.2, e um punhado de registros avulsos de outro modelo não pode tomar
    o lugar da série da noite).
    """
    if not caminho(raiz, ambiente).exists():
        return None
    con = _abrir(raiz, ambiente, 5.0)
    try:
        ult = con.execute(
            "select eleicao, turno from ponto where cargo = ? and unidade = ? "
            "order by momento desc limit 1", (cargo, unidade)).fetchone()
        if ult is None:
            return None
        disputa = (cargo, unidade, ult[0], ult[1])
        onde = "cargo = ? and unidade = ? and eleicao = ? and turno = ?"
        modelo, rodadas = con.execute(
            f"select modelo, count(distinct momento) n from ponto where {onde} "
            "group by modelo order by n desc, max(momento) desc limit 1",
            disputa).fetchone()
        serie = disputa + (modelo,)
        onde += " and modelo = ?"
        momento = con.execute(
            f"select max(momento) from ponto where {onde}", serie).fetchone()[0]
        escolhidos = con.execute(
            f"select sqcand, nome from ponto where {onde} and momento = ? "
            "order by votos desc limit ?",
            serie + (momento, max(1, top))).fetchall()
        candidatos = []
        for sq, nome in escolhidos:
            pontos, ref = [], None
            # Em ordem de registro, para a trava de largura: acima de 95%
            # apurado a faixa publicada mantém a largura do último registro
            # abaixo disso (`engine/nivel.py`), como na rota /projection.
            for (m, cob, pend, v, den, q500, q100, q900, sec, q050, q950,
                 q025, q975) in con.execute(
                    "select momento, cobertura_pct, pendente_max, votos, "
                    "concorrentes, pct_projetado, pct_q100, pct_q900, "
                    "secoes_pct, pct_q050, pct_q950, pct_q025, pct_q975 "
                    f"from ponto where {onde} and sqcand = ? "
                    "order by momento", serie + (sq,)):
                qs = {"q500": q500, "q100": q100, "q900": q900,
                      "q050": q050, "q950": q950, "q025": q025, "q975": q975}
                if cob < NV.TRAVA_LARGURA_APURADO:
                    ref = qs
                    publicados = qs
                else:
                    publicados = NV.travar_largura(qs, ref) if pend else qs
                nivel, _ = NV.nivel_por_apurado(sec)
                lo, hi = NV.faixa(publicados, nivel)
                if publicados.get(NV.CHAVES[nivel][0]) is None:
                    nivel = 80         # registro antigo, sem os quantis largos
                pontos.append({
                    "momento": m, "cobertura_pct": cob,
                    # Sem voto contado o apurado é nulo, não zero (§9).
                    "pct_apurado": 100.0 * v / den if den > 0 else None,
                    "pct_projetado": q500, "pct_q100": q100, "pct_q900": q900,
                    # A faixa PUBLICADA naquele registro: o nível sobe de 80%
                    # para 90% aos 15% apurado, e a largura trava aos 95%.
                    "faixa_min": lo, "faixa_max": hi, "faixa_nivel": nivel,
                })
            pontos.sort(key=lambda x: (x["cobertura_pct"], x["momento"]))
            candidatos.append({"sqcand": sq, "nome": nome, "pontos": pontos})
        return {"eleicao": ult[0], "turno": ult[1], "cargo": cargo,
                "unidade": unidade, "modelo": modelo, "execucoes": rodadas,
                "ultimo_registro": momento, "candidatos": candidatos}
    finally:
        con.close()
