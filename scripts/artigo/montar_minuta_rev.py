# -*- coding: utf-8 -*-
"""Revisa e complementa a minuta: edita word/document.xml da cópia descompactada."""
import re, json, shutil, statistics as st
from pathlib import Path
from xml.sax.saxutils import escape
from PIL import Image

S = Path(r"C:/Users/micuc/AppData/Local/Temp/claude/C--Users-micuc-Desktop-eleicao2026/e3802470-08af-4d87-ba4c-1b827a2975ea/scratchpad")
U = S / "unp"
R = Path(r"C:/Users/micuc/Desktop/eleicao2026/observatorio2026")
doc = (U / "word/document.xml").read_text(encoding="utf8")

# ---------------- tokenizar o corpo ----------------
i0 = doc.index("<w:body>") + len("<w:body>")
i1 = doc.index("<w:sectPr")
head, body, tail = doc[:i0], doc[i0:i1], doc[i1:]
els = []
pos = 0
while pos < len(body):
    if body.startswith("<w:tbl>", pos):
        e = body.index("</w:tbl>", pos) + len("</w:tbl>")
    elif body.startswith("<w:p ", pos) or body.startswith("<w:p>", pos):
        e = body.index("</w:p>", pos) + len("</w:p>")
    else:
        raise RuntimeError(body[pos:pos + 80])
    els.append(body[pos:e]); pos = e

def text_of(el):
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", el))

def find(prefix, start=0):
    for i in range(start, len(els)):
        if text_of(els[i]).startswith(prefix):
            return i
    raise KeyError(prefix)

# ---------------- construtores ----------------
def run(text, bold=False, italic=False, sz=None, font=None):
    rpr = ""
    if font: rpr += f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>'
    if bold: rpr += "<w:b/>"
    if italic: rpr += "<w:i/>"
    if sz: rpr += f'<w:sz w:val="{sz}"/>'
    return f"<w:r>{'<w:rPr>' + rpr + '</w:rPr>' if rpr else ''}<w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r>"

def P(text, style=None, kind=None):
    ppr = ""
    if style: ppr += f'<w:pStyle w:val="{style}"/>'
    if kind == "caption": ppr += '<w:keepNext/><w:spacing w:before="140"/>'
    if kind == "code": ppr += '<w:spacing w:after="60"/>'
    if kind == "fig": ppr += "<w:keepNext/>"
    r = {None: run(text), "caption": run(text, bold=True, sz=20), "fonte": run(text, italic=True, sz=18),
         "code": run(text, sz=16, font="Consolas"), "nota": run(text, sz=18)}[kind if kind != "fig" else None]
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{r}</w:p>"

BORD = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>' for s in ("top", "left", "bottom", "right"))
def TBL(headers, rows, widths, sz=18):
    assert len(headers) == len(widths)
    def cell(t, w, hdr):
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="F1F1F1"/>' if hdr else ""
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:tcBorders>{BORD}</w:tcBorders>{shd}<w:vAlign w:val="center"/></w:tcPr>'
                f'<w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>{run(str(t), bold=hdr, sz=sz)}</w:p></w:tc>')
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    trs = ['<w:tr><w:trPr><w:cantSplit/><w:tblHeader/><w:jc w:val="center"/></w:trPr>' + "".join(cell(h, w, True) for h, w in zip(headers, widths)) + "</w:tr>"]
    for row in rows:
        trs.append('<w:tr><w:trPr><w:cantSplit/><w:jc w:val="center"/></w:trPr>' + "".join(cell(c, w, False) for c, w in zip(row, widths)) + "</w:tr>")
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:jc w:val="center"/><w:tblLayout w:type="fixed"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl>')

rels = (U / "word/_rels/document.xml.rels").read_text(encoding="utf8")
next_rid = [max(int(x) for x in re.findall(r'Id="rId(\d+)"', rels)) + 1]
next_doc = [10]
def FIG(png, nome):
    n = len(list((U / "word/media").glob("image*.png"))) + 1
    shutil.copy(png, U / f"word/media/image{n}.png")
    rid = f"rId{next_rid[0]}"; next_rid[0] += 1
    global rels
    rels = rels.replace("</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{n}.png"/></Relationships>')
    w, h = Image.open(png).size
    cx = 5943600; cy = int(cx * h / w)
    did = next_doc[0]; next_doc[0] += 1
    return (f'<w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:rPr><w:noProof/></w:rPr><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{did}" name="{nome}"/>'
            f'<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="{nome}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

def set_text(i, text):
    """Troca o texto do único run do parágrafo i, preservando a formatação."""
    el = els[i]
    m = list(re.finditer(r"<w:t[^>]*>([^<]*)</w:t>", el))
    assert len(m) == 1, text_of(el)[:60]
    els[i] = el[:m[0].start()] + f'<w:t xml:space="preserve">{escape(text)}</w:t>' + el[m[0].end():]

def replace_in_table(i, old, new):
    assert old in els[i], old
    els[i] = els[i].replace(escape(old) if "&" in old else old, escape(new), 1)

def insert_after(i, *new):
    els[i + 1:i + 1] = list(new)
    return i + len(new)

# ================= DADOS =================
ex = json.load(open(S / "exante2026.json", encoding="utf8"))
gm1 = json.load(open(R / "data/exports/ensaio2022/gate2022-m1.json", encoding="utf8")) if (R / "data/exports/ensaio2022/gate2022-m1.json").exists() else None
def tri(rows, ordem):
    rr = [r for r in rows if r["ordem"] == ordem]
    return "{:.2f} / {:.2f} / {:.2f}".format(rr[0]["erro_m2"], rr[4]["erro_m2"], rr[8]["erro_m2"]).replace(".", ",")
def tri0(rows, ordem):
    rr = [r for r in rows if r["ordem"] == ordem]
    return "{:.2f} / {:.2f} / {:.2f}".format(rr[0]["erro_m0"], rr[4]["erro_m0"], rr[8]["erro_m0"]).replace(".", ",")
if gm1:
    m1 = gm1["por_modelo"]["m1"]
    m1_real, m1_al = tri(m1["por_rodada"], "real"), tri(m1["por_rodada"], "aleatoria")
    m1_cob = f"{m1['cobertura80_m2_pct']['real']:.1f} / {m1['cobertura80_m2_pct']['aleatoria']:.1f}".replace(".", ",")
    e9 = m1["disputas_com_90pct_num_perdedor_m2"]
    m1_gate = ("passou" if m1["passou"] else "não passou: " + ("vence o M0 em todas as rodadas, mas a cobertura fica fora da tolerância" if not m1["derrotas_m2"] else f"{len(m1['derrotas_m2'])} rodada(s) sem vencer o M0")) + f"; ≥ 90% a perdedor em {e9['real']} / {e9['aleatoria']} disputas"
    m1_fonte = "gate2022-m1 (5/10, 3.000 sim.)"
else:
    m1_real = m1_al = m1_cob = "NR"; m1_gate = "pendente"; m1_fonte = "ensaio teste2022 (M1 e M2, 21/09)"

# ================= EDIÇÕES =================
# 0. nota de versão
i = find("Versão de trabalho de 5 de outubro")
set_text(i, "Versão de trabalho revisada em 5 de outubro de 2026, após o primeiro turno. Referências em APA 7. Os campos entre colchetes e o Apêndice B orientam a finalização. Os resultados de 2026 referem-se ao primeiro turno e ao diário original de previsões; o segundo turno (25 de outubro) exigirá novo protocolo congelado.")

# 1. resumo
resumo = ("A divulgação progressiva de resultados eleitorais produz um problema de previsão sob cobertura territorial desigual: a preferência observada entre os votos já contabilizados pode diferir daquela dos votos pendentes. "
          "Este estudo apresenta o Observatório Eleitoral 2026, sistema brasileiro de projeção de resultados para presidente, governador e senador durante a apuração. O método combina decomposição municipal do eleitorado pendente, regularização hierárquica das preferências, correção pela votação presidencial histórica e simulação de Monte Carlo com choques compartilhados entre territórios. "
          "A investigação reúne auditoria do código, análise exploratória reproduzida, ensaios sobre a eleição de 2022 e avaliação ex ante do diário de previsões do primeiro turno de 2026 (55 disputas, 26.835 registros). "
          "Em 5.570 municípios pareados, a correlação da participação do PT nos votos válidos do segundo turno entre 2018 e 2022 foi de 0,975. No ensaio de 2022, o modelo com inclinação histórica reduziu o erro mediano do líder em relação à extrapolação dos votos apurados em todas as rodadas (1,69 contra 3,32 pontos percentuais na primeira rodada da ordem real), com cobertura de 81,6% para intervalos nominais de 80%. "
          "Em 2026, as previsões emitidas com menos de 6% do eleitorado apurado tiveram erro absoluto médio de 1,40 ponto, contra 2,52 da contagem parcial; os intervalos de 80% contiveram o resultado em 96% a 100% dos casos, indicando largura excessiva, e nenhuma probabilidade igual ou superior a 90% foi atribuída a candidato não eleito após 0,04% apurado. "
          "O caso oferece uma arquitetura estatística auditável e evidencia que plausibilidade metodológica, funcionamento computacional e calibração prospectiva precisam ser demonstrados separadamente.")
i = find("A divulgação progressiva de resultados eleitorais produz")
set_text(i, resumo)
i = find("Resumo provisório")
set_text(i, f"Extensão do resumo: {len(resumo.split())} palavras. Os números de 2026 provêm do diário original (data/previsoes/previsoes-oficial2026.jsonl), avaliado sem recálculo das previsões.")

# 2. numeração dos subtítulos
for k, el in enumerate(els):
    if 'w:val="Ttulo2"' in el:
        t = text_of(el)
        if re.match(r"^\d \d ", t):
            set_text(k, re.sub(r"^(\d) (\d) ", r"\1.\2 ", t))

# 3. seção 4.1
i = find("Este trabalho adota um estudo de caso computacional")
set_text(i, "Este trabalho adota um estudo de caso computacional com análise documental e avaliação temporal. O código de referência é o commit a57452c9dde817febb13c9cec2c62994ce0be0d2 do repositório observatorio2026, datado de 5 de outubro de 2026, 20h52min50s UTC. O commit seguinte (dd16c5d, 21h05 UTC) alterou apenas config/thresholds.yaml, para registrar a homologação assinada da variante M3.3; o código executável é idêntico. Foram examinados o inventário do repositório, documentação, configurações, módulos de coleta e projeção, scripts de ensaio, testes, três bases históricas derivadas e, no computador de operação, o diário de previsões, o manifesto de capturas, o banco oficial e os relatórios de ensaio. A evidência foi classificada em três níveis: recalculada nesta revisão (análise histórica, métricas ex ante do diário, medidas de coleta), gerada pelos scripts do projeto e lida nos relatórios (gate de 2022, replay de 2026) e ainda pendente (Apêndice B).")
i = find("A versão auditada contém alterações posteriores")
set_text(i, text_of(els[i]) + " O diário confirma que todas as 26.835 previsões do primeiro turno foram emitidas pelo modelo m3 (versão m3-0.1), sob o mesmo hash de thresholds (527f8ea6…) e com homologação ativa; nenhuma variante posterior operou na noite. Por isso, a avaliação ex ante da seção 5 refere-se ao M3, enquanto o M3.3, homologado em 5 de outubro, é tratado como desenvolvimento retrospectivo.")

# 4. tabela 1
i = find("Tabela 1")
t = i + 1
replace_in_table(t, "Contrato e exemplos disponíveis; capturas completas de 2026 pendentes.", "Capturas de 4–5/10/2026 preservadas: 894.695 entradas de manifesto, 692.341 delas EA20; só respostas 200 e 304.")
replace_in_table(t, "Importação prevista; conferir arquivo efetivamente usado em produção.", "A tabela electorate do banco oficial está vazia: o universo esperado veio do próprio EA20 (eleitorado por município). O cadastro do SIG não entrou na rota de produção.")
replace_in_table(t, "Diretórios operacionais não versionados.", "Disponíveis no computador de operação: diário com 26.835 registros em 55 disputas (4/10 17h23 a 5/10 16h34, horário de Brasília) e relatórios em data/exports/.")

# 5. frequência medida
i = find("A configuração auditada prevê acompanhamento a cada 20 segundos")
set_text(i, "A configuração auditada prevê acompanhamento a cada 20 segundos, controles agregados a cada 10 segundos, reconciliação rotativa em 3.600 segundos e registro de projeções a cada 120 segundos, com 1.500 simulações por rodada contínua. As frequências efetivas foram medidas no diário e no manifesto. O período amostral de 2026 vai da primeira projeção registrada, às 17h23min05s de 4 de outubro (0,38% do eleitorado nacional apurado), à última emissão presidencial com apuração incompleta, às 0h48 de 5 de outubro; o laço de registro operou até 3h00 e foi retomado às 15h31 de 5 de outubro, quando as 54 disputas estaduais já constavam como finalizadas. O intervalo entre emissões sucessivas de uma mesma disputa teve mediana de 121 segundos e percentil 90 de 303 segundos; cinco intervalos excederam 600 segundos, o maior com 6.873 segundos. Dos 26.835 registros, 26.826 usaram 1.500 simulações; oito usaram 1.200 e um usou 2.000, todos nos primeiros minutos. A latência entre a geração do arquivo municipal no TSE e sua captura, considerando apenas arquivos com conteúdo novo, teve mediana de 130 segundos às 17h, 173 às 18h e 380 às 19h, com percentis 90 de 323, 664 e 2.171 segundos; a hora mais carregada (19h) coincide com o pico de totalizações. A base usa UTC e os gráficos usam o horário de Brasília (UTC−3).")

# 6. cobertura do universo e snapshot_set_id
i = find("Dados faltantes são diferentes de zero votos")
insert_after(i, P("Na noite de 2026, o manifesto registra EA20 para 5.757 unidades na disputa presidencial (5.571 municípios e 186 localidades do exterior) e 5.571 em cada disputa estadual, isto é, o universo completo; nenhuma unidade ficou sem captura, e os totais de eleitorado por disputa foram conciliados contra o arquivo da UF e do Brasil. O campo snapshot_set_id está nulo em todos os 26.835 registros do diário, o que confirma a limitação apontada no Apêndice A: a ligação entre cada previsão e o conjunto de capturas que a alimentou precisa ser reconstruída pelo horário de emissão, como faz o replay descrito na seção 4.7."))

# 7. AED 2026
i = find("Para completar a AED de 2026, devem ser acrescentadas curvas")
set_text(i, "A Figura 3 completa a análise exploratória com a dinâmica intranoite de 2026, reconstruída do diário. A apuração nacional cruzou 10% do eleitorado às 18h02, 50% às 19h05, 90% às 20h34 e 99% às 21h46; entre 21h44 e 0h08 ficou estacionada entre 98,99% e 99,99%, e 42 seções (13.858 eleitores) permaneceram não totalizadas na última captura, às 12h54 de 5 de outubro. As UFs cruzaram 95% entre 18h47 (DF) e 21h28 (BA), com dez delas depois das 21h: o Nordeste e as três maiores UFs do Sudeste fecharam por último. A ordem de chegada não é aleatória nem uniforme entre estados, e o patamar de 81% a 83% entre 19h41 e 20h06 mostra que a cobertura nacional pode ficar parada enquanto o modelo continua a ser consultado.")
j = insert_after(i, P("Figura 3  Eleitorado apurado ao longo da noite de 4 de outubro de 2026", kind="caption"), FIG(S / "fig6_apuracao.png", "apuracao2026"),
                 P("Fonte: diário de previsões, campos eleitorado_apurado e eleitorado_total de cada registro; horário de Brasília. Presidente agrega o exterior. Rótulos indicam as UFs que alcançaram 95% mais cedo e mais tarde.", kind="fonte"))

# 8. simulação: B efetivo
i = find("O padrão de SimConfig é 5.000 simulações")
set_text(i, text_of(els[i]).replace("O número efetivo deverá vir de cada registro.", "O diário registra B = 1.500 em 26.826 dos 26.835 registros."))

# 9. validação: replay 2026
i = find("Os ensaios de 2022 disponíveis no código não equivalem")
insert_after(i, P("Para 2026 existe um segundo instrumento, scripts/ensaio2026.py, que não reconstrói nada: a cada instante escolhido, monta a disputa com o último arquivo de cada município capturado até aquele instante, inclusive cidades grandes parcialmente apuradas, e roda o modelo sobre essa entrada. É a mesma informação que o coletor teve, de modo que a variante base deve reproduzir o diário; a seção 5.2 mostra que as larguras dos intervalos de 90% coincidem até a segunda casa decimal. As demais variantes multiplicam os choques de preferência por um fator k ou alteram a constante de inclinação, e constituem análise retrospectiva. Os instantes fotografados são os primeiros registros em que cada disputa cruzou 2, 5, 10, 17, 25, 35, 50, 65, 80 e 92% do eleitorado; um salto que atravessa vários alvos conta uma vez só, como recomendado na seção 4.7."))

# 10. seção 5.1
i = find("A análise histórica das Figuras 1 e 2 foi reproduzida")
set_text(i, "A análise histórica das Figuras 1 e 2 foi reproduzida nesta revisão. Também foram executados três módulos de testes (contratos, M2 e definição/auditoria), com 78 testes aprovados e nenhum ignorado em 40 segundos, no ambiente descrito no Apêndice A. Esse resultado demonstra funcionamento das verificações executadas; não substitui avaliação de erro eleitoral nem certifica toda a aplicação em produção.")
i = find("A configuração thresholds.yaml registra homologação de M3")
set_text(i, "Os relatórios do gate de 2022 (data/exports/ensaio2022/) foram localizados e lidos. A execução de 22 de setembro compara M2 e M3 contra o M0 em 55 disputas, com 3.000 simulações e semente 7: o M2 não passou, por perder do M0 na primeira rodada da ordem aleatória (2,10 contra 2,05 pp), e o M3 passou, vencendo em todas as 18 rodadas com cobertura de 81,6% (ordem real) e 81,2% (aleatória). A execução de 5 de outubro, com os mesmos parâmetros, repetiu M3 (82,7% e 81,7%) e acrescentou M3.3 e as variantes exploratórias ×0,85 e ×0,9. A configuração thresholds.yaml passou a registrar, às 17h53 de 5 de outubro, a homologação assinada do M3.3, com cobertura de 75,9% e 75,8%, dentro da tolerância de ±5 pontos. A Tabela 3 consolida esses números e a Figura 4 mostra o erro mediano por rodada. O termo ordem real deve ser lido com a ressalva semissintética da seção 4.7.")
i = find("Tabela 3")
set_text(i, "Tabela 3  Gate de 2022: erro mediano do líder e cobertura do intervalo de 80%, por modelo")
rows3 = [
    ["M0", "3,32 / 1,42 / 0,50", "2,05 / 0,97 / 0,21", "—", "referência (extrapolação dos votos apurados)", "gate 5/10"],
    ["M1", m1_real, m1_al, m1_cob, m1_gate, m1_fonte],
    ["M2", "2,38 / 1,08 / 0,30", "2,10 / 0,47 / 0,15", "78,2 / 80,8", "não passou: perde na rodada 1 aleatória", "gate 22/9"],
    ["M3", "1,69 / 0,62 / 0,21", "1,97 / 0,40 / 0,12", "82,7 / 81,7 (5/10); 81,6 / 81,2 (22/9)", "passou; em produção no 1º turno", "gate 5/10 e 22/9"],
    ["M3.1", "—", "—", "2018, governador: 82 / 81 (M3: 81 / 81)", "sem ganho sobre M3 em 2018 (erro r1 1,48 contra 1,45); não levado ao gate", "calibracao_m31_2018"],
    ["M3.2", "—", "—", "2018: 93 a 95", "sobrecobertura e erro maior (r1 1,54 a 1,99); descartado", "calibracao_m32_2018"],
    ["M3.3", "1,66 / 0,68 / 0,21", "1,96 / 0,39 / 0,12", "75,9 / 75,8", "passou (tolerância ±5 pp); homologado em 5/10 para o 2º turno", "gate 5/10"],
    ["M3 ×0,85; ×0,9", "≈ M3", "≈ M3", "78,3 / 77,6; 79,9 / 79,2", "exploratório", "gate2022-m33"],
    ["M3a, M3.4 (agro)", "—", "—", "—", "só replay de 2026: erro 0,81 contra 0,90 pp na faixa 0–6%, igual nas demais; sem gate", "ensaio2026-agro"],
]
els[i + 1] = TBL(["Modelo", "Erro mediano, ordem real, rodadas 1 / 5 / 9 (pp)", "Erro mediano, ordem aleatória, 1 / 5 / 9 (pp)", "Cobertura 80%, real / aleatória (%)", "Situação", "Fonte"], rows3, [1150, 1700, 1700, 1750, 2300, 1190], sz=16)
i = find("NR = número não recuperado")
set_text(i, "Erro mediano do líder entre as 55 disputas (1 presidencial, 27 de governador, 27 de senador), nas rodadas 1, 5 e 9 de 10; a rodada 10 (100%) fica fora. Os valores de M0 são da execução de 5/10; na de 22/9 foram 3,32 / 1,50 / 0,50 (real) e 2,05 / 0,97 / 0,21 (aleatória). M2 e M3 de 22/9 e M3 de 5/10 diferem na segunda casa por mudanças na reconstrução da ordem entre as execuções. O gate exige vencer o M0 em todas as rodadas e cobertura dentro de ±5 pontos do nominal. As linhas de M3.1 e M3.2 vêm de ensaios de 2018 (governador e senador, 54 disputas), com cobertura média do intervalo de 80% por disputa; não são comparáveis diretamente às demais.")
j = insert_after(i, P("Figura 4  Erro mediano do líder por rodada no ensaio de 2022", kind="caption"), FIG(S / "fig7_gate.png", "gate2022"),
                 P("Fonte: gate2022-m33.json (M0, M3, M3.3; 5/10/2026) e gate2022.json (M2; 22/9/2026)" + (" e gate2022-m1.json (M1; 5/10/2026)" if gm1 else "") + ", 55 disputas, 3.000 simulações, semente 7. Rodadas em décimos do número de municípios; a ordem real é a reconstrução descrita na seção 4.7.", kind="fonte"))
i = find("Não é possível indicar, nesta versão do artigo, um vencedor")
set_text(i, "O quadro histórico é consistente. A estratificação por UF e porte (M1) já reduz o erro em relação à extrapolação em todas as rodadas (2,07 contra 3,32 pp na primeira rodada real), mas seus intervalos cobrem apenas 57% e 64% para o nível nominal de 80%, e o modelo chegou a dar 90% ou mais a perdedores em oito disputas; a hierarquia territorial com choques de grupo (M2) corrige a cobertura (78% e 81%), mas não vence o M0 na primeira rodada aleatória; a inclinação histórica (M3) é o componente que produz o ganho decisivo, sobretudo na ordem real, em que o erro da primeira rodada cai de 3,32 para 1,69 pp e o da quinta de 1,42 para 0,62. Na ordem aleatória, em que a composição territorial dos votos iniciais é representativa, o ganho da primeira rodada é pequeno (2,05 para 1,97), o que indica que o M3 corrige principalmente o viés de chegada, não o ruído amostral. O M3.3 preserva o erro pontual do M3 e estreita os intervalos; sua cobertura de 75,9% fica no limite inferior da tolerância no ensaio de 2022, mas a seção 5.2 mostra que, na noite real de 2026, os intervalos do M3 foram largos demais, o que motivou a escolha.")
i = find("A escolha final deverá considerar erro pontual")
set_text(i, "A sequência de decisões deve ficar explícita: o M3 foi homologado em 22 de setembro e operou no primeiro turno; o fator 0,8 foi escolhido em 5 de outubro depois de observar o primeiro turno de 2026 e conferido no ensaio de 2022; o M3.3 foi assinado às 17h53 do mesmo dia para o segundo turno. Portanto, os resultados do M3.3 sobre 2026 são retrospectivos, e o segundo turno de 25 de outubro é a primeira oportunidade de avaliação prospectiva dessa variante, desde que modelo, limiares e regras de apresentação permaneçam congelados até o fim da apuração.")

# 11. seção 5.2 — painel empírico
i = find("[INSERÇÃO EMPÍRICA PENDENTE]")
set_text(i, "As Tabelas 4 e 5 e as Figuras 5 e 6 avaliam as previsões do diário original de 2026 contra o resultado de referência de cada disputa, sem recalcular nenhuma previsão. A referência é o último registro de cada disputa: para as 54 disputas estaduais, o estado resultado_oficial com 100% do eleitorado; para a presidencial, a última captura, com 99,991% apurado, já que o TSE não marcou o total nacional como finalizado enquanto o coletor esteve ativo. A unidade de análise é o primeiro registro em que cada disputa cruzou cada um dos dez alvos de apuração (540 registros), com os candidatos que terminaram com 5% ou mais (217 candidatos); erro é a mediana projetada menos a participação final, em pontos percentuais, sobre os votos a concorrentes. A contagem parcial (participação observada no instante) serve de comparação, por ser o que um leitor veria sem modelo.")
rows4 = [["0–6", 77, 295, "1,40", "1,96", "−0,25", "2,52", "96 / 99 / 100", "9,8 / 12,5", "10,0", "87 / 78", "0,053"],
         ["6–15", 77, 307, "0,92", "1,72", "−0,22", "1,91", "98 / 99 / 99", "8,8 / 11,2", "9,5", "96 / 88", "0,034"],
         ["15–30", 105, 417, "0,61", "0,84", "−0,18", "1,62", "100 / 100 / 100", "7,3 / 9,3", "7,3", "98 / 92", "0,028"],
         ["30–50", 62, 244, "0,46", "0,67", "−0,12", "1,18", "100 / 100 / 100", "5,6 / 7,1", "5,6", "95 / 90", "0,021"],
         ["50–80", 109, 431, "0,30", "0,42", "−0,06", "0,80", "100 / 100 / 100", "3,1 / 4,0", "3,1", "97 / 94", "0,012"],
         ["80–95", 105, 413, "0,12", "0,18", "−0,01", "0,31", "96 / 99 / 99", "0,8 / 1,0", "0,8", "98 / 96", "0,008"],
         ["95–99", 5, 21, "0,12", "0,19", "0,00", "0,13", "67 / 67 / 71", "0,2 / 0,3", "0,8", "100 / 100", "0,000"]]
j = insert_after(i, P("Tabela 4  Avaliação ex ante do diário de 2026 por estágio de apuração, 55 disputas", kind="caption"),
                 TBL(["Apurado (%)", "Reg.", "Cand.", "MAE (pp)", "RMSE (pp)", "Viés (pp)", "MAE da contagem (pp)", "Cobertura 80 / 90 / 95 (%)", "Largura 80 / 90 (pp)", "IS 80", "Vagas certas: proj. / contagem (%)", "Brier (vaga)"],
                     rows4, [900, 600, 640, 680, 680, 680, 900, 1150, 950, 660, 1200, 750], sz=16),
                 P("Primeiro registro por alvo de apuração (2, 5, 10, 17, 25, 35, 50, 65, 80, 92%) e candidatos com 5% ou mais no final. Viés = média de (mediana projetada − final), negativo quando o modelo subestima. Cobertura e largura referem-se aos intervalos centrais entre os quantis registrados (10–90, 5–95 e 2,5–97,5). Vagas certas: fração de registros em que o conjunto projetado (ordem das medianas) ou a contagem parcial coincide com os eleitos. Brier: probabilidade de ocupar vaga contra o desfecho. A faixa 95–99 tem só cinco registros, pois a maioria das disputas saltou de abaixo de 92% para 100%.", kind="nota"),
                 P("Tabela 5  Avaliação ex ante por cargo e estágio", kind="caption"),
                 TBL(["Cargo", "Apurado (%)", "Cand.", "MAE (pp)", "MAE da contagem (pp)", "Cobertura 80 (%)", "Largura 90 (pp)", "Vagas: projeção / contagem (%)", "Eleitos já com P ≥ 99% (%)"],
                     [["Presidente", "0–6", 4, "0,75", "4,56", "100", "7,0", "100 / 100", "0"], ["Presidente", "15–30", 4, "0,76", "4,50", "100", "5,7", "100 / 100", "0"], ["Presidente", "50–80", 4, "0,37", "2,82", "100", "2,8", "100 / 100", "0"], ["Presidente", "80–95", 4, "0,17", "1,26", "100", "0,8", "100 / 100", "100"],
                      ["Governador", "0–6", 103, "1,94", "3,26", "99", "16,4", "90 / 92", "38"], ["Governador", "15–30", 137, "0,89", "2,44", "100", "12,0", "96 / 92", "50"], ["Governador", "50–80", 145, "0,42", "1,17", "99", "5,1", "100 / 94", "75"], ["Governador", "80–95", 142, "0,17", "0,42", "94", "1,3", "100 / 98", "94"],
                      ["Senador", "0–6", 188, "1,12", "2,08", "95", "10,5", "83 / 61", "0"], ["Senador", "15–30", 276, "0,47", "1,18", "100", "8,0", "100 / 92", "13"], ["Senador", "50–80", 282, "0,23", "0,58", "100", "3,4", "94 / 93", "50"], ["Senador", "80–95", 267, "0,09", "0,24", "97", "0,9", "96 / 94", "86"]],
                     [1100, 950, 700, 850, 1100, 1000, 1000, 1550, 1500], sz=16),
                 P("Mesma base da Tabela 4. A última coluna é a fração de registros em que todos os eleitos já tinham probabilidade de vaga igual ou superior a 99%. Para presidente, vaga significa liderança do primeiro turno; nenhum candidato alcançou maioria e a classificação para o segundo turno (dois primeiros) nunca esteve em dúvida nos registros.", kind="nota"),
                 P("Figura 5  Cobertura e largura dos intervalos por estágio: diário ex ante e replay com fator k", kind="caption"), FIG(S / "fig4_cobertura.png", "cobertura2026"),
                 P("Fonte: painel A, diário de 2026 (Tabela 4) e ensaio2026.txt (cobertura de 80% da variante k = 0,8); painel B, diário e ensaio2026.txt (largura média do intervalo de 90%, variantes base, k = 0,8, 0,65 e 0,5). A coincidência entre a linha do diário e a do replay base confirma que o replay reproduz a entrada da noite.", kind="fonte"),
                 P("Figura 6  Calibração das probabilidades emitidas no primeiro turno de 2026", kind="caption"), FIG(S / "fig5_calibracao.png", "calibracao2026"),
                 P("Fonte: diário de 2026, registros nos alvos de apuração. Painel A: probabilidade de ocupar vaga, 2.128 pares candidato × registro; painel B: probabilidade de maioria absoluta, 744 pares nas 28 disputas executivas. Tamanho do ponto proporcional à raiz do número de casos; a diagonal é a calibração perfeita.", kind="fonte"),
                 P("Cinco resultados se sustentam. Primeiro, o erro pontual cai de forma monotônica com a apuração e é sempre menor que o da contagem parcial: 1,40 contra 2,52 pp abaixo de 6% apurado e 0,30 contra 0,80 entre 50% e 80%; na disputa presidencial a diferença é maior (0,75 contra 4,56 pp no início), porque a contagem do primeiro registro dava 55,5% a Flavio Bolsonaro e 34,1% a Lula, contra 47,0% e 45,2% no final, e a correção histórica trouxe as medianas para 44,0% e 47,5% já com 0,38% apurado. Segundo, o viés é negativo e concentrado nos eleitos: entre os candidatos que venceram, a mediana projetada ficou em média 1,23 pp abaixo do final com menos de 6% apurado e 0,61 pp entre 15% e 30%. O modelo regulariza a preferência local para a referência do estrato e, no início, puxa os vencedores para baixo; o replay com constante de inclinação reduzida (10 mil em vez de 50 mil) não alterou esse viés, o que sugere que ele decorre do encolhimento das preferências, não da entrada tardia da correção histórica."),
                 P("Terceiro, os intervalos foram largos demais. A cobertura empírica dos intervalos de 80% ficou entre 96% e 100% até 80% apurado, e a dos intervalos de 90% e 95% entre 99% e 100%; o interval score, que penaliza largura, é quase igual à largura, porque raramente há violação. O replay indica que o fator 0,8 (M3.3) reduz a largura em cerca de 19% e mantém cobertura de 92% a 100%, e que só com k = 0,5 a cobertura dos intervalos de 80% se aproxima do nominal (81% a 98%). O ensaio de 2022, porém, dá 75,9% para k = 0,8 e 60,5% para k = 0,5. A discrepância é esperada: no ensaio de 2022 cada município entra inteiro, em décimos, o que torna a ordem mais adversa que a chegada real, por seções, de 2026. A calibração dos intervalos depende, portanto, do processo de chegada assumido, e o fator 0,8 é um compromisso entre os dois cenários, não um valor identificado."),
                 P("Quarto, as probabilidades de evento são subconfiantes, o que é a contrapartida dos intervalos largos: candidatos com probabilidade emitida entre 0,20 e 0,40 ocuparam vaga em 12% dos casos, e aqueles entre 0,60 e 0,80, em 95% (Figura 6). A curva em S aparece também na probabilidade de maioria absoluta. Os escores de Brier foram 0,024 (vaga), 0,025 (maioria) e 0,026 (liderança). Nenhum registro nos alvos atribuiu 90% ou mais a candidato não eleito. Considerando todas as 6.617 emissões, isso ocorreu em cinco registros, todos antes das 17h35 e com menos de 0,04% apurado: probabilidade de maioria de 97,6% ao líder do Rio de Janeiro, que terminou com 49,27% e foi ao segundo turno, e de 92,6% a um candidato em Alagoas, que perdeu. O limiar de 10% de eleitorado apurado previsto em thresholds.yaml não impediu a emissão, pois o estado projecao_disponivel aparece no diário desde 0,01%; a regra de só exibir probabilidade a partir de 6% foi acrescentada em 5 de outubro (commit f13d80c) e não protegeu essas emissões."),
                 P("Quinto, na reta final os intervalos ficam estreitos demais: na faixa de 95% a 99% apurado, a cobertura dos intervalos de 80% cai para 67% (cinco registros nos alvos) e, considerando todas as emissões, para 75% entre 95% e 99% e 55% acima de 99%, com larguras de 0,01 a 0,2 pp. O erro absoluto nessas faixas é de centésimos de ponto e não tem consequência prática, mas o padrão justifica a trava de largura mínima introduzida em 5 de outubro, que deve ser avaliada como política de apresentação. A heterogeneidade entre cargos é a esperada: o Senado, com duas vagas e oito disputas decididas por menos de 2 pp (PA 0,19; RN 0,16; SE 0,90; AM 1,03; AC 1,35; RR 1,74; PB 1,85; MG 1,98), acertou o conjunto de eleitos em 83% dos registros iniciais, contra 61% da contagem, e só estabilizou a probabilidade de 90% para ambos os eleitos, em mediana, com 35,6% apurado; para governador, a mediana foi 4,4% (Tabela A2)."))

# 12. seção 5.3
i = find("O horizonte é o encerramento da totalização")
set_text(i, "O horizonte é o encerramento da totalização da disputa, visto a partir de cada horário de emissão. As previsões do primeiro turno foram preservadas no diário antes do resultado, e a Figura 7 as apresenta para quatro disputas escolhidas por regra fixada antes de inspecionar os erros: a presidencial e as três disputas estaduais com menor margem final entre o último eleito e o primeiro não eleito (governador do RN, 0,78 pp; senador do PA, 0,19 pp; senador do RN, 0,16 pp). Executar hoje o modelo com dados retrospectivos constitui replay; não cria evidência de uma previsão originalmente publicada.")
i = find("[FIGURA 3 PENDENTE DE DADOS ORIGINAIS]")
set_text(i, "Na disputa presidencial, nenhum candidato alcançou maioria: Flavio Bolsonaro terminou com 47,03% e Lula com 45,16% dos votos a concorrentes, diferença de 1,87 pp, e ambos foram ao segundo turno, como em outras sete disputas de governador (AC, AM, DF, ES, RJ, RN e TO). O primeiro registro, às 17h24 com 0,38% apurado, dava a Lula 78% de probabilidade de liderar, com medianas de 47,5% para Lula e 44,0% para Bolsonaro, enquanto a contagem mostrava 55,5% a 34,1% a favor de Bolsonaro: a correção pela inclinação histórica inverteu a leitura da contagem inicial, e inverteu demais. Às 17h42, com 3,8% apurado, as medianas já eram 48,4% e 43,8%, e a partir daí Lula ficou abaixo do final (45,16%) e Bolsonaro acima (47,03%) até a convergência. A probabilidade de liderança de Bolsonaro era 81% com 10,6% apurado (18h02), 91% com 52,8% (19h05) e só ultrapassou 99% a partir de 75,9% apurado (19h33); a contagem parcial, em contraste, mostrou o líder correto desde o primeiro registro. As faixas de 80% e 90% contiveram os dois resultados em todos os registros. O modelo foi deliberadamente cético quanto à liderança inicial, porque o ensaio de 2022 ensinou que o interior chega antes e favorece o candidato errado; em 2026 a contagem inicial acertou o líder, embora com margem muito exagerada, e a cautela custou duas horas de probabilidade abaixo de 90%.")
j = insert_after(i, P("Figura 7  Trajetória ex ante das projeções em quatro disputas do primeiro turno de 2026", kind="caption"), FIG(S / "fig3_exante.png", "exante2026"),
                 P("Fonte: diário de previsões, modelo M3 (m3-0.1), B = 1.500, registros com apuração entre 0% e 99,99%; horário de Brasília. Linha: mediana projetada; faixa escura: intervalo de 80%; faixa clara: intervalo de 90%; tracejado: participação final de referência. As linhas verticais marcam o primeiro registro com 10%, 50% e 90% do eleitorado apurado. Lacunas de emissão aparecem como trechos retos.", kind="fonte"),
                 P("Nas três disputas estaduais apertadas, o comportamento foi o desejável: as faixas permaneceram sobrepostas enquanto a decisão dependia dos municípios pendentes, e a probabilidade de 90% para o conjunto de eleitos só se estabilizou com 93,8% apurado no governo do RN (20h54), 98,9% no Senado do PA (21h30) e 92,1% no Senado do RN (20h46). No RN, o modelo chegou a dar 63% ao adversário com 1,5% apurado, e a mediana de Allyson caiu a 31% às 17h45, com cerca de 3% apurado; a faixa de 80%, contudo, continha o resultado final de 36,9%. A largura inicial (de 25% a 47% no painel do governador do RN) é o preço da calibração em disputas em que a ordem de chegada ainda não permite distinguir os candidatos; a Tabela A2 mostra que, nas 27 disputas de governador, a probabilidade de 90% para o eleito se estabilizou com mediana de 4,4% apurado e percentil 90 de 66,4%, e que a maior probabilidade atribuída a um não eleito, depois dos primeiros minutos, foi 93% (Alagoas, com 0,01% apurado)."))
i = find("O mesmo conjunto de quantis deve ser exibido")
set_text(i, text_of(els[i]) + " As duas regras acrescentadas em 5 de outubro, probabilidade só a partir de 6% apurado e trava de largura a partir de 95%, foram avaliadas aqui apenas por sua motivação empírica (seção 5.2); o diário guarda o valor bruto, e nenhuma delas foi aplicada às curvas da Figura 7.")

# 13. seção 5.4 — senado
i = find("No Senado, a probabilidade de ocupar uma das vagas")
set_text(i, text_of(els[i]) + " Em 2026, oito das 27 disputas de senador foram decididas por menos de 2 pp entre o segundo e o terceiro colocados, e nelas a projeção manteve as faixas sobrepostas até o fim; a utilidade do sistema nesses casos está em dizer que a vaga continua em aberto, não em antecipar o nome.")

# 14. conclusão
i = find("O objetivo de formalizar a metodologia foi alcançado")
set_text(i, "O objetivo de formalizar a metodologia foi alcançado, e a avaliação ex ante do primeiro turno de 2026 foi realizada sobre o diário original: o erro pontual foi menor que o da contagem parcial em todos os estágios e cargos, nenhuma certeza de 90% ou mais foi atribuída a candidato não eleito depois de 0,04% apurado, e as probabilidades e os intervalos se mostraram subconfiantes, com cobertura de 96% a 100% para o nível nominal de 80% até 80% apurado. A homologação histórica do M3 e a do M3.3 estão documentadas nos relatórios do gate de 2022, mas a escolha do fator 0,8 usou o primeiro turno de 2026; sua avaliação prospectiva depende do segundo turno.")
i = find("As principais limitações são a reconstrução semissintética")
set_text(i, "As principais limitações são a reconstrução semissintética dos ensaios históricos, cuja ordem de chegada por município inteiro é mais adversa que a real e produz calibrações diferentes das observadas em 2026; o uso de 2022 no desenvolvimento e do primeiro turno de 2026 na escolha do fator de largura; a referência presidencial tomada da última captura, com 42 seções não totalizadas; a ausência de vínculo explícito entre cada previsão e seu conjunto de capturas; a dependência entre previsões sucessivas, que impede tratar os 540 registros como observações independentes; e o fato de se tratar de uma única eleição. Choques políticos novos, decisões judiciais, mudanças de comparecimento e padrões de chegada não explicados pelas covariáveis também limitam a extrapolação.")
i = find("Trabalhos futuros incluem uma avaliação prospectiva")
set_text(i, "Trabalhos futuros incluem a avaliação prospectiva do M3.3 no segundo turno com protocolo congelado; a correção do viés inicial contra os vencedores, que o replay atribui ao encolhimento das preferências e não à constante de inclinação; a calibração dos intervalos por estágio e por processo de chegada, em vez de um fator único; a modelagem explícita do processo de chegada dos resultados, que a Figura 3 mostra ser heterogêneo entre UFs; e o teste de novas covariáveis com ablação e validação externa, uma vez que a participação agropecuária só melhorou a faixa inicial no replay. A inclusão de métodos conformais deve ser condicionada a ganho demonstrado e a hipóteses verificáveis. A contribuição central pretendida é um estudo replicável sobre o que a projeção consegue informar durante a apuração e sobre as condições em que essa informação merece confiança.")

# 15. referências
i = find("Tribunal Superior Eleitoral. (2026). EA20")
insert_after(i, P("Tribunal Superior Eleitoral. (2026). Divulgação de resultados: eleições 2026, 1º turno [Arquivos EA11 a EA20 capturados em 4 e 5 de outubro de 2026]. https://resultados.tse.jus.br/oficial/"))

# 16. apêndice A
i = find("A especificação executável é o código de Micucci")
set_text(i, "A especificação executável é o código de Micucci (2026), no commit indicado, e não apenas a descrição textual. A revisão estatística foi executada em Python 3.12.10 com NumPy 2.4.4 e Matplotlib 3.11.2 para as figuras (Python Software Foundation, s.d.; Harris et al., 2020), no computador de operação, em 5 de outubro de 2026. As dependências do projeto usam limites mínimos de versão; é necessário preservar o ambiente de produção em arquivo de versões exatas antes de alegar reprodução bit a bit. O repositório utiliza Python, NumPy, SQLAlchemy e FastAPI.")
i = find("Para os ensaios, preparar os dados esperados pelos scripts")
set_text(i, "Para os ensaios e a avaliação ex ante, fixar o commit e registrar a saída completa dos comandos abaixo. Os três últimos scripts, em scripts/artigo/, foram acrescentados nesta revisão: o primeiro avalia o diário contra a referência final e grava as tabelas desta seção; o segundo mede a coleta a partir do manifesto; o terceiro gera as Figuras 3 a 7. Conferir o catálogo de modelos aceitos em cada script antes de incluir variantes posteriores.")
i = find("python -m pytest tests/test_contratos.py")
j = insert_after(i, P("python scripts/gate2022.py --modelos m1 --sims 3000 --semente 7 --nome gate2022-m1", kind="code"),
                 P("python scripts/ensaio2026.py --variantes base,k0.8,k0.65,k0.5,k0.4,inc10k,inc10k+k0.65", kind="code"),
                 P("python scripts/artigo/avaliar_diario2026.py", kind="code"),
                 P("python scripts/artigo/medir_coleta2026.py", kind="code"),
                 P("python scripts/artigo/figuras_artigo.py", kind="code"))
i = find("O pacote de reprodução da análise de 2026 deverá conter")
set_text(i, "O pacote de reprodução da análise de 2026 deverá conter capturas brutas e manifesto (data/raw/oficial2026/, 3,1 GB comprimidos), catálogo completo do universo, diário imutável de previsões, versão e configuração por execução, sementes, número de simulações, relatórios detalhados e resultados oficiais de referência. O campo snapshot_set_id está nulo em todos os registros do diário; a vinculação de cada previsão ao conjunto de capturas foi reconstruída pelo horário de emissão e precisa ser implementada no motor. O depósito acadêmico deverá definir licença, acesso e versão persistente. Nesta revisão, os dados operacionais foram lidos diretamente dos diretórios data/ do computador de operação; o servidor local não foi consultado.")
# Tabela A2
pd = sorted(ex["por_disputa"].values(), key=lambda d: ({"presidente": 0, "governador": 1, "senador": 2}[d["cargo"]], d["uf"]))
def f1(x): return "—" if x is None else f"{x:.1f}".replace(".", ",")
rowsA2 = []
for d in pd:
    nome = d["disputa"].replace("presidente-br", "Presidente").replace("governador-", "Gov. ").replace("senador-", "Sen. ")
    nome = nome if nome == "Presidente" else nome[:-2] + nome[-2:].upper()
    rowsA2.append([nome, "; ".join(e.title() for e in d["eleitos"]), " / ".join(f"{v:.2f}".replace(".", ",") for v in d["final_eleitos"]), f1(d["margem_pp"]),
                   f"{f1(d['estab90_cov'])} ({d['estab90_hora']})" if d["estab90_cov"] is not None else "—", f1(d["estab99_cov"]), f1(d["contagem_cov"]), f"{d['pior_errada']:.2f}".replace(".", ",")])
j = insert_after(i, P("Tabela A2  Estabilização das probabilidades por disputa no primeiro turno de 2026", kind="caption"),
                 TBL(["Disputa", "Eleito(s) ou líder", "% final", "Margem (pp)", "P ≥ 90% estável desde (% apurado, hora)", "P ≥ 99% desde (%)", "Contagem estável desde (%)", "Maior P a não eleito"],
                     rowsA2, [1000, 2700, 1000, 800, 1500, 900, 1000, 900], sz=16),
                 P("Estável desde: menor apuração a partir da qual todos os eleitos (ou o líder, para presidente) mantiveram probabilidade de vaga no nível indicado até o fim, segundo o diário; contagem: idem para a ordem dos votos observados. Margem: diferença entre o último eleito e o primeiro não eleito. Maior P a não eleito: máxima probabilidade de vaga atribuída a quem não se elegeu, em qualquer registro.", kind="nota"))

# 17. apêndice B
i = find("Tabela B1")
t = i + 1
replace_in_table(t, "data/previsoes/previsoes-oficial2026.jsonl e demais diários existentes.", "Localizado e avaliado (26.835 registros). Resta depositar cópia imutável com hash.")
replace_in_table(t, "data/raw/oficial2026/ completo.", "Localizados: 894.695 entradas de manifesto em dois dias. Resta implementar snapshot_set_id no motor.")
replace_in_table(t, "data/exports/ensaio2022/gate2022.json e gate2022.md; ensaios de todas as variantes.", "Localizados e lidos (gate2022, gate2022-m33, gate2022-largura, calibrações de 2018). O gate do M1 foi executado nesta revisão; falta o gate das variantes agro.")
replace_in_table(t, "data/exports/ensaio2026/ e relatórios de calibração disponíveis.", "Localizados (ensaio2026, ensaio2026-agro); são replay retrospectivo e foram rotulados como tal.")
replace_in_table(t, "Configurações usadas na noite, logs, histórico de alterações e ambiente exato.", "Modelo, versão e hash constam em cada registro do diário; falta congelar o ambiente (pip freeze) e guardar config/operacao.anterior/.")
replace_in_table(t, "EA20 final reconciliado por disputa, horário e destino dos votos.", "Estaduais: último registro oficial. Presidencial: última captura com 42 seções pendentes; conferir contra o resultado proclamado pelo TSE.")
replace_in_table(t, "Arquivo de aptos, data de extração, códigos e universo esperado.", "Universo veio do EA20 (5.571 municípios + 186 localidades do exterior); documentar que o cadastro do SIG não foi usado.")
i = find("Antes de concluir a validade operacional, verificar a execução efetiva")
set_text(i, "Antes de concluir a validade operacional, verificar a execução efetiva dos controles de cobertura, contabilidade, atraso e erro Monte Carlo. A existência de limiares em YAML não demonstra que todos sejam aplicados na rota de publicação: o diário registra o estado projecao_disponivel desde 0,01% do eleitorado apurado, apesar do limiar de 10% em thresholds.yaml, e as cinco emissões com certeza indevida da seção 5.2 ocorreram nessa janela. Conferir ainda o desempate: a probabilidade de liderança rateia empates, mas a seleção de vagas usa ordenação estável e pode depender da ordem dos candidatos em empates exatos. Para cargos executivos, métricas que chamam o primeiro colocado de eleito precisam ser renomeadas ou restringidas ao evento adequado; nesta revisão, vaga para presidente significa liderança do primeiro turno.")
i = find("As notas de desenvolvimento e alguns textos introdutórios do projeto estão defasados")
set_text(i, "As notas de desenvolvimento e alguns textos introdutórios do projeto estão defasados em relação ao código atual (o README ainda descreve a projeção com probabilidade como desligada). Na submissão, substituir afirmações genéricas de homologação pela identificação precisa do modelo, do relatório e da data: M3 homologado em 22/9/2026 (gate2022.md, 81,6% e 81,2%); M3.3 homologado em 5/10/2026 às 17h53 (gate2022.md acrescido, 75,9% e 75,8%). Para o segundo turno, registrar antes do início da apuração o modelo, o hash de thresholds, as regras de exibição (6% e trava de 95%) e o número de simulações. Informar autoria, afiliação, financiamento, conflitos de interesse e disponibilidade de dados conforme o periódico. Caso tenha havido assistência de inteligência artificial no desenvolvimento ou na redação, declarar seu uso conforme a política editorial, mantendo a responsabilidade científica dos autores.")

# ---------------- gravar ----------------
(U / "word/document.xml").write_text(head + "".join(els) + tail, encoding="utf8")
(U / "word/_rels/document.xml.rels").write_text(rels, encoding="utf8")
print("elementos:", len(els), "| M1:", m1_gate)
