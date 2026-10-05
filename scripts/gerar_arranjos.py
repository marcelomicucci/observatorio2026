"""Gera `config/arranjos_capitais.json`: capital e cidades do arranjo dela.

    python scripts/gerar_arranjos.py

Fonte: IBGE, *Arranjos Populacionais e Concentrações Urbanas do Brasil*,
2ª ed., tabela 1.1 (`data/raw/ibge/arranjos_tab01.xlsx`, baixada de
geoftp.ibge.gov.br/organizacao_do_territorio/divisao_regional/
arranjos_populacionais/tabelas_xls_2ed/tab01.xlsx). O arranjo é definido por
integração real — gente que mora num município e trabalha ou estuda no outro
— e não pela lei estadual que cria "regiões metropolitanas". As RMs legais
estão infladas em vários estados (SC cobre quase o território inteiro), e o
grupo "capital" viraria meio interior.

Regra: um município é do grupo capital da UF se for a capital ou fizer parte
do arranjo da capital **e** estiver na mesma UF. Cidades de outra UF no mesmo
arranjo (Timon/MA no de Teresina, entorno goiano no de Brasília) ficam no
interior do próprio estado: numa disputa estadual elas não votam junto com a
capital vizinha.

O cruzamento IBGE -> TSE usa o `cdi` do cadastro de municípios do TSE
(`data/raw/ensaio2022/mun-e000544-cm.json`), que também marca a capital
(`c='S'`). O JSON gerado é pequeno e fica em `config/`: nada disso é lido da
rede nem da planilha durante a apuração.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

RAIZ = Path(__file__).resolve().parent.parent
XLSX = RAIZ / "data" / "raw" / "ibge" / "arranjos_tab01.xlsx"
CM = RAIZ / "data" / "raw" / "ensaio2022" / "mun-e000544-cm.json"
SAIDA = RAIZ / "config" / "arranjos_capitais.json"


def main():
    cm = json.loads(CM.read_text(encoding="utf-8"))
    por_ibge, capitais = {}, {}
    for uf in cm["abr"]:
        sigla = uf["cd"].lower()
        for m in uf.get("mu", []):
            if not m.get("cdi"):
                continue                      # exterior: sem codigo IBGE
            por_ibge[int(m["cdi"])] = (sigla, m["cd"], m["nm"])
            if m.get("c") == "S":
                capitais[sigla] = (int(m["cdi"]), m["cd"], m["nm"])

    arranjos, atual = {}, None
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    for row in wb.worksheets[0].iter_rows(values_only=True):
        nome, cod = row[0], row[1]
        if nome and cod is None:
            atual = nome
            arranjos[atual] = []
        elif atual and isinstance(cod, (int, float)):
            arranjos[atual].append(int(cod))

    saida, sem = {}, []
    for sigla, (cdi_cap, cd_cap, nm_cap) in sorted(capitais.items()):
        if sigla == "zz":
            continue
        arr = next((n for n, ms in arranjos.items() if cdi_cap in ms), None)
        membros = [cd_cap]
        if arr:
            for cdi in arranjos[arr]:
                info = por_ibge.get(cdi)
                if info and info[0] == sigla and info[1] != cd_cap:
                    membros.append(info[1])
        else:
            sem.append(nm_cap)
        saida[sigla] = {
            "capital": cd_cap, "nome": nm_cap, "arranjo": arr,
            "membros": sorted(membros),
            "nomes": sorted(por_ibge[c][2] for c in
                            [k for k, v in por_ibge.items()
                             if v[0] == sigla and v[1] in membros]),
        }

    SAIDA.write_text(json.dumps({
        "fonte": "IBGE, Arranjos Populacionais e Concentracoes Urbanas do "
                 "Brasil, 2a ed., tabela 1.1; cruzado com o cadastro de "
                 "municipios do TSE (eleicao 544)",
        "regra": "capital + municipios do arranjo da capital na mesma UF",
        "ufs": saida}, ensure_ascii=False, indent=1), encoding="utf-8")
    for s, v in saida.items():
        print(f"{s.upper()} {v['nome']:<16} {len(v['membros']):>3} municipio(s)"
              f"{'' if v['arranjo'] else '  (capital fora de arranjo)'}")
    print(f"\n{SAIDA}")
    if len(saida) != 27:
        sys.exit(f"esperadas 27 UFs, vieram {len(saida)}")


if __name__ == "__main__":
    main()
