/* Telas de resumo do telão (05/10/2026).
 *
 * Quatro telas pedidas depois do 1º turno, a partir de exemplos de portais:
 *
 *   panorama      os 27 estados numa tela só — quem levou o governo (ou as
 *                 duas vagas do Senado) e onde há 2º turno. O exemplo de
 *                 portal mostrava um cartão grande por estado, em rolagem:
 *                 não dá visão geral. Aqui cabem todos, por região.
 *   duelo         presidente: os dois primeiros do país, estado a estado,
 *                 em barras opostas, do maior eleitorado para o menor.
 *   participacao  ausentes, brancos e nulos de um cargo, em três degraus:
 *                 entre os aptos, entre os que foram votar, e a soma.
 *   estado        resumo de uma UF: presidente (o voto do estado),
 *                 governador, senadores e deputados federais.
 *
 * Como em politico.js, nada aqui busca dado: recebe o que a API mandou e
 * desenha. Medidas em `--u` (1vw no telão), para valer em qualquer projetor.
 *
 * Rótulos seguem o §4.7: "eleito" e "2º turno" são palavra do TSE
 * (`situacao`); o que vem da conta leva "matematicamente"; apuração em
 * andamento nunca se desenha como resultado.
 */

const TELAS = (() => {
  const U = x => `calc(var(--u) * ${x})`;
  const fmt = n => n == null ? '—' : n.toLocaleString('pt-BR');
  const pct = (v, d = 2) => v == null ? '—'
    : v.toFixed(d).replace('.', ',') + '%';

  function h(t, estilo, ...filhos) {
    const n = document.createElement(t);
    if (estilo) n.style.cssText = estilo;
    for (const f of filhos.flat()) if (f != null && f !== false)
      n.append(f.nodeType ? f : document.createTextNode(String(f)));
    return n;
  }

  // "FERNANDO HADDAD" → "Fernando Haddad": o TSE manda em maiúsculas.
  const nomeProprio = t => String(t || '').toLowerCase().replace(
    /(^|\s|\.|')\S/g, m => m.toUpperCase());

  const NOME_UF = (typeof POLITICO !== 'undefined' && POLITICO.NOME_UF) || {};
  const REGIOES = [
    ['Norte', ['ac', 'am', 'ap', 'pa', 'ro', 'rr', 'to']],
    ['Nordeste', ['al', 'ba', 'ce', 'ma', 'pb', 'pe', 'pi', 'rn', 'se']],
    ['Centro-Oeste', ['df', 'go', 'ms', 'mt']],
    ['Sudeste', ['es', 'mg', 'rj', 'sp']],
    ['Sul', ['pr', 'rs', 'sc']],
  ];
  const COR = {ok: '#42c98a', turno: '#f5b544', aberto: '#8e99a8',
               painel: '#141922', linha: '#222834', texto: '#f2f5f9',
               fraco: '#8e99a8'};
  const caixa = `background:${COR.painel};border:1px solid ${COR.linha};`
    + `border-radius:${U(.6)}`;
  const elipse = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
    + 'min-width:0';
  const chip = (txt, cor) => h('span', `font-size:${U(.62)};font-weight:700;`
    + `letter-spacing:.04em;text-transform:uppercase;white-space:nowrap;`
    + `padding:${U(.05)} ${U(.4)};border-radius:${U(.3)};`
    + `border:1px solid ${cor};color:${cor}`, txt);
  const bola = cor => h('span', `flex:none;display:inline-block;`
    + `width:${U(.6)};height:${U(.6)};border-radius:50%;`
    + `background:${cor || '#59636e'}`);
  const kpi = (rot, valor, nota, cor) => h('div', caixa
    + `;padding:${U(.5)} ${U(.9)};display:flex;align-items:baseline;`
    + `gap:${U(.6)}`,
    h('span', `font-size:${U(2)};font-weight:800;color:${cor || COR.texto};`
      + 'font-variant-numeric:tabular-nums', valor),
    h('span', `font-size:${U(.9)};color:#c7ced9;font-weight:600`, rot),
    nota ? h('span', `font-size:${U(.78)};color:${COR.fraco}`, nota) : null);

  /* ------------------------------------------------------------ panorama
   * `ufs` é a rota /panorama: por UF, o estado das vagas e os primeiros
   * colocados. Governador (1 vaga) ou senador (2 vagas). */
  function panorama(ufs, opts = {}) {
    const vagas = opts.vagas || 1;
    const por = Object.fromEntries(ufs.map(u => [u.uf, u]));
    const situacao = u => {
      if (!u || u.estado === 'aguardando')
        return {chave: 'aberto', txt: 'aguardando', cor: COR.aberto, n: 0};
      if (u.estado === 'eleito')
        return {chave: 'eleito', cor: COR.ok, n: vagas,
                txt: vagas > 1 ? 'eleitos' : 'eleito'};
      if (u.estado === 'definido')
        return {chave: 'eleito', cor: COR.ok, n: vagas, txt: 'definido'};
      if (u.estado === 'segundo_turno')
        return {chave: 'turno', txt: '2º turno', cor: COR.turno, n: 2};
      // Apuração encerrada e nada definido: só resta voto sub judice.
      if (u.andamento === 'f')
        return {chave: 'aberto', txt: 'sub judice', cor: '#c98fa8',
                n: vagas + 1};
      return {chave: 'aberto', cor: COR.aberto, n: vagas + 1,
              txt: pct(u.apurado_pct, 0) + ' apurado'};
    };

    const conta = {eleito: 0, turno: 0, aberto: 0};
    for (const [, lista] of REGIOES)
      for (const uf of lista) conta[situacao(por[uf]).chave]++;

    const SIGLA_REGIAO = {Norte: 'N', Nordeste: 'NE', 'Centro-Oeste': 'CO',
                          Sudeste: 'SE', Sul: 'S'};
    const cartao = (uf, regiao) => {
      const u = por[uf];
      const s = situacao(u);
      // Quem fica com a vaga em destaque; o seguinte, apagado, dá a medida
      // da distância.
      const fortes = s.chave === 'turno' ? 2 : s.chave === 'eleito' ? vagas
        : 0;
      const linhas = ((u && u.candidatos) || []).slice(0, vagas + 1).map(
        (c, i) => h('div', `display:flex;align-items:center;gap:${U(.35)};`
          + `font-size:${U(.92)};min-width:0;`
          + (i < fortes ? '' : s.chave === 'aberto' ? 'opacity:.8;'
             : 'opacity:.45;'),
          bola(c.partido && c.partido.cor),
          h('span', `flex:1;font-weight:${i < fortes ? 700 : 500};${elipse}`,
            nomeProprio(c.nome)),
          h('span', `color:${COR.fraco};font-size:${U(.66)};`
            + `max-width:${U(3.2)};${elipse}`,
            (c.partido && c.partido.sigla) || ''),
          h('span', 'font-variant-numeric:tabular-nums;white-space:nowrap;'
            + `font-weight:${i < fortes ? 700 : 500}`, pct(c.pct, 1))));
      return h('div', caixa + `;padding:${U(.45)} ${U(.65)};display:flex;`
        + `flex-direction:column;justify-content:center;gap:${U(.18)};`
        + `min-width:0;min-height:0;border-left:${U(.3)} solid ${s.cor}`,
        h('div', `display:flex;align-items:baseline;gap:${U(.4)}`,
          h('span', `font-size:${U(1.2)};font-weight:800`, uf.toUpperCase()),
          h('span', `flex:1;font-size:${U(.66)};color:${COR.fraco};${elipse}`,
            (NOME_UF[uf] || '') + ' · ' + SIGLA_REGIAO[regiao]),
          chip(s.txt, s.cor)),
        ...linhas);
    };

    const topo = h('div', `display:flex;gap:${U(.8)};flex-wrap:wrap`,
      vagas > 1
        ? kpi('estados com as duas vagas definidas', String(conta.eleito),
              'de 27', COR.ok)
        : kpi('eleitos no 1º turno', String(conta.eleito), null, COR.ok),
      vagas > 1 ? null
        : kpi('vão ao 2º turno', String(conta.turno), null, COR.turno),
      conta.aberto ? kpi('em aberto', String(conta.aberto), null, COR.aberto)
                   : null);

    // Grade uniforme, 7 × 4, na ordem das regiões: cabem os 27 e cada
    // cartão fica largo o bastante para o nome inteiro.
    const grade = h('div', `flex:1;min-height:0;display:grid;gap:${U(.5)};`
      + 'grid-template-columns:repeat(7,minmax(0,1fr));'
      + 'grid-template-rows:repeat(4,minmax(0,1fr))');
    for (const [regiao, lista] of REGIOES)
      for (const uf of lista) grade.append(cartao(uf, regiao));
    return h('div', `display:flex;flex-direction:column;gap:${U(.8)};`
      + 'height:100%', topo, grade);
  }

  /* --------------------------------------------------------------- duelo
   * `unidades`: /map-layers por UF (líder e segundo de cada uma). `a` e `b`:
   * os dois primeiros do país, {nome, sigla, cor}. */
  function duelo(unidades, a, b) {
    const total = unidades.reduce((s, u) => s + (u.eleitorado || 0), 0);
    const de = (u, c) => {
      const n = (c.nome || '').toUpperCase();
      if ((u.lider_nome || '').toUpperCase() === n) return u.lider_pct;
      if ((u.segundo_nome || '').toUpperCase() === n) return u.segundo_pct;
      return null;
    };
    const us = unidades.filter(u => u.lider_nome)
      .sort((x, y) => (y.eleitorado || 0) - (x.eleitorado || 0));
    const metade = Math.ceil(us.length / 2);

    const linha = u => {
      const pa = de(u, a), pb = de(u, b);
      const nome = NOME_UF[u.uf] || (u.uf === 'zz' ? 'Exterior' : u.nome);
      return h('div', `display:grid;grid-template-columns:${U(10.5)} `
        + `${U(3.6)} 1fr ${U(3.6)};align-items:center;gap:${U(.5)};`
        + 'min-height:0',
        h('div', `min-width:0;line-height:1.15`,
          h('div', `font-size:${U(.95)};font-weight:650;${elipse}`, nome),
          h('div', `font-size:${U(.62)};color:${COR.fraco}`,
            total ? pct(100 * u.eleitorado / total, 1) + ' dos eleitores'
                  : '')),
        h('div', `font-size:${U(.95)};font-weight:700;text-align:right;`
          + `font-variant-numeric:tabular-nums;`
          + (pa != null && pb != null && pa > pb ? '' : 'opacity:.7'),
          pct(pa, 1)),
        h('div', `display:flex;height:${U(.85)};border-radius:${U(.2)};`
          + 'overflow:hidden;background:#2a313d',
          h('i', `display:block;width:${pa || 0}%;background:${a.cor}`),
          h('i', 'display:block;flex:1'),
          h('i', `display:block;width:${pb || 0}%;background:${b.cor}`)),
        h('div', `font-size:${U(.95)};font-weight:700;`
          + 'font-variant-numeric:tabular-nums;'
          + (pa != null && pb != null && pb > pa ? '' : 'opacity:.7'),
          pct(pb, 1)));
    };
    const coluna = lista => h('div', 'display:grid;min-height:0;'
      + `grid-template-rows:repeat(${metade},minmax(0,1fr));gap:${U(.25)}`,
      lista.map(linha));
    const venceu = c => us.filter(u => {
      const x = de(u, c), y = de(u, c === a ? b : a);
      return x != null && (y == null || x > y) && u.uf !== 'zz';
    }).length;
    const lado = (c, alinh) => h('div', `display:flex;align-items:center;`
      + `gap:${U(.5)};font-size:${U(1.15)};font-weight:700;`
      + `justify-content:${alinh}`,
      bola(c.cor), c.nome, h('span', `color:${COR.fraco};font-weight:500;`
        + `font-size:${U(.9)}`, `${c.sigla} · à frente em ${venceu(c)} UFs`));
    return h('div', `display:flex;flex-direction:column;gap:${U(.6)};`
      + 'height:100%',
      h('div', 'display:flex;justify-content:space-between', lado(a, 'flex-start'),
        lado(b, 'flex-end')),
      h('div', caixa + `;flex:1;min-height:0;padding:${U(.7)} ${U(1)};`
        + `display:grid;grid-template-columns:1fr 1fr;gap:${U(2.2)}`,
        coluna(us.slice(0, metade)), coluna(us.slice(metade))));
  }

  /* -------------------------------------------------------- participacao
   * `c`: uma coluna da rota /comparativo (eleitorado, comparecimento,
   * abstenções, brancos, nulos, válidos). */
  function participacao(c) {
    const LARANJA = '#e08a3c', ROXO = '#7a68c9', VERDE = '#6fbf73';
    const naoEscolheu = c.abstencoes + c.brancos + c.nulos;
    const bn = c.brancos + c.nulos;
    const degrau = (titulo, pEsq, rotEsq, nEsq, pDir, rotDir, nDir, extra) =>
      h('div', `display:flex;flex-direction:column;gap:${U(.35)}`,
        h('div', `font-size:${U(1.05)};color:#c7ced9`, titulo),
        h('div', 'display:flex;justify-content:space-between;'
          + 'align-items:flex-end',
          h('div', '',
            h('div', `font-size:${U(2.3)};font-weight:800;color:${LARANJA};`
              + 'line-height:1', pct(pEsq)),
            h('div', `font-size:${U(.95)};color:${COR.fraco}`, rotEsq)),
          h('div', 'text-align:right',
            h('div', `font-size:${U(2.3)};font-weight:800;color:${ROXO};`
              + 'line-height:1', pct(pDir)),
            h('div', `font-size:${U(.95)};color:${COR.fraco}`, rotDir))),
        h('div', `display:flex;height:${U(1.7)};border-radius:${U(.25)};`
          + 'overflow:hidden',
          h('i', `display:block;width:${pEsq}%;background:${LARANJA}`),
          h('i', `display:block;flex:1;background:${ROXO}`)),
        h('div', `display:flex;justify-content:space-between;`
          + `font-size:${U(.9)};color:#c7ced9`,
          h('span', '', nEsq), h('span', '', nDir)),
        extra || null);
    return h('div', `display:grid;grid-template-columns:1.25fr .75fr;`
      + `gap:${U(1.6)};height:100%`,
      h('div', caixa + `;padding:${U(1.1)} ${U(1.4)};display:flex;`
        + `flex-direction:column;justify-content:space-around;gap:${U(.8)}`,
        degrau('Entre os eleitores aptos a votar',
          c.abstencao_sobre_aptos_pct, 'não foram votar',
          fmt(c.abstencoes) + ' eleitores',
          100 - c.abstencao_sobre_aptos_pct, 'foram votar',
          fmt(c.comparecimento) + ' eleitores'),
        degrau('Entre os eleitores que foram votar',
          100 * bn / c.total_votos, 'anularam ou votaram em branco',
          `${fmt(c.brancos)} brancos · ${fmt(c.nulos)} nulos`,
          100 * c.validos / c.total_votos, 'votaram em um candidato',
          fmt(c.validos) + ' votos')),
      h('div', caixa + `;padding:${U(1.1)} ${U(1.4)};display:flex;`
        + `flex-direction:column;justify-content:center;gap:${U(.6)}`,
        h('div', `font-size:${U(1.05)};color:#c7ced9`,
          'Somando quem não foi votar, anulou ou votou em branco'),
        h('div', `font-size:${U(4.2)};font-weight:800;color:${VERDE};`
          + 'line-height:1', pct(100 * naoEscolheu / c.eleitorado)),
        h('div', `font-size:${U(1.1)};color:${COR.fraco}`,
          'dos eleitores não escolheram candidato'),
        h('div', `height:${U(1.7)};border-radius:${U(.25)};overflow:hidden;`
          + 'background:#2a313d',
          h('i', `display:block;height:100%;background:${VERDE};`
            + `width:${100 * naoEscolheu / c.eleitorado}%`)),
        h('div', `font-size:${U(1.3)};font-weight:700`,
          fmt(naoEscolheu) + ' eleitores'),
        h('div', `font-size:${U(.85)};color:${COR.fraco}`,
          `de ${fmt(c.eleitorado)} aptos`
          + (c.apurado_pct != null && c.apurado_pct < 99.995
             ? ` · ${pct(c.apurado_pct)} apurado: os números ainda mudam`
             : ' · apuração completa'))));
  }

  /* -------------------------------------------------------------- estado
   * Resumo de uma UF. Cada bloco recebe os candidatos da rota /candidates
   * (com `oficial` e `enriquecimento`) e, quando há, a definição matemática
   * por candidato (`estados`: sqcand → estado). */
  function selo(o, estado, turnoCerto, temEleito) {
    const sit = (o.situacao || '').trim();
    if (o.eleito_segundo_tse === true || /^eleit/i.test(sit))
      return chip('eleito', COR.ok);
    if (/^2/.test(sit)) return chip('2º turno', COR.turno);
    // O TSE já deu a situação deste candidato (ex.: "Não eleito"), ou a
    // disputa já tem eleito: a conta não acrescenta selo nenhum.
    if (sit || temEleito) return null;
    if (estado === 'matematicamente_eleito')
      return chip('matematicamente eleito', COR.ok);
    if (estado === 'matematicamente_no_segundo_turno')
      return chip(turnoCerto ? '2º turno' : 'entre os 2 primeiros',
                  COR.turno);
    return null;
  }

  function bloco(titulo, apurado, cands, opts = {}) {
    const n = opts.n || 2;
    const grande = !!opts.grande;
    const t = grande ? 1.35 : 1;
    const lista = cands.slice().sort(
      (x, y) => y.oficial.votos - x.oficial.votos).slice(0, n);
    const temEleito = cands.some(c => c.oficial.eleito_segundo_tse === true
      || /^eleit/i.test(c.oficial.situacao || '')
      || (opts.estados || {})[c.oficial.sqcand]
         === 'matematicamente_eleito') && (opts.vagas || 1) === 1;
    const linhas = lista.map(c => {
      const o = c.oficial, e = c.enriquecimento;
      const cor = (opts.cores && opts.cores[o.partido]) || '#5aa2f0';
      const lado = U(grande ? 3.6 : 2.5);
      return h('div', `display:grid;grid-template-columns:${lado} 1fr auto;`
        + `gap:${U(.3)} ${U(.7)};align-items:center`,
        e && e.foto_url
          ? h('img', `width:${lado};height:${lado};border-radius:${U(.5)};`
              + 'object-fit:cover;grid-row:span 2')
          : h('div', `width:${lado};height:${lado};border-radius:${U(.5)};`
              + `background:#2a313d;display:flex;align-items:center;`
              + `justify-content:center;font-weight:800;grid-row:span 2;`
              + `font-size:${U(.95 * t)}`, o.numero),
        h('div', 'min-width:0',
          h('div', `display:flex;align-items:center;gap:${U(.5)};`
            + 'min-width:0',
            h('span', `font-size:${U(1.1 * t)};font-weight:700;${elipse}`,
              (e && e.nome) || nomeProprio(o.nome_urna)),
            selo(o, (opts.estados || {})[o.sqcand], opts.turnoCerto,
                 temEleito)),
          h('div', `font-size:${U(.72 * t)};color:${COR.fraco};${elipse}`,
            o.partido)),
        h('div', 'text-align:right;line-height:1.15',
          h('div', `font-size:${U(1.25 * t)};font-weight:800;`
            + 'font-variant-numeric:tabular-nums', pct(o.pct)),
          h('div', `font-size:${U(.72 * t)};color:${COR.fraco};`
            + 'font-variant-numeric:tabular-nums', fmt(o.votos))),
        h('div', `grid-column:2 / 4;height:${U(.45 * t)};`
          + `border-radius:${U(.2)};background:#2a313d;overflow:hidden`,
          h('i', `display:block;height:100%;width:${Math.min(100,
            o.pct || 0)}%;background:${cor}`)));
    });
    // A foto entra depois de montada a linha, para o `src` não ir por
    // cssText.
    linhas.forEach((ln, i) => {
      const img = ln.querySelector('img');
      const e = lista[i].enriquecimento;
      if (img && e) { img.src = e.foto_url; img.alt = ''; }
    });
    return h('div', caixa + `;padding:${U(.8)} ${U(1)};display:flex;`
      + `flex-direction:column;gap:${U(.75)};min-height:0`,
      h('div', 'display:flex;justify-content:space-between;'
        + 'align-items:baseline',
        h('span', `font-size:${U(1.15 * t)};font-weight:750`, titulo),
        h('span', `font-size:${U(.85)};color:${COR.fraco};`
          + 'font-variant-numeric:tabular-nums',
          apurado == null ? '' : pct(apurado) + ' apurado')),
      opts.nota ? h('div', `font-size:${U(.7)};color:#f0dfa8;`
        + `margin-top:${U(-.5)}`, opts.nota) : null,
      ...linhas, opts.rodape || null);
  }

  function deputados(det, n) {
    if (!det) {
      return h('div', caixa + `;padding:${U(.8)} ${U(1)};color:${COR.fraco};`
        + `font-size:${U(.9)}`, 'Câmara: arquivo ainda não coletado.');
    }
    const oficial = det.eleitos.length > 0;
    const nomes = (oficial ? det.eleitos
      : det.listas.flatMap(x => x.candidatos).filter(c => c.dentro_das_vagas
          && !c.abaixo_10pct_qe && c.votos > 0))
      .slice().sort((x, y) => y.votos - x.votos);
    return h('div', caixa + `;padding:${U(.8)} ${U(1)};display:flex;`
      + `flex-direction:column;gap:${U(.4)};min-height:0;flex:1`,
      h('div', 'display:flex;justify-content:space-between;'
        + 'align-items:baseline',
        h('span', `font-size:${U(1.15)};font-weight:750`, 'Câmara'),
        h('span', `font-size:${U(.85)};color:${COR.fraco}`,
          `${oficial ? nomes.length : 0} de ${det.vagas} eleitos`
          + (oficial ? '' : ' · provisório'))),
      nomes.length ? null : h('div', `color:${COR.fraco};font-size:${U(.9)}`,
        'nenhum voto contado ainda'),
      ...nomes.slice(0, n).map(c => h('div', `display:flex;`
        + `align-items:center;gap:${U(.45)};font-size:${U(.92)};min-width:0;`
        + (oficial ? '' : 'opacity:.72'),
        bola(c.partido && c.partido.cor),
        h('span', `flex:1;font-weight:650;${elipse}`, nomeProprio(c.nome)),
        h('span', `color:${COR.fraco};font-size:${U(.72)};`
          + `max-width:${U(4.5)};${elipse}`, c.partido && c.partido.sigla),
        h('span', 'font-variant-numeric:tabular-nums;white-space:nowrap',
          fmt(c.votos)))),
      nomes.length > n ? h('div', `font-size:${U(.75)};color:${COR.fraco}`,
        `e mais ${nomes.length - n} — os ${n} mais votados`) : null);
  }

  function estado(d) {
    const votos = d.governador.votos;
    const total = votos ? votos.total : 0;
    const linhaVotos = total ? h('div', `display:grid;`
      + `grid-template-columns:repeat(3,1fr);gap:${U(.6)};`
      + `margin-top:auto;padding-top:${U(.6)};`
      + `border-top:1px solid ${COR.linha}`,
      ...[['Válidos', votos.validos], ['Brancos', votos.brancos],
          ['Nulos', votos.nulos]].map(([r, v]) => h('div', '',
        h('div', `font-size:${U(.78)};color:${COR.fraco}`, r),
        h('div', `font-size:${U(1.2)};font-weight:700;`
          + 'font-variant-numeric:tabular-nums', pct(100 * v / total)),
        h('div', `font-size:${U(.72)};color:${COR.fraco};`
          + 'font-variant-numeric:tabular-nums', fmt(v))))) : null;
    return h('div', `display:grid;grid-template-columns:.9fr 1.3fr .9fr;`
      + `gap:${U(1.1)};height:100%;min-height:0`,
      h('div', `display:flex;flex-direction:column;gap:${U(1.1)};`
        + 'min-height:0',
        bloco('Presidente', d.presidente.apurado, d.presidente.cands, {
          n: 3, cores: d.cores,
          nota: `voto de ${d.uf.toUpperCase()} — quem decide é o total do `
            + 'Brasil'}),
        bloco('Senador', d.senador.apurado, d.senador.cands, {
          n: 3, vagas: 2, cores: d.cores, estados: d.senador.estados})),
      bloco('Governador', d.governador.apurado, d.governador.cands, {
        n: 4, grande: true, cores: d.cores, estados: d.governador.estados,
        turnoCerto: d.governador.turnoCerto, rodape: linhaVotos}),
      deputados(d.camara, 14));
  }

  return {panorama, duelo, participacao, estado};
})();
