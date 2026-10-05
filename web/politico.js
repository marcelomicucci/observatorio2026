/* Relatórios políticos — hemiciclo, blocos e listas de partido (§6).
 *
 * Usado pelo telão. Nada aqui busca dado: recebe o que a API mandou e
 * desenha. As regras que o desenho cumpre:
 *
 * 1. Uma cadeira em disputa tem quatro estados e cada um tem desenho
 *    próprio. "Liderando" nunca se parece com "eleito": fica translúcida e
 *    tracejada, porque a apuração parcial pode virar. "Aguardando" é um
 *    lugar vazio — a cadeira existe, só não tem voto contado.
 * 2. As cadeiras que não estão em disputa (Senado, mandato até 2031) são
 *    anéis: têm a cor do partido, mas se distinguem das eleitas agora.
 * 3. Com a classificação ideológica assinada, as cadeiras vão da esquerda
 *    para a direita e uma faixa externa mostra o bloco. Sem ela, a ordem é
 *    por tamanho de bancada e não há faixa — nada de ideologia inventada.
 * 4. Maioria é marcada como número de cadeiras, não como vitória de bloco:
 *    bloco não vota unido por ter a mesma cor (§6).
 */

const POLITICO = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ORDEM = ['esquerda', 'centro_esquerda', 'centro', 'centro_direita',
                 'direita'];
  const BLOCOS = ['esquerda', 'centro', 'direita'];
  const ROTULO_BLOCO = {esquerda: 'Esquerda', centro: 'Centro',
                        direita: 'Direita',
                        sem_classificacao: 'Sem classificação'};
  const COR_BLOCO = {esquerda: '#C62828', centro: '#F2C94C',
                     direita: '#1565C0'};

  function svg(t, a = {}) {
    const n = document.createElementNS(NS, t);
    for (const [k, v] of Object.entries(a)) if (v != null) n.setAttribute(k, v);
    return n;
  }
  function h(t, estilo, ...filhos) {
    const n = document.createElement(t);
    if (estilo) n.style.cssText = estilo;
    for (const f of filhos.flat()) if (f != null)
      n.append(f.nodeType ? f : document.createTextNode(String(f)));
    return n;
  }
  const fmt = n => n == null ? '—' : n.toLocaleString('pt-BR');
  const pct = (v, d = 1) => v == null ? '—'
    : v.toFixed(d).replace('.', ',') + '%';

  /* Posições do hemiciclo. Fileiras concêntricas; cada fileira recebe
   * cadeiras em proporção ao comprimento do seu arco, e as posições são
   * preenchidas por ângulo, da esquerda para a direita — assim cada
   * partido vira uma fatia contínua, como num plenário de verdade. */
  function posicoes(n, largura, altura, folga = 0) {
    let fileiras = Math.max(2, Math.round(Math.sqrt(n / 4.2)));
    if (n <= 30) fileiras = Math.max(2, Math.round(n / 10));
    // Geometria em unidades de R = 1; a escala vem no fim, contando o raio
    // da cadeira e a faixa de bloco — senão a fileira de baixo sai cortada.
    const r0 = n > 200 ? 0.34 : 0.42;
    const passo = fileiras > 1 ? (1 - r0) / (fileiras - 1) : 0;
    const raios = Array.from({length: fileiras}, (_, i) => r0 + passo * i);
    const soma = raios.reduce((a, b) => a + b, 0);
    // Maiores restos: a soma fecha exatamente em n.
    const cotas = raios.map(r => n * r / soma);
    const por = cotas.map(Math.floor);
    const falta = n - por.reduce((a, b) => a + b, 0);
    cotas.map((c, i) => [c - Math.floor(c), i]).sort((a, b) => b[0] - a[0])
      .slice(0, falta).forEach(([, i]) => { por[i] += 1; });
    // Raio da cadeira: o menor entre o espaço radial e o do arco interno.
    const arco = Math.PI * r0 / Math.max(1, por[0] - 1);
    const rn = Math.min(passo || arco, arco) * 0.42;
    const ext = rn + folga;
    const esc = Math.min(largura / (2 * (1 + ext)), altura / (1 + ext + rn));
    const cx = largura / 2, cy = altura - rn * esc;
    const pts = [];
    raios.forEach((r, i) => {
      const k = por[i];
      for (let j = 0; j < k; j++) {
        const th = k === 1 ? Math.PI / 2 : Math.PI * (1 - j / (k - 1));
        pts.push({x: cx + r * esc * Math.cos(th),
                  y: cy - r * esc * Math.sin(th), th, r});
      }
    });
    pts.sort((a, b) => b.th - a.th || a.r - b.r);
    return {pts, raio: Math.max(2, rn * esc), R: esc, r0: r0 * esc, cx, cy,
            fileiras};
  }

  /* Ordem das cadeiras.
   *
   * Com classificação: categoria ideológica (esquerda → direita), e dentro
   * dela por bancada. Sem classificação: por bancada, maior primeiro. As
   * cadeiras vazias vão para o fim; entre as de um partido, as definidas
   * antes das parciais, para a fatia ler "firme → provisório". */
  // `sem_eleito`: eleição passada em que o arquivo atual do TSE não traz
  // eleito para a vaga (MT/2018: eleita cassada, vaga refeita em
  // suplementar). Não é "aguardando voto" — a eleição acabou.
  const PESO_ESTADO = {mandato_em_curso: 0, eleito: 1, definido: 2,
                       liderando: 3, aguardando: 4, sem_eleito: 5};
  function ordenar(cadeiras, comIdeologia) {
    const tam = new Map();
    for (const c of cadeiras) {
      if (!c.partido) continue;
      const k = c.partido.sigla;
      tam.set(k, (tam.get(k) || 0) + 1);
    }
    const idx = c => {
      if (!c.partido) return 99;
      if (!comIdeologia) return 0;
      return c.ideologia ? ORDEM.indexOf(c.ideologia.categoria) : 7;
    };
    return cadeiras.slice().sort((a, b) =>
      idx(a) - idx(b)
      || (b.partido ? tam.get(b.partido.sigla) : 0)
         - (a.partido ? tam.get(a.partido.sigla) : 0)
      || String(a.partido && a.partido.sigla).localeCompare(
           String(b.partido && b.partido.sigla))
      || PESO_ESTADO[a.estado] - PESO_ESTADO[b.estado]);
  }

  function hemiciclo(cadeiras, opts = {}) {
    const W = opts.largura || 1000, H = opts.altura || 540;
    const comIdeologia = !!opts.comIdeologia;
    const lista = ordenar(cadeiras, comIdeologia);
    // A faixa de bloco fica por fora do arco; a folga reserva o espaço dela.
    const {pts, raio, R, r0, cx, cy} = posicoes(lista.length, W, H,
                                                comIdeologia ? 0.05 : 0);
    const s = svg('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%',
                          role: 'img', 'aria-label': opts.rotulo || 'plenário',
                          style: 'display:block;max-height:100%'});
    const defs = svg('defs');
    const pat = svg('pattern', {id: 'hach-hem', width: 5, height: 5,
      patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'});
    pat.append(svg('rect', {width: 5, height: 5, fill: '#1b212c'}));
    pat.append(svg('line', {x1: 0, y1: 0, x2: 0, y2: 5, stroke: '#4a5566',
                            'stroke-width': 1.6}));
    defs.append(pat);
    s.append(defs);

    // Faixa de bloco, por fora do arco: cada cadeira projeta sua cor
    // ideológica num segmento. Só com classificação assinada.
    if (comIdeologia) {
      const faixa = svg('g');
      const rf = R + raio + R * 0.022;
      // Segmento por posição na ordem, não pelo ângulo da cadeira: as
      // cadeiras de fileiras diferentes dividem o mesmo ângulo nas pontas,
      // e a faixa sairia empilhada ali. Como a ordem já é por ângulo, a
      // fração da ordem acompanha a posição no arco.
      const n = Math.max(1, lista.length);
      lista.forEach((c, i) => {
        const cor = c.ideologia ? c.ideologia.cor
          : (c.partido ? 'url(#hach-hem)' : null);
        if (!cor) return;
        const a0 = Math.PI * (1 - i / n), a1 = Math.PI * (1 - (i + 1) / n);
        const x0 = cx + rf * Math.cos(a0), y0 = cy - rf * Math.sin(a0);
        const x1 = cx + rf * Math.cos(a1), y1 = cy - rf * Math.sin(a1);
        faixa.append(svg('path', {d: `M${x0} ${y0}A${rf} ${rf} 0 0 1 ${x1} ${y1}`,
          stroke: cor, 'stroke-width': R * 0.028, fill: 'none'}));
      });
      s.append(faixa);
    }

    lista.forEach((c, i) => {
      const p = pts[i];
      const cor = c.partido ? c.partido.cor : null;
      let el;
      if (c.estado === 'sem_eleito') {
        el = svg('circle', {cx: p.x, cy: p.y, r: raio * 0.92,
          fill: 'none', stroke: '#b86b6b', 'stroke-width': 1.4,
          'stroke-dasharray': '2 1.6'});
      } else if (c.estado === 'aguardando' || !cor) {
        el = svg('circle', {cx: p.x, cy: p.y, r: raio * 0.92,
          fill: 'none', stroke: '#3a4352', 'stroke-width': 1.4});
      } else if (c.estado === 'mandato_em_curso') {
        // Anel: cor do partido, miolo escuro. Mandato que continua.
        el = svg('circle', {cx: p.x, cy: p.y, r: raio * 0.78, fill: '#0b0e13',
          stroke: cor, 'stroke-width': raio * 0.44});
      } else if (c.estado === 'liderando') {
        el = svg('circle', {cx: p.x, cy: p.y, r: raio * 0.9, fill: cor,
          'fill-opacity': 0.38, stroke: cor, 'stroke-width': 1.3,
          'stroke-dasharray': '2.2 1.8'});
      } else {
        el = svg('circle', {cx: p.x, cy: p.y, r: raio, fill: cor});
      }
      const t = svg('title');
      t.textContent = (c.nome ? c.nome + ' · ' : '')
        + (c.partido ? c.partido.sigla : 'vaga') + ' · ' + (c.uf || '').toUpperCase()
        + ' · ' + ({mandato_em_curso: 'mandato até 2031', eleito: 'eleito',
                    definido: 'matematicamente definido',
                    liderando: 'liderando (parcial)',
                    aguardando: 'aguardando',
                    sem_eleito: 'vaga sem eleito no arquivo do TSE'}[c.estado]
                   || c.estado);
      el.append(t);
      s.append(el);
    });

    // Marca de maioria: a cadeira que completa a maioria absoluta.
    if (opts.maioria && lista.length) {
      const i = Math.min(lista.length - 1, opts.maioria - 1);
      const p = pts[i];
      const x0 = cx + (r0 - raio * 2) * Math.cos(p.th);
      const y0 = cy - (r0 - raio * 2) * Math.sin(p.th);
      const x1 = cx + (R + raio * 1.6) * Math.cos(p.th);
      const y1 = cy - (R + raio * 1.6) * Math.sin(p.th);
      s.append(svg('line', {x1: x0, y1: y0, x2: x1, y2: y1, stroke: '#f2f5f9',
        'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.35}));
    }

    // Número no centro.
    const n1 = svg('text', {x: cx, y: cy - r0 * 0.28, 'text-anchor': 'middle',
      fill: '#f2f5f9', 'font-size': Math.max(18, r0 * 0.42),
      'font-weight': 800});
    n1.textContent = opts.centro || String(lista.length);
    s.append(n1);
    if (opts.subcentro) {
      const n2 = svg('text', {x: cx, y: cy - r0 * 0.04, 'text-anchor': 'middle',
        fill: '#8e99a8', 'font-size': Math.max(11, r0 * 0.13)});
      n2.textContent = opts.subcentro;
      s.append(n2);
    }
    return s;
  }

  /* Barra de blocos, 100% da largura = total da casa (ou dos votos). Cada
   * segmento com rótulo; marcas opcionais (maioria absoluta, 3/5). */
  function barraBlocos(porBloco, total, opts = {}) {
    const caixa = h('div', 'display:flex;flex-direction:column;gap:.5vh');
    const barra = h('div', 'position:relative;display:flex;height:3.2vh;'
      + 'border-radius:.5vw;overflow:hidden;background:#1b212c;'
      + 'border:1px solid #222834');
    const chave = opts.chave || 'total';
    for (const b of [...BLOCOS, 'sem_classificacao']) {
      const v = porBloco[b] ? porBloco[b][chave] || 0 : 0;
      if (!v) continue;
      const w = 100 * v / total;
      const seg = h('div', `width:${w}%;display:flex;align-items:center;`
        + 'justify-content:center;font-weight:800;font-size:1.05vw;'
        + (b === 'sem_classificacao'
          ? 'background:repeating-linear-gradient(45deg,#1b212c 0 5px,'
            + '#4a5566 5px 7px);color:#f2f5f9'
          : `background:${COR_BLOCO[b]};color:${b === 'centro'
              ? '#1a1400' : '#fff'}`));
      seg.textContent = w > 6 ? (opts.formato ? opts.formato(v) : fmt(v)) : '';
      seg.title = ROTULO_BLOCO[b] + ': ' + (opts.formato
        ? opts.formato(v) : fmt(v));
      barra.append(seg);
    }
    for (const m of opts.marcas || []) {
      const x = 100 * m.valor / total;
      barra.append(h('div', `position:absolute;top:0;bottom:0;left:${x}%;`
        + 'border-left:2px dashed rgba(255,255,255,.75)'));
    }
    caixa.append(barra);
    const leg = h('div', 'display:flex;gap:1.2vw;flex-wrap:wrap;'
      + 'font-size:.95vw;color:#8e99a8');
    for (const b of [...BLOCOS, 'sem_classificacao']) {
      const v = porBloco[b] ? porBloco[b][chave] || 0 : 0;
      if (!v && b === 'sem_classificacao') continue;
      leg.append(h('span', '', h('b', `color:${b === 'sem_classificacao'
        ? '#c7ced9' : COR_BLOCO[b]}`, ROTULO_BLOCO[b]), ' ',
        opts.formato ? opts.formato(v) : fmt(v)));
    }
    for (const m of opts.marcas || []) {
      leg.append(h('span', '', '┆ ' + m.rotulo));
    }
    caixa.append(leg);
    return caixa;
  }

  /* Lista de partidos: sigla com a cor, barra proporcional e contagem. */
  function listaPartidos(itens, opts = {}) {
    const max = Math.max(1, ...itens.map(i => i.valor));
    const caixa = h('div', 'display:flex;flex-direction:column;gap:.55vh;'
      + 'min-height:0;overflow:hidden');
    for (const it of itens.slice(0, opts.limite || 12)) {
      const p = it.partido;
      caixa.append(h('div', 'display:grid;grid-template-columns:'
        + '10.5vw 1fr auto;align-items:center;gap:.7vw',
        h('div', 'display:flex;align-items:center;gap:.45vw;min-width:0',
          h('i', `display:inline-block;width:1vw;height:1vw;border-radius:`
            + `.2vw;flex:0 0 auto;background:${p.cor}`),
          h('span', 'font-weight:700;font-size:1.15vw;white-space:nowrap;'
            + 'overflow:hidden;text-overflow:ellipsis', p.sigla)),
        h('div', 'height:1.6vh;background:#1b212c;border-radius:1vh;'
          + 'overflow:hidden;display:flex',
          ...(it.partes || [{valor: it.valor, estilo: ''}]).map(pt =>
            h('i', `display:block;height:100%;width:${100 * pt.valor / max}%;`
              + `background:${p.cor};${pt.estilo || ''}`))),
        h('div', 'font-size:1.25vw;font-weight:800;text-align:right;'
          + 'font-variant-numeric:tabular-nums;white-space:nowrap',
          it.rotulo != null ? it.rotulo : fmt(it.valor),
          it.nota ? h('span', 'font-size:.85vw;color:#8e99a8;'
            + 'font-weight:500;margin-left:.4vw', it.nota) : null)));
    }
    if (itens.length > (opts.limite || 12)) {
      const resto = itens.slice(opts.limite || 12)
        .reduce((a, i) => a + i.valor, 0);
      caixa.append(h('div', 'font-size:.95vw;color:#8e99a8',
        `+ ${itens.length - (opts.limite || 12)} partidos · `
        + (opts.formatoResto ? opts.formatoResto(resto) : fmt(resto))));
    }
    return caixa;
  }

  /* Legenda dos estados das cadeiras, com o próprio desenho de cada uma. */
  function legendaEstados(estados, rotulos) {
    const caixa = h('div', 'display:flex;gap:1.1vw;flex-wrap:wrap;'
      + 'font-size:.95vw;color:#c7ced9;align-items:center');
    const amostra = (tipo) => {
      const s = svg('svg', {width: 18, height: 18, viewBox: '0 0 18 18'});
      const c = '#9aa5b5';
      if (tipo === 'mandato_em_curso') {
        s.append(svg('circle', {cx: 9, cy: 9, r: 5.5, fill: '#0b0e13',
                                stroke: c, 'stroke-width': 3.2}));
      } else if (tipo === 'liderando') {
        s.append(svg('circle', {cx: 9, cy: 9, r: 7, fill: c,
          'fill-opacity': .38, stroke: c, 'stroke-width': 1.2,
          'stroke-dasharray': '2 1.6'}));
      } else if (tipo === 'sem_eleito') {
        s.append(svg('circle', {cx: 9, cy: 9, r: 7, fill: 'none',
          stroke: '#b86b6b', 'stroke-width': 1.4, 'stroke-dasharray': '2 1.6'}));
      } else if (tipo === 'aguardando') {
        s.append(svg('circle', {cx: 9, cy: 9, r: 7, fill: 'none',
                                stroke: '#3a4352', 'stroke-width': 1.4}));
      } else {
        s.append(svg('circle', {cx: 9, cy: 9, r: 7.5, fill: c}));
      }
      return s;
    };
    const rot = {mandato_em_curso: 'mandato até 2031', eleito: 'eleito (TSE)',
                 definido: 'matematicamente definido',
                 liderando: 'liderando — pode virar',
                 aguardando: 'aguardando voto',
                 sem_eleito: 'vaga sem eleito (TSE)', ...(rotulos || {})};
    for (const [k, n] of estados) {
      if (!n) continue;
      caixa.append(h('span', 'display:inline-flex;align-items:center;gap:.35vw',
        amostra(k), `${rot[k]} ${fmt(n)}`));
    }
    return caixa;
  }


  /* Recordes da noite — um cartão por recorde. Serve ao painel e ao telão:
   * as medidas saem de `--u` (1vw no telão, px no painel). Sem projeção:
   * tudo "até agora", e o que vem de UF não encerrada leva o selo. */
  const NOME_UF = {ac: 'Acre', al: 'Alagoas', am: 'Amazonas', ap: 'Amapá',
    ba: 'Bahia', ce: 'Ceará', df: 'Distrito Federal', es: 'Espírito Santo',
    go: 'Goiás', ma: 'Maranhão', mg: 'Minas Gerais',
    ms: 'Mato Grosso do Sul', mt: 'Mato Grosso', pa: 'Pará', pb: 'Paraíba',
    pe: 'Pernambuco', pi: 'Piauí', pr: 'Paraná', rj: 'Rio de Janeiro',
    rn: 'Rio Grande do Norte', ro: 'Rondônia', rr: 'Roraima',
    rs: 'Rio Grande do Sul', sc: 'Santa Catarina', se: 'Sergipe',
    sp: 'São Paulo', to: 'Tocantins'};
  const U = x => `calc(var(--u) * ${x})`;
  const hora = iso => iso ? new Date(iso).toLocaleTimeString('pt-BR',
    {timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit'})
    : '—';
  const duracao = m => m == null ? null
    : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')} após o `
      + 'fechamento das urnas';

  function recordes(d) {
    const grade = h('div', 'display:grid;grid-template-columns:repeat(4,'
      + `minmax(0,1fr));gap:${U(.8)};align-items:stretch`);
    const cartao = (grupo, titulo, ...corpo) => h('div',
      `background:#121821;border:1px solid #232c3a;border-radius:${U(.5)};`
      + `padding:${U(.8)} ${U(.9)};display:flex;flex-direction:column;`
      + `gap:${U(.3)};min-width:0`,
      h('div', `font-size:${U(.72)};letter-spacing:.08em;`
        + 'text-transform:uppercase;color:#8a95a6', grupo),
      h('div', `font-size:${U(.95)};color:#c7ced9;font-weight:600`, titulo),
      ...corpo);
    const grande = (txt, cor) => h('div', `font-size:${U(1.9)};`
      + `font-weight:800;line-height:1.1;color:${cor || '#f2f5f9'};`
      + 'overflow-wrap:anywhere', txt);
    const linha = (txt, cor) => h('div', `font-size:${U(.9)};`
      + `color:${cor || '#aab4c3'}`, txt);
    const selo = () => h('span', `font-size:${U(.65)};align-self:flex-start;`
      + `padding:0 ${U(.3)};border:1px solid #b08a3a;color:#e0b45a;`
      + `border-radius:${U(.25)}`, 'até agora');
    const vazio = () => linha('ainda sem dados', '#6b7687');
    const pessoa = c => h('div', `font-size:${U(1.15)};font-weight:700;`
      + 'color:#f2f5f9;overflow-wrap:anywhere',
      h('span', `display:inline-block;width:${U(.7)};height:${U(.7)};`
        + `border-radius:50%;background:${c.partido.cor};`
        + `margin-right:${U(.4)}`), c.nome);
    const quem = c => linha(`${c.partido.sigla} · `
      + `${NOME_UF[c.uf] || (c.uf || '').toUpperCase()}`);

    const apuracao = (a, tipo) => {
      const tit = tipo === 'estado' ? 'Estado' : 'Município';
      // Com 0% em todo lugar, "o mais adiantado" é só o primeiro da lista.
      if (!a || a.estado === 'aguardando'
          || (a.estado === 'em_andamento' && !(a.apurado_pct > 0)))
        return cartao('Apuração mais rápida', tit, vazio());
      const nome = tipo === 'estado' ? (NOME_UF[a.uf] || a.nome)
        : `${a.nome} (${a.uf.toUpperCase()})`;
      if (a.estado === 'em_andamento') {
        return cartao('Apuração mais rápida', tit + ' — mais adiantado',
          grande(nome), linha(`${pct(a.apurado_pct)} das seções`), selo());
      }
      return cartao('Apuração mais rápida', tit, grande(nome),
        linha(`100% das seções às ${hora(a.concluida_em)}`, '#dfe5ee'),
        duracao(a.minutos_apos_fechamento)
          ? linha(duracao(a.minutos_apos_fechamento)) : null,
        a.empatados > 1 ? linha(`empatado com ${fmt(a.empatados - 1)} no `
          + 'mesmo boletim do TSE', '#8a95a6') : null);
    };
    const votos = (grupo, r, modo) => {
      const tit = modo === 'abs' ? 'Mais votos' : 'Maior % dos votos';
      const c = r && (modo === 'abs' ? r.absoluto : r.relativo);
      // Antes do primeiro voto o arquivo já traz os candidatos, todos com
      // zero: não há "mais votado" nenhum.
      if (!c || !(c.votos > 0)) return cartao(grupo, tit, vazio());
      return cartao(grupo, tit, pessoa(c), quem(c),
        grande(modo === 'abs' ? fmt(c.votos) : pct(c.pct), c.partido.cor),
        linha(modo === 'abs' ? `${pct(c.pct)} dos votos na UF`
                             : `${fmt(c.votos)} votos`, '#8a95a6'),
        c.provisorio ? selo() : null);
    };
    const diferenca = (grupo, r) => {
      const m = r && r.menor_diferenca;
      if (!m || !(m.dentro.votos > 0))
        return cartao('Menor diferença', grupo, vazio());
      return cartao('Menor diferença', `${grupo} — 1º × 2º`,
        grande(`${fmt(m.votos)} voto${m.votos === 1 ? '' : 's'}`),
        linha(`${pct(m.pp, 2)} · ${NOME_UF[m.uf] || m.uf.toUpperCase()}`,
              '#dfe5ee'),
        h('div', `font-size:${U(.9)};color:#aab4c3;overflow-wrap:anywhere`,
          `${m.dentro.nome} (${m.dentro.partido.sigla}) × `
          + `${m.fora.nome} (${m.fora.partido.sigla})`),
        m.provisorio ? linha(`${pct(m.apurado_pct)} apurado`, '#8a95a6')
                     : null,
        m.provisorio ? selo() : null);
    };

    const a = d.apuracao || {};
    const ex = d.extras || {};
    const un = ex.municipio_unanime;
    const ab = ex.abstencao;
    grade.append(
      apuracao(a.estado, 'estado'), apuracao(a.municipio, 'municipio'),
      diferenca('Governador', d.governador),
      diferenca('Senador', d.senador),
      votos('Governador', d.governador, 'abs'),
      votos('Senador', d.senador, 'abs'),
      votos('Deputado federal', d.deputado_federal, 'abs'),
      un ? cartao('Presidente', 'Município mais unânime',
             grande(`${un.municipio} (${un.uf.toUpperCase()})`),
             linha(`${pct(un.pct)} para ${un.nome} (${un.partido.sigla})`,
                   '#dfe5ee'),
             linha(`entre municípios com ${fmt(un.minimo_validos)}+ votos `
                   + 'válidos', '#8a95a6'),
             un.provisorio ? selo() : null)
         : cartao('Presidente', 'Município mais unânime', vazio()),
      votos('Governador', d.governador, 'rel'),
      votos('Senador', d.senador, 'rel'),
      votos('Deputado federal', d.deputado_federal, 'rel'),
      ab ? cartao('Comparecimento', 'Abstenção por estado',
             linha('maior', '#8a95a6'),
             grande(`${NOME_UF[ab.maior.uf]} ${pct(ab.maior.pct)}`),
             linha('menor', '#8a95a6'),
             h('div', `font-size:${U(1.3)};font-weight:700;color:#dfe5ee`,
               `${NOME_UF[ab.menor.uf]} ${pct(ab.menor.pct)}`),
             ab.maior.provisorio ? selo() : null)
         : cartao('Comparecimento', 'Abstenção por estado', vazio()));
    return grade;
  }

  return {hemiciclo, barraBlocos, listaPartidos, legendaEstados, ordenar,
          recordes, NOME_UF,
          ROTULO_BLOCO, COR_BLOCO, BLOCOS, fmt, pct};
})();
