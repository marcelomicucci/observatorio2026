# Achados nos dados reais do TSE

Tudo aqui foi verificado contra payloads baixados do servidor de simulado do
TSE em **20/09/2026** e contra a base de eleitorado fornecida. As fixtures
estão em `tests/fixtures/simulado2026/` e os testes em
`tests/test_contratos.py` travam cada afirmação.

Os seis primeiros achados mudam decisões da especificação.

---

## 1. O crosswalk TSE↔IBGE é oficial, não precisa ser construído

O §2.1 previa criar a tabela TSE↔IBGE por nome, com revisão de ambiguidades.
Não é necessário: o **EA12 traz `cdi`**, o código IBGE, preenchido nos 5.571
municípios brasileiros, todos com 7 dígitos.

```json
{"cd": "01120", "cdi": "1200013", "nm": "ACRELÂNDIA", "c": "n", "z": ["0008"]}
```

Localidades do exterior têm `cdi` vazio, o que é correto — não são municípios.

O pareamento por nome continua existindo, mas só para conciliar a base de
eleitorado recebida, e sempre como *candidato* confirmado por código.

## 2. O EA15 elimina o gargalo de 14 minutos

O §8.1 calculava, corretamente, que uma varredura municipal dos três cargos
são ~16,9 mil arquivos — 14 minutos a 20 req/s — e concluía que prometer
atualização municipal em segundos seria inadequado.

O EA15 de cada UF lista **todos** os municípios dela com `and` (andamento) e
`ht` (hora da totalização). Logo:

```
2 eleições × (1 EA14 + 27 EA15) = 56 requisições  →  detecta o que mudou
```

A 20 s de cadência isso custa 2,9 req/s, **independente do tamanho do país**,
e o EA20 municipal só é buscado para quem mudou.

A varredura completa continua existindo como rede de segurança: a FAQ avisa
que EA14/EA15 podem chegar **antes ou depois** do EA20, então a detecção
acelera mas não é fonte de verdade.

## 3. A cadência proposta no §8.1 não cabia no orçamento

Executando o cálculo com os números reais do catálogo:

| | requisições | req/s |
|---|---:|---:|
| reconciliação a cada 900 s (proposta original) | 16.897 | **18,8** |
| acompanhamento a cada 20 s | 57 | 2,9 |
| controle a cada 20 s | 83 | 4,2 |
| **piso contínuo** | | **25,8** |
| teto interno proposto | | 20,0 |

Saldo negativo de 5,8 req/s, antes de qualquer EA20 movido por mudança.

Correções: reconciliação **rotativa** (uma fatia a cada 5 s, em vez de rajada)
e ciclo de 1800 s. O piso cai para 16,4 req/s, com 3,6 de folga. A trava
`CollectorConfig.assert_budget` recusa iniciar o coletor com cadência que não
caiba — esse erro não pode voltar em silêncio.

A folga de 3,6 req/s **não basta para a noite de apuração**. O valor final de
`max_rps` sai da medição dos simulados, que é o que o §8.1 pediu.

## 4. O percentual que o TSE exibe não é o que decide o primeiro turno

O denominador de `pvap` é a **soma dos votos de todos os candidatos**,
incluindo os anulados e os sub judice. Verificado com erro **exatamente zero**
nos três payloads; contra `vv` o erro chega a 1,69 pp.

| denominador | valor nacional | erro máximo vs `pvapn` |
|---|---:|---:|
| `vv` (válidos) | 100.982.116 | 1,689167573 pp |
| `vvc` (válidos computados) | 120.704.576 | 0,010366753 pp |
| **Σ `vap`** | **120.560.949** | **0,000000000 pp** |
| `tv` (total de votos) | 138.863.131 | 1,148276076 pp |

O mesmo candidato, sob bases diferentes:

| candidato 57 (`Anulado sub judice`, 10.503.573 votos) | |
|---|---:|
| sobre Σ `vap` — o que o TSE mostra | 8,70% |
| sobre `tv` | 7,56% |
| sobre `vv` | **indefinido** — ele não compõe `vv` |

A maioria absoluta se apura sobre **votos válidos**. A API exige a base
declarada em toda resposta e devolve `null` com motivo quando numerador e
denominador não pertencem ao mesmo universo.

### O arquivo usa cinco denominadores ao mesmo tempo

Decodificados com erro exatamente zero nos três payloads:

| campo | numerador / denominador | responde |
|---|---|---|
| `v.pvvcn` | `vvc / tv` | quanto do total foi computado |
| `v.pvvn` | `vv / vvc` | quanto do computado é válido |
| `v.pvann` | `van / vvc` | fração anulada |
| `v.pvansjn` | `vansj / vvc` | fração sub judice |
| `v.pvbn` | `vb / tv` | brancos sobre o total |
| `v.ptvnn` | `tvn / tv` | nulos sobre o total |
| `v.pvnomn` | `vnom / vv` | nominais sobre válidos |
| `cand.pvapn` | `vap / Σvap` | percentual do candidato |

A armadilha é concreta. No arquivo nacional, `v.pvansjn` vale **8,701884674**
e o `pvapn` do candidato 57 vale **8,712251427**. Os dois se referem ao mesmo
bloco de 10.503.573 votos e diferem porque o denominador é outro. Errei
exatamente isso ao escrever o primeiro teste da API — daí a invariante
`test_mapa_completo_de_denominadores_do_bloco_v`, que trava os oito.

## 5. `e='s'` não significa "eleito"

No arquivo nacional de Presidente, dois candidatos têm `e='s'` — e os dois
têm `st='2º turno'`. O campo marca **classificação**, não eleição.

Tratar `e` como "eleito" colocaria um selo de eleito em quem foi ao segundo
turno. O sistema usa a situação oficial (`st`) literal, e só chama de eleito
quem o TSE diz `Eleito`.

Relacionado: o arquivo **municipal** de Senador traz `st='Eleito'`. O status é
da disputa na UF, replicado para baixo. A API só devolve
`eleito_segundo_tse` no arquivo da abrangência que decide; em qualquer outro
território o campo é `null` — nunca `false`, porque "não se decide aqui" é
diferente de "não foi eleito".

## 6. `A_pendente` sai dos estados oficiais, mas com ambiguidade residual

O §4.2 proíbe calcular o pendente como "aptos × percentual de seções
restantes". Os payloads permitem derivá-lo, com duas decomposições exatas:

```
te  = esi + esni      total       = instaladas + não instaladas
esi = esa + esna      instaladas  = apuradas   + não apuradas
te  = est + esnt      total       = totalizadas + não totalizadas
c + a = esa           comparecimento e abstenção se medem contra
                      o eleitorado das seções APURADAS
```

A última confirma empiricamente o §4.3: comparecimento nunca se mede contra o
eleitorado cadastrado.

Eleitores de seções **não instaladas** não são voto futuro. Mas o contrato não
expõe a interseção entre "não totalizada" e "não instalada", então
`A_pendente` é um **intervalo**:

```
superior:  esnt
inferior:  esnt − esni
```

No arquivo nacional a folga é de 267 eleitores em 163 milhões (0,00016%). Num
município pequeno pode pesar, e é lá que importa. A simulação sorteia dentro
do intervalo, transformando a ambiguidade do contrato em largura de intervalo
em vez de falsa precisão.

---

## Achados menores, mas que quebram código

**Entidade HTML em maiúsculas.** O EA12 escreve `OLHO D&APOS;AGUA`. Sem
unescape, 45 municípios brasileiros não pareiam com nada. `html.unescape` não
reconhece `&APOS;` maiúsculo — é preciso normalizar antes.

**Nome não é chave.** `PAU D'ARCO` existe em PA e TO: 45 municípios com
apóstrofo, 44 nomes distintos.

**`tpabr` é inconsistente entre arquivos.** O EA20 usa `mu` para município; o
EA15 usa `mun`. O parser normaliza e preserva o original.

**O EA12 é por eleição e os catálogos não são equivalentes.** O da eleição
federal traz 184 localidades do exterior; o da estadual traz zero —
corretamente, porque não há governador nem senador em ZZ. Conciliar a base de
eleitorado contra um catálogo só manda as 186 linhas de exterior inteiras
para quarentena.

**`vvc` não é reconstruível somando candidatos.** No arquivo nacional, Σ `vap`
fica 143.627 votos abaixo de `vvc`, e a diferença é exatamente
`van − Σ vap(Anulado)`: existem votos anulados não atribuídos a candidato
listado.

**No Senado, `q` não é 2.** No simulado, as escolhas válidas por comparecente
ficaram em **1,70** — não 2,00. Supor duas escolhas válidas por eleitor
superestimaria o total de votos em 17%.

---

## Reconciliação da base de eleitorado recebida

Todos os números declarados no §2.1 conferem exatamente:

| verificação | valor |
|---|---:|
| linhas de localidade | 5.757 |
| linhas Brasil / Exterior | 5.571 / 186 |
| aptos, incluindo exterior | 158.745.463 |
| aptos no exterior | 918.876 |
| aptos no Pará | 6.265.355 |
| soma da coluna de seções | 517.179 |
| duplicações UF + nome | 0 |

`sha256` do zip: `bd19da79e4d3e3d071ae24fd1bf5bced1f1129f7f4dd28cef5280d21ed1cc2ca`

Contra a união dos catálogos EA12:

```
linhas conciliadas          5.753 de 5.757
aptos conciliados     158.745.459 de 158.745.463
residual                        4 eleitores  (0,000003 pp)
```

O Brasil fecha **exatamente**: 5.571 = 5.571. Toda a divergência está no
exterior e é de versão — o CSV é de 20/09, o catálogo do simulado é de 14/09:

- no CSV e não no EA12: Dacca, Pyongyang, Saint Johns, Santa Elena de Uairén
  (1 apto cada);
- no EA12 e não no CSV: Monróvia, Vaticano.

Quatro eleitores não liberam a projeção pelo critério do §4.7. As saídas
previstas são corresponder 100% ou modelar o residual explicitamente — e
nenhuma delas é descartá-lo.

**As contagens por UF batem com os totais oficiais de municípios** (MG 853,
SP 645, RS 497, BA 417, PR 399, SC 295, GO 246, PI 224, PB 223, MA 217,
PE 185, CE 184, RN 167, PA 144, MT 142, TO 139, AL 102, RJ 92, MS 79, ES 78,
SE 75, AM 62, RO 52, AC 22, AP 16, RR 15), mais Brasília. O EA14 confirma:
`munf` somado nas 27 UFs = 5.571.

**A soma da coluna de zonas (6.292) não é contagem de zonas.** O EA12 permite
contar as distintas: **2.639**. Uma zona pode abranger mais de um município,
exatamente como o §2.1 advertia.
