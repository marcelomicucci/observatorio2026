# Metodologia de projeção

## A decisão central

Projetar o voto dos territórios que faltam e **depois** agregar. Nunca
extrapolar o percentual nacional já contado.

A razão é que a ordem de chegada não é aleatória. A apuração é uma sequência
administrativa: municípios pequenos e do interior tendem a fechar antes das
capitais, e eles não votam como a média do que já chegou. Extrapolar o
percentual contado assume, sem dizer, que o que falta é uma amostra aleatória
do que veio — e não é.

## A identidade

Para unidade `g`, candidato `k`, simulação `b`:

```
R[g,b]         = A_pendente[g,b] × comparecimento[g,b] × taxa_válida[g,b]
V_final[g,k,b] = V_observado[g,k] + R[g,b] × preferência[g,k,b]
```

Três grandezas estimadas **separadamente**, porque se movem por razões
diferentes: quanta gente ainda vota, quanto desse voto é válido, e para quem
vai.

O voto já contado é fixo no cenário-base — entra igual em todas as
simulações. Retotalização cria versão nova, não ruído.

## `A_pendente`

Derivado dos estados oficiais, não de "aptos × percentual de seções
restantes" (§4.2). Ver [ACHADOS.md](ACHADOS.md#6) para as identidades
verificadas. Em resumo:

- eleitores de seções **não instaladas** não são voto futuro;
- o contrato não separa "não totalizada" de "não instalada", então
  `A_pendente` é um intervalo `[esnt − esni, esnt]`;
- a simulação sorteia dentro do intervalo.

Onde o contrato não resolve, a incerteza cresce. A estimativa não finge
precisão que o dado não tem.

## Por que os choques são compartilhados

Este é o ponto que mais afeta a largura dos intervalos.

Se cada município sorteasse seu desvio de forma independente, a variância do
total nacional cairia com √5.571 e o intervalo publicado sairia absurdamente
estreito — dando a impressão de uma precisão que nenhuma projeção eleitoral
tem.

O erro real é dominado por um desvio **comum**: se os lugares que faltam
votam diferente do previsto, eles tendem a votar diferente *na mesma direção*.
Por isso o desvio é decomposto:

```
logit(comparecimento) = logit(ĉ) + σ_nac·z_nac + σ_uf·z_uf + σ_loc·z_loc
```

Só `z_loc` é independente entre unidades. `z_nac` e `z_uf` são compartilhados,
e são eles que dominam o intervalo agregado. A preferência usa a mesma
estrutura em escala log, com softmax — um candidato que vai melhor que o
esperado, vai melhor em toda parte.

Consequências operacionais:

- **quantis saem das somas simuladas**, nunca somando quantis municipais;
- **percentual é calculado depois da soma**, com o denominador da mesma
  simulação.

## Os modelos

**M0 — extrapolação ingênua.** Aplica o comparecimento, a taxa de válidos e a
preferência nacionais já contados a todo o eleitorado pendente. Existe para
ser batido: o gate de lançamento exige superá-lo no erro mediano. Um modelo
que não vence a extrapolação ingênua não merece ir ao ar.

**M1 — estratificação municipal.** Cada unidade usa o próprio dado, encolhido
na direção do estrato:

```
w = n_observado / (n_observado + k₀)
preferência = w · local + (1 − w) · estrato
```

O encolhimento importa mais do que parece. Um município com 40 votos contados
e 12 mil pendentes, usando a preferência local crua, injeta ruído amplificado
300 vezes. `k₀ = 500` é proposta; o valor sai do backtest.

O estrato é **UF × porte eleitoral × capital/interior** — escolhido para
agrupar unidades que tendem a chegar juntas e votar parecido, que é a forma
de o modelo enxergar o viés de ordem de chegada. Variáveis adicionais só
entram com ganho medido em validação.

Unidade ainda zerada usa a estimativa do grupo, **nunca zero como
preferência**, e recebe incerteza ampliada.

**M2 — hierárquico territorial.** Não implementado. Depende de séries
históricas harmonizadas (2018 e 2022) que ainda não foram baixadas.

## Senado: duas vagas, duas escolhas

Cada eleitor faz até duas escolhas. O total de escolhas válidas restantes é
`C × q`, com `q` entre 0 e 2 — **não** `2 × C`. No simulado, `q` observado foi
**1,70**; supor 2,00 superestimaria o total em 17%.

Restrições impostas **em cada simulação**:

- votos adicionais de um candidato ≤ comparecentes restantes (ninguém dá dois
  votos válidos ao mesmo candidato);
- soma dos votos adicionais ≤ 2 × comparecentes restantes.

O excedente que estoura o teto é redistribuído entre quem tem folga, e o
total é reescalado se ainda passar do máximo.

**O que este modelo não faz:** recuperar quais pares de candidatos cada
eleitor escolheu. As contagens agregadas não identificam pares. O §4.6 admite
como alternativa inicial as marginais conjuntas restritas, que é o que está
implementado — sem alegar recuperação de pares individuais. A dependência
entre escolhas é hipótese explícita, a ser estimada só quando houver
evidência.

Saídas: probabilidade de estar entre os dois primeiros, distribuição das
duplas eleitas, e margem do segundo para o terceiro — que é a pergunta que
decide a disputa.

Percentuais sobre escolhas válidas somam 100%; sobre eleitores podem somar
até 200%. A votação de um candidato nunca é dividida por dois.

## Segundo turno

Eleição própria, com código próprio (`cdt2`: 21270→21271, 21272→21273),
parâmetros de comparecimento próprios e modelo próprio. Percentuais do
primeiro turno não são transferidos automaticamente.

## Validação

Backtest por cortes de cobertura de 1%, 5%, 10%, 25%, 50%, 75%, 90% e 99%,
em ordens de chegada explícitas.

**Honestidade obrigatória:** quando só existem resultados finais, a ordem de
chegada é construída por nós. Isso é **teste de estresse**, não replay
histórico real — e todo relatório carrega `ordem_e_sintetica`.

Ordens implementadas: eleitorado crescente (o padrão real mais comum, e o que
mais engana a extrapolação ingênua), eleitorado decrescente, aleatória (o caso
benigno, que a extrapolação ingênua acerta e que por isso não prova nada) e
por UF.

Métricas: erro absoluto em pontos percentuais, erro de votos, cobertura e
largura dos intervalos, acerto de ranking e de vagas, erro de Monte Carlo.

### Estado atual: o gate NÃO passou

Rodando o backtest sobre os dados reais de Alagoas do simulado:

```
Governador AL, ordem por eleitorado crescente
corte    cob    mod   err_med  err_max   c80   c95  largura80  vagas
   5%   5,1%    M0       1,10     2,55   50%   88%      2,35    NÃO
   5%   5,1%    M1       1,19     2,44   75%  100%      2,94    NÃO
  25%  25,2%    M0       0,40     0,87  100%  100%      2,01    NÃO
  25%  25,2%    M1       0,34     0,77  100%  100%      2,71    NÃO
  50%  51,1%    M0       0,24     0,40  100%  100%      1,53    sim
  50%  51,1%    M1       0,23     0,41  100%  100%      2,32    sim

gate M1 > M0: NÃO PASSOU (2 vitórias, 1 empate, 1 derrota)
```

Duas leituras, e as duas importam:

1. **A máquina de validação funciona.** Ela roda, calcula cobertura de
   intervalo e aplica o gate — e o gate recusou, que é o comportamento
   correto quando o modelo não se prova.

2. **Os dados não permitem concluir nada sobre o modelo.** O simulado é
   sintético: todos os candidatos ficam próximos de 1/n (12,6% para 8
   candidatos a governador; 10,4% para 10 a senador). Onde não existe
   estrutura territorial, estratificar não tem o que capturar e só acrescenta
   variância — M0 é ótimo por construção. É exatamente o que o §11.3 avisa: o
   simulado testa contrato e operação, não o comportamento estatístico do
   eleitorado.

Um achado de calibração que **é** real: aos 5% de cobertura, a cobertura dos
intervalos de 80% ficou em 50–75%. Os intervalos estão estreitos demais
quando pouco foi contado. Os σ precisam crescer conforme a cobertura cai —
correção a fazer com dados reais, não ajustando parâmetros para o simulado
parecer melhor.

**Enquanto o gate não passar**, `config/thresholds.yaml` fica com
`homologated: false`, `/api/v1/forecasts` responde `cenario_exploratorio` e
nenhuma probabilidade de vitória é publicada. A apuração oficial continua
normalmente disponível.

## O que falta para liberar o forecast

1. Baixar os resultados históricos do TSE de 2018 e 2022.
2. Harmonizar território e partidos entre os anos.
3. Rodar o backtest sobre eleições encerradas reais, separando treino,
   calibração e avaliação.
4. Calibrar os σ, especialmente sua dependência da cobertura.
5. Registrar tolerâncias **antes** do holdout.
6. Assinar `config/thresholds.yaml`.

Sem isso, o produto entrega apuração e cenários — que já é o que o §12 manda
não deixar depender da conclusão das etapas estatísticas.
