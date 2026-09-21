# Observatório Eleitoral 2026

**Apuração de presidente, governador e senador em tempo real, com projeção do
que ainda falta contar — e registro de cada projeção, para provar depois se
ela acertou.**

Roda numa máquina só: coleta do TSE, banco, painel de operação no notebook e
telão limpo no projetor. Fora o próprio TSE, nada depende de rede.

Implementa a [especificação v1.0](docs/fonte/Especificacoes_Observatorio_Eleitoral_2026.md),
validado contra o servidor de simulado do TSE.

> **Hoje, tudo vem do simulado do TSE — dados de teste, não resultado
> eleitoral.** A separação é estrutural: o ambiente faz parte da chave do
> banco, toda tela exibe a tarja de simulado, e o ambiente oficial recusa
> iniciar até o TSE publicar os códigos de outubro. Nada é presumido.

---

## A decisão central

**Projetar os territórios que faltam e somar. Nunca extrapolar o percentual
já contado.**

A apuração não chega em ordem aleatória: capitais, interior e exterior
totalizam em ritmos diferentes, e cada um vota diferente. O percentual
nacional às 19h é o retrato de *quem já foi contado*, não do país. Por isso o
motor projeta cada município pendente — comparecimento, votos válidos e
preferência estimados separadamente, com choques compartilhados por país, UF
e localidade — e só então agrega. Todo número projetado sai com intervalo.

---

## O que ele faz

**Coleta.** Lê a divulgação do TSE dentro do limite de 100 req/s por IP, com
teto interno de 20. Não varre o país a cada ciclo: os arquivos de
acompanhamento (EA14/EA15) dizem quais municípios mudaram, e só esses são
buscados. **Totais de Brasil e de cada UF têm nível próprio na fila** — saem
antes de qualquer município, por maior que seja, a cada 10 s. O que o TSE já
finalizou sai das buscas e continua vigiado; se houver re-totalização, volta.
Reiniciado no meio da noite, retoma em um segundo do que já sabia.

**Guarda.** Todo arquivo recebido vai para um arquivo bruto imutável,
endereçado por hash, com manifesto. O banco é derivado dele e se reconstrói
inteiro. A carga é contínua e incremental: a cada 5 s, só o que chegou, com
os totais gravados antes dos municípios.

**Confere.** Onze invariantes contábeis em cada arquivo (`vvc = vv + van +
vansj`, `tv = vvc + vb + tvn`, `te = est + esnt`…). Divergência crítica
bloqueia a projeção e mantém a apuração no ar.

**Projeta.** Monte Carlo por município pendente, modelos M0 (baseline
ingênuo) e M1 (estratificado), Senado com modelo próprio para duas vagas.
A cada 2 minutos todas as 55 disputas são projetadas e **registradas** num
diário só de acréscimo.

**Define.** "Matematicamente eleito" e "segundo turno matematicamente
definido" são aritmética, não modelo: nenhuma distribuição possível dos votos
restantes muda o resultado. Voto sub judice em jogo suspende qualquer
definição.

**Presta contas.** O gráfico de convergência mostra, para cada candidato, o
apurado e o projetado ao longo da noite. Se o modelo presta, a linha
projetada nasce perto do resultado final e fica lá, enquanto a apurada
caminha até encontrá-la. Nada é recalculado depois: são os números como
foram ditos no momento.

---

## A noite da apuração

**Dois monitores.** No notebook, o painel: resultado, mapa com filtro por
região, estado e município, projeção, convergência, auditoria. No projetor,
o telão: mapa grande e poucos números, legíveis a quatro metros.

**O telão roda sozinho** um roteiro configurável — "Presidente no Brasil,
governador no PA, senador em SP, presidente em Belém", com o tempo de cada
tela. O roteiro se edita e se grava pelo próprio painel. Qualquer pesquisa
vira tela com um clique; "mostrar agora" põe um recorte no projetor fora da
sequência.

**O grupo pede "manda aí como está".** Botão **📷 WhatsApp** no painel —
em qualquer pesquisa, ou na aba Telão para o roteiro inteiro. Fotografa as
telas como aparecem no telão, escreve a legenda com os números do mesmo
instante e abre a janela de compartilhar do Windows, onde é só escolher o
grupo. Sem ela, copiar imagem e legenda e colar no WhatsApp Web. Também pelo
terminal: `python -m obs2026.cli compartilhar`. O envio
fica com quem opera, de propósito: a API oficial não entrega para grupos, e
projeção eleitoral não deve circular sem alguém ter olhado. A legenda sempre
diz que o número não é oficial, e só mostra projeção se o gate estatístico
tiver passado — encaminhada, a imagem perde o contexto; o texto vai junto.

---

## Começar

```bash
pip install -e .
python -m pytest tests/ -q
```

127 testes, a maioria contra payloads reais do TSE. Se quebrarem, o contrato
mudou.

**Antes do dia**, uma vez:

```bash
python -m obs2026.cli preparar
```

```bash
python scripts/criar_atalhos.py
```

O primeiro baixa para o disco tudo o que não é TSE (perfis, fotos, malha do
IBGE). O segundo põe na pasta o `Iniciar Observatório 2026.cmd`, que sobe o
servidor e abre o painel.

**No dia:**

```bash
python -m obs2026.cli coletar
```

```bash
python -m uvicorn obs2026.api.app:app --port 8026
```

O `coletar` coleta, grava no banco e registra projeções — um comando só.
Painel em `http://localhost:8026`, telão em `/telao`, API em `/api/docs`.
A sequência completa, com o que fazer quando algo dá errado, está no
[runbook](docs/RUNBOOK-SIMULADO.md).

Os demais comandos:

| comando | para quê |
|---|---|
| `descobrir` | lê o catálogo oficial do TSE e mostra o que existe |
| `orcamento` | confere se a cadência cabe no limite de requisições |
| `ensaio` | ponta a ponta com as invariantes, antes de ligar a coleta |
| `carregar` | bruto → banco, depois de coleta avulsa ou para reconstruir |
| `prever` | projeta uma disputa e registra o que foi dito |
| `previsoes` / `auditar-previsoes` | o histórico do que dissemos, contra o resultado |
| `auditar-csv` | audita a base de eleitorado recebida |

---

## As regras que o código impõe

1. **Projetar territórios, depois agregar.** Nunca extrapolar o contado.
2. **Todo percentual declara seu denominador.** Há cinco, e todos diferem. O
   percentual que o TSE exibe é sobre a soma dos votos dos candidatos; a
   maioria absoluta é sobre *votos a concorrentes* (válidos + anulados + sub
   judice). Confundir os dois pode anunciar uma vitória em primeiro turno que
   não existe.
3. **Ausência é nulo com motivo, nunca zero.** Município sem voto contado
   está *aguardando*, não em 0%. Partido sem classificação é desconhecido,
   não centro.
4. **Projetado, matematicamente definido e eleito segundo o TSE são três
   coisas.** Nunca se fundem num rótulo só.
5. **Liderar um território não é ser eleito por ele.** Presidente se decide
   no Brasil; governador e senador, na UF. Todo recorte menor diz isso na
   tela.
6. **Nada se resolve por adivinhação.** Município se identifica por código,
   nunca por nome — `PAU D'ARCO` existe no PA e no TO.
7. **Toda projeção é registrada.** Não há caminho no código que projete sem
   gravar o que disse.

---

## Estado

| | |
|---|---|
| **Coleta** | 31.773 capturas do TSE, **zero** erros, 404 ou bloqueios. 20 req/s sustentados |
| **Banco** | 5.784 territórios, 31.028 snapshots, 440.971 resultados |
| **Qualidade** | 11 invariantes, zero divergências críticas |
| **Históricos** | 2018 e 2022 lidos e conferidos, zero violações |
| **Motor** | M0, M1, Senado, Monte Carlo e backtest funcionando |
| **Definição matemática** | funcionando, sem modelo |
| **Auditoria** | diário de projeções e gráfico de convergência funcionando |
| **Painel e telão** | funcionando, com roteiro gravável e captura para WhatsApp |
| **Projeção com probabilidade** | **desligada** — o gate não passou |
| **Mapa ideológico** | **desligado** — curadoria pendente |

**As duas coisas desligadas estão desligadas de propósito.** Ligá-las sem o
trabalho que falta seria pior do que deixá-las assim.

*Probabilidade de vitória* só sai quando o modelo superar o baseline ingênuo
e mostrar intervalos com a cobertura que prometem, em eleições encerradas
**reais**. Sobre o simulado, o M1 não supera o M0 — porque o TSE gera dados
sintéticos sem estrutura territorial, não por defeito do modelo. Até lá, o
painel mostra "cenário exploratório" e diz por quê. Ver
[METODOLOGIA.md](docs/METODOLOGIA.md#estado-atual-o-gate-não-passou).

*Classificar partidos* em esquerda, centro e direita é ato editorial. A
máquina está pronta e `config/classificacao_2026.yaml` está vazio: cada
entrada exige fonte, justificativa, autor e data. Preencher por conta própria
seria inventar autoria.

---

## O que os dados reais ensinaram

Construído contra o que o TSE de fato publica, não contra a documentação.
Alguns achados mudaram decisões — todos em [ACHADOS.md](docs/ACHADOS.md):

- O percentual do TSE **não** é sobre votos válidos. É sobre a soma dos votos
  nominais — e a base da maioria absoluta é uma terceira coisa.
- A cadência proposta na especificação **não cabia** no limite de requisições.
  A ferramenta de orçamento recusou; a varredura virou rotativa.
- No Senado com duas vagas, cada eleitor faz em média **menos de 2**
  escolhas válidas: 1,79 no Acre em 2018, 1,70 no simulado. Supor 2
  superestimaria o total em cerca de 12%.
- Um município novo do MT veio com código IBGE da faixa do DF, e sumia com o
  MT do mapa.
- O EA14 e o EA20 do Brasil discordam no horário de totalização. A conferir em
  outubro.

---

## Documentação

- **[ACHADOS.md](docs/ACHADOS.md)** — o que os dados reais revelaram. Comece
  por aqui.
- **[RUNBOOK-SIMULADO.md](docs/RUNBOOK-SIMULADO.md)** — operação, do preparo
  ao que fazer quando algo dá errado.
- **[METODOLOGIA.md](docs/METODOLOGIA.md)** — projeção, Senado, backtest,
  gate.
- **[DEFINICAO-E-AUDITORIA.md](docs/DEFINICAO-E-AUDITORIA.md)** — definição
  matemática e prestação de contas das projeções.
- **[ARQUITETURA.md](docs/ARQUITETURA.md)** — componentes, orçamento de
  requisições, implantação.

---

## Estrutura

```
config/            fontes, cadência, limiares, roteiro do telão, classificação
obs2026/
  tse/             urls, cliente com limite de taxa, catálogo, coletor, parsers
  storage/         arquivo bruto imutável, modelos, carga incremental
  engine/          pendentes, denominadores, simulação, modelos, backtest,
                   definição matemática, auditoria, registro contínuo
  api/             FastAPI: resultado, mapa, projeção, convergência, telão
  history/         resultados históricos do TSE (2018, 2022)
  quality/         invariantes contábeis
  candidates/      perfis e fotos, servidos localmente
  captura.py       fotografia das telas do telão
web/               painel, telão, mapa, malha do IBGE
tests/             127 testes; fixtures com payloads reais do TSE
docs/              achados, runbook, metodologia, arquitetura, especificação
data/              arquivo bruto, banco, previsões (fora do git)
```

---

## Próximos passos

1. **Simulados de 22 a 24/09.** Capturar a transição de 0% a 100% — a única
   chance antes de outubro de exercitar a apuração em andamento; os arquivos
   disponíveis hoje já estão todos finalizados.
2. **Calibrar com 2018 e 2022**, já baixados e conferidos. 2018 é a
   referência do Senado: a última eleição com duas vagas por UF.
3. **Corrigir os intervalos no começo da apuração.** Com pouco contado, os
   intervalos de 80% cobrem bem menos que 80% — estreitos demais. A cobertura
   já é medida a cada rodada; falta corrigir.
4. **Curar a classificação ideológica**, com autoria nominal.
5. **Medir as metas de desempenho** da especificação: p95, carga útil,
   1.000 leitores.
