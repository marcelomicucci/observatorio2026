# Runbook — simulados de 22, 23 e 24 de setembro

Janelas anunciadas pelo TSE: **9h–12h e 14h–17h**, nos três dias.
Reconfirmar horário e parâmetros na [página
técnica](https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados)
na manhã de cada dia e **registrar a versão consultada** — a página mistura
avisos atualizados com trechos antigos de FAQ.

O objetivo do simulado não é testar o modelo estatístico. É verificar
contrato de dados e medir capacidade real de atualização. O TSE gera os dados
sinteticamente; o comportamento do eleitorado ali não é real (§11.3).

---

## O que precisa ficar respondido ao fim dos três dias

| Pergunta | Como se responde | Onde aparece |
|---|---|---|
| O contrato mudou desde 20/09? | `pytest` contra as fixtures + `ensaio` | falha de teste ou `critical` |
| Quanto tempo leva de o TSE gerar até estarmos com o dado? | `latencia_fonte_s` na timeline | painel → Histórico |
| Quantas requisições por segundo dá para sustentar? | `effective_rps` e `http` no ensaio | saída do `ensaio` |
| A detecção por EA15 funciona? | `changes_detected` vs `result_fetches` | saída do `coletar` |
| Quanto pesa um snapshot? | `bytes` / `ok` no `http` | saída do `ensaio` |
| Os estados oficiais batem em apuração parcial? | invariantes durante o parcial | `critical` no log |

A última linha é a mais importante e **não pode ser respondida hoje**: os
arquivos da 1ª janela estão todos com 100% totalizado. As invariantes de
`A_pendente` só se exercitam com apuração em andamento.

---

## Antes de começar (fazer no domingo, 21/09)

```bash
cd observatorio2026
python -m pytest tests/ -q
```

Os 41 testes rodam contra os payloads reais baixados em 20/09. Se algum
quebrar, o contrato mudou — investigar antes de ligar qualquer coisa.

```bash
python -m obs2026.cli descobrir
```

Confere que o catálogo continua legível e que `fase` é `s`. Se vier `f`
(oficial) num ambiente de simulado, a coleta **recusa** iniciar — é a
barreira contra contaminação do §9.

```bash
python -m obs2026.cli orcamento
```

Confirma que a cadência cabe no teto. Com a configuração atual:

```
acompanhamento EA14+EA15      57 req   →  2,9 req/s (a cada 20s)
controle EA20 br+UF           83 req   →  4,2 req/s (a cada 20s)
reconciliação rotativa    16.897 req   →  9,4 req/s (ciclo de 1800s)
---------------------------------------------------------------
piso contínuo                            16,4 req/s
teto interno                             20,0 req/s
folga para EA20 movido por mudança        3,6 req/s
```

Se o piso passar do teto, o coletor **recusa iniciar** em vez de estourar o
limite do TSE e comer um bloqueio de 10 minutos.

---

## Durante cada janela

### 1. Ensaio, antes de ligar a coleta contínua

```bash
python -m obs2026.cli ensaio --amostra 40
```

Roda descoberta, controle e uma amostra municipal, aplicando as 11
invariantes contábeis a cada arquivo. Só seguir se sair
**"Nenhuma divergência contábil crítica"**.

### 2. Coleta contínua

```bash
python -m obs2026.cli coletar
```

Um lockfile impede um segundo coletor na mesma máquina. O limite do TSE é
**por IP**, não por processo: dois coletores somam requisições e o operador
só descobre no bloqueio.

### 3. Carga e painel, em outro terminal

```bash
python -m obs2026.cli carregar
```

```bash
python -m uvicorn obs2026.api.app:app --port 8026
```

Painel em `http://localhost:8026`. A carga é reexecutável: rodar de novo não
duplica snapshot nem voto.

---

## Se algo der errado

| Sintoma | O que o sistema faz | O que o operador faz |
|---|---|---|
| **404 isolado** | suspende aquela fonte por 10 min, sem retry | revalidar catálogo com `descobrir`; 404 repetido bloqueia IP |
| **403 / 429** | para **toda** a coleta pela penalidade inteira | **não reiniciar**: nova tentativa durante o bloqueio reinicia os 10 min |
| **série de erros na mesma fonte** | abre o circuito por 5 min | verificar rede antes de forçar |
| **payload fora de contrato** | recusa ingerir, registra `ParseError` | comparar com a fixture; o bruto está salvo |
| **divergência contábil crítica** | bloqueia projeção, mantém apuração no ar | ler o código da invariante em `quality/checks.py` |
| **queda do processo** | bruto em disco está íntegro | religar e rodar `carregar` — é idempotente |

Regra que não se quebra: **nunca multiplicar IPs para contornar limite.**

---

## O que guardar de cada janela

O arquivo bruto (`data/raw/simulado2026/`) é a evidência. Guardar inteiro:

- `manifest/AAAA-MM-DD.jsonl` — uma linha por captura, com hash, horário de
  geração no TSE, horário da nossa captura, ETag e status;
- os blobs, deduplicados por hash.

Ao fim dos três dias, o manifesto responde sozinho ao dimensionamento de
retenção do §10: bytes por snapshot × atualizações × unidades, **medido**, sem
estimativa fictícia.

Vale especialmente capturar a **fase de transição** — o TSE disse que começa
tudo zerado e avança até 100%. É a única chance, antes de outubro, de ver
`andamento='p'`, `esnt > 0` e `esna > 0` com valores reais, que é o que o
motor de projeção precisa e o que a 1ª janela já não tem mais.

---

## O que NÃO se conclui do simulado

- **Que o modelo funciona.** Os dados são sintéticos: no simulado todos os
  candidatos ficam próximos de 1/n (12,6% para 8 candidatos a governador em
  AL, 10,4% para 10 a senador). Sem estrutura territorial, estratificar não
  ajuda — e de fato o backtest mostra M1 empatando ou perdendo do M0. Isso
  diz respeito aos dados, não ao modelo.
- **Que a calibração está boa.** Calibração exige backtest sobre eleições
  encerradas **reais** (2018 e 2022), que ainda não foram baixadas.
- **Que a capacidade medida vale para outubro.** O simulado não tem a carga
  de leitura pública nem a concorrência de dezenas de entidades coletando ao
  mesmo tempo.
