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

## Antes de começar (fazer na segunda, 21/09)

**Notebook sem dormir.** O simulado vai das 9h às 17h com o coletor ligado;
se o Windows suspender, a coleta para e o telão congela. Na tomada, em
Configurações → Sistema → Energia, pôr "Suspender" e "Desligar a tela" em
**Nunca** — ou, num terminal:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

Deixar o carregador ligado e a tampa aberta (fechar a tampa pode suspender
mesmo assim). Depois dos simulados, voltar aos valores anteriores.

```bash
cd observatorio2026
python -m pytest tests/ -q
```

Os 161 testes rodam contra os payloads reais baixados em 20/09 (e os do M2
e dos relatórios políticos, desde 21/09). Se algum quebrar, o contrato mudou — investigar antes de ligar
qualquer coisa.

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

```bash
python -m obs2026.cli preparar
```

Baixa e guarda em disco **tudo que o painel busca fora do TSE**: perfis de
candidato, fotos, malha territorial e a composição atual do Senado (as 27
cadeiras que seguem até 2031, com o partido de hoje — só essa parte também
sai sozinha com `python -m obs2026.cli preparar-senado`). Depois disso a única dependência de
rede é o próprio TSE. Numa noite de apuração a internet oscila, e foto
sumindo do projetor no meio do evento é constrangimento evitável.

```bash
python scripts/criar_atalhos.py
```

Põe três arquivos em `Desktop\eleicao2026\`, ao lado dos históricos:

| Arquivo | Para quê |
|---|---|
| `Iniciar Observatório 2026.cmd` | sobe o servidor e abre o painel sozinho |
| `Observatório 2026 — painel.url` | painel, com o servidor já no ar |
| `Observatório 2026 — telão.url` | a janela que vai para o projetor |

Rodar uma vez basta. Não estão versionados de propósito: um `.url` guarda
caminho absoluto, e isso é da máquina, não do projeto.

**Classificação ideológica — revisar e assinar.** O arquivo
`config/classificacao_2026.yaml` traz uma proposta para os 30 partidos de
2026, com fonte, justificativa e grau de controvérsia, **sem autor**.
Enquanto ninguém assina, o lado ideológico dos mapas e das composições fica
desligado, com o aviso — o resto funciona. Para revisar:

```bash
python -m obs2026.cli classificacao
```

Para assinar (ou editar partido a partido e assinar), aba **Configuração**
do painel, seção **Partidos × ideologia**; ou, pela linha de comando:

```bash
python -m obs2026.cli assinar-classificacao --curador "Seu Nome"
```

Cada gravação é versão nova com o nome de quem gravou; a anterior fica em
`config/classificacao.anterior/`. O telão passa a mostrar a ideologia na
atualização seguinte, sem reiniciar.

**Demonstração com dado real (opcional).** O simulado tem partidos
fictícios ("P 9998"). Para ver as telas de partido, ideologia e composição
com o resultado de 2022 (Senado de 2018, que somado às 27 cadeiras eleitas
em 2022 é o Senado de hoje):

```bash
python scripts/demo2022.py
python -m obs2026.cli servir --env demo2022 --porta 8027
```

A demonstração roda em http://localhost:8027, com faixa própria
("DEMONSTRAÇÃO — resultado de 2022"). O coletor recusa esse ambiente.

---

## Durante cada janela

### 1. Ensaio, antes de ligar a coleta contínua

```bash
python -m obs2026.cli ensaio --amostra 40
```

Roda descoberta, controle e uma amostra municipal, aplicando as 11
invariantes contábeis a cada arquivo. Só seguir se sair
**"Nenhuma divergência contábil crítica"**.

### 2. Coleta contínua, já projetando

```bash
python -m obs2026.cli coletar
```

Um lockfile impede um segundo coletor na mesma máquina. O limite do TSE é
**por IP**, não por processo: dois coletores somam requisições e o operador
só descobre no bloqueio.

Com `projecao.ativa: true` em `config/collector.yaml` (o padrão), o mesmo
processo projeta todas as disputas a cada `intervalo_s` e grava cada uma no
diário de auditoria. **Isso é o que dá pontos ao gráfico de convergência**:
sem registro ao longo da noite o gráfico tem um ponto só e não prova nada.

Não espere a coleta acabar para projetar. A pergunta que a convergência
responde — *a projeção estava certa antes de o resultado aparecer?* — só se
responde com o registro feito **enquanto** o resultado ainda não apareceu.
Projetar depois seria escrever a previsão com o resultado na mão.

A simulação roda em thread separada e nunca bloqueia a busca; disputa que
falha é registrada e a rodada segue; rodada que estoura o intervalo
recomeça em vez de enfileirar. Nada disso derruba a apuração, que é o que
importa.

**Modelo: M2 desde 21/09.** Capital e cidades do arranjo dela (IBGE) formam
um grupo separado do interior, e o grupo que ainda não chegou recebe
incerteza larga. No ensaio com a ordem real de 2022 (calibrado em 2018,
testado em 2022) a cobertura do intervalo de 80% foi de 54-59% (M1) para
77-82% (M2); relatório em `data/exports/ensaio2022/teste2022.md`. Os
registros do diário dizem `modelo: m2`. Uma rodada com as 55 disputas leva
~14 s (intervalo: 180 s). O que olhar no simulado: se a rodada continua
cabendo no intervalo e se a convergência do M2 aparece no painel.

### 3. Painel, em outro terminal

```bash
python -m uvicorn obs2026.api.app:app --port 8026
```

Ou o atalho `Iniciar Observatório 2026.cmd` na pasta. Painel em
`http://localhost:8026`.

**Conferir a caixa "atualizar sozinho"** (ao lado do relógio, no alto). Ela
fica gravada no navegador: desmarcada numa sessão, continua desmarcada na
seguinte, e a tela parece congelada com o sistema funcionando — aconteceu em
23/09. Com a janela em segundo plano a atualização pausa de propósito, e a
etiqueta diz isso; voltar para a janela atualiza na hora.

**Não é preciso rodar `carregar` durante a coleta.** O `coletar` grava no
banco sozinho, a cada 5 s, só o que chegou — e grava os totais de Brasil e UF
antes dos municípios. O `carregar` à mão serve depois de uma coleta avulsa
(`scripts/coletar_uf.py`) ou para reconstruir o banco (`--recriar`). Ele
também é incremental: a primeira vez lê tudo (uns 6 minutos com o simulado
inteiro), as seguintes só o novo.

**O que o `coletar` prioriza, nesta ordem:**

1. **totais de Brasil e de cada UF**, a cada 10 s, em nível próprio na fila —
   nenhum município passa na frente, por maior que seja;
2. municípios que o EA14/EA15 acusou como mudados;
3. a varredura de segurança, uma vez por hora.

**O que ele não busca:** o que o TSE já marcou como finalizado. Sai das
buscas e continua vigiado pelo EA14/EA15; se o TSE re-totalizar, volta na
hora. Para desligar e buscar tudo sempre:

```bash
python -m obs2026.cli coletar --buscar-finalizados
```

**Se o coletor cair ou o notebook dormir:** religar. Ele retoma do arquivo
bruto o que já sabia (ETags e quem já está em dia) em cerca de um segundo, em
vez de baixar tudo de novo.

### 4. Telão

Abra o painel, aba **Telão**, botão **Abrir telão ↗**. Arraste a janela nova
para o projetor e deixe em tela cheia (F11).

No próprio telão: **espaço** pausa, **setas** navegam, **R** recarrega.

O carrossel vem de `config/telao.yaml`. Para mexer nele, a aba **Telão**
mostra a sequência editável: tempo, camada, ordem, remover. Os botões
**＋ tela pronta** acrescentam os relatórios políticos, que já estão no
roteiro desde 21/09:

| Tela | O que mostra |
|---|---|
| Presidente: partido e ideologia | mapa municipal por partido do líder e mapa ideológico lado a lado; votos por partido e por bloco |
| Governadores: partido e ideologia | os 27 estados; governos por partido (eleito, 2º turno, liderando) e por bloco, em UFs e em eleitorado |
| Senado: composição | hemiciclo de 81: anel = mandato até 2031, cheio = eleito, claro tracejado = liderando, vazio = sem voto |
| Câmara: composição | hemiciclo de 513, só com o resultado do TSE ("Eleito por QP/média"); deputado não tem projeção |

As camadas **Partido do líder** e **Ideologia** também existem no mapa comum
(telão e aba Mapa). Nada vai para o
projetor até clicar em **Gravar** — aí o arquivo é regravado (a versão
anterior fica em `config/telao.anterior/`) e o telão passa à sequência nova
na hora. Tela que não resolve impede a gravação inteira, com o motivo.

Em qualquer pesquisa do painel há dois botões na trilha:

- **＋ enviar para o telão** — acrescenta aquele recorte ao fim da sequência
  (vale depois de gravar);
- **▸ mostrar no telão agora** — põe aquele recorte no projetor já, fora da
  sequência. Ele fica lá até **↻ retomar roteiro**, na aba Telão.

Navegar no painel **não** mexe mais no projetor. Antes, cada troca de cargo
ou UF interrompia o carrossel — pesquisar no notebook virava troca de tela
na parede.

A gravação só é aceita da própria máquina e só da própria página do painel.
Se um dia o servidor for publicado, a rota continua recusando quem vem de
fora.

As duas janelas conversam por `BroadcastChannel`, que só liga janelas do
**mesmo navegador**. Segundo monitor funciona; segunda máquina, não — lá o
telão roda sozinho pelo roteiro do arquivo, sem controle ao vivo.

### Configuração, durante a janela

A aba **Configuração** do painel reúne as chaves que se mexem com o sistema
no ar — todas gravam em arquivo e valem na próxima passada, sem reiniciar:

- **Modelo de projeção**: M3 (em produção desde 22/09, homologado), M2, M1
  ou M0 (referência); M3.1 e M3.2 aparecem como "em teste" e não devem ser
  ligados sem passar no gate. Projeções já registradas ficam com o modelo
  com que foram feitas;
- **Coleta**: pular finalizados, buscar deputados federais, **parar o
  coletor quando o TSE encerrar** (ele espera 10 min pelos últimos arquivos
  e sai; só vale se tiver visto a apuração aberta na própria execução),
  **iniciar/parar o coletor** (abre numa janela própria; o botão espera a
  confirmação do batimento, que leva alguns segundos), **forçar atualização
  dos totais** (Brasil, UFs e deputados vão para a frente da fila, inclusive
  os finalizados) e forçar a varredura completa. O painel mostra se o
  coletor está no ar, por que parou e quando atendeu o pedido;
- **Telão**: recarregar a janela do projetor;
- **Partidos × ideologia**: categoria, controvérsia, justificativa, fonte e
  cor de cada partido; assinar;
- **Dados institucionais**: data da composição do Senado e botão para
  rebaixá-la (usa internet — fazer antes, não durante).

Deputado federal: o coletor busca só os 27 arquivos de UF, a cada 2 min
(0,23 req/s), sem município e sem banco — a composição da Câmara é lida
direto do arquivo bruto.

### O que a tela afirma, e com que força (23/09)

Três coisas convivem e nunca se somam:

1. **eleito** — o TSE marcou;
2. **matematicamente eleito / 2º turno garantido** — conta: o que falta
   apurar não alcança. Só aparece na unidade que decide (a UF em governador
   e senador, o Brasil em presidente). Num recorte municipal a tela diz
   apenas "apuração local encerrada";
3. **vencedor projetado** — probabilidade: 99% de chance, estável em 3
   registros e ao menos 20% apurado (`engine/chamada.py`). Aparece junto com
   a conta quando as duas dizem coisas diferentes ("2º turno garantido" +
   "vencedor projetado").

**Intervalo publicado**: 80% até 15% apurado, 90% daí em diante
(`engine/nivel.py`); o nível vai escrito ao lado da faixa. O degrau de 95%
foi testado no ensaio de 2018 e reprovado (entregava 92%).

**Sub judice**: não suspende mais a definição por si só. O motor calcula o
cenário de validação e o de cassação; se derem o mesmo resultado, a conta
vale com o aviso de que os votos seguem pendentes.

### 5. Mandar no grupo, quando pedirem

**Pelo painel**, sem terminal:

- **📷 WhatsApp**, na trilha de qualquer pesquisa — fotografa aquele recorte
  como ele aparece no telão;
- **📷 WhatsApp: roteiro inteiro**, na aba Telão — fotografa cada tela do
  roteiro gravado.

Uns 3 segundos por tela (o roteiro de cinco sai em cerca de 12). Quando
termina, **todas as imagens já estão na área de transferência do Windows**:

1. no WhatsApp, abrir o grupo e **Ctrl+V** — as imagens entram juntas;
2. **Copiar legenda** no painel, e **Ctrl+V** no campo de legenda.

Nessa ordem: copiar a legenda substitui as imagens na área de
transferência.

**Pelo terminal**, o mesmo:

```bash
python -m obs2026.cli compartilhar
```

Fotografa as telas do roteiro, escreve a legenda com os números **daquele
instante** e deixa as imagens na área de transferência. No grupo do WhatsApp:
**Ctrl+V**.

```bash
python -m obs2026.cli compartilhar --telas 0,3 --abrir
```

```bash
python -m obs2026.cli compartilhar --cargo 3 --uf pa
```

O último fotografa um recorte que nem está no roteiro. Tudo vai para
`data/telao/AAAA-MM-DD/`, com as legendas num `.txt` ao lado.

**O envio é seu, de propósito.** A API oficial do WhatsApp não entrega para
grupos; robô em cima do WhatsApp Web contraria os termos e arrisca o número
justamente na noite em que ele é mais necessário; e projeção eleitoral
circulando sem ninguém ter olhado é exatamente o que não deve sair de um
laço automático. O comando faz o trabalho todo e para um passo antes.

Se a tela não renderizar, **nenhuma imagem é gravada** e o comando diz qual
tela pulou e por quê. Mandar para um grupo a foto de uma tela quebrada é
pior do que não mandar foto.

A legenda fecha sempre dizendo que o número não é oficial. Imagem
encaminhada perde o contexto de origem no primeiro repasse; o texto vai
colado nela.

---

## Se algo der errado

| Sintoma | O que o sistema faz | O que o operador faz |
|---|---|---|
| **404 isolado** | suspende aquela fonte por 10 min, sem retry | revalidar catálogo com `descobrir`; 404 repetido bloqueia IP |
| **403 / 429** | para **toda** a coleta pela penalidade inteira | **não reiniciar**: nova tentativa durante o bloqueio reinicia os 10 min |
| **série de erros na mesma fonte** | abre o circuito por 5 min | verificar rede antes de forçar |
| **payload fora de contrato** | recusa ingerir, registra `ParseError` | comparar com a fixture; o bruto está salvo |
| **divergência contábil crítica** | bloqueia projeção, mantém apuração no ar | ler o código da invariante em `quality/checks.py` |
| **queda do processo** | bruto em disco está íntegro; ao religar, retoma ETags e quem já está em dia | religar o `coletar`; ele recarrega o banco sozinho |
| **projeção falha numa disputa** | registra e segue; a coleta não para | ver `falhas` no resumo do `coletar` |
| **captura não grava imagem** | recusa a foto e diz qual tela e por quê | o servidor está no ar? a tela resolve? |
| **telão preto no projetor** | mantém o último recorte se o controle fechar | F5 na janela do telão; o roteiro volta do arquivo |
| **M2 falha ou projeta algo absurdo** | registra a falha e segue | voltar ao M1 na aba **Configuração → Modelo de projeção**; vale na próxima rodada, sem religar nada |

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
