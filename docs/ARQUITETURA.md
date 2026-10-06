# Arquitetura e implantação

Estado em 06/10/2026, depois da entrada em produção no 1º turno (04/10).

## Princípios que organizam tudo

**O arquivo bruto é a fonte de verdade; o banco é derivado.** Os bytes
originais do TSE, com hash, URL, horário de geração, horário de captura e
versão do parser, são gravados antes de qualquer interpretação e nunca são
alterados. O banco pode ser apagado e reconstruído inteiro a partir deles.
Isso torna baratas decisões que seriam caras: mudar o schema, corrigir um
parser e reprocessar, provar o que o sistema sabia em determinado instante.

**Uma máquina só, e nada de rede além do TSE.** Coletor, banco, API, painel e
telão rodam no mesmo notebook. Fotos, perfis, malha do IBGE e composição do
Senado são baixados antes (`obs2026 preparar`) e servidos do disco. Com o
projetor ligado, a única dependência externa é a divulgação do TSE.

**A apuração não depende da projeção.** O motor estatístico é uma camada
sobre o resultado. Erro numa projeção é registrado e a rodada segue; a
contagem continua no ar.

**O ambiente é parte da chave.** Simulado, oficial e eleições passadas têm
banco, diário e arquivo bruto próprios. Dado de teste não se mistura com
resultado.

## Componentes

| Componente | Onde | Responsabilidade |
|---|---|---|
| Cliente TSE | `tse/client.py` | limite de taxa, repetição, disjuntor, checagem de host |
| Catálogo | `tse/catalog.py`, `tse/urls.py` | descobre ciclo, pleito, eleições e cargos |
| Coletor | `tse/collector.py` | três laços, fila por prioridade, parada automática |
| Parsers | `tse/parsers/` | EA11, EA12, EA14/EA15, EA20 |
| Arquivo bruto | `storage/raw.py` | payloads imutáveis, deduplicação por hash, manifesto |
| Qualidade | `quality/checks.py` | 22 verificações contábeis e de contrato sobre o EA20 |
| Carga | `storage/carga.py` | bruto → banco, incremental, totais antes dos municípios |
| Banco ao vivo | `storage/models.py` | 16 tabelas, SQLite |
| Motor | `engine/` | modelos, simulação, definição matemática, chamada, alertas |
| Registro contínuo | `engine/registro_continuo.py` | projeta as 55 disputas e grava no diário |
| Diário | `engine/audit.py` | projeções em JSONL, só acréscimo |
| Histórico | `history/` | importador, banco histórico, anos congelados |
| Política | `politics/` | partidos, classificação, linhagem, Senado, Câmara |
| API | `api/` | cerca de 50 rotas FastAPI |
| Painel e telão | `web/` | arquivos estáticos servidos pela API |
| Captura | `captura.py` | fotografa telas do telão com navegador sem cabeça |
| Operação | `operacao.py`, `homologacao.py` | chaves, comandos ao coletor, homologação |

## Processos

São dois, e podem ser reiniciados um sem o outro.

```
┌─ coletor ───────────────────────────────────┐    ┌─ servidor ──────────────┐
│  obs2026 coletar                            │    │  obs2026 servir         │
│                                             │    │                         │
│  laço de controle        (totais, 10 s)     │    │  API FastAPI            │
│  laço de acompanhamento  (mudanças, 20 s)   │    │  painel   /             │
│  laço de reconciliação   (varredura lenta)  │    │  telão    /telao        │
│  carga no banco          (5 s)              │    │  caches por lote        │
│  registro de projeções   (120 s, em thread) │    │  aquecedor de recortes  │
└──────────────┬──────────────────────────────┘    └────────────┬────────────┘
               │ escreve                                        │ lê
               ▼                                                ▼
        arquivo bruto ──→ banco SQLite (WAL) ◀──────────────────┘
        diário de projeções (JSONL) ◀───────────────────────────┘
```

Os dois conversam por arquivo, sem fila nem socket:

- `config/operacao.yaml` — chaves de operação (modelo em produção, pular
  finalizados, deputados, encerrar ao finalizar). Relido pela data de
  modificação: vale na próxima passada, sem reiniciar.
- `data/comandos/<ambiente>.json` — comandos do painel ao coletor e o
  batimento do coletor, que o painel mostra.
- `data/<ambiente>.lock` — um coletor por ambiente.

O servidor também sabe iniciar o coletor (botão na aba Configuração).

## Fluxo da coleta

```
ele-c.json (EA11)                  descobre ciclo, pleito, eleições, cargos
   ↓
mun-cm.json (EA12) por eleição     catálogo + código IBGE oficial
   ↓
┌──────────────── três laços concorrentes, orçamento compartilhado ────────┐
│ controle        EA20 Brasil + UF, a cada 10 s, sem depender de detecção   │
│ acompanhamento  EA14 + EA15, a cada 20 s → enfileira EA20 de quem mudou   │
│ reconciliação   varredura municipal rotativa, ciclo de 3.600 s            │
└──────────────────────────────────────────────────────────────────────────┘
   ↓
arquivo bruto (hash, manifesto)    imutável
   ↓
parser + verificações              divergência crítica suspende a projeção
   ↓
banco                              derivado, reconstruível
   ↓
API                                envelope com denominador e horários
   ↓
painel e telão                     o navegador nunca fala com o TSE
```

**Não varrer.** Uma varredura municipal completa dos três cargos são quase 17
mil arquivos — 14 minutos a 20 req/s. O EA15 de cada UF lista todos os
municípios com andamento e hora de totalização; a detecção custa 56
requisições a cada 20 s, e o EA20 municipal só é buscado para quem mudou.

**Totais primeiro.** A fila tem dois níveis: nada municipal sai enquanto
houver total de Brasil ou de UF esperando. Dentro do nível, prioridade com
envelhecimento, para que nenhum território fique sem atendimento. A carga
segue a mesma regra: numa rajada de três mil municípios, o total do estado é
gravado e confirmado antes.

**Finalizados fora.** O que o TSE marcou como finalizado sai das buscas e
segue vigiado pelo EA14/EA15; se houver retotalização, volta.

**Reconciliação.** Existe porque o acompanhamento pode chegar antes ou depois
do EA20: a detecção acelera, não é fonte de verdade.

**Proporcionais.** Deputado federal, estadual e distrital: só os 27 arquivos
de UF de cada cargo, em cadência lenta. Vão para o arquivo bruto e são lidos
direto de lá — não passam pelo banco nem pelo motor.

**Retomada.** Reiniciado no meio da noite, o coletor relê o que já sabia e
retoma em cerca de um segundo.

**Parada automática.** O coletor se desliga 10 minutos depois de o TSE
encerrar Brasil e todas as UFs. Se o Brasil estiver completo sem a marca de
finalizado, espera 30 minutos sem mudança: no 1º turno o TSE deixou o total
nacional sem a marca, e o coletor ficou 7 horas pedindo 25 mil arquivos por
hora que não mudavam.

## Orçamento de requisições

Limite do TSE: **100 req/s por IP**, com bloqueio de 10 minutos ao exceder —
e nova tentativa durante o bloqueio **reinicia** a contagem. Respostas 304
contam. 404 repetido também bloqueia.

Teto interno: **20 req/s**. Proteções:

- token bucket global, debitado **antes** de saber o status;
- trava por ambiente: o limite é por IP, não por processo;
- 404 **não é retentável** — suspende a fonte por 10 minutos e manda
  revalidar o catálogo;
- 403/429 param **toda** a coleta pela penalidade inteira;
- redirecionamento não é seguido automaticamente: o destino passa pela mesma
  checagem de host;
- `obs2026 orcamento` recusa cadência que não caiba no teto;
- nunca multiplicar IPs para contornar limite.

Na noite de 04/10 o coletor rodou das 11h53 até o encerramento sem 404 e sem
bloqueio.

## Dados

### Bancos

| arquivo | conteúdo | quem escreve |
|---|---|---|
| `data/db/<ambiente>.sqlite` | apuração ao vivo: território, disputas, candidatos, retratos, resultados, totais | o coletor, a cada 5 s |
| `data/db/historico.sqlite` | 2014 (Senado), 2018 e 2022: totais e votos por unidade | `importar-historico`, uma vez |
| `data/db/tse<ano>t<turno>.sqlite` | eleição passada no formato do ao vivo, 100% apurada | `montar-ano`, uma vez |

O histórico é separado do ao vivo de propósito: um representa apuração em
andamento, o outro, resultado final. Os bancos `tse<ano>t<turno>` existem
para o seletor de anos — as mesmas telas da noite leem 2018 e 2022 sem
aprender outro formato; a API troca de banco por pedido
(`?eleicao_passada=`), e o coletor recusa esses ambientes.

SQLite em modo WAL: o servidor lê enquanto o coletor escreve.

### Arquivos

```
data/raw/           arquivo bruto: payloads por hash + manifesto por dia
data/previsoes/     diário de projeções, um JSONL por ambiente
data/historico/     inclinação por município (2014, 2018, 2022), economia IBGE
data/institucional/ Senado em exercício (mandatos até 2031)
data/cache/         perfis e fotos de candidatos
data/telao/         imagens capturadas para compartilhar
data/exports/       ensaios, gate, avaliação do 1º turno
data/comandos/      comandos e batimento do coletor
```

Os arquivos históricos do TSE (votação por município e zona) ficam fora do
repositório, em `Dados/<ano>/`, e são lidos por `history/tse_odsele.py`.

### Configuração

Tudo em `config/`, em YAML. O que o painel grava — roteiro do telão, chaves
de operação, classificação, limiares — deixa antes uma cópia datada em
`config/<nome>.anterior/`.

| arquivo | o que guarda |
|---|---|
| `sources.yaml` | ambientes, códigos do TSE, hosts permitidos, cargos |
| `collector.yaml` | taxa, repetição, disjuntor, cadências |
| `operacao.yaml` | modelo em produção e chaves do coletor |
| `thresholds.yaml` | limiares, homologação e a evidência assinada |
| `telao.yaml` | roteiro do telão |
| `classificacao_<ano>.yaml` | ideologia dos partidos, assinada, por ano |
| `linhagem_partidos.yaml` | sucessões partidárias, assinada |
| `partidos_2026.yaml` | cadastro de partidos (número e sigla) |
| `arranjos_capitais.json` | grupo da capital de cada UF (IBGE) |

## Consistência

`source_generated_at` (quando o TSE gerou) e `captured_at` (quando recebemos)
são campos distintos, e a latência entre eles é medida separadamente do tempo
de fila.

Resultados agregados oficiais e soma local podem divergir temporariamente.
Nenhum substitui o outro em silêncio; a divergência aparece como marca de
qualidade.

Correções podem **reduzir** votos. Isso é revisão, não erro — as duas versões
ficam, e a redução é detectada como retotalização.

`idg` é registrado como evidência e **não** é tratado como contador global
crescente.

Os zips históricos do TSE misturam eleições suplementares (MT/2018 no
Senado, RR/2022 no governo); o leitor filtra a eleição ordinária.

## Desempenho

O telão troca de tela a cada 15–30 s e o painel atualiza a cada 10 s:
qualquer rota acima de 1 s aparece como travamento na parede. Medido com o
coletor trabalhando:

| | antes | depois | como |
|---|---|---|---|
| mapa municipal (5.755 linhas, 3,5 MB) | 4,2 s | 0,6 s | resposta em cache por lote de retratos, gzip |
| projeção para exibição | 56 s | 0,05 s | serve o último registro do diário, não recalcula |
| tela inteira do telão | 2,3 s | 0,6 s | os dois acima |

Regras que saíram disso:

- **Cache por lote, nunca por tempo.** A chave é o conjunto de retratos que a
  resposta leu; muda quando o dado muda.
- **Tela não recalcula projeção.** Painel, telão e legenda leem o diário. No
  pico do 1º turno um recálculo levava 112 s; o telão não espera mais de
  1,5 s pela projeção e segue sem ela.
- **Aquecedor discreto.** Refaz em segundo plano os recortes pedidos nos
  últimos 5 minutos, só depois de 4 s de silêncio e no máximo dois por
  passada.
- **Projeção fora do laço de eventos.** A simulação roda em thread; o coletor
  continua buscando.

Não medido: p95 formal das rotas e carga com muitos leitores simultâneos —
metas da especificação pensadas para publicação na internet, que não é o
desenho atual.

## Segurança

- O servidor escuta só em `127.0.0.1`.
- Rotas que gravam ou executam (roteiro, configuração, captura, comandos ao
  coletor) exigem pedido vindo da própria máquina, em JSON e da mesma origem
  — loopback sozinho não basta, porque qualquer página aberta no navegador
  de quem opera também está em loopback (`api/local.py`). Não há
  autenticação de usuário: o controle é estar na máquina.
- Domínios do TSE permitidos explicitamente, inclusive após
  redirecionamento.
- Teto de tamanho de payload e de descompactação.
- Texto externo passa por escape na exibição.
- Nenhuma identidade de eleitor é coletada; os dados são agregados.
- Barreira de ambiente: o catálogo recusa iniciar se a fase do payload não
  bater com o ambiente (`"o"` no oficial).
- Atos editoriais — classificação, linhagem, homologação — levam nome e
  data, e cada alteração é versão nova.

Hash local **não** autentica origem: detecta alteração e permite deduplicar,
só isso.

## Decisões que divergem da especificação, e por quê

**SQLite em vez de PostgreSQL.** A especificação propõe PostgreSQL. O SQLite
roda na máquina de quem opera, sem instalação, e sustentou a noite do 1º
turno. O schema usa só tipos portáveis e a escolha é da URL de conexão; como
o banco é derivado, migrar é um `carregar` a mais.

**Interface sem etapa de build.** A especificação propõe TypeScript/React em
hospedagem estática. Painel e telão são HTML e JavaScript servidos pela
própria API — operáveis sem toolchain e sem rede.

**Uma máquina em vez de serviços distribuídos.** A especificação descreve
coletor, banco gerenciado, armazenamento de objetos, API atrás de CDN. O
produto que foi ao ar é local, por decisão: a sala não pode depender de
rede. A arquitetura não impede a outra — o arquivo bruto é portável, a API
não guarda estado além de cache — mas ela não foi construída nem provada.

**Reconciliação rotativa em vez de periódica.** Ver
[ACHADOS.md §3](ACHADOS.md#3-a-cadência-proposta-no-81-não-cabia-no-orçamento).

**Zonas e seções fora.** EA16, EA18 e boletim de urna não são coletados; a
unidade mínima é o município.

## Implantação

### Preparar

```bash
pip install -e ".[dev]"
```

```bash
python -m pytest tests/ -q
```

```bash
python -m obs2026.cli preparar
```

```bash
python scripts/criar_atalhos.py
```

### Operar

```bash
python -m obs2026.cli coletar
```

```bash
python -m obs2026.cli servir
```

O padrão de `--env` é `oficial2026`. O `servir` recusa subir se a porta já
estiver em uso — dois servidores na mesma porta já confundiram a operação.
Mudança em código do motor só vale em processo reiniciado; mudança em
`config/` vale na passada seguinte.

O passo a passo, escrito para os simulados de setembro, está em
[RUNBOOK-SIMULADO.md](RUNBOOK-SIMULADO.md).

### Ambiente oficial

Códigos lidos do catálogo de produção do TSE em 04/10/2026: ciclo `ele2026`,
pleito 3220, eleição 6257 (presidente) e 6259 (governador, senador,
deputados). Três coisas só apareceram na produção:

- a fase oficial vem como `"o"`, não `"f"`;
- o catálogo traz dezenas de suplementares de outro ciclo — a descoberta só
  olha pleitos do ciclo coletado;
- presidente tem 5.757 localidades (5.571 municípios e 186 do exterior),
  duas a mais que no simulado.

**2º turno (25/10/2026):** eleições 6258 e 6260, ainda a conferir no
catálogo. A seleção de turno no painel e no telão não foi exercitada com
dado real.

### Plataforma

Feito e operado em Windows. A captura de telas procura Chrome ou Edge nos
caminhos padrão do sistema; os atalhos são `.cmd`.
