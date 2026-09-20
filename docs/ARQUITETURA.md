# Arquitetura e implantação

## Princípio que organiza tudo

**O arquivo bruto é a fonte de verdade; o banco é derivado.**

Os bytes originais do TSE, com hash, URL, horário de geração, horário de
captura e versão do parser, são gravados antes de qualquer interpretação e
nunca são alterados. O banco pode ser apagado e reconstruído inteiro a partir
deles.

Isso torna baratas decisões que seriam caras: trocar o motor do banco, mudar
o schema, corrigir um parser e reprocessar, provar o que o sistema sabia em
determinado instante. E torna verdadeira a exigência do §11.3 de que a
restauração não duplique snapshots — a carga é idempotente por construção.

## Componentes

| Componente | Responsabilidade | Estado |
|---|---|---|
| Coletor | descoberta, detecção de mudança, orçamento, circuito | **funcionando** |
| Arquivo bruto | payloads imutáveis, dedup por hash, manifesto | **funcionando** |
| Parsers | EA11, EA12, EA14/EA15, EA20 | **funcionando** |
| Qualidade | 11 invariantes contábeis | **funcionando** |
| Banco | 14 tabelas, SQLite/PostgreSQL | **funcionando** |
| API de leitura | 8 rotas com envelope do §9 | **funcionando** |
| Painel | 3 abas × 4 modos | **funcionando** |
| Motor estatístico | M0, M1, Senado, Monte Carlo, backtest | **funcionando, não homologado** |
| Classificação ideológica | máquina pronta, curadoria pendente | **desabilitado por desenho** |
| M2 hierárquico | depende de históricos | **não iniciado** |
| Zonas e seções | EA16/EA18/BU | **fora do caminho crítico** |

## Fluxo

```
ele-c.json (EA11)                  descobre ciclo, pleito, eleições, cargos
   ↓
mun-cm.json (EA12) por eleição     catálogo + código IBGE oficial
   ↓
┌──────────────── três laços concorrentes, orçamento compartilhado ────────┐
│ controle        EA20 br+UF, a cada 20 s, independente de detecção         │
│ acompanhamento  EA14+EA15, a cada 20 s → enfileira EA20 de quem mudou     │
│ reconciliação   varredura rotativa, ciclo de 1800 s, prioridade baixa     │
└──────────────────────────────────────────────────────────────────────────┘
   ↓
arquivo bruto (hash, manifesto)    imutável
   ↓
parser + invariantes               recusa ingerir fora de contrato
   ↓
banco                              derivado, reconstruível
   ↓
API                                envelope com denominador e lote
   ↓
painel                             navegador nunca fala com o TSE
```

A indisponibilidade do motor estatístico não interrompe a apuração.

## Decisões que divergem da especificação, e por quê

**SQLite em vez de PostgreSQL para os simulados.** O §8 propõe PostgreSQL, e
ele continua sendo o alvo de produção. Mas a dois dias do simulado, exigir
infraestrutura para operar é risco sem retorno: o SQLite roda na máquina do
operador, sem instalação, com WAL para o painel ler enquanto o coletor
escreve. O schema usa só tipos portáveis entre os dois, e a escolha é da URL
de conexão. Como o banco é derivado, a migração é um `carregar` a mais.

**Painel sem etapa de build.** O §8 propõe TypeScript/React com hospedagem
estática. O painel entregue é um arquivo estático servido pela própria API —
operável hoje, sem toolchain. O contrato da API está estável e documentado em
OpenAPI, então trocar por React depois não mexe em mais nada. A decisão é de
prazo, não de arquitetura.

**Reconciliação rotativa em vez de periódica.** Ver
[ACHADOS.md §3](ACHADOS.md#3-a-cadência-proposta-no-81-não-cabia-no-orçamento).

## Orçamento de requisições

Limite do TSE: **100 req/s por IP**, com bloqueio de 10 minutos ao exceder — e
nova tentativa durante o bloqueio **reinicia** a contagem. Respostas 304
contam. 404 repetido também bloqueia.

Proteções implementadas:

- token bucket global, debitado **antes** de saber o status;
- lockfile por ambiente: o limite é por IP, não por processo, e dois
  coletores somando requisições é um erro que só aparece no bloqueio;
- 404 **não é retentável** — suspende a fonte e manda revalidar o catálogo;
- 403/429 param **toda** a coleta pela penalidade inteira, sem "só um retry";
- redirecionamento não é seguido automaticamente: o destino passa pela mesma
  checagem de host;
- `assert_budget` recusa iniciar com cadência que não caiba no teto;
- nunca multiplicar IPs para contornar limite.

## Consistência

Cada resposta da API referencia um único `snapshot_set_id`: todos os cards
vêm do mesmo lote, sem misturar horários. `source_generated_at` (quando o TSE
gerou) e `captured_at` (quando recebemos) são campos distintos, e a latência
entre eles é medida separadamente do tempo de fila.

Resultados agregados oficiais e soma local podem divergir temporariamente.
Nenhum substitui o outro em silêncio; a divergência aparece como
`quality_flag`.

Correções podem **reduzir** votos. Isso é revisão, não erro — as duas versões
ficam, e a redução é detectada por `retotalizacao_detectada`.

`idg` é registrado como evidência e **não** é tratado como contador global
crescente.

## Implantação

### Desenvolvimento e simulados

```bash
pip install -e .
python -m pytest tests/ -q
python -m obs2026.cli descobrir
python -m obs2026.cli ensaio
```

### Produção

O ambiente `oficial2026` em `config/sources.yaml` está **deliberadamente
incompleto**: o TSE ainda não publicou os códigos de produção. O carregador
recusa operar nesse ambiente com a razão explícita, em vez de deixar `None`
virar string vazia e produzir uma URL que gera 404 em massa.

Quando os códigos saírem, preencher `base`, `ambiente` e `seed`. Nada será
adivinhado.

Topologia proposta:

- **coletor**: um processo, um IP, serviço persistente. Escala horizontal é
  contraindicada — mais processos no mesmo IP somam requisições.
- **banco**: PostgreSQL gerenciado.
- **arquivo bruto**: armazenamento de objetos, imutável, com versionamento.
- **API**: sem estado, escala horizontal livre, atrás de cache/CDN.
- **painel**: estático em CDN.
- **motor**: processo separado; sua queda não afeta a apuração.

O GitHub guarda código, migrações e configuração versionada. Não é banco de
resultados nem agendador de coleta.

Um desenho só com Workers de borda fica condicionado a prova de capacidade e
agendamento; nada nesta solicitação justifica forçar o motor estatístico para
a borda.

## Segurança

- domínios TSE permitidos explicitamente, inclusive após redirecionamento;
- teto de tamanho de payload e de descompactação;
- texto externo passa por unescape e escape na exibição;
- nenhuma identidade de eleitor é coletada; os dados são agregados;
- administração autenticada, separada da leitura pública;
- barreira de ambiente estrutural: `environment` entra na unicidade do banco,
  e o catálogo recusa iniciar se a fase do payload não bater com o ambiente.

Validação de assinatura, quando o TSE disponibilizar o procedimento. Hash
local **não** autentica origem — ele detecta alteração em trânsito e permite
deduplicar, só isso.

## Metas de desempenho

As metas do §10 (p95 ≤ 500 ms em leitura cacheada, carga útil ≤ 3 s,
processamento ≤ 60 s para observado) **ainda não foram medidas**. Teste de
carga de 1.000 leitores simultâneos e medição de p95 estão pendentes.

O que já se mede: latência da fonte (geração no TSE → nossa captura) aparece
no painel, e é grandeza distinta do tempo de fila. Nenhuma das duas está
garantida pelas metas acima — e a latência da fonte não está sob nosso
controle.
