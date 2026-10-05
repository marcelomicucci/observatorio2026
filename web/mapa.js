/* Mapa territorial — §6.
 *
 * Três compromissos que o §6 impõe e que este arquivo cumpre no código, não
 * só no texto:
 *
 * 1. Área geográfica NÃO é eleitorado. Roraima ocupa mais pixels que o
 *    Distrito Federal e tem um oitavo dos eleitores. Por isso todo mapa vem
 *    com uma barra ponderada por eleitores logo abaixo, mostrando a mesma
 *    informação na proporção que decide a eleição.
 *
 * 2. A leitura não pode depender só de cor. Cada categoria tem símbolo e
 *    rótulo, o valor aparece no título de cada forma, e a tabela abaixo é
 *    alternativa completa ao mapa.
 *
 * 3. Ausência de dado tem padrão visual próprio — hachura —, nunca uma cor
 *    que possa ser lida como categoria.
 *
 * A junção geometria↔resultado é por código IBGE, que vem do EA12 (`cdi`) e
 * da malha do IBGE (`codarea`). Nenhum pareamento por nome.
 *
 * Projeção: equirretangular com correção de cosseno na latitude média do
 * Brasil. Não é equivalente em área — e é por isso que a barra ponderada
 * existe.
 */

const MAPA = (() => {
  const cacheMalha = {};

  async function malha(nome) {
    if (!cacheMalha[nome]) {
      const r = await fetch('/geo/' + nome);
      if (!r.ok) throw new Error('malha indisponível: ' + nome);
      cacheMalha[nome] = await r.json();
    }
    return cacheMalha[nome];
  }

  // -- projeção ------------------------------------------------------------

  /* `expandir` afasta a moldura do que está sendo enquadrado.
   *
   * Serve para abrir um município sem que ele vire uma mancha solta: a
   * escala sai do município, o desenho continua sendo o do estado inteiro,
   * e o que sobra em volta é a vizinhança. Um território sozinho na tela,
   * sem nada ao redor, não diz onde fica. */
  function projetor(features, largura, altura, pad = 8, expandir = 1) {
    let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
    for (const f of features) {
      const polis = f.geometry.type === 'Polygon'
        ? [f.geometry.coordinates] : f.geometry.coordinates;
      for (const poli of polis) for (const anel of poli) for (const [x, y] of anel) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (expandir > 1 && x1 > x0) {
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      const mw = (x1 - x0) * expandir / 2, mh = (y1 - y0) * expandir / 2;
      x0 = cx - mw; x1 = cx + mw; y0 = cy - mh; y1 = cy + mh;
    }
    // Correção de cosseno: sem ela o Brasil aparece esticado na horizontal.
    const k = Math.cos((y0 + y1) / 2 * Math.PI / 180);
    const w = (x1 - x0) * k, h = (y1 - y0);
    const s = Math.min((largura - 2 * pad) / w, (altura - 2 * pad) / h);
    const dx = pad + (largura - 2 * pad - w * s) / 2;
    const dy = pad + (altura - 2 * pad - h * s) / 2;
    return ([x, y]) => [
      dx + (x - x0) * k * s,
      dy + (y1 - y) * s,
    ];
  }

  function caminho(feature, proj) {
    const polis = feature.geometry.type === 'Polygon'
      ? [feature.geometry.coordinates] : feature.geometry.coordinates;
    const partes = [];
    for (const poli of polis) {
      for (const anel of poli) {
        if (anel.length < 3) continue;
        let d = '';
        for (let i = 0; i < anel.length; i++) {
          const [px, py] = proj(anel[i]);
          d += (i ? 'L' : 'M') + px.toFixed(1) + ' ' + py.toFixed(1);
        }
        partes.push(d + 'Z');
      }
    }
    return partes.join('');
  }

  // -- escalas de cor ------------------------------------------------------

  // Categórica para líderes. Neutra de propósito: NÃO usa a paleta
  // ideológica, para que ninguém leia ideologia onde há só identidade de
  // candidato. A paleta ideológica só entra quando a curadoria existir.
  // Guardadas como matiz+saturação, não como hex, porque a luminosidade
  // passou a carregar informação: ela é a margem do líder.
  const CAT = [[200, 46], [22, 51], [120, 23], [280, 28], [38, 49],
               [180, 29], [342, 31], [224, 21], [42, 32], [259, 21]];

  function corCategorica(chaves) {
    const m = new Map();
    chaves.forEach((k, i) => m.set(k, CAT[i % CAT.length]));
    return m;
  }

  /* Luminosidade pela margem, em escala FIXA — não relativa aos dados.
   *
   * Relativa seria mais bonita e mentiria: o mesmo tom significaria 2 pp
   * num mapa e 30 pp no seguinte, e a cor deixaria de ser comparável entre
   * estados, entre cargos e entre dois momentos da mesma noite. Com escala
   * fixa, claro é sempre disputa apertada.
   *
   * A âncora de 20 pp é o ponto em que uma vantagem deixa de depender do
   * que falta apurar na maioria das disputas. Acima disso o tom satura: a
   * diferença entre 30 e 60 pp não muda a leitura de ninguém.
   */
  const MARGEM_CHEIA = 20;

  function corCandidato(hs, margem) {
    const [h, s] = hs;
    if (margem == null) return `hsl(${h} ${s}% 56%)`;
    const t = Math.max(0, Math.min(1, margem / MARGEM_CHEIA));
    return `hsl(${h} ${s}% ${(70 - t * 30).toFixed(0)}%)`;
  }

  /* Cor de partido modulada pela margem. A cor do partido é identidade e
   * não pode virar outra (PSOL amarelo escurecido parece outro partido);
   * então a margem não mexe no matiz: ela mistura a cor com o fundo. Disputa
   * apertada fica apagada, vitória folgada fica cheia. Mesma âncora fixa de
   * 20 pp do mapa de líderes. */
  function hexRgb(h) {
    const m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const FUNDO_MISTURA = [20, 25, 34];
  function corPorMargem(hex, margem) {
    const c = hexRgb(hex);
    if (!c) return hex;
    const t = margem == null ? 1
      : Math.max(0, Math.min(1, margem / MARGEM_CHEIA));
    const k = 0.6 * (1 - t);
    const m = c.map((v, i) => Math.round(v * (1 - k) + FUNDO_MISTURA[i] * k));
    return `rgb(${m[0]} ${m[1]} ${m[2]})`;
  }

  // Sequencial. Escura = mais. Clara = menos. Nunca vermelho-verde, que
  // é o par que mais falha em daltonismo.
  function corSequencial(v, min, max) {
    if (v == null || max <= min) return null;
    const t = Math.max(0, Math.min(1, (v - min) / (max - min)));
    const l = 92 - t * 58;                    // 92% .. 34% de luminosidade
    return `hsl(210 45% ${l.toFixed(0)}%)`;
  }

  /* Estado da unidade, desenhado como CONTORNO sobre a cor do líder.
   *
   * Em aberto não está aqui: ausência de marca é o estado normal, e
   * inventar uma cor para "nada decidido ainda" gastaria atenção com a
   * situação mais comum da noite.
   *
   * Sub judice é tracejado, não mais um tom de amarelo: não é um grau de
   * definição, é a definição **suspensa** — o contrário. Tracejado lê como
   * provisório sem precisar de legenda.
   */
  const DEF_CORES = {
    eleito: {cor: '#3fa06a', sim: '●', txt: 'eleito segundo o TSE',
             traco: null},
    definida: {cor: '#e3b341', sim: '◆', txt: 'matematicamente definida',
               traco: null},
    segundo_turno_definido: {cor: '#e3b341', sim: '◐',
                             txt: '2º turno matematicamente definido',
                             traco: null},
    suspensa_sub_judice: {cor: '#c98fa8', sim: '△',
                          txt: 'definição suspensa (sub judice)',
                          traco: '4 3'},
    // Recorte do voto (município numa disputa estadual ou nacional): aqui
    // não há o que definir, só contagem terminada.
    local_encerrada: {cor: '#8a95a6', sim: '✓',
                      txt: 'apuração local encerrada', traco: null},
    // A UF fechou e o arquivo deste município não foi atualizado pelo TSE.
    // Diferente de "falta apurar": não falta voto, falta publicação.
    nao_atualizado: {cor: '#c9a227', sim: '⚠',
                     txt: 'o TSE não atualizou este município',
                     traco: '3 2'},
  };

  const SEM_DADO = 'url(#hachura)';

  /* Território conhecido, resultado ainda não chegou. Tom liso e apagado,
   * não hachura: hachura diz "não sabemos nada deste lugar", e aqui se sabe
   * — ele existe, está na disputa e ainda não tem voto contado. No começo
   * da noite o mapa inteiro fica assim, e é essa a imagem certa dele. */
  const AGUARDANDO = '#2a313d';

  // -- camadas -------------------------------------------------------------

  /* Duas camadas, não cinco.
   *
   * Líder, margem e definição matemática eram três botões, e não deviam
   * ser: ninguém olha um mapa para saber *só* a margem. São três aspectos
   * de uma leitura — quem está na frente, por quanto, e se isso já está
   * resolvido — e agora saem juntos na mesma pintura:
   *
   *   cor      identifica o líder;
   *   força    é a margem sobre o 2º (claro = disputa apertada);
   *   contorno é o estado: amarelo definido, verde eleito, nada em aberto.
   *
   * O que sobra é a pergunta que muda de resposta durante a noite — onde
   * ainda falta voto entrar —, que é a única que merece um botão próprio.
   */
  /* Texto de sobrevoo comum às camadas que pintam pelo líder. */
  function textoLider(d) {
    if (!d.lider_nome) return 'sem votação';
    const p = d.lider_partido ? ' (' + d.lider_partido + ')' : '';
    let s = d.lider_nome + p;
    if (d.lider_pct != null) s += ' ' + num(d.lider_pct) + '%';
    if (d.segundo_nome) {
      s += ' · 2º ' + d.segundo_nome
         + (d.segundo_pct != null ? ' ' + num(d.segundo_pct) + '%' : '');
    }
    if (d.margem_pp != null) s += ' · margem ' + num(d.margem_pp) + ' pp';
    const e = ESTADO_DE(d);
    if (e) s += ' · ' + e.txt;
    return s;
  }

  const ORDEM_IDEOLOGIA = ['esquerda', 'centro_esquerda', 'centro',
                           'centro_direita', 'direita'];

  const CAMADAS = {
    lider: {
      rotulo: 'Quem lidera',
      nota: 'Cor identifica o candidato; tom mais forte é margem maior ' +
            'sobre o 2º. Contorno amarelo: matematicamente definido. ' +
            'Verde: eleito segundo o TSE. Cores não são ideologia — a ' +
            'classificação ideológica espera curadoria com autoria.',
      tipo: 'categorica',
      chave: d => d.lider_sqcand,
      // Modula o tom pela margem: o mapa deixa de esconder que um estado
      // decidido por 30 pp e outro por 0,2 pp são coisas diferentes.
      forca: d => d.margem_pp,
      // Na legenda, só a identidade. O resto se lê passando o mouse no
      // território ou na linha do filtro — repetir margem e estado em 27
      // linhas de legenda devolve a poluição que a fusão veio tirar.
      etiqueta: d => d.lider_nome
        ? d.lider_nome + (d.lider_partido ? ' (' + d.lider_partido + ')' : '')
        : 'sem votação',
      texto: d => {
        if (!d.lider_nome) return 'sem votação';
        const p = d.lider_partido ? ' (' + d.lider_partido + ')' : '';
        let s = d.lider_nome + p;
        if (d.lider_pct != null) s += ' ' + num(d.lider_pct) + '%';
        if (d.segundo_nome) {
          s += ' · 2º ' + d.segundo_nome
             + (d.segundo_pct != null ? ' ' + num(d.segundo_pct) + '%' : '');
        }
        if (d.margem_pp != null) s += ' · margem ' + num(d.margem_pp) + ' pp';
        const e = ESTADO_DE(d);
        if (e) s += ' · ' + e.txt;
        return s;
      },
    },
    /* Partido e ideologia pintam pela cor DADA (tipo 'direta'): a cor do
     * partido vem do cadastro e a da ideologia da paleta do §6 — não de uma
     * paleta sorteada por candidato. */
    partido: {
      rotulo: 'Partido do líder',
      nota: 'Cor do partido de quem lidera; tom apagado é disputa apertada ' +
            '(menos de 20 pp sobre o 2º). Contorno amarelo: definido; ' +
            'verde: eleito segundo o TSE. Partido não é ideologia.',
      tipo: 'direta',
      chave: d => d.lider_partido_sigla || d.lider_partido || null,
      cor: d => d.lider_partido_cor
        ? corPorMargem(d.lider_partido_cor, d.margem_pp) : null,
      corGrupo: d => d.lider_partido_cor || null,
      etiqueta: d => (d.lider_partido_sigla || d.lider_partido || '?')
        + (d.lider_partido_cadastrado === false ? ' (fora do cadastro)' : ''),
      texto: textoLider,
      margem: true,
    },
    ideologia: {
      rotulo: 'Ideologia (partido do líder)',
      nota: 'Classificação analítica do partido de quem lidera, com autoria ' +
            'e versão (§6) — não é atributo oficial do TSE. Vale o partido ' +
            'do candidato, nunca o da coligação. Hachura: partido sem ' +
            'classificação.',
      tipo: 'direta',
      chave: d => d.lider_ideologia ? d.lider_ideologia.categoria : null,
      cor: d => d.lider_ideologia ? d.lider_ideologia.cor : null,
      corGrupo: d => d.lider_ideologia ? d.lider_ideologia.cor : null,
      simbolo: d => d.lider_ideologia ? d.lider_ideologia.simbolo : null,
      ordem: k => ORDEM_IDEOLOGIA.indexOf(k),
      etiqueta: d => d.lider_ideologia ? d.lider_ideologia.rotulo
        : 'sem classificação',
      texto: d => (d.lider_ideologia
        ? d.lider_ideologia.rotulo + ' · ' : 'sem classificação · ')
        + textoLider(d),
    },
    apurar: {
      rotulo: 'Onde falta apurar',
      nota: 'Percentual do eleitorado que ainda não entrou na conta. ' +
            'Escuro = falta mais. É a camada que importa durante a ' +
            'contagem, e a que explica por que uma liderança parcial pode ' +
            'virar. Tudo claro significa apuração completa.',
      tipo: 'sequencial',
      valor: d => (d.cobertura_pct == null ? null : 100 - d.cobertura_pct),
      unidade: '% por apurar',
      limiarUniforme: 1.0,
    },
  };

  /* Nomes antigos de camada, de antes da fusão.
   *
   * Roteiros já escritos, listas guardadas no navegador e o próprio
   * config/telao.yaml ainda falam em definicao/margem/cobertura. Sem esta
   * tabela, o `CAMADAS[nome] || lider` caía em silêncio no mapa de líderes
   * — e a tela "onde ainda falta apurar" passou a mostrar quem lidera sem
   * ninguém perceber. Apelido explícito é diferente de fallback: diz para
   * onde cada nome antigo foi. */
  const APELIDOS = {definicao: 'lider', margem: 'lider', cobertura: 'apurar'};

  function camadaCanonica(nome) {
    const c = APELIDOS[nome] || nome;
    return CAMADAS[c] ? c : null;
  }

  function num(v) {
    return v == null ? 'n/d' : v.toFixed(1).replace('.', ',');
  }

  /* Estado da unidade, na ordem em que um leitor deve receber: proclamado
   * vence definido, e definido vence em aberto. `em_aberto` devolve null
   * de propósito — "normal" é ausência de marca, não mais uma cor. */
  function ESTADO_DE(d) {
    if (!d) return null;
    if (d.eleito_tse) return DEF_CORES.eleito;
    if (d.definicao === 'definida') return DEF_CORES.definida;
    if (d.definicao === 'segundo_turno_definido')
      return DEF_CORES.segundo_turno_definido;
    if (d.definicao === 'suspensa_sub_judice')
      return DEF_CORES.suspensa_sub_judice;
    // `local_encerrada` (recorte do voto) não vira marca no mapa: seriam
    // milhares de municípios marcados dizendo o óbvio. Aparece só na
    // etiqueta, em `detalhe`.
    return null;
  }

  // -- desenho -------------------------------------------------------------

  function svgEl(t, a = {}) {
    const n = document.createElementNS('http://www.w3.org/2000/svg', t);
    for (const [k, v] of Object.entries(a)) if (v != null) n.setAttribute(k, v);
    return n;
  }

  function pinta(dado, camada, ctx) {
    if (!dado) return SEM_DADO;
    if (dado.aguardando) return AGUARDANDO;
    if (camada.tipo === 'categorica') {
      const k = camada.chave(dado);
      if (k == null) return SEM_DADO;
      const hs = ctx.cores.get(k);
      if (!hs) return SEM_DADO;
      return corCandidato(hs, camada.forca ? camada.forca(dado) : null);
    }
    if (camada.tipo === 'direta') {
      if (!dado.lider_nome) return SEM_DADO;
      return camada.cor(dado) || SEM_DADO;
    }
    const v = camada.valor(dado);
    if (v == null) return SEM_DADO;
    if (ctx.degenerado) return corSequencial(0.5, 0, 1);   // tom único
    return corSequencial(v, ctx.min, ctx.max);
  }

  function descreve(dado, camada, ctx) {
    if (!dado) return 'sem dado';
    if (dado.aguardando) return 'aguardando resultado';
    if (camada.tipo === 'categorica' || camada.tipo === 'direta')
      return camada.texto(dado);
    const v = camada.valor(dado);
    if (v == null) return 'sem dado';
    const casas = (ctx && ctx.faixa != null && ctx.faixa < 1) ? 3 : 1;
    return v.toFixed(casas).replace('.', ',') + (camada.unidade || '');
  }

  /* -- painel de detalhe do território -------------------------------------
   *
   * Substitui a dica nativa do SVG, que era uma linha corrida: nome, 1º, 2º,
   * margem e estado emendados por pontos médios. Cabia num `<title>` e não
   * cabia na cabeça de ninguém — para comparar dois territórios era preciso
   * reler a frase inteira duas vezes.
   *
   * Aqui cada fato tem sua linha e os números ficam alinhados à direita, que
   * é o que permite comparar de relance. O conteúdo NÃO muda com a camada:
   * quem aponta um território quer saber o que há nele, não o recorte que o
   * botão de cima selecionou.
   */
  function fmt(n) {
    return n == null ? 'n/d' : n.toLocaleString('pt-BR');
  }

  function cria(tag, estilo, texto) {
    const e = document.createElement(tag);
    if (estilo) e.style.cssText = estilo;
    if (texto != null) e.textContent = texto;
    return e;
  }

  function detalhe(d) {
    const box = cria('div', 'display:flex;flex-direction:column;gap:6px');
    if (!d) {
      box.append(cria('div', 'color:var(--muted)', 'sem dado'));
      return box;
    }

    const tit = cria('div', 'font-weight:700;font-size:14px;color:var(--fg)',
                     d.nome_extenso || d.nome);
    if (d.capital) {
      tit.append(cria('span',
        'font-weight:400;font-size:11px;color:var(--muted);margin-left:6px',
        'capital'));
    }
    box.append(tit);

    if (d.aguardando) {
      box.append(cria('div', 'font-size:12.5px;color:var(--muted)',
                      'aguardando resultado'));
      return box;
    }

    const tab = cria('table', 'border-collapse:collapse;font-size:12.5px');
    const linha = (nome, partido, votos, pct, forte) => {
      const tr = document.createElement('tr');
      const cel = (txt, estilo) => {
        const td = cria('td', 'padding:1px 0', txt);
        if (estilo) td.style.cssText += ';' + estilo;
        return td;
      };
      tr.append(cel(nome || '—',
        (forte ? 'font-weight:600;' : '') + 'padding-right:10px'));
      // As três colunas da direita não quebram. Só o nome quebra, porque é
      // o único campo de tamanho imprevisível — deixar "P 9963" virar duas
      // linhas desalinha a tabela inteira e some com a comparação.
      tr.append(cel(partido || '',
        'color:var(--muted);padding-right:12px;white-space:nowrap'));
      // Alinhados à direita: coluna de número que não alinha não se compara.
      tr.append(cel(fmt(votos),
        'text-align:right;font-variant-numeric:tabular-nums;'
        + 'padding-right:10px;white-space:nowrap'));
      tr.append(cel(pct == null ? 'n/d'
                    : pct.toFixed(2).replace('.', ',') + '%',
        'text-align:right;font-variant-numeric:tabular-nums;'
        + 'white-space:nowrap'
        + (forte ? ';font-weight:600' : '')));
      return tr;
    };
    tab.append(linha(d.lider_nome, d.lider_partido, d.lider_votos,
                     d.lider_pct, true));
    if (d.segundo_nome) {
      tab.append(linha(d.segundo_nome, d.segundo_partido, d.segundo_votos,
                       d.segundo_pct, false));
    }
    box.append(tab);

    const meta = cria('div',
      'font-size:11.5px;color:var(--muted);display:flex;flex-direction:'
      + 'column;gap:2px');
    if (d.margem_pp != null) {
      meta.append(cria('div', null,
        'margem ' + d.margem_pp.toFixed(1).replace('.', ',') + ' pp'));
    }
    meta.append(cria('div', null,
      'apurado ' + (d.cobertura_pct == null ? 'n/d'
        : d.cobertura_pct.toFixed(1).replace('.', ',') + '%')
      + ' de ' + fmt(d.eleitorado) + ' eleitores'));
    box.append(meta);

    // Estado sempre, inclusive "em aberto". No mapa a ausência de contorno
    // já diz isso; num texto, o silêncio seria lido como esquecimento.
    const e = ESTADO_DE(d)
      || (d.definicao === 'local_encerrada' ? DEF_CORES.local_encerrada
          : d.definicao === 'nao_atualizado' ? DEF_CORES.nao_atualizado
                                             : null);
    box.append(cria('div',
      'font-size:12px;font-weight:600;color:'
      + (e ? e.cor : 'var(--muted)'),
      (e ? e.sim + ' ' + e.txt : '○ em aberto')));

    if (d.unidade_decide === false) {
      box.append(cria('div',
        'font-size:11px;color:var(--muted);max-width:280px;line-height:1.35',
        'Recorte do voto: liderar aqui não é ser eleito por aqui.'));
    }
    return box;
  }

  /** Mesma informação em texto puro, para leitor de tela e para o `alt`. */
  function resumoTexto(d, camada, ctx) {
    if (!d) return 'sem dado';
    return (d.nome_extenso || d.nome) + ': ' + descreve(d, camada, ctx);
  }

  async function desenhar(alvo, dados, opts) {
    const idCamada = camadaCanonica(opts.camada);
    if (!idCamada && opts.camada) {
      console.warn('camada desconhecida, usando lider:', opts.camada);
    }
    const camada = CAMADAS[idCamada || 'lider'];
    const gj = await malha(opts.malha);
    const porCodigo = new Map(dados.filter(d => d.cd_ibge)
                                   .map(d => [d.cd_ibge, d]));

    /* Recorte: quando o pedido foi explicitamente por uma UF, desenhar o
     * Brasil inteiro faz Alagoas virar um respingo. Nesse caso só as feições
     * do recorte entram, e a projeção é calculada a partir delas.
     *
     * Sem recorte explícito, o mapa continua nacional de propósito: ver o
     * que ainda NÃO tem dado (hachura) é informação, não sobra. */
    let feicoes = gj.features;
    if (opts.recorte) {
      const pref = String(opts.recorte);
      const filtradas = gj.features.filter(f =>
        String(f.properties.cd).startsWith(pref));
      if (filtradas.length) feicoes = filtradas;
    }

    const ctx = {};
    if (camada.tipo === 'categorica') {
      ctx.cores = corCategorica(
        [...new Set(dados.map(camada.chave).filter(x => x != null))].sort());
    } else if (camada.tipo === 'sequencial') {
      const vs = dados.map(camada.valor).filter(v => v != null);
      ctx.min = vs.length ? Math.min(...vs) : 0;
      ctx.max = vs.length ? Math.max(...vs) : 1;
      /* Faixa degenerada: quando todos os valores são praticamente iguais,
       * uma rampa que vai do mínimo ao máximo transforma ruído em sinal.
       * Com 27 estados todos a 100,00% de apuração, diferenças de centésimo
       * viram contraste visual forte e sugerem uma diferença que não existe.
       * O mesmo vale no começo da noite, com quase tudo em zero.
       * Nesse caso o mapa recebe um tom único e diz que é uniforme. */
      ctx.faixa = ctx.max - ctx.min;
      ctx.degenerado = vs.length > 0 && ctx.faixa < (camada.limiarUniforme
                                                     ?? 0.5);
    }

    const W = opts.largura || 720, H = opts.altura || 620;
    /* Abrir um território: a escala vem dele, o desenho continua sendo o do
     * conjunto. O fator 3,2 foi escolhido para o alvo ocupar cerca de um
     * terço da moldura — grande o bastante para ser lido, pequeno o
     * bastante para os vizinhos dizerem onde ele fica. */
    const foco = opts.foco
      ? feicoes.filter(f => String(f.properties.cd) === String(opts.foco))
      : [];
    const proj = foco.length ? projetor(foco, W, H, 8, 3.2)
                             : projetor(feicoes, W, H);
    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img',
      'aria-label': 'Mapa: ' + camada.rotulo,
      style: 'max-width:100%;height:auto;display:block',
    });

    // Hachura para ausência de dado. Não é cinza-categoria: é padrão.
    const defs = svgEl('defs');
    const pat = svgEl('pattern', {
      id: 'hachura', width: 6, height: 6,
      patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)',
    });
    pat.append(svgEl('rect', {width: 6, height: 6, fill: 'var(--panel)'}));
    pat.append(svgEl('line', {
      x1: 0, y1: 0, x2: 0, y2: 6,
      stroke: 'var(--line)', 'stroke-width': 2,
    }));
    defs.append(pat);
    svg.append(defs);

    const g = svgEl('g');
    let semDado = 0;
    for (const f of feicoes) {
      const dado = porCodigo.get(f.properties.cd);
      if (!dado) semDado++;
      // O contorno de status só entra na camada do líder: em "onde falta
      // apurar" a pergunta é outra, e marcar definição ali competiria com
      // a informação que a camada existe para dar.
      // Nas camadas de partido e ideologia, contorno só onde a unidade
      // decide: "matematicamente definido" num município, para presidente,
      // é afirmação sobre um recorte que não elege ninguém — e 5.570
      // contornos amarelos apagam a cor que a camada existe para mostrar.
      const est = ((camada.tipo === 'categorica'
                    || (camada.tipo === 'direta'
                        && dado && dado.unidade_decide !== false))
                   && dado && !dado.aguardando)
        ? ESTADO_DE(dado) : null;
      const finoPadrao = opts.malha === 'uf.json' ? 1 : 0.25;
      // Território escolhido no filtro: contorno branco, acima de tudo.
      // Mostrar um município sozinho num mapa o deixa sem referência de
      // onde fica; destacá-lo dentro do estado responde a mesma pergunta
      // sem tirar o contexto.
      // `destacado`, não `alvo`: `alvo` é o contêiner recebido por
      // parâmetro, e sombreá-lo aqui é a mesma armadilha que já fez o mapa
      // desenhar o Brasil inteiro para mostrar Alagoas.
      const destacado = opts.destaque && dado
                        && dado.cd_ibge === opts.destaque;
      const p = svgEl('path', {
        d: caminho(f, proj),
        fill: pinta(dado, camada, ctx),
        stroke: destacado ? 'var(--fg)' : (est ? est.cor : 'var(--bg)'),
        'stroke-width': destacado ? (opts.malha === 'uf.json' ? 3.2 : 1.6)
                        : est ? (opts.malha === 'uf.json' ? 2.6 : 0.9)
                        : finoPadrao,
        'stroke-dasharray': (!destacado && est && est.traco) ? est.traco
                            : null,
        'stroke-linejoin': 'round',
      });
      // `aria-label` e não `<title>`: o `<title>` faz o navegador abrir a
      // dica nativa por cima do painel, e ficam duas.
      p.setAttribute('aria-label', dado
        ? resumoTexto(dado, camada, ctx)
        : f.properties.cd + ': sem dado');
      if (dado) {
        p.addEventListener('mouseenter', () => mostrarDica(dado));
        p.addEventListener('mouseleave', esconderDica);
        p.addEventListener('mousemove', moverDica);
      }
      if (dado && opts.aoClicar) {
        p.style.cursor = 'pointer';
        p.addEventListener('click', () => opts.aoClicar(dado));
      }
      g.append(p);
    }
    svg.append(g);

    /* A dica vive no contêiner, para sumir junto com o mapa quando a tela é
     * trocada, mas se posiciona em coordenadas de JANELA (`fixed`).
     *
     * Medir contra o contêiner parecia o certo e falhava do jeito mais
     * chato: o mapa é mais alto que a tela, então, com a página rolada, um
     * município no alto do mapa estava dentro do contêiner e fora da janela
     * — a dica abria acima da área visível e o leitor via só a última
     * linha. A moldura que importa é a que a pessoa enxerga.
     *
     * Uma dica de sobrevoo some ao tirar o mouse, então não precisa rolar
     * junto com nada. */
    const dica = cria('div',
      'position:fixed;z-index:60;pointer-events:none;display:none;'
      + 'background:var(--panel);border:1px solid var(--line);'
      + 'border-radius:8px;padding:10px 12px;'
      + 'box-shadow:0 6px 22px rgba(0,0,0,.45);max-width:320px');
    let dentro = null;

    function mostrarDica(d) {
      dentro = d;
      dica.replaceChildren(detalhe(d));
      dica.style.display = 'block';
    }
    function esconderDica() {
      dentro = null;
      dica.style.display = 'none';
    }
    function moverDica(ev) {
      if (!dentro) return;
      const l = dica.offsetWidth, a = dica.offsetHeight;
      const W = window.innerWidth, H = window.innerHeight;
      const M = 8;
      // Preferência: à direita e abaixo do cursor. Se não couber, vira para
      // o outro lado; se ainda não couber, encosta na borda. O grude final
      // é o que garante que ela apareça inteira sempre.
      let x = ev.clientX + 16;
      let y = ev.clientY + 14;
      if (x + l > W - M) x = ev.clientX - l - 14;
      if (y + a > H - M) y = ev.clientY - a - 12;
      dica.style.left = Math.max(M, Math.min(x, W - l - M)) + 'px';
      dica.style.top = Math.max(M, Math.min(y, H - a - M)) + 'px';
    }

    alvo.replaceChildren(svg, dica);
    return {camada, ctx, semDado, total: feicoes.length,
            recortado: feicoes.length !== gj.features.length};
  }

  // -- barra ponderada por eleitorado --------------------------------------

  function barraPonderada(dados, camada, ctx) {
    /* O contrapeso ao mapa. Mesma informação, largura proporcional a
     * eleitores em vez de a área — que é a proporção que decide a eleição. */
    const grupos = new Map();
    let total = 0;
    for (const d of dados) {
      if (camada.tipo === 'direta' && d.aguardando) continue;
      const k = camada.tipo === 'sequencial'
        ? 'todos' : String(camada.chave(d));
      const g = grupos.get(k) || {eleitorado: 0, n: 0, exemplo: d};
      g.eleitorado += d.eleitorado || 0;
      g.n++;
      grupos.set(k, g);
      total += d.eleitorado || 0;
    }
    const linha = document.createElement('div');
    linha.style.cssText =
      'display:flex;height:26px;border-radius:5px;overflow:hidden;' +
      'border:1px solid var(--line)';
    const ordenado = [...grupos.entries()].sort((a, b) =>
      b[1].eleitorado - a[1].eleitorado);
    for (const [k, g] of ordenado) {
      const seg = document.createElement('div');
      const pctEl = total ? 100 * g.eleitorado / total : 0;
      /* Tom médio, não o do exemplo: aqui cada faixa é um grupo inteiro, e
       * herdar a margem de um território sorteado faria a barra afirmar
       * sobre o grupo algo que só vale para um membro dele. */
      const cor = camada.tipo === 'categorica' && ctx.cores.get(k)
        ? corCandidato(ctx.cores.get(k), null)
        : camada.tipo === 'direta'
          ? (camada.corGrupo(g.exemplo) || SEM_DADO)
          : pinta(g.exemplo, camada, ctx);
      seg.style.cssText = 'width:' + pctEl.toFixed(2) + '%;background:' + cor;
      seg.title = descreve(g.exemplo, camada, ctx)
        + ' — ' + g.eleitorado.toLocaleString('pt-BR') + ' eleitores ('
        + pctEl.toFixed(1).replace('.', ',') + '%), '
        + g.n + ' territórios';
      linha.append(seg);
    }
    return linha;
  }

  // -- legenda -------------------------------------------------------------

  function legenda(camada, ctx, dados) {
    const box = document.createElement('div');
    box.className = 'legend';
    const item = (cor, sim, txt) => {
      const s = document.createElement('span');
      const i = document.createElement('i');
      i.className = 'sw';
      i.style.background = cor;
      s.append(i);
      if (sim) {
        const b = document.createElement('span');
        b.textContent = sim;
        s.append(b);
      }
      s.append(document.createTextNode(txt));
      return s;
    };
    if (camada.tipo === 'categorica') {
      const vistos = new Map();
      for (const d of dados) {
        const k = camada.chave(d);
        if (k != null && !vistos.has(k)) vistos.set(k, d);
      }
      for (const [k, d] of [...vistos].sort()) {
        // Amostra no tom médio: a legenda identifica o candidato, e a
        // margem se lê no mapa, não aqui.
        box.append(item(corCandidato(ctx.cores.get(k), null), null,
                        (camada.etiqueta || camada.texto)(d)));
      }
      // Margem e estado entram como escala própria, porque agora moram na
      // mesma pintura e a legenda precisa dizer o que cada coisa é.
      box.append(item(corCandidato([214, 8], 0), null,
                      'tom claro: disputa apertada'));
      box.append(item(corCandidato([214, 8], MARGEM_CHEIA), null,
                      `tom forte: ${MARGEM_CHEIA} pp ou mais de vantagem`));
      for (const v of [DEF_CORES.eleito, DEF_CORES.definida,
                       DEF_CORES.segundo_turno_definido,
                       DEF_CORES.suspensa_sub_judice]) {
        if (!dados.some(d => { const e = ESTADO_DE(d); return e && e === v; }))
          continue;
        box.append(item(v.cor, v.sim, 'contorno: ' + v.txt));
      }
    } else if (camada.tipo === 'direta') {
      // Uma entrada por grupo, na ordem que se lê: ideologia da esquerda
      // para a direita; partido por eleitorado liderado.
      const grupos = new Map();
      for (const d of dados) {
        if (d.aguardando || !d.lider_nome) continue;
        const k = camada.chave(d);
        const g = grupos.get(k) || {d, el: 0};
        g.el += d.eleitorado || 0;
        grupos.set(k, g);
      }
      const ord = [...grupos.entries()].sort((a, b) => camada.ordem
        ? camada.ordem(a[0]) - camada.ordem(b[0]) : b[1].el - a[1].el);
      for (const [, g] of ord) {
        box.append(item(camada.corGrupo(g.d) || SEM_DADO,
                        camada.simbolo ? camada.simbolo(g.d) : null,
                        camada.etiqueta(g.d)));
      }
      if (camada.margem) {
        box.append(item(corPorMargem('#9aa5b5', 0), null,
                        'tom apagado: disputa apertada'));
      }
      for (const v of [DEF_CORES.eleito, DEF_CORES.definida,
                       DEF_CORES.segundo_turno_definido]) {
        if (!dados.some(d => d.unidade_decide !== false
                             && ESTADO_DE(d) === v)) continue;
        box.append(item(v.cor, v.sim, 'contorno: ' + v.txt));
      }
    } else {
      const n = v => v.toFixed(2).replace('.', ',');
      const f = v => n(v) + (camada.unidade || '');
      if (ctx.degenerado) {
        box.append(item(corSequencial(0.5, 0, 1), '≡',
          // A unidade uma vez só: "entre 0,00% por apurar e 0,00% por
          // apurar" faz o leitor reler para descobrir que não há variação.
          'uniforme: todos entre ' + n(ctx.min) + ' e ' + f(ctx.max)
          + ' — sem variação que justifique gradiente'));
      } else {
        box.append(item(corSequencial(ctx.min, ctx.min, ctx.max), null,
                        'mínimo ' + f(ctx.min)));
        box.append(item(corSequencial((ctx.min + ctx.max) / 2,
                                      ctx.min, ctx.max), null, 'meio'));
        box.append(item(corSequencial(ctx.max, ctx.min, ctx.max), null,
                        'máximo ' + f(ctx.max)));
      }
    }
    if (dados.some(d => d.aguardando)) {
      box.append(item(AGUARDANDO, null, 'aguardando resultado'));
    }
    box.append(item(SEM_DADO, '╱', 'sem dado'));
    return box;
  }

  return {CAMADAS, desenhar, barraPonderada, legenda, descreve, DEF_CORES,
          estadoDe: ESTADO_DE, detalhe, camadaCanonica, corPorMargem,
          ORDEM_IDEOLOGIA};
})();
