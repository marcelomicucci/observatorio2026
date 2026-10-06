# Observatório Eleitoral 2026

**A noite da eleição, antes de ela terminar.**

O Observatório acompanha a apuração do TSE voto a voto, mostra no mapa quem
lidera em cada canto do país e calcula para onde o resultado caminha — com a
margem de erro à vista e com cada projeção guardada, para conferir depois se
ela estava certa.

Estreou na noite de 4 de outubro de 2026, no 1º turno. Volta ao ar em 25 de
outubro, no 2º.

---

# Parte 1 — Para quem só quer entender

## O problema de toda noite de eleição

Às 19h a televisão diz: "com 12% das urnas apuradas, fulano tem 54%". E todo
mundo faz a mesma pergunta: **isso vai continuar assim?**

Quase nunca continua. As urnas não chegam misturadas: chegam primeiro as de
alguns lugares, depois as de outros, e cada lugar vota de um jeito. O
percentual das 19h não é um retrato do país — é um retrato de *quem já foi
contado*. Quem acompanhou 2022 lembra de ver um candidato liderar por horas e
ser ultrapassado no fim da noite. Nada tinha mudado na eleição; só a ordem em
que os votos chegaram.

## O que o Observatório faz de diferente

Em vez de esticar o percentual já contado, ele olha para **o que ainda falta
contar**. Para cada um dos mais de 5.500 municípios pendentes, estima quanta
gente votou e em quem, usando o que já chegou de lugares parecidos e como
aquele município votou na eleição anterior. Só depois soma tudo.

O resultado é uma frase que a apuração sozinha não consegue dizer: *"o
candidato tem 47% agora, mas deve terminar entre 49% e 52%"*.

## Funcionou?

Na noite de 4 de outubro, com menos de 6% dos votos contados:

| | erro médio em relação ao resultado final |
|---|---|
| o percentual apurado, como aparece na TV | 2,5 pontos |
| a projeção do Observatório | 1,4 ponto |
| **presidente** — percentual apurado | 4,6 pontos |
| **presidente** — projeção do Observatório | **0,8 ponto** |

Foram 55 disputas acompanhadas ao mesmo tempo — presidente, 27 governos e 27
eleições para o Senado — e mais de 26 mil projeções registradas ao longo da
noite. Depois dos primeiríssimos votos, nenhuma vez o sistema deu 90% ou mais
de chance a quem acabou perdendo.

E onde errou, está escrito: as faixas de incerteza saíram largas demais — o
modelo foi mais cauteloso do que precisava. Isso foi medido, corrigido e
assinado antes do 2º turno.

## Três coisas que ele nunca mistura

- **Contado** — o que o TSE já somou. É fato.
- **Matematicamente definido** — os votos que faltam não mudam mais o
  resultado. É aritmética, não palpite.
- **Projetado** — o que o modelo estima. É probabilidade, e sempre aparece
  com a margem.

"Eleito", sem adjetivo, é palavra do TSE. O Observatório só aponta um
"vencedor projetado" quando a chance passa de 99%, se mantém assim por três
rodadas seguidas e já há pelo menos 20% apurado. Uma chamada errada custa
mais do que dez chamadas atrasadas.

## Ele presta contas

Toda projeção é gravada no instante em que é feita, num diário que só aceita
acréscimo — nada é corrigido depois. No fim da noite, um gráfico mostra, para
cada candidato, a linha do que foi apurado e a linha do que foi projetado.
Se o modelo presta, a projetada nasce perto do resultado final e fica lá,
enquanto a apurada caminha até encontrá-la. Qualquer pessoa pode conferir.

## Não é só quem ganhou

- **Mapas** do país, de cada estado e de cada município: quem lidera, por
  partido e por campo ideológico.
- **O novo desenho do poder**: como ficam o Senado, a Câmara dos Deputados e
  as 27 assembleias legislativas, em esquerda, centro e direita.
- **Comparação com 2018 e 2022**: o que mudou em cada estado, em cada
  bancada, em cada partido.
- **Participação de verdade**: abstenção, brancos e nulos somados na mesma
  conta — porque a abstenção sozinha esconde se o eleitor de fato escolheu
  alguém.
- **Alertas da noite**: quem foi matematicamente eleito, onde haverá 2º
  turno, onde a liderança virou.

## Feito para uma sala cheia

Num notebook, o painel de quem opera. No projetor, o **telão**: mapa grande e
poucos números, legíveis a quatro metros, passando sozinho por um roteiro de
telas. E quando o grupo do WhatsApp pede "manda aí como está", um botão
fotografa as telas e escreve a legenda — com o aviso de que projeção não é
resultado oficial.

Tudo roda num computador só. A única coisa que vem de fora é o dado do TSE.

## O que ele não é

Não é pesquisa de intenção de voto: só trabalha com voto já depositado e
contado. Não substitui o TSE: quem proclama resultado é a Justiça Eleitoral.
E não esconde incerteza — quando não dá para afirmar, a tela diz que não dá.

---

# Parte 2 — Para quem vai abrir o código

## Estado atual

| | |
|---|---|
| **1º turno (04/10/2026)** | em produção no ambiente oficial do TSE; coletor no ar das 11h53 até o fim, sem 404 nem bloqueio; primeiro boletim às 17h21; zero divergência contábil |
| **Modelo em produção** | **M3.3**, homologado e assinado em 05/10/2026 (gate sobre 2022: vence o baseline em todas as rodadas; cobertura da faixa de 80% em 75,9% / 75,8%, tolerância ±5) |
| **Classificação ideológica** | assinada, com versão por ano (2018, 2022, 2026) e linhagem partidária |
| **Históricos** | 2014 (Senado), 2018 e 2022 importados e conferidos contra o oficial |
| **Testes** | 217 passando, 1 pulado |
| **2º turno (25/10/2026)** | códigos de eleição do TSE (6258/6260) ainda a conferir no catálogo; a seleção de turno no painel e no telão não foi exercitada com dado real |

## Stack

| camada | tecnologia |
|---|---|
| Linguagem | Python ≥ 3.11 |
| Coleta | `httpx` assíncrono, com limite de taxa, repetição e disjuntor próprios |
| Banco | SQLite via SQLAlchemy 2 (PostgreSQL opcional: extra `pg`) |
| Contratos | Pydantic 2 |
| Motor estatístico | NumPy (Monte Carlo) |
| API | FastAPI + Uvicorn |
| Linha de comando | Typer + Rich |
| Configuração | YAML (`config/`), relida em execução |
| Interface | HTML, CSS e JavaScript puros — sem framework, sem build, sem CDN |
| Mapas | malha do IBGE em JSON, servida localmente e desenhada em SVG |
| Captura de telas | Chrome ou Edge em modo headless, chamado por `subprocess` |
| Testes | pytest, pytest-asyncio, respx; fixtures com payloads reais do TSE |

Os scripts de estudo (`scripts/artigo/`) usam ainda matplotlib e Pillow, que
não são dependência do produto.

## Arquitetura

```
TSE (divulgação oficial)
   ↓  coletor: descobre o catálogo, detecta o que mudou, respeita o orçamento
arquivo bruto imutável (hash + manifesto)        ← fonte de verdade
   ↓  parsers + invariantes contábeis
banco SQLite (derivado, reconstruível)
   ↓                         ↓
motor de projeção        API FastAPI ──→ painel (notebook)
   ↓                         ↑        └─→ telão (projetor)
diário de projeções ─────────┘        └─→ captura para WhatsApp
(só acréscimo)
```

Três decisões organizam o resto:

- **O arquivo bruto é a fonte de verdade.** Os bytes do TSE são gravados com
  hash antes de qualquer interpretação e nunca mudam. O banco se apaga e se
  reconstrói inteiro a partir deles.
- **O navegador nunca fala com o TSE.** Painel e telão leem só a API local;
  fotos, perfis e malha ficam em disco. Com o projetor ligado, nada depende
  de rede além da coleta.
- **O motor pode cair sem derrubar a apuração.** Projeção é uma camada sobre
  o resultado, não um pré-requisito dele.

## Funcionalidades

### Coleta

- Lê a divulgação do TSE com teto interno de 20 req/s (o limite do TSE é de
  100 por IP).
- Não varre o país a cada ciclo: os arquivos de acompanhamento dizem quais
  municípios mudaram, e só esses são buscados.
- **Totais primeiro**: Brasil e as 27 UFs têm nível próprio na fila, a cada
  10 s, antes de qualquer município.
- O que o TSE finalizou sai das buscas e segue vigiado; se houver
  retotalização, volta.
- Deputado federal, estadual e distrital: só o resultado, sem projeção.
- Retoma em um segundo depois de reiniciado; desliga sozinho quando o TSE
  encerra Brasil e todas as UFs.
- Carga no banco contínua e incremental, a cada 5 s.

### Qualidade

Invariantes contábeis em cada arquivo recebido (`tv = vvc + vb + tvn`,
`te = est + esnt`…). Divergência crítica suspende a projeção e mantém a
apuração no ar.

### Projeção

Monte Carlo por município pendente: comparecimento, votos válidos e
preferência estimados em separado, com choques compartilhados por país, UF e
localidade. As 55 disputas são projetadas e registradas a cada 2 minutos.

| modelo | o que é | situação |
|---|---|---|
| M0 | extrapolação ingênua do já contado | referência: existe para ser batido |
| M1 | município encolhido para o estrato (UF × porte) | aposentado: subestimava a incerteza |
| M2 | hierárquico: município → porte → grupo da capital → UF | não passou no gate |
| M3 | M2 + inclinação histórica de cada município (o voto dele na eleição anterior) | foi o modelo do 1º turno |
| **M3.3** | M3 com a faixa 20% mais estreita | **em produção** |
| M3.1, M3.2, M3.4 | bloco ideológico, calibrador dinâmico, PIB agropecuário | experimentais, fora da produção |

O Senado tem tratamento próprio para as duas vagas de 2026: cada eleitor faz,
em média, menos de duas escolhas válidas.

### Regras de publicação

O diário guarda sempre o número que o modelo produziu. O que vai à tela passa
por regras fixas:

- **Gate estatístico.** Probabilidade só é publicada com o modelo homologado:
  precisa vencer o M0 no erro mediano em todas as rodadas e cobrir o
  intervalo dentro da tolerância, em eleição real já encerrada. A homologação
  é assinada e vale só para o modelo assinado.
- **Nível da faixa.** 80% ("tendência") até 15% apurado; 90% ("projeção")
  daí em diante. A tela sempre diz qual vale.
- **Probabilidade só a partir de 6% apurado.**
- **Acima de 95% apurado a faixa para de encolher** — o que falta entrar não
  se parece com o que já entrou.
- **Vencedor projetado** exige ≥ 99%, três registros seguidos e 20% apurado.
  Com uma vaga, só vale com maioria absoluta; senão, é "2º turno projetado".
- **Definição matemática** é aritmética, sem modelo. Voto sub judice em jogo
  suspende qualquer definição.

### Painel (`/`)

Cargos: Presidente, Governador, Senador, Deputado federal, Deputado estadual
e Recordes da noite. Visões:

| visão | o que mostra |
|---|---|
| Resultado | a contagem, com denominador declarado; seletor 2018 / 2022 / 2026 |
| Mapa | quem lidera, por partido ou ideologia; filtro por região, UF e município |
| Projeção | mediana, faixa e probabilidade de cada candidato |
| Convergência | apurado × projetado ao longo da noite |
| Auditoria | o que foi dito, contra o que deu |
| Comparar | território × anos, bancadas ano a ano, panorama dos 27 estados |
| Telão | edição do roteiro, "mostrar agora", captura para WhatsApp |
| Configuração | modelo em produção, chaves do coletor, classificação, homologação |

### Telão (`/telao`)

Roda sozinho o roteiro de `config/telao.yaml`, editável e gravável pelo
painel. Tipos de tela: recorte de uma disputa com mapa, mapa político,
composição (Senado, Câmara, assembleias), comparativo entre anos, resumo dos
cargos, panorama dos 27 estados, duelo presidencial estado a estado,
participação, blocos nas esferas de poder, deputados eleitos por UF e
recordes. No pé, a faixa de alertas da noite.

### Análise política

- Partido é fato; **ideologia é classificação analítica com autor** e só
  aparece assinada. Uma classificação por ano, cada alteração é versão nova.
- **Linhagem partidária** para comparar bancadas entre anos, com fusões e
  trocas de nome (União 2018 = PSL + DEM).
- Senado em duas linhas: eleitos no ano e a casa inteira, somando os
  mandatos em curso.

### Compartilhamento

Botão **📷 WhatsApp** no painel, ou `obs2026 compartilhar`: fotografa as
telas como aparecem no telão, escreve a legenda com os números do mesmo
instante e deixa tudo na área de transferência. O envio fica com quem opera,
de propósito. A legenda só traz projeção se o gate tiver passado.

## Regras que o código impõe

1. **Projetar territórios, depois agregar.** Nunca extrapolar o contado.
2. **Todo percentual declara seu denominador.** O percentual que o TSE exibe
   é sobre os votos nominais; a maioria absoluta é sobre outra base.
3. **Ausência é nulo com motivo, nunca zero.** Município sem voto contado
   está *aguardando*, não em 0%.
4. **Contado, matematicamente definido e projetado são três coisas.** Nunca
   se fundem num rótulo.
5. **Liderar um território não é ser eleito por ele.**
6. **Nada se resolve por adivinhação.** Município se identifica por código —
   `PAU D'ARCO` existe no PA e no TO.
7. **Toda projeção publicada foi registrada.**
8. **O que é editorial tem assinatura.** Classificação, linhagem e
   homologação levam nome e data, e ninguém assina por outra pessoa.

## Estrutura

```
config/              ambientes e fontes, cadência do coletor, limiares e
                     homologação, chaves de operação, roteiro do telão,
                     partidos, classificação por ano, linhagem
obs2026/
  tse/               urls, cliente com limite de taxa, catálogo, coletor
    parsers/         EA11, EA12, EA14/EA15, EA20
  storage/           arquivo bruto imutável, modelos do banco, carga incremental
  quality/           invariantes contábeis
  engine/            pendentes, denominadores, modelos, simulação, backtest,
                     definição matemática, chamada de vencedor, nível da
                     faixa, alertas, diário de projeções, registro contínuo
  history/           leitor dos arquivos históricos do TSE, importador,
                     banco histórico, anos congelados
  politics/          partidos, classificação, linhagem, Senado, Câmara
  candidates/        perfis e fotos, servidos localmente
  ingest/            auditoria da base de eleitorado
  api/               FastAPI: resultado, mapa, projeção, convergência,
                     auditoria, comparativo, político, alertas, recordes,
                     telão, compartilhar, configuração
  captura.py         fotografia das telas do telão
  homologacao.py     o ato que libera probabilidade
  operacao.py        chaves de operação e comandos ao coletor
  cli*.py            linha de comando
web/                 index.html (painel), telao.html, mapa.js, politico.js,
                     telas.js, geo/ (malha do IBGE)
scripts/             ensaios e gate sobre 2018/2022/2026, medições, atalhos
  artigo/            avaliação e figuras do estudo acadêmico
tests/               217 testes; fixtures com payloads reais do TSE
docs/                achados, metodologia, arquitetura, runbook, especificação
data/                arquivo bruto, bancos, diário, exports (fora do git)
```

## Como rodar

```bash
pip install -e ".[dev]"
```

```bash
python -m pytest tests/ -q
```

Uma vez, antes do dia — baixa para o disco tudo o que não é TSE (perfis,
fotos, malha, Senado em exercício):

```bash
python -m obs2026.cli preparar
```

No dia, o coletor (coleta, grava no banco e registra projeções):

```bash
python -m obs2026.cli coletar
```

E o servidor, que recusa subir se a porta já estiver em uso:

```bash
python -m obs2026.cli servir
```

Painel em `http://localhost:8026`, telão em `/telao`, API em `/api/docs`.
No Windows, `python scripts/criar_atalhos.py` cria o atalho que sobe o
servidor e abre o painel.

### Ambientes

O ambiente faz parte da chave do banco: dado de teste nunca se mistura com
resultado.

| `--env` | o que é |
|---|---|
| `oficial2026` | produção do TSE — padrão da linha de comando |
| `simulado2026` | servidor de simulado do TSE (setembro); dados de teste |
| `demo2022` | demonstração com resultado real de 2022 |
| `tse2018t1` … `tse2022t2` | eleições passadas, congeladas; o coletor recusa |

### Comandos

| comando | para quê |
|---|---|
| `descobrir` | lê o catálogo do TSE e mostra o que existe |
| `orcamento` | confere se a cadência cabe no limite de requisições |
| `ensaio` | ponta a ponta com as invariantes, antes de ligar a coleta |
| `coletar` / `servir` | a operação do dia |
| `carregar` | bruto → banco; reconstrói do zero com `--recriar` |
| `prever` | projeta uma disputa e registra |
| `previsoes` / `auditar-previsoes` | o diário, contra o resultado |
| `compartilhar` | captura as telas do telão com legenda |
| `preparar` / `preparar-senado` | baixa o que não é TSE |
| `importar-historico` / `montar-ano` | traz eleições passadas para o banco |
| `classificacao` / `assinar-classificacao` / `assinar-linhagem` | atos editoriais |
| `homologar` / `revogar-homologacao` | liga e desliga a probabilidade |
| `auditar-csv` | audita a base de eleitorado recebida |

## Validação do modelo

- **Gate** (`scripts/gate2022.py`): 55 disputas de 2022 reapuradas em nove
  rodadas, na ordem real de chegada e em ordem aleatória, contra o M0. É a
  evidência que a homologação assina.
- **Ensaios** (`scripts/ensaio2022.py`, `ensaio2026.py`): a noite refeita com
  qualquer modelo, para comparar variantes sem tocar na produção.
- **Avaliação ex ante** (`scripts/artigo/avaliar_diario2026.py`): o diário
  real do 1º turno contra o resultado final — é de onde saem os números da
  Parte 1.

2022 é eleição de teste: não se ajusta parâmetro nela.

## Documentação

- **[ACHADOS.md](docs/ACHADOS.md)** — o que os dados reais do TSE revelaram e
  as decisões que isso mudou. Comece por aqui.
- **[METODOLOGIA.md](docs/METODOLOGIA.md)** — a identidade da projeção,
  os modelos do M0 ao M3.3, regras de publicação, gate e a conferência
  do 1º turno.
- **[M3-DESENHO.md](docs/M3-DESENHO.md)** — histórico do desenho da família
  M3 e as referências bibliográficas.
- **[DEFINICAO-E-AUDITORIA.md](docs/DEFINICAO-E-AUDITORIA.md)** — definição
  matemática e prestação de contas.
- **[ARQUITETURA.md](docs/ARQUITETURA.md)** — componentes, orçamento de
  requisições, implantação.
- **[RUNBOOK-SIMULADO.md](docs/RUNBOOK-SIMULADO.md)** — operação passo a
  passo, escrita para os simulados de setembro.
- **[Especificação v1.0](docs/fonte/Especificacoes_Observatorio_Eleitoral_2026.md)**
  — o documento de origem.

## Limites conhecidos

- A cobertura do M3.3 passou no gate com margem pequena; será revista com
  duas noites reais, depois do 2º turno.
- O voto que falta de um município parcialmente apurado difere do que já
  chegou dele. O efeito foi medido no 1º turno e ainda não está no modelo.
- A projeção vai até o município; zonas e seções ficam fora.
- Proporcionais (deputados) entram só como resultado do TSE.
- Feito e operado em Windows; a captura de telas procura Chrome ou Edge nos
  caminhos padrão do sistema.

## Autoria

Projeto de Marcelo Micucci, que assina a classificação ideológica, a linhagem
partidária e a homologação do modelo. Os dados são da divulgação pública do
Tribunal Superior Eleitoral; a malha territorial e os dados econômicos, do
IBGE. As projeções não são resultado oficial.
