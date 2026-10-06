# M3 — projeção pela mudança em relação à eleição anterior

Desenho aberto em 22/09/2026, durante o simulado do TSE. Este documento
guarda o histórico do desenho e as referências; o método como está em uso
fica em [METODOLOGIA.md](METODOLOGIA.md).

**Estado em 06/10/2026:**

| variante | o que é | situação |
|---|---|---|
| M3 (M3.0) | M2 + inclinação histórica de cada município | passou no gate em 22/09; foi o modelo do 1º turno (04/10) |
| **M3.3** | M3 com os choques de preferência × 0,8 | **em produção**, homologado em 05/10/2026 |
| M3.1 | resposta à inclinação por bloco ideológico | implementado, experimental, sem gate |
| M3.2 | calibrador dinâmico da incerteza | implementado, reprovado no ensaio de 2018 |
| M3a, M3.4 | PIB agropecuário como segunda covariável (sobre o M3 e sobre o M3.3) | passam no gate de 06/10 sem ganho consistente; não adotados |

## O problema que o M3 resolve

O ensaio de 2022 (`scripts/ensaio2022.py`) mostrou que a ordem de chegada
dos municípios não é aleatória: no 1º turno de 2022 o interior bolsonarista
chegou antes, e M1/M2 — que estimam o que falta só a partir do que já chegou
na noite — demoraram a virar para Lula. Eles ignoram a informação mais forte
disponível antes da eleição: **como cada município votou antes**.

Medido (21–22/09): a fatia do PT no 2º turno de 2018, por município, tem
correlação de **0,97** com o voto de Lula no 1º turno de 2022. Prever cada
município pela média nacional erra 12,5 pp em média; pela inclinação
histórica, 2,5 pp.

## O que dizem os modelos de referência

A pesquisa (22/09, referências abertas e lidas) aponta o mesmo princípio nos
modelos de noite eleitoral documentados: **modelar a mudança em relação à
eleição anterior, não o voto bruto** — a mudança varia bem menos que a
escolha.

- Washington Post 2020 — alvo = mudança normalizada em relação à eleição
  anterior no mesmo condado; intervalos por *conformal quantile
  regression*; ordem de chegada não aleatória tratada explicitamente.
  Cherian & Bronner (2020),
  <https://s3.us-east-1.amazonaws.com/elex-models-prod/2020-general/write-up/election_model_writeup.pdf>;
  código <https://github.com/washingtonpost/elex-live-model>.
- Washington Post 2024 — margem anterior como covariável (ridge), bootstrap
  + conformal por subgrupo. Cherian, Bronner & Candès (2024),
  <https://jjcherian.github.io/assets/pdf/election_poster.pdf>.
- BBC — exit poll de painel: mudança local, regressão multipartidária da
  mudança. Curtice & Firth (2008), JRSS A 171(3),
  <https://strathprints.strath.ac.uk/8807/>.
- África do Sul — agrupamento do eleitorado por comportamento passado contra
  resultados iniciais enviesados. Greben, Elphinstone & Holloway (2006),
  ORiON 22(1), <https://orion.journals.ac.za/pub/article/view/35>.
- Calibração: conformal sob mudança de covariáveis (Tibshirani et al., 2019,
  <https://arxiv.org/abs/1904.06019>); conformal adaptativo sob mudança de
  distribuição (Gibbs & Candès, 2021, <https://arxiv.org/abs/2106.00170>);
  garantias condicionais por grupo (Gibbs, Cherian & Candès, 2023,
  <https://arxiv.org/abs/2305.12616>).

Não se encontrou modelo público de projeção durante a apuração no Brasil.

## M3.0 — o modelo do 1º turno

`engine/models.build_m3`, sobre a hierarquia do M2 (município → porte →
grupo da capital → UF), sem mexer na simulação:

1. **Inclinação** de cada município: fatia do PT nos válidos do 2º turno
   presidencial da eleição anterior (`scripts/historico_inclinacao.py`,
   `data/historico/inclinacao_<ano>.json`; 2018 e 2022 conferidos contra o
   oficial: 44,87% e 50,90%). Mesma régua para todo cargo; nenhum candidato
   precisa ser ligado à mão ao passado.
2. **Resposta à inclinação medida na noite**: para cada candidato,
   regressão ponderada do log da fatia observada na inclinação (logit), só
   com os municípios já contados; encolhida para zero com
   `K_INCLINACAO = 50 mil` votos (pouca apuração → M3 ≈ M2).
3. **Correção de cada pendente**:
   `log p[g,k] = log p_estrato[k] + slope[k] · (x[g] − x_estrato)`,
   limitada a ±1,5 em log. Sem histórico (município novo): fica o M2.
4. Incerteza: a do M2 (calibrada em 2018). **2022 não é usado para ajuste**:
   é a eleição de teste.

Primeiro teste (22/09, MG, BA e PA, 6 disputas, 500 simulações): M3 vence o
M0 no erro mediano em 18 de 18 rodadas, com erro 2 a 4 vezes menor (ordem
real, rodada 1: M0 1,68 pp, M2 3,73, M3 1,21). Intervalo largo demais:
cobertura 85,8% (real) — esperado, pois herda a incerteza de um modelo que
erra mais.

Gate completo (22/09, 55 disputas, 3.000 simulações,
`data/exports/ensaio2022/gate2022.md`): **passou**. Vence o M0 nas 18
rodadas, com cerca de metade do erro na ordem real; cobertura da faixa de
80% em 81,6% (real) e 81,2% (aleatória); nenhuma disputa com 90% ou mais
dado a um perdedor. Homologado e posto em produção no mesmo dia.

Na noite de 04/10/2026 o M3 projetou as 55 disputas do 1º turno. O número
central foi bom desde o início (erro médio de 1,40 pp com menos de 6%
apurado, contra 2,52 pp do percentual apurado), mas a faixa de 80% conteve o
resultado em 96–100% dos casos: larga demais.

## M3.3 — em produção (05/10)

`engine/models.build_m33`: o número central do M3, com os três choques de
preferência do simulador multiplicados por `FATOR_M33 = 0,8`
(`simulate.estreitar`). Nada mais muda.

Cobertura da faixa de 80% com cada fator testado:

| fator | 2026 (noite real) | 2022 (ensaio) | 2018 (ensaio) |
|---|---|---|---|
| 1,0 (M3) | 96–100% | 82% | 81–82% |
| **0,8** | 92–100% | 76% | 74–76% |
| 0,65 | 89–99% | 70% | 67–70% |
| 0,5 | 81–98% | 60% | 57–61% |

O ensaio sintético é mais duro que a noite real; só o 0,8 passa nas três. Com
0,65 o modelo já dava 90% ou mais a um perdedor em 2022.

Gate oficial (05/10, mesmos parâmetros do M3): **passou** — vence o M0 nas
18 rodadas, cobertura 75,9% / 75,8%, nenhum perdedor com 90% ou mais. A
margem é pequena (o piso é 75%); 0,85 daria 78,3% / 77,6% e 0,9, 79,9% /
79,2%. A rever depois do 2º turno, com duas noites reais.

Homologado e assinado em 05/10/2026. A evidência do M3, de 22/09, continua
intacta no mesmo arquivo.

## M3.1 — bloco ideológico (experimental)

Proposta do usuário (22/09): medir a "inclinação ideológica" dos dados que
chegam. Com a classificação assinada (`config/classificacao_2026.yaml`):

- referência por município = fatia de cada **bloco** (esquerda, centro,
  direita) em 2022 no mesmo cargo;
- swing de bloco medido na noite (nacional → UF → grupo), aplicado aos
  pendentes; o candidato herda o swing do seu bloco, e a divisão dentro do
  bloco vem do que já foi contado;
- resolve candidatos sem correspondência direta em 2022 (partido novo,
  candidato novo) e liga governador e senador ao alinhamento local.
- Risco: alianças locais que atravessam blocos; mitigado por deixar o
  coeficiente ser aprendido na noite, não imposto.

O que foi implementado (`build_m31`) é mais estreito que esse desenho: igual
ao M3, menos a resposta de cada candidato à inclinação, que em vez de
encolher para zero encolhe para a média do bloco ideológico dele, com peso
dado pelo erro-padrão da regressão. Mira o candidato regional cujo voto não
segue o eixo presidencial. Não passou por gate; fora da produção.

## M3.2 — calibrador dinâmico (reprovado no ensaio)

Também da proposta do usuário: o intervalo se auto-avalia durante a noite.

- A cada rodada, os municípios que acabaram de chegar são comparados com o
  que a rodada anterior projetou para eles; os resíduos (por faixa de %
  apurado e estrato) reescalam os choques da simulação.
- Base: conformal adaptativo (Gibbs & Candès, 2021) — ajusta o nível para
  manter a cobertura nominal quando a distribuição muda ao longo da noite;
  com pesos de mudança de covariáveis (Tibshirani et al., 2019) para quem
  chegou cedo representar mal quem falta.
- Calibração inicial: resíduos do ensaio de 2018 (exige a inclinação de
  2014 — download pendente) e de 2022 (teste).
- Risco: pouca informação nas primeiras rodadas → intervalo largo. É o
  comportamento correto.

### Tentativa de 22/09: validação cruzada nos municípios contados — reprovada

Implementada como `build_m32` (modelo `m32`, "em teste"; nada em produção).
Em vez de comparar rodadas, cada município já contado é previsto sem o
próprio voto e o desvio real, descontado o ruído amostral, reescala o choque
local e o de grupo. Calibração só em 2018 (governador e senador, 54
disputas; `data/exports/ensaio2022/calibracao_m32*_2018.md`):

- **v1 (régua absoluta σ_local = 0,12):** cobertura 93–95% contra 81–83% do
  M3 — alargou tudo. O desvio real entre municípios é ~3,6× o suposto
  (`scripts/medir_conformal.py`), mas a cobertura do M3 não depende do choque
  local, que se dilui; alargar o grupo junto passou do ponto.
- **v2 (fator relativo à mediana de 2018, amortecido por γ = 0,25/0,5/1):**
  neutro. Média igual à do M3; dispersão entre UFs só melhora no senador em
  ordem real (desvio médio de 80%: 9,3 → 7,6 pp com γ = 1); no governador
  não muda (12,5 → 12,3–12,9 pp); "90% num perdedor" igual.
- **Por quê:** a heterogeneidade entre municípios mal prevê onde o M3 erra
  (Spearman −0,22 por disputa). Os erros de cobertura vêm de desvios
  compartilhados — o grupo ou a UF inteira se afastando do previsto (TO
  governador 2018: 40%) —, que um resíduo por município não enxerga.
- **Conclusão:** pelo critério fixado antes, não entra. O caminho que ataca
  o erro compartilhado é o desenho original acima — comparar a projeção
  anterior com o que chegou depois, no agregado — ou reduzir o próprio erro
  central (M3.1).

## M3a e M3.4 — PIB agropecuário (sem ganho consistente)

Hipótese levantada em 05/10: o que a inclinação não explica acompanha o
perfil econômico do município. Com o valor adicionado por setor do IBGE
(SIDRA 5938, ano 2021; `scripts/economia_ibge.py`), o resíduo do presidente
tem correlação de −0,38 com a fatia do agro. Como segunda covariável do
nível, porém, o erro no replay de 2026 não caiu: 0,458 × 0,459 pp, melhor em
18 disputas e pior em 20.

São duas variantes: o **M3a** põe o agro sobre o M3; o **M3.4**
(`build_m34`), sobre o M3.3. No ensaio de 2022 chamam-se `m3a` e `m34`; no
replay de 2026, `agro` e `agro+k0.8`.

Gate de 2022 (06/10, 3.000 simulações, semente 7,
`data/exports/ensaio2022/gate2022-agro.md`), erro mediano nas rodadas 1, 5 e
9 e cobertura da faixa de 80%:

| modelo | ordem real | ordem aleatória | cobertura real / aleatória |
|---|---|---|---|
| M3 | 1,69 / 0,62 / 0,21 | 1,97 / 0,40 / 0,12 | 82,7% / 81,7% |
| M3.3 | 1,66 / 0,68 / 0,21 | 1,96 / 0,39 / 0,12 | 75,9% / 75,8% |
| M3a | 1,74 / 0,67 / 0,19 | 1,84 / 0,43 / 0,13 | 83,1% / 81,0% |
| M3.4 | 1,68 / 0,66 / 0,19 | 1,86 / 0,40 / 0,12 | 77,2% / 75,4% |

As duas passam, e nenhuma deu 90% ou mais a um perdedor. O ganho aparece só
na primeira rodada da ordem aleatória; na ordem real, que é o teste, ficam
empatadas ou um pouco piores no começo. Não há motivo para trocar o modelo
em produção. O M3 rodado de novo nesse gate reproduziu exatamente os números
de 05/10.

## O que o 1º turno deixou em aberto

- **Deriva dentro do município.** O voto que falta de um município parcial
  difere do que já chegou dele (presidente: 0,9 a 1,8 pp). Não depende do
  perfil econômico — é uniforme. Um estimador medido na hora ajudou em 6 de
  10 disputas e atrapalhou em 2 ou 3; falta freio e ensaio próprio.
- **Calibrador dinâmico.** A largura do próprio modelo é o melhor preditor
  do erro (Spearman 0,48); as outras variáveis testadas acrescentam cerca de
  1 ponto de R².
- **`K_INCLINACAO` não pesa no presidente**, que tem milhões de votos desde
  o primeiro boletim.

## Critério de entrada em produção

O mesmo gate do §11.2 (`scripts/gate2022.py`, `obs2026/homologacao.py`):
vencer o M0 no erro mediano em toda rodada, nas ordens real e aleatória, e
cobertura do intervalo de 80% dentro de ±5 pp. A troca de modelo é na aba
Configuração; a homologação exige a evidência do modelo em produção.
