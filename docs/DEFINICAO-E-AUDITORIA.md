# Definição matemática e auditoria das projeções

Dois mecanismos que respondem a perguntas diferentes e nunca devem aparecer
com o mesmo rótulo:

| | pergunta | erra? |
|---|---|---|
| **Projeção** | o que provavelmente vai acontecer | sim, e por isso tem intervalo |
| **Definição matemática** | o que já não pode mudar | não, se os dados estiverem certos |
| **Eleito segundo TSE** | o que o TSE proclamou | é a fonte oficial |

O §4.7 exige rótulos distintos para os três. No painel eles aparecem em abas
diferentes; na API, em rotas diferentes (`/forecasts`, `/decided`,
`situacao_oficial` dentro de `/results`).

---

## Definição matemática

É uma conta de limite, sem nenhuma premissa estatística:

> Existe alguma distribuição possível dos votos que ainda faltam que mude
> este resultado?

Se não existe, está definido.

### O limite

`R` é o máximo de votos que ainda podem entrar, derivado do **limite
superior** de `A_pendente` com comparecimento 100% e validade 100%. Usar o
limite superior é deliberado: declarar definido algo que não está é o erro
caro; demorar a declarar é apenas conservador.

```
maioria absoluta garantida   2 · V_x  >  C_obs + R_total
vaga garantida               soma dos `lugares` menores déficits > R_total
eliminado                    nem com R_por_candidato entra entre os
                             `lugares` primeiros
```

O denominador da maioria são os **votos a concorrentes** (`vvc`), não os
votos válidos — ver [ACHADOS.md](ACHADOS.md).

### Três armadilhas que o código evita

**1. Eliminação medida contra o líder.** Com duas vagas, alcançar o segundo
já basta. Comparar todo mundo com o primeiro elimina candidatos vivos. No
Senado isso apagaria da tela quem ainda disputa a segunda vaga.

**2. Segundo colocado presidencial marcado como eliminado.** Presidente e
governador têm uma vaga, mas o primeiro turno seleciona **dois** nomes. Um
candidato que não pode mais vencer no primeiro turno não está eliminado — está
indo ao segundo. Por isso `lugares = 2` quando há segundo turno, e
`tem_segundo_turno=False` para Senador, que não tem repescagem.

**3. Déficits somados como se fossem independentes.** Os rivais disputam o
mesmo estoque `R`. Para perder uma vaga entre duas, dois rivais precisam
passar ao mesmo tempo, e o caminho mais barato usa os dois menores déficits.
Somar déficits individuais daria uma leitura mais frouxa que a verdade.

### Quando a conta não vale

Havendo voto **anulado sub judice**, a definição é suspensa. Esses votos
podem ser revalidados ou cassados por decisão judicial, e isso muda a
aritmética depois do fato. O §4.7 manda tratar sensibilidade judicial em
cenário separado; aqui a consequência é não afirmar certeza.

Nos dados de teste de 2026 isso acontece de verdade: o arquivo presidencial
tem 10.503.573 votos sub judice, e a definição fica suspensa mesmo com a
apuração encerrada.

---

## Auditoria das projeções

Toda projeção é **registrada no instante em que é feita**, com o que se sabia
naquele momento. Depois, quando o resultado final chega, cada registro é
confrontado com a verdade.

Uma projeção que ninguém conferiu depois é uma opinião com aparência de
número. Registrar antes e conferir depois é o que torna a frase "nosso
intervalo de 80% cobre 80% das vezes" verificável em vez de decorativa.

### O que fica gravado, e por quê

| campo | por que existe |
|---|---|
| `cobertura` | sem ela não dá para saber se um erro grande aos 3% é fracasso ou o esperado |
| `snapshot_set_id` | exatamente quais arquivos o motor leu |
| `modelo` + `model_version` | reanálise com modelo novo é **outra série** (§5.2) |
| `semente`, `n_sims` | sem elas não há reprodução |
| `thresholds_hash` | prova de quais limiares valiam ali |
| quantis e probabilidades | o que foi efetivamente dito |
| `estado_matematico` | o que já estava definido naquele momento |

O diário é **append-only**. Reprocessar não reescreve o passado: alterar o
valor de uma projeção antiga é falsificação do histórico, não correção.

### Como se avalia

```bash
obs2026 prever governador --uf al        # projeta e registra
obs2026 previsoes                        # lista o histórico
obs2026 auditar-previsoes                # confronta com o resultado final
obs2026 ensaiar-auditoria senador --uf al  # ensaio antes da apuração real
```

Métricas por faixa de cobertura: erro absoluto em pontos percentuais,
cobertura e largura dos intervalos, acerto de liderança e de vagas, e Brier
score dos eventos binários.

**Duas salvaguardas contra leitura otimista:**

O consolidado traz o **intervalo de confiança da própria métrica**. Com uma
dúzia de execuções não se demonstra que um intervalo de 95% cobre 95%, e o
campo `amostra_pequena` marca isso explicitamente (§11.2).

A **definição matemática contrariada é falha grave**, não mais uma métrica.
Se algo declarado definido não se confirmou, houve retotalização ou bug de
contagem — e o relatório grita, em vez de diluir o caso numa média.

### O que a auditoria já encontrou

Rodando o ensaio sobre os dados reais do simulado, com apuração progressiva
reconstruída:

```
Senador AL — cobertura dos intervalos de 80%
   1,0% apurado   10%
   5,1% apurado   20%
  10,1% apurado   30%
  25,2% apurado   20%
  51,1% apurado   60%
```

O nominal é 80%. Os intervalos estão **estreitos demais** nas fases
iniciais, e o problema é maior no Senado, onde dez candidatos ficam
concentrados perto de 10% e diferenças pequenas decidem vagas.

Isso não é surpresa nova — era a suspeita registrada em
[METODOLOGIA.md](METODOLOGIA.md). O que mudou é que agora **é medido
automaticamente a cada execução**, em vez de depender de alguém lembrar de
conferir. A correção (fazer os σ crescerem quando a cobertura cai) precisa de
dados históricos reais, não de ajuste contra o simulado sintético.
