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

  function projetor(features, largura, altura, pad = 8) {
    let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
    for (const f of features) {
      const polis = f.geometry.type === 'Polygon'
        ? [f.geometry.coordinates] : f.geometry.coordinates;
      for (const poli of polis) for (const anel of poli) for (const [x, y] of anel) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
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
  };

  const SEM_DADO = 'url(#hachura)';

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
    if (camada.tipo === 'categorica') {
      const k = camada.chave(dado);
      if (k == null) return SEM_DADO;
      const hs = ctx.cores.get(k);
      if (!hs) return SEM_DADO;
      return corCandidato(hs, camada.forca ? camada.forca(dado) : null);
    }
    const v = camada.valor(dado);
    if (v == null) return SEM_DADO;
    if (ctx.degenerado) return corSequencial(0.5, 0, 1);   // tom único
    return corSequencial(v, ctx.min, ctx.max);
  }

  function descreve(dado, camada, ctx) {
    if (!dado) return 'sem dado';
    if (camada.tipo === 'categorica') return camada.texto(dado);
    const v = camada.valor(dado);
    if (v == null) return 'sem dado';
    const casas = (ctx && ctx.faixa != null && ctx.faixa < 1) ? 3 : 1;
    return v.toFixed(casas).replace('.', ',') + (camada.unidade || '');
  }

  async function desenhar(alvo, dados, opts) {
    const camada = CAMADAS[opts.camada] || CAMADAS.lider;
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
    const proj = projetor(feicoes, W, H);
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
      const est = camada.tipo === 'categorica' ? ESTADO_DE(dado) : null;
      const finoPadrao = opts.malha === 'uf.json' ? 1 : 0.25;
      // Território escolhido no filtro: contorno branco, acima de tudo.
      // Mostrar um município sozinho num mapa o deixa sem referência de
      // onde fica; destacá-lo dentro do estado responde a mesma pergunta
      // sem tirar o contexto.
      const alvo = opts.destaque && dado && dado.cd_ibge === opts.destaque;
      const p = svgEl('path', {
        d: caminho(f, proj),
        fill: pinta(dado, camada, ctx),
        stroke: alvo ? 'var(--fg)' : (est ? est.cor : 'var(--bg)'),
        'stroke-width': alvo ? (opts.malha === 'uf.json' ? 3.2 : 1.6)
                        : est ? (opts.malha === 'uf.json' ? 2.6 : 0.9)
                        : finoPadrao,
        'stroke-dasharray': (!alvo && est && est.traco) ? est.traco : null,
        'stroke-linejoin': 'round',
      });
      const t = svgEl('title');
      t.textContent = (dado ? dado.nome : f.properties.cd)
        + ' — ' + descreve(dado, camada, ctx)
        + (dado ? '\neleitorado: ' + dado.eleitorado.toLocaleString('pt-BR')
                : '');
      p.append(t);
      if (dado && opts.aoClicar) {
        p.style.cursor = 'pointer';
        p.addEventListener('click', () => opts.aoClicar(dado));
      }
      g.append(p);
    }
    svg.append(g);

    alvo.replaceChildren(svg);
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
    box.append(item(SEM_DADO, '╱', 'sem dado'));
    return box;
  }

  return {CAMADAS, desenhar, barraPonderada, legenda, descreve, DEF_CORES,
          estadoDe: ESTADO_DE};
})();
