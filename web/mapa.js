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
  const CAT = ['#3d7ea6', '#c26b3f', '#5a8f5a', '#8a5fa8', '#b5893f',
               '#4f8f8f', '#a35670', '#6b7aa1', '#8f7a4a', '#7a6ba1'];

  function corCategorica(chaves) {
    const m = new Map();
    chaves.forEach((k, i) => m.set(k, CAT[i % CAT.length]));
    return m;
  }

  // Sequencial. Escura = mais. Clara = menos. Nunca vermelho-verde, que
  // é o par que mais falha em daltonismo.
  function corSequencial(v, min, max) {
    if (v == null || max <= min) return null;
    const t = Math.max(0, Math.min(1, (v - min) / (max - min)));
    const l = 92 - t * 58;                    // 92% .. 34% de luminosidade
    return `hsl(210 45% ${l.toFixed(0)}%)`;
  }

  const DEF_CORES = {
    definida:               {cor: '#2e7d5b', sim: '●', txt: 'definida'},
    segundo_turno_definido: {cor: '#4a8fbf', sim: '◐', txt: '2º turno definido'},
    suspensa_sub_judice:    {cor: '#b5893f', sim: '△', txt: 'suspensa (sub judice)'},
    em_aberto:              {cor: '#d8dde3', sim: '○', txt: 'em aberto'},
  };

  const SEM_DADO = 'url(#hachura)';

  // -- camadas -------------------------------------------------------------

  const CAMADAS = {
    lider: {
      rotulo: 'Candidato líder',
      nota: 'Cores identificam candidatos, não ideologia. A classificação ' +
            'ideológica está desabilitada até haver curadoria com autoria.',
      tipo: 'categorica',
      chave: d => d.lider_sqcand,
      texto: d => d.lider_nome
        ? d.lider_nome + (d.lider_partido ? ' (' + d.lider_partido + ')' : '')
        : 'sem votação',
    },
    margem: {
      rotulo: 'Margem do líder',
      nota: 'Diferença entre 1º e 2º, em pontos percentuais sobre votos a ' +
            'concorrentes. Escuro = disputa mais folgada.',
      tipo: 'sequencial',
      valor: d => d.margem_pp,
      unidade: ' pp',
      limiarUniforme: 0.5,
    },
    cobertura: {
      rotulo: 'Cobertura da apuração',
      nota: 'Percentual do eleitorado já apurado. É a camada que importa ' +
            'durante a contagem: mostra onde ainda falta voto entrar.',
      tipo: 'sequencial',
      valor: d => d.cobertura_pct,
      unidade: '%',
      limiarUniforme: 1.0,
    },
    definicao: {
      rotulo: 'Definição matemática',
      nota: 'Verde: nenhuma distribuição possível dos votos restantes muda ' +
            'o resultado. Não é proclamação — essa é do TSE.',
      tipo: 'estado',
      chave: d => d.definicao,
    },
  };

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
      return k == null ? SEM_DADO : (ctx.cores.get(k) || SEM_DADO);
    }
    if (camada.tipo === 'estado') {
      const e = DEF_CORES[camada.chave(dado)];
      return e ? e.cor : SEM_DADO;
    }
    const v = camada.valor(dado);
    if (v == null) return SEM_DADO;
    if (ctx.degenerado) return corSequencial(0.5, 0, 1);   // tom único
    return corSequencial(v, ctx.min, ctx.max);
  }

  function descreve(dado, camada, ctx) {
    if (!dado) return 'sem dado';
    if (camada.tipo === 'categorica') return camada.texto(dado);
    if (camada.tipo === 'estado') {
      const e = DEF_CORES[camada.chave(dado)];
      return e ? e.txt : 'sem dado';
    }
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
      const p = svgEl('path', {
        d: caminho(f, proj),
        fill: pinta(dado, camada, ctx),
        stroke: 'var(--bg)',
        'stroke-width': opts.malha === 'uf.json' ? 1 : 0.25,
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
      seg.style.cssText = 'width:' + pctEl.toFixed(2) + '%;background:'
        + pinta(g.exemplo, camada, ctx);
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
        box.append(item(ctx.cores.get(k), null, camada.texto(d)));
      }
    } else if (camada.tipo === 'estado') {
      for (const [k, v] of Object.entries(DEF_CORES)) {
        if (!dados.some(d => camada.chave(d) === k)) continue;
        box.append(item(v.cor, v.sim, v.txt));
      }
    } else {
      const f = v => v.toFixed(2).replace('.', ',') + (camada.unidade || '');
      if (ctx.degenerado) {
        box.append(item(corSequencial(0.5, 0, 1), '≡',
          'uniforme: todos entre ' + f(ctx.min) + ' e ' + f(ctx.max)
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

  return {CAMADAS, desenhar, barraPonderada, legenda, descreve, DEF_CORES};
})();
