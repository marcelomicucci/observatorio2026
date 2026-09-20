# Observatório Eleitoral 2026
## Especificações funcionais, estatísticas e técnicas — versão 1.0

Preparado para Marcelo Micucci em 20/09/2026. Documento de projeto; não representa sistema implementado, modelo calibrado ou previsão eleitoral atual. Números de desempenho e parâmetros identificados como propostas dependem de homologação.

## 1. Objetivo e decisões estruturais

Construir uma plataforma para acompanhar a apuração de presidente, governador e senador, estimar resultados finais com incerteza explícita e analisar a distribuição territorial e institucional do voto após a eleição.

A pergunta principal será: **considerando o que já foi contado e o perfil dos lugares que faltam, quais resultados ainda são plausíveis?**

Decisões recomendadas:

1. Separar resultado oficial observado, projeção estatística e interpretação política em todas as telas e exportações.
2. Cobertura municipal nacional no primeiro produto; zona eleitoral como refinamento prioritário; seção eleitoral como evolução condicionada à disponibilidade e validação dos boletins.
3. Projetar votos restantes por território e agregar contagens. Nunca extrapolar simplesmente o percentual nacional já apurado.
4. Implementar motor específico para o Senado, com duas escolhas por eleitor e duas vagas por UF em 2026.
5. Tratar ideologia como classificação analítica documentada e versionada, separada da posição em relação ao governo.
6. Manter uma série de snapshots para reproduzir exatamente o que o sistema sabia em cada momento.
7. Usar aplicação independente com código no GitHub. O plugin WordPress é referência de integração, não dependência obrigatória.

## 2. Evidência disponível e lacunas

### 2.1 Auditoria da base fornecida

Arquivo: `eleitorado_eleicao_capilaridade.csv.zip`. Leitura do CSV com separador ponto e vírgula e codificação compatível com Windows-1252. Não foi alterado.

| Verificação | Resultado calculado no arquivo |
|---|---:|
| Ano | 2026 |
| Data de carga informada | 20/09/2026 06:30:41 |
| Linhas de localidades | 5.757 |
| Linhas com país Brasil | 5.571 |
| Linhas com país Exterior | 186 |
| Aptos, incluindo exterior | 158.745.463 |
| Aptos no exterior | 918.876 |
| Aptos no Brasil, excluindo exterior | 157.826.587 |
| Aptos no Pará | 6.265.355 |
| Soma da coluna de seções | 517.179 |
| Duplicações exatas de UF + nome | 0 |

Esses totais descrevem a exportação recebida; devem ser reconciliados com a configuração oficial do pleito antes da produção. “Localidade eleitoral” não equivale necessariamente a município administrativo do IBGE. Não converter automaticamente as 5.571 linhas brasileiras em uma contagem de municípios do IBGE.

Colunas presentes: ano, município, UF, região, país, quantidades de municípios, zonas, seções, aptos e data de carga.

Limitações: não há códigos TSE/IBGE, identificação de cada zona/seção, distribuição de aptos entre seções, abstenção histórica ou votação anterior. A quantidade de zonas por município não pode ser somada para inferir zonas únicas: uma zona pode abranger mais de um município.

Importação: preservar original e hash; normalizar nomes apenas para procurar correspondências; resolver UF + nome contra catálogo oficial; criar tabela explícita TSE ↔ IBGE; revisar ambiguidades e localidades especiais. Proibir associação aproximada silenciosa. Registros não conciliados entram em quarentena com seu peso eleitoral visível.

### 2.2 Fontes e contratos

| Fonte | Função no projeto | Condição |
|---|---|---|
| CSV recebido | Base inicial de aptos e cobertura | Inspecionado nesta especificação |
| EA11 | Descobrir eleição, turno, ciclo e caminhos | Parser a homologar |
| EA12 | Catálogo de localidades | Complementação obrigatória; correspondência IBGE separada |
| EA14 / EA15 | Direcionar coleta por abrangência alterada | Homologar contratos e sincronização |
| EA20 | Resultados e andamento | Documento examinado; testar payloads reais |
| Candidatos 2026 | Identidade e cadastro partidário | Importação a implementar |
| EA16 / EA18 / BU | Refinamento por seção | Evolução, fora do caminho crítico inicial |
| Resultados históricos TSE | Priors, comparação e validação | Ainda não baixados |
| Malhas e correspondências IBGE | Geometria e recortes territoriais | Versões a selecionar |
| Senado Federal | Mandatos remanescentes e composição | Necessário para visão da Casa inteira |

O EA20 documenta abrangências nacional, UF, município e zona. A árvore de eleitorado diferencia totalizadas, instaladas, apuradas e não apuradas; esses conjuntos não são intercambiáveis. Campos centrais: `e.te`, `e.est`, `e.esnt`, `e.esa`, `e.esna`, `e.esni`, `e.c`, `e.a`; seções em `s`; candidatos em `carg[].agr[].par[].cand[]`. Preservar destinação do voto e situação oficial. [EA20 — TSE](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea20-arquivo-de-resultado-unificado).

Não fixar códigos de eleição a partir de exemplos de simulado. Construir URLs pelo catálogo e pelos padrões oficiais, preservando zeros à esquerda. [Instruções de download — TSE](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-instrucoes-para-download-dos-arquivos-da-divulgacao-2026).

Os documentos EA11, EA12 e EA15 estavam acessíveis por link, mas sem extração textual útil nesta consulta. Seus dicionários completos e respectivas amostras são pendências de homologação, não contratos já validados.

### 2.3 Avaliação do projeto Resultado-TSE

Foram examinados README, documentação arquitetural e trecho do cliente PHP, sem executar o plugin nem realizar auditoria integral. Há coleta no servidor, snapshots e API local. A documentação apresenta mapas EA14/EA15 como planejados. Não foi identificado um motor estatístico documentado para a projeção solicitada.

Há uma divergência relevante: a documentação descreve pausa geral por 404, enquanto o trecho atual trata fonte inexistente isoladamente. Logo, documentação não substitui revisão do código. Os metadados consultados não identificaram licença; verificar licença explícita antes de reutilizar código. Recomendação: aproveitar conceitos e construir os adaptadores necessários independentemente. [Documentação do projeto](https://github.com/pricevicius/Resultado-TSE/blob/master/DOCUMENTACAO-PLUGIN-APURACAO.md) · [Cliente examinado](https://github.com/pricevicius/Resultado-TSE/blob/master/includes/class-tse-client.php).

## 3. Escopo e regras eleitorais

| Cargo | Unidade decisória | Saída da projeção |
|---|---|---|
| Presidente | Brasil, incluindo exterior | Votos finais; probabilidade de vitória no turno; de maioria absoluta no primeiro turno; de classificação |
| Governador | Cada UF/DF | Mesmas métricas, calculadas separadamente por disputa |
| Senador | Cada UF/DF | Votos finais; probabilidades de 1º, 2º e fora das vagas; combinação de eleitos |

No primeiro turno presidencial e estadual, distinguir ultrapassar 50% dos votos válidos de apenas liderar. No Senado, ordenar candidatos em cada realização conjunta e selecionar duas vagas, observadas elegibilidade e regras oficiais. Empates exatos e situações judiciais exigem tratamento próprio; não usar ordem alfabética como desempate.

O município é um recorte do voto, não a unidade que elege presidente, governador ou senador. “Líder no município” nunca será apresentado como “eleito pelo município”. Exterior participa do cálculo presidencial; não criar disputa de governador ou senador em ZZ. Segundo turno possui eleição, parâmetros de comparecimento e modelo próprios; não transferir percentuais do primeiro turno automaticamente.

A FAQ confirma duas vagas senatoriais por UF em 2026. A implementação deve obedecer à regulamentação vigente e aos estados oficiais do TSE, inclusive pendências de atribuição de eleitos. [FAQ técnica](https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados) · [Resolução 23.751/2026](https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-751-de-26-de-fevereiro-de-2026).

Deputados ficam fora da projeção inicial. Sua inclusão futura é necessária para uma leitura completa do poder legislativo; resultados majoritários sozinhos não medem governabilidade.

## 4. Motor de projeção

### 4.1 Unidade de cálculo e granularidade

A unidade inicial é município × cargo × turno. Onde houver dados consistentes por zona, usar município × zona × cargo × turno. A chave deve incluir município, pois zonas não formam necessariamente uma árvore territorial simples.

Para cada território, escolher uma única partição: município inteiro OU conjunto completo de suas zonas. Proibir soma de pai e filhos. Se o detalhe estiver incompleto, manter o município como unidade ou representar explicitamente um residual sem sobreposição. O detalhe por seção só substitui a unidade anterior após reconciliar cobertura e aptos.

Mais granularidade não garante menos erro: unidades pequenas exigem regularização para evitar oscilações artificiais. O dashboard mostrará a granularidade efetivamente usada pelo modelo.

### 4.2 Identidade fundamental

Para unidade g, candidato k e instante t:

`votos_finais[g,k] = votos_observados[g,k,t] + votos_restantes_estimados[g,k,t]`

Para presidente/governador, em cada simulação b:

`R[g,b] = A_pendente[g] × comparecimento_restante[g,b] × taxa_valida_restante[g,b]`

`V_final[g,k,b] = V_observado[g,k] + R[g,b] × preferencia_restante[g,k,b]`

Os valores simulados serão inteiros no processamento final, com alocação que conserva o total e respeita limites. Preferências somam 1 no universo válido. Total nacional/estadual é soma de votos, e percentual é calculado depois dessa soma, usando o denominador da mesma simulação.

`A_pendente` não será calculado indiscriminadamente como aptos × percentual de seções restantes. Deve representar eleitores de unidades cujo resultado ainda pode entrar na totalização, conciliando estados oficiais. Eleitores de seções encerradas sem votação não são votos futuros. Pendências judiciais entram em cenários separados, não como novos eleitores.

Na indisponibilidade dos campos necessários, uma aproximação por seções poderá aparecer somente como cenário exploratório identificado, com incerteza ampliada e sem probabilidades de vitória publicadas.

### 4.3 Comparecimento e votos válidos

Estimar separadamente:

1. Comparecimento esperado nos locais pendentes.
2. Taxa de votos válidos por cargo, incluindo brancos, nulos e outras destinações segundo o contrato.
3. Distribuição dos votos válidos restantes entre candidatos.

O comparecimento observado precisa usar o eleitorado correspondente ao mesmo universo de seções, não todo o eleitorado cadastrado. Havendo diferenças entre instaladas e apuradas, o adaptador deve reconciliá-las antes do uso estatístico. Sem reconciliação, abstenção observada fica indisponível para atualização do modelo.

Abstenção histórica será prior; a parcial atual atualizará esse prior conforme representatividade territorial. Não aplicar a abstenção nacional uniformemente ao país nem reaplicar abstenção sobre votos já contados.

### 4.4 Baselines e modelo recomendado

**M0 — diagnóstico ingênuo:** extrapolação do percentual contado. Serve apenas como comparação de erro.

**M1 — estratificação municipal:** comparecimento e votos válidos por município, com regularização regional e preferência histórica ajustada. É a primeira versão de produção candidata.

**M2 — modelo hierárquico territorial:** efeitos nacional, UF, região e unidade local; preferências multivariadas, turnout e validade com dispersão adicional; atualização a partir de territórios comparáveis e diferenças históricas. Seleção de família estatística e hiperparâmetros mediante backtests.

Para localidades ainda zeradas, usar estimativa de grupo e histórico, nunca zero como preferência eleitoral. Para candidatos novos, não transferir votos de outro candidato só por partido: registrar a regra de correspondência e ampliar a incerteza. Pesquisas podem compor cenário inicial opcional, com registro, método e data, sem inventar estimativas municipais a partir de amostra nacional.

Partidas históricas recomendadas: 2018 e 2022 para presidência/governadores; 2018 é especialmente útil para Senado com duas vagas, e 2022 requer ajuste por ter dinâmica distinta. Harmonizar território e partidos por ano. Não comparar automaticamente candidatos diferentes como se fossem continuidade pessoal.

### 4.5 Viés da ordem de chegada

A apuração é uma sequência administrativa, não uma amostra aleatória. O modelo deve considerar que os lugares pendentes podem votar e se abster diferentemente dos já recebidos.

Variáveis candidatas: porte eleitoral, região, capital/interior, votação histórica, cobertura local e padrão de chegada observado. Fatores adicionais só entram com dados verificáveis e ganho de validação. Atualização de preferência deve usar unidades comparáveis, com efeitos compartilhados; tratar cada voto como observação independente produziria confiança excessiva.

O painel explicará a mudança da projeção em componentes: nova votação observada, localização do eleitorado pendente, atualização de comparecimento, validade e preferência. Isso permite distinguir uma mudança de composição territorial de uma mudança na estimativa local.

### 4.6 Senado: modelo de duas escolhas

Projetar comparecimento restante C e quantidade média de escolhas válidas por comparecente q, entre 0 e 2. O total esperado de escolhas válidas restantes é `C × q`; não assumir que todos fazem duas escolhas válidas.

Impor em cada simulação: votos adicionais de um candidato ≤ comparecentes restantes; soma de votos adicionais ≤ duas vezes esses comparecentes; candidato não pode receber dois votos válidos do mesmo eleitor.

Modelo preferencial: distribuição sobre pares distintos de candidatos, escolhas únicas e nenhuma escolha válida. As contagens agregadas não identificam os pares escolhidos por pessoa. Portanto, dependência entre escolhas será hipótese explícita, estimada apenas quando houver evidência e submetida a sensibilidade. Alternativa inicial: marginais conjuntas restritas, sem alegar recuperação de pares individuais.

Exibir percentual entre escolhas válidas e, quando disponível, votos do candidato por comparecentes como métricas distintas. Percentuais sobre eleitores podem somar até 200%; percentuais sobre escolhas válidas somam 100%. Não dividir a votação de cada candidato por dois.

Gerar probabilidade de estar entre os dois primeiros, distribuição de cadeiras por bloco e margem do segundo para o terceiro. Não modelar “primeiro voto” e “segundo voto” como eleições independentes.

### 4.7 Incerteza e publicação

Proposta inicial: 5.000 simulações conjuntas por execução, ajustáveis conforme erro de Monte Carlo e tempo medido. Compartilhar choques territoriais e nacionais entre unidades; não somar variâncias independentes de municípios. Medianas e intervalos de 80%/95% são extraídos das somas simuladas. Quantis municipais não devem ser somados.

Incluir incerteza de comparecimento, validade, preferência, chegada e parâmetros. Voto já validado fica fixo no cenário-base; retotalizações criam nova versão. Sensibilidades judiciais e de dados são apresentadas separadamente.

Estados do produto: aguardando; cenário exploratório; projeção disponível; projeção suspensa; resultado oficial. Publicar probabilidades somente após aprovação de calibração, cobertura territorial e qualidade. Um limiar único como “10% apurado” é insuficiente.

Requisitos para liberar projeções: correspondência de 100% do eleitorado ou residual explicitamente modelado; nenhuma divergência contábil crítica; cobertura por estratos suficiente segundo backtest; inexistência de atraso crítico em territórios com peso decisivo. Limiares numéricos serão congelados em configuração assinada antes da operação.

Exibir motivo de suspensão e manter apuração oficial disponível. A versão inicial não fará anúncio automático de vencedor. “Projetado”, “matematicamente definido pelo TSE” e “eleito segundo TSE” terão rótulos distintos. Intervalo preditivo não será chamado de margem de erro de pesquisa.

## 5. Dashboard

### 5.1 Navegação e filtros

Abas principais: **Nacional, Estados e Municípios**. Em cada aba, modos **Apuração, Projeção, Mapa político e Histórico**. Área administrativa separada.

Filtros persistentes na URL: ano, turno, cargo, região, UF, município, candidato, partido, bloco ideológico, estado da apuração e instante histórico. Seleções incompatíveis ficam desabilitadas com explicação. Preferências de exibição podem ser salvas; filtros não modificam a definição oficial de uma disputa.

| Visão | Conteúdo obrigatório |
|---|---|
| Nacional / Presidente | Ranking, votos e percentual oficial; projeção e intervalos; regiões; exterior; pendentes; série temporal |
| Nacional / Governador | Grade de 27 disputas; vitórias oficiais e cenários de governos por bloco; sem ranking nacional de candidatos |
| Nacional / Senado | Disputas por UF; distribuição das 54 vagas em renovação; composição completa em módulo próprio |
| Estado | Disputa estadual, voto presidencial na UF, municípios, contribuição para margem e eleitorado pendente |
| Município | Todos os candidatos do cargo; cobertura; previsão local; comparação histórica; zonas quando validadas |

Cards sempre mostrarão horário da fonte e da captura, votos válidos, brancos/nulos conforme definição, comparecimento, seções totalizadas e cobertura de eleitorado. Percentual de votos finais já recebido é estimativa e terá rótulo próprio.

Ranking de territórios pendentes: ordenar por volume esperado de votos ou contribuição para a incerteza. Não transformar essa funcionalidade analítica em recomendação de persuasão ou direcionamento de campanha.

### 5.2 Gráficos e interação

Linha do percentual observado versus projetado com faixa de incerteza; evolução da margem; barras de cobertura territorial; tabela de candidatos; mapa; distribuição das simulações. Permitir clicar no estado e depois município, comparar até quatro territórios e retornar ao recorte anterior.

Replay terá relógio virtual e opção de acelerar/pausar. Mostrar apenas informações conhecidas no instante selecionado. Mudanças de modelo estarão marcadas; reanálise retrospectiva com modelo novo será outra série.

Exportação CSV e imagem dos gráficos, com filtros, fonte, horário, versão do modelo/classificação e distinção observado/projetado. Relatório pós-eleição poderá ser produzido posteriormente a partir dos mesmos dados.

## 6. Mapa político e ideológico

Paleta solicitada: esquerda em vermelho (`#C62828`), centro em amarelo (`#F2C94C`), direita em azul (`#1565C0`). Sem classificação em cinza; ausência de dado tem padrão visual distinto. Legenda textual, contornos e símbolos garantem que a leitura não dependa apenas da cor.

Cadastro versionado por partido/candidato e ano: categoria, fonte, justificativa, autor, data, validade temporal e grau de controvérsia. Centro-esquerda/centro-direita podem existir internamente, com regra explícita de agrupamento. Não atribuir automaticamente a cor do cabeça de chapa a toda a coligação. Não interpretar classificação como atributo oficial do TSE.

Separar três dimensões: ideologia; apoio/oposição ao governo; alianças estaduais. Uma não determina a outra. Alterações não reescrevem silenciosamente mapas históricos. Comparações entre anos devem poder usar classificação de cada época ou classificação harmonizada, identificando o método.

Camadas:

1. Partido/bloco do candidato líder: intensidade expressa margem, com indicação de parcial/projeção/final.
2. Participação dos votos de cada bloco: mostra composição, evitando resumir município inteiro pelo vencedor.
3. Governos conquistados: contar governos e eleitorado/população abrangidos separadamente.
4. Senado: duas marcas por UF para as vagas em disputa, admitindo cores diferentes.
5. Variação entre eleições comparáveis: pontos percentuais e mudança de liderança.
6. Incerteza e ausência de informação: camada própria, não usar o amarelo do centro para “indefinido”.

Mapa territorial deve ser acompanhado de barras ponderadas por votos/eleitorado e opção de símbolos proporcionais. Grandes áreas geográficas não representam necessariamente muitos eleitores.

Indicador descritivo opcional: `saldo = % votos direita − % votos esquerda`, de −100 a +100 pontos. Sempre mostrar também centro e não classificados; saldo zero pode indicar situações políticas muito diferentes. Não apresentar o saldo como medida psicológica de polarização.

Senado completo: 54 vagas em renovação + 27 mandatos remanescentes, com data da composição, partido e substituições pertinentes. A análise de maioria usará cenários explícitos de alinhamento, sem presumir disciplina automática por cor. A ausência da Câmara deve aparecer como limitação de qualquer leitura de governabilidade.

## 7. Pós-eleição

Congelar snapshots finais e permitir novas versões por retotalização. Relatórios: comparação 2018/2022/2026; margens e concentração territorial; participação/abstenção; votos por blocos; divergências entre voto presidencial, estadual e senatorial; renovação institucional; avaliação do próprio forecast.

Decompor variações agregadas em mudança do tamanho do eleitorado, comparecimento, validade e composição do voto. Quando houver decomposição contrafactual, registrar ordem ou usar média das ordens, evitando atribuição arbitrária de contribuição.

Não inferir que os mesmos indivíduos migraram entre candidatos a partir de totais municipais. Não concluir polarização afetiva, radicalização ou causalidade econômica apenas pelo mapa. Estudos agregados descrevem associações; hipóteses causais exigem desenho adicional. Eventual índice de concentração, dispersão ou segregação territorial precisa de definição e referência metodológica antes de entrar no produto.

Análises sobre os caminhos do Brasil serão cenários condicionais apoiados em composição institucional, alianças e agendas documentadas. Não converter vitória de um bloco em previsão determinística de política pública.

## 8. Arquitetura técnica proposta

| Componente | Responsabilidade | Opção inicial |
|---|---|---|
| Front-end estático | Interface e mapas | TypeScript/React, hospedagem estática/CDN |
| API de leitura | Entregar snapshots e filtros | Serviço HTTP cacheado |
| Coletor | Descoberta, polling, retries, normalização | Serviço persistente com fila |
| Motor estatístico | Simulação, agregação, diagnóstico | Python em processo separado |
| Banco transacional | Cadastros e estado atual | PostgreSQL; extensão espacial se necessária |
| Arquivo bruto | Payloads imutáveis e artefatos | Armazenamento de objetos |
| Cache | Respostas públicas e ponteiro atual | Cache compartilhado/CDN |
| Operação | Métricas, logs, alertas e replay | Painel administrativo |

GitHub guardará código, migrações e configurações versionadas. Não será banco de resultados nem agendador da coleta ao vivo. Cloudflare pode servir interface e API de borda; um desenho exclusivamente com Workers fica condicionado a prova de capacidade e agendamento. Não há, nesta solicitação, restrição que justifique forçar o motor estatístico para a borda.

Fluxo: descobrir catálogos → detectar mudanças → coletar → persistir bruto → validar → publicar observado → enfileirar projeção → publicar lote consistente. A indisponibilidade do motor não interrompe a apuração. Navegadores consultam apenas a infraestrutura do sistema.

### 8.1 Coleta e orçamento de requisições

A FAQ informa limite de 100 requisições/s por IP, contando 304, e risco de bloqueio por excesso ou 404 repetido. Também informa que EA14/EA15 ajudam a detectar mudanças, mas podem chegar antes ou depois do EA20. Propõe-se teto interno inicial de 20/s, compartilhado por todos os processos do mesmo IP, ajustável após teste. [FAQ técnica TSE](https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados).

Proposta de cadência: controle nacional/UF a cada 15–30 segundos; municípios por alterações detectadas; varredura de reconciliação periódica, inicialmente a cada 15 minutos; refazer leitura após defasagem entre arquivos. Intervalos são metas internas, não frequência prometida pelo TSE.

Uma varredura municipal dos três cargos representa aproximadamente 16,9 mil arquivos usando a base recebida e exterior só para presidente. A 20/s, isso exige cerca de 14 minutos, antes de controle/retries. Portanto, prometer todo município atualizado em segundos seria inadequado. Medir fila e priorizar, sem deixar territórios sem atendimento indefinidamente.

Usar ETag/Last-Modified quando disponíveis, jitter, retry exponencial e limitador global. 404 isolado: suspender fonte e verificar catálogo; série de erros: interromper coleta afetada para evitar bloqueio. 429/bloqueio: respeitar espera e não insistir durante a penalidade. Nunca multiplicar IPs para contornar limite.

Guardar bytes originais, hash, URL, geração, captura, headers úteis e versão do parser. IDG não será tratado como contador global crescente. Correções podem reduzir votos; classificar como revisão e preservar ambas as versões. Validar assinatura conforme procedimento oficial quando disponibilizada; hash local não autentica origem.

### 8.2 Consistência

Resultados nacionais/estaduais oficiais e soma local podem divergir temporariamente por horários. Não substituir um pelo outro silenciosamente. Exibir indicador de sincronização e reconciliar antes de uma projeção que dependa da soma.

Cada execução do modelo referencia um manifesto de snapshots, com horário de corte, maior/menor horário da fonte, unidades ausentes e peso eleitoral atrasado. Atualização do ponteiro público é atômica; todos os cards de uma resposta referenciam o mesmo lote. Guardar estado de coleta e qualidade por fonte.

## 9. Modelo de dados e API

| Entidade | Chave e conteúdo essencial |
|---|---|
| election | Ambiente, ciclo, pleito, eleição, turno, data, catálogos |
| contest | Eleição, cargo, abrangência decisória, vagas |
| geography | Código TSE string, IBGE quando aplicável, UF, tipo, validade |
| geo_crosswalk | Origem/destino, regra, versão e qualidade da correspondência |
| electorate | Geografia, eleição, aptos, seções, fonte e data |
| candidate | Eleição, identificador oficial, número string, partido, situação |
| party_classification | Ano, entidade, categoria, fonte, autor e versão |
| source_snapshot | Fonte, bytes/hash, geração/captura, status, parser |
| observed_result | Snapshot, cargo, geografia, candidato, votos e destinação |
| forecast_run | Manifesto, modelo, parâmetros, semente, diagnósticos |
| forecast_result | Execução, território, candidato, quantis/probabilidades |
| institutional_seat | Casa/UF, mandato, ocupante, partido e data |
| audit_event | Operador, ação, justificativa, antes/depois |

Unicidade inclui ambiente e turno para impedir contaminação entre simulado e oficial. Votos em inteiros de 64 bits; percentuais com precisão controlada; UTC interno e exibição em America/Sao_Paulo. Preservar horário original e regra de conversão.

Contrato de API proposto, independente dos endpoints TSE:

| Rota | Uso |
|---|---|
| `GET /api/v1/elections` | Eleições e turnos disponíveis |
| `GET /api/v1/results` | Observado por cargo/geografia/instante |
| `GET /api/v1/forecasts` | Quantis e probabilidades liberadas |
| `GET /api/v1/map` | Dados compactos sem repetir geometria |
| `GET /api/v1/timeline` | Evolução/replay |
| `GET /api/v1/quality` | Atrasos, cobertura e divergências |
| `GET /api/v1/classifications` | Metodologia e versão política |
| `GET /api/v1/exports` | Exportação limitada por escopo |

Todas as respostas incluem `as_of`, `source_generated_at`, `captured_at`, `snapshot_set_id`, `status`, `quality_flags`, definição do denominador e, quando aplicável, `model_version`/`classification_version`. Ausência é `null` com motivo, nunca zero. Paginação e limites de exportação obrigatórios. Administração autenticada, separada da leitura pública.

## 10. Operação, segurança e metas

Perfis: leitor, analista, administrador. Iniciar com acesso privado; publicação pública é configuração posterior. Analista pode explorar cenários privados, mas não alterar votos oficiais. Toda mudança de premissas ou ideologia gera auditoria.

Proteções: domínios TSE permitidos explicitamente, validação de redirecionamento, limites de download e descompactação, escape de textos externos, proteção das credenciais, backup e recuperação. Não coletar identidade de eleitores. Dados são agregados e não devem ser usados para deduzir escolhas individuais.

Metas propostas para homologação: leitura cacheada p95 ≤ 500 ms; carga inicial útil ≤ 3 segundos em conexão de referência; processamento após captura ≤ 60 segundos para observado e ≤ 120 segundos para projeção; teste inicial de 1.000 leitores simultâneos. Latência da fonte e tempo na fila são medidos separadamente e não estão garantidos por essas metas.

Alertas: atraso de fila, ausência de atualização, erro de parser, mudança de schema, divergência de votos/aptos, queda do motor, bloqueio e backup. Disputa concluída não será marcada como atrasada só por não receber novidades. Durante falha, exibir último dado válido e sua idade; suspender projeção quando perder validade.

Retenção proposta: todas as versões oficiais capturadas e execuções publicadas durante o pleito; depois, compactação sem perda para arquivos históricos. Dimensionar bytes por snapshot × atualizações × unidades com medições do simulado, sem estimar orçamento financeiro fictício.

## 11. Validação e critérios de aceite

### 11.1 Integração e regras

Testar início zerado, parcial, final, segundo turno, duas vagas do Senado, ausência de candidato, candidato substituído, votos sub judice, 304, 404, bloqueio, payload inválido, arquivo atrasado, retotalização e reinício. Estados oficiais precisam coincidir com a fonte correspondente ao mesmo instante.

Critérios: nenhuma dupla contagem geográfica; exterior somente no universo pertinente; nenhum payload de simulado no oficial; nenhum percentual agregado por média simples; correspondência territorial rastreável; reprodução do mesmo forecast com manifesto, código e semente; divergências conhecidas sinalizadas antes da publicação.

### 11.2 Estatística

Backtests em eleições encerradas, por cargo/turno e faixas de cobertura de 1%, 5%, 10%, 25%, 50%, 75%, 90% e 99%. Cortes ordenados por eleitorado coberto, além de seções. Separar treinamento, calibração e avaliação; parâmetros não podem usar o resultado futuro do teste.

Métricas: erro absoluto em pontos percentuais, erro de votos, cobertura e largura dos intervalos, Brier/log score para eventos, estabilidade de ranking e erro de classificação das vagas senatoriais. Avaliar por UF, porte e perfil de atraso. Usar intervalos de confiança das métricas; poucos pleitos não sustentam alegação de probabilidades extremas muito bem calibradas.

Se só existirem resultados finais, não chamar ordem artificial de “replay histórico real”. Simular atrasos como teste de estresse e identificar a limitação. Simulado TSE testa contratos e operação, não valida sozinho o comportamento estatístico do eleitorado.

Gate de lançamento do forecast: superar M0 em erro mediano nos cortes relevantes, demonstrar cobertura dos intervalos compatível com o nominal e ausência de falhas graves por território. Registrar tolerâncias e critérios antes do holdout. Se não passar, lançar apuração e cenários, mantendo probabilidades ocultas.

### 11.3 Aceite visual e operacional

Sem confusão entre dado oficial/projeção; legenda política sempre acessível; mapa com alternativa tabular; navegação móvel; filtros refletidos nos títulos/exportações; leitura de valores sem depender de cor. Teste de recuperação comprova restauração sem duplicar snapshots. Carga demonstra que novos leitores não aumentam requisições ao TSE.

## 12. Plano de implantação

| Etapa | Entrega | Gate |
|---|---|---|
| A — Contratos e dados | Cadastro territorial; CSV conciliado; catálogos; capturador | Reconciliação e simulado |
| B — Apuração | Nacional/UF/município; histórico; qualidade; contingência | Conferência com TSE e teste de carga |
| C — Forecast | M1 e modelo Senado; simulações; diagnóstico | Backtests e liberação por cargo |
| D — Mapa político | Classificação, camadas e cadeiras | Revisão metodológica e auditabilidade |
| E — Refinamento | Zonas, seções quando viáveis, M2 | Ganho medido e capacidade operacional |
| F — Pós-eleição | Comparativos, composição institucional, avaliação do modelo | Base final versionada |

Na data desta especificação, a página técnica anuncia nova janela de simulados em **22, 23 e 24/09/2026**, das **9h às 12h e 14h às 17h**. Prioridade imediata: preparar captura desses testes, reconfirmando horários e configurações na fonte. A página contém trechos antigos de FAQ que ainda dizem que URLs seriam divulgadas; usar aviso atualizado e registrar a versão consultada. [Página técnica TSE](https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados).

Não tornar a apuração dependente da conclusão de todas as etapas estatísticas. As próximas decisões de execução são dimensionamento de equipe/hospedagem, obtenção dos históricos e curadoria da classificação ideológica; nenhuma exige mudar a arquitetura proposta.

## 13. Referências e rastreabilidade

Fontes consultadas em 20/09/2026. As recomendações estatísticas e de arquitetura são propostas deste documento, não recomendações atribuídas ao TSE.

- [TSE — documentação técnica, FAQ e simulados](https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados).
- [TSE — instruções de download](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-instrucoes-para-download-dos-arquivos-da-divulgacao-2026).
- [TSE — EA11](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea11-arquivo-de-configuracao-de-eleicoes).
- [TSE — EA12](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea12-arquivo-de-configuracao-de-municipios).
- [TSE — EA14](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea14-arquivo-de-acompanhamento-brasil).
- [TSE — EA15](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea15-arquivo-de-acompanhamento-uf).
- [TSE — EA20](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea20-arquivo-de-resultado-unificado).
- [TSE — candidatos 2026](https://dadosabertos.tse.jus.br/dataset/candidatos-2026).
- [TSE — portal de dados abertos](https://dadosabertos.tse.jus.br/).
- [TSE — Resolução 23.751/2026](https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-751-de-26-de-fevereiro-de-2026).
- [GitHub — Resultado-TSE](https://github.com/pricevicius/Resultado-TSE).
- Base local fornecida: `eleitorado_eleicao_capilaridade.csv.zip`, carga declarada de 20/09/2026.
