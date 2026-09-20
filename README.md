# Observatório Eleitoral 2026

Acompanhamento da apuração de presidente, governador e senador, com projeção
dos territórios pendentes e análise territorial e institucional do voto.

Implementação da [especificação v1.0](../Especificacoes_Observatorio_Eleitoral_2026.md).
Validado contra os payloads reais do servidor de simulado do TSE em
20/09/2026.

> **Ambiente de simulado.** Tudo que este repositório lê hoje vem do ambiente
> de teste do TSE. Não são resultados eleitorais. A barreira é estrutural: o
> ambiente entra na chave de unicidade do banco e a coleta recusa iniciar se a
> fase do payload não bater com o ambiente configurado.

---

## Começar

```bash
pip install -e .
python -m pytest tests/ -q
```

41 testes rodando contra payloads reais do TSE. Se quebrarem, o contrato
mudou.

```bash
python -m obs2026.cli descobrir      # catálogo oficial, sem inventar nada
python -m obs2026.cli orcamento      # cabe no limite de requisições?
python -m obs2026.cli ensaio         # ponta a ponta, com as invariantes
python -m obs2026.cli coletar        # coleta contínua
python -m obs2026.cli carregar       # bruto → banco (idempotente)
```

```bash
python -m uvicorn obs2026.api.app:app --port 8026
```

Painel em `http://localhost:8026`, API em `/api/docs`.

```bash
python -m obs2026.cli auditar-csv ../eleitorado_eleicao_capilaridade.csv.zip
```

---

## Estado

| | |
|---|---|
| **Coletor** | funcionando. 161 requisições contra o TSE, 0 erros, 0 404, 0 bloqueios |
| **Parsers** | EA11, EA12, EA14/EA15, EA20 — validados contra payloads reais |
| **Qualidade** | 11 invariantes contábeis, 0 divergências críticas em 362 arquivos |
| **Banco** | 14 tabelas, 5.784 geografias, 4.367 resultados carregados |
| **API** | 8 rotas, envelope completo do §9 |
| **Painel** | 3 abas × 4 modos, servindo dados reais |
| **Motor** | M0, M1, Senado, Monte Carlo e backtest funcionando — **não homologado** |
| **Mapa ideológico** | máquina pronta, **curadoria pendente por desenho** |
| **Projeção publicada** | **não**, e o sistema diz por quê |

Duas coisas estão deliberadamente desligadas, e ligá-las sem o trabalho que
falta seria pior do que deixá-las desligadas:

**A projeção não publica probabilidade.** O gate do §11.2 exige superar o
baseline ingênuo M0 e demonstrar cobertura de intervalo compatível com o
nominal, em backtests sobre eleições encerradas **reais**. Rodando sobre o
simulado, o M1 não supera o M0 — mas isso diz respeito aos dados sintéticos,
não ao modelo. Ver [METODOLOGIA.md](docs/METODOLOGIA.md#estado-atual-o-gate-não-passou).

**O mapa ideológico está desabilitado.** Classificar partidos é ato
editorial, e o §6 exige fonte, justificativa, autor, data, validade e grau de
controvérsia por entrada. A máquina está pronta e `config/classificacao_2026.yaml`
está vazio. Preencher por conta própria seria inventar autoria.

---

## Documentação

- **[ACHADOS.md](docs/ACHADOS.md)** — o que os dados reais revelaram, incluindo
  seis achados que mudam decisões da especificação. Comece por aqui.
- **[RUNBOOK-SIMULADO.md](docs/RUNBOOK-SIMULADO.md)** — operação nos dias
  22, 23 e 24/09.
- **[METODOLOGIA.md](docs/METODOLOGIA.md)** — projeção, Senado, backtest.
- **[ARQUITETURA.md](docs/ARQUITETURA.md)** — componentes, orçamento de
  requisições, implantação.

---

## As três regras que o código impõe

**1. Projetar territórios, depois agregar.** Nunca extrapolar o percentual
nacional contado. A ordem de chegada não é aleatória.

**2. Todo percentual declara seu denominador.** O percentual que o TSE exibe
é sobre a soma dos votos dos candidatos, incluindo anulados e sub judice — e
não é o percentual que decide o primeiro turno, que é sobre votos válidos. A
API devolve `null` com motivo quando numerador e denominador não pertencem ao
mesmo universo.

**3. Ausência é `null` com motivo, nunca zero.** Município sem voto contado
tem percentual indefinido, não 0%. Partido sem classificação é desconhecido,
não centro.

---

## Estrutura

```
config/          sources, cadência, limiares assinados, classificação
obs2026/
  tse/           urls, client, catalog, collector, parsers/
  storage/       raw (bruto imutável), models, db, ingest
  engine/        pending, denominators, simulate, models, backtest
  quality/       invariantes contábeis
  politics/      classificação ideológica versionada
  api/           FastAPI
  ingest/        conciliação da base de eleitorado
web/             painel
tests/fixtures/  payloads reais do TSE de 20/09/2026
data/raw/        arquivo bruto (fora do git)
```

---

## Próximos passos, em ordem

1. **Capturar as janelas de 22–24/09**, especialmente a fase de transição de
   0% a 100%. É a única chance antes de outubro de exercitar `A_pendente` com
   apuração em andamento — os arquivos da 1ª janela já estão todos finais.
2. **Baixar os históricos do TSE de 2018 e 2022** e harmonizar território e
   partidos. Sem isso não há calibração possível.
3. **Medir e fixar `max_rps`** a partir do simulado. A folga atual de
   3,6 req/s não basta para a noite de apuração.
4. **Corrigir a dependência dos σ com a cobertura.** Aos 5% contados, os
   intervalos de 80% cobriram 50–75%: estreitos demais.
5. **Curar a classificação ideológica**, com autoria nominal.
6. **Medir as metas do §10** — p95, carga útil, 1.000 leitores.

---

## Scripts auxiliares

```bash
python scripts/coletar_uf.py al senador      # coleta uma UF inteira
python scripts/backtest.py   al senador      # backtest por cortes de cobertura
```

## Medições de 20/09/2026

| | |
|---|---|
| requisições ao TSE | 386 capturas, 0 erros, 0 404, 0 bloqueios |
| bytes por snapshot | 30.618 em média |
| arquivo bruto | 2,1 MB para 386 capturas (deduplicado por hash) |
| req/s efetivo no ensaio | 1,11 (limitado pelo jitter, não pelo teto) |
| invariantes | 0 divergências críticas em 362 arquivos EA20 |

O dimensionamento de retenção do §10 sai daí, medido: bytes por snapshot ×
atualizações × unidades. Uma varredura municipal completa dos três cargos
(16.897 arquivos) custa cerca de **517 MB** por passada sem deduplicação — e
muito menos com ela, já que a maioria dos arquivos não muda entre passadas.
