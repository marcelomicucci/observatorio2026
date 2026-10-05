# M3 — projeção pela mudança em relação à eleição anterior

Desenho aberto em 22/09/2026, durante o simulado do TSE. Estado: **M3.0
implementado e em teste**; M3.1 (bloco ideológico) e M3.2 (calibrador
dinâmico) desenhados.

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

## M3.0 — implementado (22/09)

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
erra mais. Gate completo com 3.000 simulações em
`data/exports/ensaio2022/gate2022.md`.

## M3.1 — bloco ideológico (desenhado)

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

## M3.2 — calibrador dinâmico (desenhado)

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

## Critério de entrada em produção

O mesmo gate do §11.2 (`scripts/gate2022.py`, `obs2026/homologacao.py`):
vencer o M0 no erro mediano em toda rodada, nas ordens real e aleatória, e
cobertura do intervalo de 80% dentro de ±5 pp. A troca de modelo é na aba
Configuração; a homologação exige a evidência do modelo em produção.
