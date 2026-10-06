# Metodologia de projeção

Estado em 06/10/2026: modelo **M3.3** em produção, homologado em 05/10/2026,
depois de uma noite real (1º turno, 04/10). Este documento descreve o método
como ele está no código; o histórico do desenho da família M3 e as
referências bibliográficas ficam em [M3-DESENHO.md](M3-DESENHO.md).

## A decisão central

Projetar o voto dos territórios que faltam e **depois** agregar. Nunca
extrapolar o percentual já contado.

A razão é que a ordem de chegada não é aleatória. A apuração é uma sequência
administrativa: municípios pequenos e do interior tendem a fechar antes das
capitais, e eles não votam como a média do que já chegou. Extrapolar o
percentual contado assume, sem dizer, que o que falta é uma amostra aleatória
do que veio — e não é.

No 1º turno de 2022, na ordem real de chegada, a contagem só mostrou o
vencedor da eleição presidencial com 77% dos votos válidos apurados. É esse o
caso que o método existe para resolver.

## A identidade

Para unidade `g`, candidato `k`, simulação `b`:

```
R[g,b]         = A_pendente[g,b] × comparecimento[g,b] × taxa_válida[g,b]
V_final[g,k,b] = V_observado[g,k] + R[g,b] × preferência[g,k,b]
```

Três grandezas estimadas **separadamente**, porque se movem por razões
diferentes: quanta gente ainda vota, quanto desse voto é válido, e para quem
vai.

O voto já contado é fixo — entra igual em todas as simulações. Retotalização
cria versão nova, não ruído.

## `A_pendente`

Derivado dos estados oficiais das seções, não de "aptos × percentual de
seções restantes" (`engine/pending.py`; identidades verificadas em
[ACHADOS.md](ACHADOS.md)). Em resumo:

- eleitores de seções **não instaladas** não são voto futuro;
- o contrato não separa "não totalizada" de "não instalada", então
  `A_pendente` é um intervalo `[esnt − esni, esnt]`;
- a simulação sorteia dentro do intervalo.

No arquivo nacional a folga é de centenas de eleitores em 160 milhões; num
município pequeno pode pesar, e é lá que importa. Onde o contrato não
resolve, a incerteza cresce — a estimativa não finge precisão que o dado não
tem.

## Por que os choques são compartilhados

Este é o ponto que mais afeta a largura dos intervalos.

Se cada município sorteasse seu desvio de forma independente, a variância do
total nacional cairia com √5.571 e o intervalo publicado sairia absurdamente
estreito.

O erro real é dominado por um desvio **comum**: se os lugares que faltam
votam diferente do previsto, eles tendem a votar diferente *na mesma
direção*. Por isso o desvio é decomposto:

```
logit(comparecimento) = logit(ĉ) + σ_nac·z_nac + σ_uf·z_uf + σ_loc·z_loc
```

Só `z_loc` é independente entre unidades. A preferência usa a mesma estrutura
em escala log, com softmax, e ganha um quarto nível a partir do M2: o choque
de **grupo** (capital ou interior da UF).

Desvios em uso (`engine/simulate.py`):

| | nacional | UF | local |
|---|---|---|---|
| comparecimento (logit) | 0,06 | 0,05 | 0,10 |
| taxa de válidos (logit) | 0,03 | 0,03 | 0,06 |
| preferência (log) | 0,05 | 0,06 | 0,12 |

No M3.3 os três desvios de preferência são multiplicados por 0,8.

Consequências operacionais:

- **quantis saem das somas simuladas**, nunca somando quantis municipais;
- **percentual é calculado depois da soma**, com o denominador da mesma
  simulação.

## Os modelos

Todos alimentam a mesma simulação; o que muda é como cada um estima a
preferência, o comparecimento e a incerteza de cada unidade pendente
(`engine/models.py`). O modelo em produção é uma chave de
`config/operacao.yaml`, trocada na aba Configuração.

| modelo | situação |
|---|---|
| M0 | referência do gate; nunca vai ao ar |
| M1 | aposentado; fica para comparação |
| M2 | disponível; não passou no gate |
| M3 | passou no gate em 22/09; foi o modelo do 1º turno |
| **M3.3** | **em produção**; passou no gate em 05/10 |
| M3.1, M3.2 | experimentais; sem gate, fora da produção |
| M3a, M3.4 | experimentais; passam no gate sem ganho consistente; não adotados |

### M0 — extrapolação ingênua

Aplica o comparecimento, a taxa de válidos e a preferência nacionais já
contados a todo o eleitorado pendente. Existe para ser batido: um modelo que
não vence a extrapolação ingênua não merece ir ao ar.

### M1 — município encolhido para o estrato

Cada unidade usa o próprio dado, encolhido na direção do estrato (UF × porte
eleitoral; a marca de capital só é lida a partir do M2):

```
w = n_observado / (n_observado + k₀)        k₀ = 500 votos
preferência = w · local + (1 − w) · estrato
```

Um município com 40 votos contados e 12 mil pendentes, usando a preferência
local crua, injetaria ruído amplificado 300 vezes. Unidade ainda zerada usa a
estimativa do grupo, **nunca zero**, com incerteza ampliada.

O ensaio de 2022 mostrou os dois defeitos do M1: a cobertura do intervalo de
80% ficou em 54–59%, e no presidente ele chegou a dar 91% de chance a quem
perdeu. A causa: quando a capital ainda não chegou, o M1 usa a média do que
já foi contado na UF — que no começo da noite é quase só interior.

### M2 — hierárquico com o grupo da capital

Município → porte → **grupo** → UF. O grupo é *capital* (a capital e as
cidades do arranjo populacional do IBGE em torno dela, na mesma UF) ou
*interior* (`engine/territorio.py`, `config/arranjos_capitais.json`).

Duas diferenças em relação ao M1:

1. **Cada unidade pendente olha para o próprio grupo.** O total da UF sai da
   soma das unidades, então cada grupo pesa pelo eleitorado dele — conhecido
   antes da eleição —, e não pelo que já chegou.
2. **O grupo carrega um choque próprio**, compartilhado por suas unidades,
   com desvio `0,25 · √(1 − f)`, onde `f` é a fração do eleitorado do grupo
   já apurada. Grupo vazio: incerteza cheia. Grupo quase todo contado:
   incerteza quase nula. O intervalo começa largo e estreita conforme o grupo
   chega, em vez de nascer estreito e errado.

O 0,25 foi calibrado em 2018 (0,20 dava 76–77% de cobertura; 0,30, 84–85%). A
distância bruta entre capital e interior, medida em 2018, é de 0,49 em log —
o M1 supunha 0,06.

Em 2022 (teste) a cobertura subiu para 77–82% e a maior chance dada ao
perdedor na eleição presidencial caiu de 91% para 56%. No gate completo, o M2
perdeu para o M0 em uma rodada (ordem aleatória, rodada 1: 2,10 × 2,05 pp) e
não foi homologado.

### M3 — a inclinação histórica de cada município

O M2 estima o que falta só a partir do que chegou na noite. Ele ignora a
informação mais forte disponível antes da eleição: **como cada município
votou antes**.

A *inclinação* de um município é a fatia do PT nos votos válidos do 2º turno
presidencial da eleição anterior (`data/historico/inclinacao_<ano>.json`). É
uma régua só, para todos os cargos: nenhum candidato precisa ser ligado à mão
ao passado. A inclinação de 2018 tem correlação de 0,97 com o voto de Lula
por município no 1º turno de 2022; prever cada município pela média nacional
erra 12,5 pp, pela inclinação, 2,5 pp.

O M3 é o M2 com uma correção:

1. para cada candidato, uma regressão ponderada do log da fatia observada na
   inclinação, só com os municípios já contados — a *resposta à inclinação*,
   medida na própria noite e encolhida para zero com 50 mil votos (pouca
   apuração → M3 ≈ M2);
2. cada pendente é corrigido:
   `log p[g,k] = log p_estrato[k] + resposta[k] · (x[g] − x_estrato)`,
   limitado a ±1,5;
3. município sem histórico fica com o M2.

Se o que chegou primeiro pende para um lado, o modelo sabe que o que falta
pende para o outro. A incerteza é a do M2.

### M3.3 — o M3 com a faixa 20% mais estreita

Na noite real do 1º turno, a faixa de 80% do M3 conteve o resultado em
96–100% dos casos: larga demais. O M3.3 mantém o número central do M3 e
multiplica os choques de preferência por `FATOR_M33 = 0,8`.

O fator saiu de três medidas, com a cobertura da faixa de 80%:

| fator | 2026 (noite real) | 2022 (ensaio) | 2018 (ensaio) |
|---|---|---|---|
| 1,0 (M3) | 96–100% | 82% | 81–82% |
| **0,8** | 92–100% | 76% | 74–76% |
| 0,65 | 89–99% | 70% | 67–70% |
| 0,5 | 81–98% | 60% | 57–61% |

O ensaio sintético é mais duro que a noite real. O 0,8 é o maior
estreitamento que ainda passa na tolerância do gate em 2022; a margem é
pequena, e o fator será revisto com duas noites reais, depois do 2º turno.

### Experimentais

Ficam no código, selecionáveis para ensaio, e não entram em produção sem
gate:

- **M3.1** — resposta à inclinação freada pela própria incerteza e puxada
  para a média do bloco ideológico do candidato. Mira o candidato regional
  que não segue o eixo presidencial.
- **M3.2** — calibrador dinâmico: cada município já contado é previsto como
  se estivesse pendente, e o desvio reescala a incerteza. No ensaio de 2018
  ficou neutro. O erro de cobertura vem de desvios compartilhados (o grupo ou
  a UF inteira se afastando), que um resíduo por município não enxerga.
- **M3a e M3.4** — PIB agropecuário do município (IBGE, 2021) como segunda
  covariável: o M3a sobre o M3, o M3.4 sobre o M3.3. O resíduo da inclinação
  acompanha o agro (correlação −0,38 no presidente), mas no replay de 2026 o
  erro não caiu (0,458 × 0,459 pp). No gate de 2022 (06/10) as duas passam,
  sem ganho consistente: melhoram a primeira rodada da ordem aleatória e
  empatam ou pioram um pouco na ordem real. Não foram adotadas.

## Senado: duas vagas, duas escolhas

Em 2026 cada eleitor faz até duas escolhas. O total de escolhas válidas
restantes é `C × q`, com `q` entre 0 e 2 — **não** `2 × C`. O `q` observado
foi de 1,79 no Acre em 2018 e de 1,70 no simulado; supor 2 superestimaria o
total em 12% a 18%.

Restrições impostas **em cada simulação**:

- votos adicionais de um candidato ≤ comparecentes restantes (ninguém dá dois
  votos válidos ao mesmo candidato);
- soma dos votos adicionais ≤ 2 × comparecentes restantes.

O excedente que estoura o teto é redistribuído entre quem tem folga.

**O que o modelo não faz:** recuperar quais pares de candidatos cada eleitor
escolheu. As contagens agregadas não identificam pares; o que está
implementado são as marginais conjuntas restritas.

Saídas: probabilidade de ficar entre os dois primeiros e margem do segundo
para o terceiro — a pergunta que decide a disputa. Percentuais sobre escolhas
válidas somam 100%; a votação de um candidato nunca é dividida por dois.

## A base da maioria absoluta

A maioria absoluta do primeiro turno se apura sobre **votos a concorrentes**:
válidos, anulados e anulados sub judice (`vv + van + vansj = vvc`). É o que
define o `leiame` do repositório de dados do TSE, coerente com a
Constituição, art. 77 §2º, que exclui do denominador apenas brancos e nulos —
voto *anulado* de candidato não é voto *nulo* do eleitor.

O percentual que o TSE exibe é outro: é sobre a soma dos votos nominais.
Confundir as bases pode anunciar uma vitória em primeiro turno que não
existe. A regra está em `denominators.BASE_MAIORIA_ABSOLUTA`, na
probabilidade de maioria do motor e na base padrão da API.

## O que vai à tela

O diário guarda sempre o número que o modelo produziu. A publicação passa por
regras fixas, decididas com medida (`engine/nivel.py`, `engine/chamada.py`).

**Nível da faixa.** 80% ("tendência") até 15% do eleitorado apurado; 90%
("projeção") daí em diante. A tela sempre diz qual vale. O degrau de 95% foi
testado e não entrou: no ensaio de 2018 a faixa de 95% conteve o resultado em
92% dos casos — a cauda da simulação é fina demais para prometer 95%.

**Probabilidade só a partir de 6% apurado.** Com 0,0% apurado, no 1º turno, o
modelo deu 97,6% de chance de maioria a quem foi ao 2º turno e 93% a quem
perdeu. Mediana e faixa continuam saindo antes disso.

**Acima de 95% apurado a faixa para de encolher.** Ela caía a 0,1 pp e
deixava de conter o resultado (64% de cobertura acima de 99% apurado), porque
o que falta entrar não se parece com o que já entrou.

**Vencedor projetado.** A afirmação mais cara do produto. Exige, ao mesmo
tempo: probabilidade ≥ 99%; o mesmo nome acima do limite em três registros
seguidos; e pelo menos 20% apurado. Com uma vaga, "vencedor" quer dizer
maioria absoluta; liderar com folga sem maioria é "2º turno projetado", com
os dois nomes. A régua veio do ensaio de 2018, em que houve um caso de 99%
dado a um perdedor — no Senado, em ordem aleatória, e por menos de três
registros.

**Definição matemática.** Não é projeção. Ver
[DEFINICAO-E-AUDITORIA.md](DEFINICAO-E-AUDITORIA.md).

## Validação

### O ensaio

`scripts/ensaio2022.py` refaz a noite de uma eleição encerrada: os municípios
entram em dez rodadas, e o modelo projeta a cada rodada sobre o que já
"chegou". Roda duas vezes:

- na **ordem real de chegada** de 2022 (hora de totalização de cada
  município, dos arquivos de acompanhamento do TSE) — é o teste;
- em **ordem aleatória** — é a régua: o caso benigno, que a extrapolação
  ingênua também acerta.

Cidades grandes entram fatiadas por zona ao longo da noite. As disputas de
2018 usam a ordem de 2022, porque a ordem de 2018 não está mais publicada.

A divisão é fixa: **calibra-se em 2018, testa-se em 2022.** Nenhum parâmetro
é ajustado em 2022.

### O gate

`scripts/gate2022.py` roda o ensaio nas 55 disputas de 2022 (presidente, 27
governos, 27 vagas ao Senado), nas duas ordens, rodadas 1 a 9, com 3.000
simulações. Para ser homologado, o modelo precisa:

1. vencer o M0 no **erro mediano em todas as 18 rodadas** — nenhuma derrota;
2. entregar a faixa de 80% com cobertura a no máximo 5 pp do nominal.

| modelo | vence o M0 | cobertura real / aleatória | 90%+ a um perdedor | resultado |
|---|---|---|---|---|
| M2 | 17 de 18 | 78,2% / 80,8% | 1 disputa | não passou |
| M3 | 18 de 18 | 81,6% / 81,2% | nenhuma | passou (22/09) |
| M3.3 | 18 de 18 | 75,9% / 75,8% | nenhuma | passou (05/10) |
| M3a | 18 de 18 | 83,1% / 81,0% | nenhuma | passou (06/10); não adotado |
| M3.4 | 18 de 18 | 77,2% / 75,4% | nenhuma | passou (06/10); não adotado |

Na ordem real, o erro mediano do M3 é cerca de metade do erro do M0 (rodada
1: 1,69 × 3,32 pp; rodada 5: 0,68 × 1,50 pp).

Ressalva registrada: os parâmetros do M3 foram fixados antes do gate, mas um
teste parcial em três estados de 2022 havia sido visto.

### A homologação

Passar no gate não liga nada sozinho. `obs2026 homologar` (ou a aba
Configuração) grava em `config/thresholds.yaml` a evidência, o nome de quem
assina e a data. A homologação vale só para o modelo assinado: trocar o
modelo em produção exige gate e assinatura novos. Sem homologação, o produto
opera em *cenário exploratório* — faixas no ar, nenhuma probabilidade.

O gate antigo, por cortes de cobertura sobre uma disputa
(`engine/backtest.py`), continua no código e nos testes.

## O 1º turno de 2026, conferido

O diário da noite de 04/10 — 26.835 registros, 55 disputas, modelo M3 —
contra o resultado final (`scripts/artigo/avaliar_diario2026.py`). Primeiro
registro de cada disputa em cada faixa de apuração:

| apurado | registros | erro médio da projeção | erro médio do apurado | cobertura da faixa de 80% | largura da faixa |
|---|---|---|---|---|---|
| 0–6% | 77 | 1,40 pp | 2,52 pp | 96% | 9,8 pp |
| 6–15% | 77 | 0,92 | 1,91 | 98% | 8,8 |
| 15–30% | 105 | 0,61 | 1,62 | 100% | 7,3 |
| 30–50% | 62 | 0,46 | 1,18 | 100% | 5,6 |
| 50–80% | 109 | 0,30 | 0,80 | 100% | 3,1 |
| 80–95% | 105 | 0,12 | 0,31 | 96% | 0,8 |
| 95–99% | 5 | 0,12 | 0,13 | 67% | 0,2 |

No presidente, com menos de 6% apurado: 0,75 pp de erro na projeção contra
4,56 pp no apurado.

O que a noite ensinou:

- **O número central é bom desde o começo**; a projeção erra de metade a um
  terço do que erra o percentual apurado.
- **As faixas eram largas demais** até 95% apurado → M3.3.
- **E estreitas demais no fim** → trava de largura acima de 95%.
- **Probabilidade com quase nada apurado engana** → limiar de 6%.
- Depois de 0,04% apurado, nenhuma probabilidade de 90% ou mais foi dada a
  quem perdeu.

Isto é avaliação *ex ante*: os números como foram ditos na hora. Refazer a
noite com outro modelo (`scripts/ensaio2026.py`) é análise retrospectiva, e
as duas nunca se misturam numa tabela.

## Segundo turno

Eleição própria, com código próprio no TSE (6258 e 6260 em 2026), duas
candidaturas e comparecimento próprio. Percentuais do primeiro turno não são
transferidos. O caminho de 2º turno ainda não foi exercitado com dado real: a
primeira prova é 25/10/2026.

## Limites conhecidos

- **Deriva dentro do município.** O voto que falta de um município
  parcialmente apurado difere do que já chegou dele — no presidente, de 0,9 a
  1,8 pp. O modelo trata o que falta como parecido com o que chegou. Um
  estimador medido na hora ajudou em 6 de 10 disputas testadas e atrapalhou
  em 2 ou 3: precisa de freio e de ensaio próprio antes de virar modelo.
- **A largura do próprio modelo é o melhor preditor do erro** (Spearman
  0,48). As variáveis testadas para um calibrador dinâmico acrescentaram
  cerca de 1 ponto de R².
- **Margem do M3.3 no gate é pequena** (75,9% para um piso de 75%).
- **Limiares não aplicados.** `min_electorate_counted_pct` e
  `min_strata_with_data_pct` estão em `thresholds.yaml`, mas a publicação
  hoje é regida pelas regras de `engine/nivel.py` e `engine/chamada.py`; no
  1º turno a projeção ficou disponível desde 0,01% apurado.
- **`snapshot_set_id` só existe a partir de 06/10/2026.** Os 26.835
  registros do 1º turno não o têm: o conjunto de capturas de cada um se
  refaz pelo horário. Do 2º turno em diante cada registro leva o hash do
  conjunto, e ele se confere contra o manifesto
  ([DEFINICAO-E-AUDITORIA.md](DEFINICAO-E-AUDITORIA.md)).
- **Unidade mínima é o município.** Zonas só entram no ensaio, para fatiar
  cidades grandes.
- **Proporcionais não são projetados.**
