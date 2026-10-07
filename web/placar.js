/* Placar — o duelo de uma vaga (presidente ou governador) numa tela só.
 *
 * Nasceu em 07/10/2026 como página de teste (/placar) e passou a ser também
 * um tipo de tela do roteiro do telão (`tipo: placar`). Por isso mora num
 * módulo: a página e o telão chamam o mesmo `PLACAR.desenhar`, e o que se
 * ensaia numa é o que vai ao ar na outra.
 *
 *   - placar dos dois primeiros, com a barra dos dois lados e a marca dos
 *     50%, e a frase que responde "ainda pode virar?";
 *   - à esquerda, os números e o gráfico "ao longo da apuração" (apurado e
 *     projeção de cada candidato);
 *   - no centro, o mapa, com ou sem municípios;
 *   - à direita, regiões e os estados mais apertados (em governador, os
 *     maiores municípios e os mais apertados entre eles).
 *
 * Só vale para disputa de UMA vaga: no Senado são duas por estado, e nas
 * proporcionais não há "os dois primeiros".
 *
 * Regras do telão que valem aqui: medidas em vw, nada clicável, e projeção
 * com cor própria (roxo) em tudo que é número ou faixa — a exceção é a
 * linha tracejada do gráfico, que usa a cor do candidato para se saber de
 * quem é (decisão de 07/10).
 *
 * As classes vivem sob `.pk-raiz`, e as que o telão também usa (kpi, selo,
 * mapa, corpo) levam o prefixo `pk-`, para um estilo não vazar no outro.
 */

const PLACAR = (() => {
  const fmt = n => n == null ? '—' : Math.round(n).toLocaleString('pt-BR');
  const pct = (v, d = 1) => v == null ? '—'
    : v.toFixed(d).replace('.', ',') + '%';
  const brt = s => s ? new Date(s).toLocaleTimeString('pt-BR',
    {timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit',
     second: '2-digit'}) : '—';
  const el = (t, a = {}, ...k) => {
    const n = document.createElement(t);
    for (const [x, v] of Object.entries(a)) {
      if (v == null) continue;
      if (x === 'cls') n.className = v; else n.setAttribute(x, v);
    }
    for (const c of k.flat()) if (c != null && c !== false)
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    return n;
  };
  const poe = (a, ...k) => a.replaceChildren(...k.flat().filter(x => x != null));
  // "JAIR BOLSONARO" → "Jair Bolsonaro": o TSE manda em maiúsculas.
  const nomeProprio = t => String(t || '').toLowerCase().replace(
    /(^|\s|\.|')\S/g, m => m.toUpperCase());
  // Número grande falado: 2.612.345 → "2,6 milhões"; 84.300 → "84 mil". Com
  // unidade, cuida do "de" e do singular: "1,2 milhão de votos".
  const falado = (n, unidade) => {
    if (n >= 1e6) {
      const m = n / 1e6;
      const t = m.toFixed(1).replace('.', ',').replace(/,0$/, '');
      return t + (m < 2 ? ' milhão' : ' milhões')
        + (unidade ? ' de ' + unidade : '');
    }
    return (n >= 1e4 ? Math.round(n / 1e3) + ' mil' : fmt(n))
      + (unidade ? ' ' + unidade : '');
  };

  /* "Ainda pode virar?" — a frase do placar.
   *
   * Função pura, para ser conferida com números inventados sem tocar na tela.
   * Só afirma o que a conta sustenta, na ordem do mais forte ao mais fraco:
   *
   *   1. palavra do TSE (eleito);
   *   2. conta fechada do motor (`matematicamente_eleito`, de /decided);
   *   3. aritmética simples do que falta contra a diferença.
   *
   * `faltam` é o teto de ELEITORES que ainda podem entrar (pendente.max), não
   * de votos: nem todos comparecem e nem todo voto é válido. Por isso o
   * "precisaria de X%" usa a taxa de votos válidos por eleitor já vista
   * (`taxa`) e sai com "cerca de" — é estimativa, não conta fechada. A
   * projeção do modelo NÃO entra aqui: ela tem linha e cor próprias. */
  function frasePlacar(x) {
    const A = x.a, B = x.b;
    const dif = A.votos - B.votos;
    const pp = (A.pct != null && B.pct != null) ? A.pct - B.pct : null;
    const difTxt = `${fmt(dif)} votos`
      + (pp != null ? ` (${pp.toFixed(2).replace('.', ',')} pp)` : '');
    const doisNaDisputa = x.nCandidatos <= 2;

    if (!A.votos) {
      return {tom: 'aberto', forte: 'Aguardando',
              texto: 'nenhum voto contado ainda.'};
    }
    if (x.eleitoTse) {
      return {tom: 'encerrado', forte: 'Eleito segundo o TSE',
              texto: `${A.nome} venceu por ${difTxt}.`};
    }
    if (x.encerrada) {
      return (doisNaDisputa || (A.pct != null && A.pct > 50))
        ? {tom: 'encerrado', forte: 'Apuração encerrada',
           texto: `${A.nome} termina à frente por ${difTxt}.`}
        : {tom: 'encerrado', forte: 'Apuração encerrada',
           texto: `ninguém passou de 50% — 2º turno entre ${A.nome} e `
                + `${B.nome}.`};
    }
    if (x.matematicamenteEleito) {
      return {tom: 'definido', forte: 'Não vira mais',
              texto: `nem todos os ${falado(x.faltam, 'eleitores')} que faltam `
                   + `tiram a vitória de ${A.nome}.`};
    }
    if (!doisNaDisputa) {
      // 1º turno com vários nomes: a pergunta é se acaba hoje.
      if (x.turnoCerto) {
        return {tom: 'definido', forte: 'Haverá 2º turno',
                texto: 'ninguém alcança 50% nem com todos os votos que '
                     + 'faltam.'};
      }
      return (A.pct != null && A.pct > 50)
        ? {tom: 'aberto', forte: 'Em aberto',
           texto: `${A.nome} está acima de 50%, mas os `
                + `${falado(x.faltam, 'eleitores')} que faltam ainda podem `
                + 'levar ao 2º turno.'}
        : {tom: 'aberto', forte: 'Em aberto',
           texto: `ninguém passa de 50% por enquanto; faltam até `
                + `${falado(x.faltam, 'eleitores')}.`};
    }
    if (x.faltam <= dif) {
      // Fato aritmético, mas sem a palavra "definido": essa é do motor, que
      // também olha sub judice e seção não instalada.
      return {tom: 'definido', forte: 'Diferença maior que o que falta',
              texto: `${A.nome} tem ${difTxt} de frente e faltam no máximo `
                   + `${falado(x.faltam, 'eleitores')}.`};
    }
    const resto = x.faltam * (x.taxa || 0);
    const precisa = resto > 0 ? (resto + dif) / (2 * resto) : null;
    const base = `faltam até ${falado(x.faltam, 'eleitores')}, mais que a `
      + `diferença de ${falado(dif, 'votos')}.`;
    if (precisa == null) {
      return {tom: 'aberto', forte: 'Ainda pode virar', texto: base};
    }
    return precisa <= 1
      ? {tom: 'aberto', forte: 'Ainda pode virar',
         texto: `${base} ${B.nome} precisaria de cerca de `
              + `${pct(100 * precisa, 0)} dos votos que faltam.`}
      : {tom: 'aberto', forte: 'Só vira com comparecimento maior',
         texto: `${base} No ritmo de votos válidos visto até aqui, nem `
              + `todos os que faltam bastariam para ${B.nome}.`};
  }

  // Estados pequenos demais para se ler no mapa a 4 m: vão em etiquetas ao
  // lado, de cima para baixo como aparecem no litoral.
  const PEQUENOS = ['rn', 'pb', 'pe', 'al', 'se', 'es', 'rj', 'df'];
  const REGIOES = ['Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul'];
  // Sete: no telão a tela divide a altura com o cabeçalho e o rodapé, e a
  // oitava linha encostava na de cima.
  const QUANTOS_APERTADOS = 7;
  // Governador: "mais apertado" entre 645 municípios daria uma lista de
  // cidades de 2 mil eleitores. Vale só entre os maiores colégios do estado.
  const MAIORES_GOV = 40;

  /* Votos de um candidato numa UF, a partir do que /map-layers entrega (só o
   * líder e o segundo de cada unidade). Em 2º turno os dois sempre estão lá;
   * em 1º turno um dos nomes do placar pode ser o terceiro num estado, e aí a
   * UF fica sem número em vez de ganhar um número errado. */
  function daUF(u, sq) {
    if (u.lider_sqcand === sq) return {votos: u.lider_votos, pct: u.lider_pct};
    if (u.segundo_sqcand === sq) return {votos: u.segundo_votos,
                                         pct: u.segundo_pct};
    return null;
  }
  const validosDaUF = u => u.lider_pct > 0
    ? u.lider_votos / (u.lider_pct / 100) : 0;

  function linhaDupla(rotulo, sub, pa, pb, c1) {
    const venceA = pa != null && pb != null && pa > pb;
    const venceB = pa != null && pb != null && pb > pa;
    return el('div', {cls: 'dl', style: `--c1:${c1}`},
      el('div', {cls: 'onde'}, el('b', {}, rotulo),
         sub ? el('small', {}, sub) : null),
      el('div', {cls: 'pa' + (venceA ? '' : ' perde')}, pct(pa, 1)),
      el('div', {cls: 'br'},
        el('i', {style: `width:${pa || 0}%;background:var(--a)`}),
        el('i', {cls: 'o'}),
        el('i', {style: `width:${pb || 0}%;background:var(--b)`})),
      el('div', {cls: 'pb' + (venceB ? '' : ' perde')}, pct(pb, 1)));
  }

  /* Sigla e percentual de quem lidera dentro de cada estado (pedido de
   * 07/10). Os pequenos ficam de fora: são os das etiquetas ao lado. A posição
   * é o centro da moldura do traço, com ajuste à mão onde o centro cai fora
   * do estado ou em cima do vizinho. Unidades do desenho (900 × 900). */
  const AJUSTE_ROTULO = {pi: [8, 20], ma: [-6, 0], ce: [0, -6], sc: [14, 0],
                         ap: [0, 6], go: [-14, 12], ba: [-6, 0], pr: [-6, 0],
                         ac: [0, 6], to: [0, 4], sp: [-10, -4]};
  function rotularEstados(alvo, unidades) {
    const svg = alvo.querySelector('svg');
    if (!svg) return;
    const NS = 'http://www.w3.org/2000/svg';
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('pointer-events', 'none');
    const texto = (t, x, y, tam) => {
      const n = document.createElementNS(NS, 'text');
      n.setAttribute('x', x); n.setAttribute('y', y);
      n.setAttribute('font-size', tam);
      n.textContent = t;
      g.append(n);
    };
    for (const traco of svg.querySelectorAll('path[data-cd]')) {
      const u = unidades.find(d => String(d.cd_ibge) === traco.dataset.cd);
      if (!u || PEQUENOS.includes(u.uf)) continue;
      const b = traco.getBBox();
      const [dx, dy] = AJUSTE_ROTULO[u.uf] || [0, 0];
      const x = b.x + b.width / 2 + dx, y = b.y + b.height / 2 + dy;
      const temLider = u.lider_sqcand && !u.aguardando && u.lider_pct != null;
      texto(u.uf.toUpperCase(), x, temLider ? y - 6 : y + 8, 21);
      if (temLider) texto(pct(u.lider_pct, 0), x, y + 20, 23);
    }
    svg.append(g);
  }

  /* O gráfico. Eixo horizontal: quanto do eleitorado já foi apurado. Para cada
   * candidato, duas linhas na cor dele: cheia = apurado, tracejada = a nossa
   * projeção naquele momento. A faixa do intervalo (10%–90%) e os números da
   * projeção ficam em roxo, a cor da projeção em todo o sistema, para que
   * estimativa nunca seja lida como resultado.
   *
   * Sem sobrevoo: é tela de parede. Os valores finais vão escritos na ponta. */
  function graficoApuracao(alvo, serie, cands) {
    const NS = 'http://www.w3.org/2000/svg';
    const linhas = !serie ? [] : cands.map(c => ({c, pts:
      ((serie.candidatos.find(x => x.sqcand === c.sq) || {}).pontos || [])
        .filter(q => q.pct_apurado != null)})).filter(l => l.pts.length > 1);
    if (!linhas.length) {
      poe(alvo, el('div', {cls: 'sem-serie'}, serie === undefined
        ? 'carregando a série…'
        : 'Sem projeções registradas para esta disputa: o gráfico aparece '
          + 'quando o coletor grava a primeira rodada.'));
      return;
    }
    const r = alvo.getBoundingClientRect();
    const W = Math.max(240, r.width), H = Math.max(140, r.height);
    const f = innerWidth / 100;                       // 1vw em pixels
    const m = {e: 2.9 * f, d: 6.4 * f, t: .7 * f, b: 1.9 * f};
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Apurado e projetado ao longo da apuração');
    const no = (t, a, txt) => {
      const n = document.createElementNS(NS, t);
      for (const [k, v] of Object.entries(a)) n.setAttribute(k, v);
      if (txt != null) n.textContent = txt;
      svg.append(n);
      return n;
    };

    // Escala: os primeiros boletins (menos de 2% apurado) oscilam demais e
    // achatariam o resto; ficam fora do cálculo e são cortados pela moldura.
    const vs = [];
    for (const l of linhas) for (const q of l.pts) if (q.cobertura_pct >= 2
        || l.pts.length < 8) {
      vs.push(q.pct_apurado);
      if (q.pct_projetado != null) vs.push(q.pct_projetado);
    }
    let lo = Math.min(...vs), hi = Math.max(...vs);
    const passo = [1, 2, 5, 10, 20].find(x => (hi - lo + 2) / x <= 6) || 20;
    lo = Math.floor((lo - .5) / passo) * passo;
    hi = Math.ceil((hi + .5) / passo) * passo;
    const X = c => m.e + (W - m.e - m.d) * Math.max(0, Math.min(100, c)) / 100;
    const Y = v => m.t + (H - m.t - m.b) * (1 - (v - lo) / (hi - lo));

    for (let v = lo; v <= hi + 1e-9; v += passo) {
      no('line', {x1: m.e, x2: W - m.d, y1: Y(v), y2: Y(v),
                  stroke: v === 50 ? '#5b6676' : '#222834',
                  'stroke-width': v === 50 ? 1.5 : 1});
      no('text', {x: m.e - .5 * f, y: Y(v) + .3 * f, 'text-anchor': 'end',
                  'font-size': .8 * f, fill: '#8e99a8'}, v + '%');
    }
    for (const c of [0, 50, 100]) {
      no('text', {x: X(c), y: H - .4 * f, 'font-size': .8 * f, fill: '#8e99a8',
                  'text-anchor': c === 0 ? 'start' : c === 100 ? 'end'
                                                                : 'middle'},
         c + '%');
    }
    const corte = no('clipPath', {id: 'corte-grafico'});
    const ret = document.createElementNS(NS, 'rect');
    for (const [k, v] of Object.entries({x: m.e, y: m.t, width: W - m.e - m.d,
                                         height: H - m.t - m.b})) {
      ret.setAttribute(k, v);
    }
    corte.append(ret);
    const caminho = (pts, campo) => pts.filter(q => q[campo] != null)
      .map((q, i) => (i ? 'L' : 'M') + X(q.cobertura_pct).toFixed(1) + ' '
                     + Y(q[campo]).toFixed(1)).join('');
    const dentro = {'clip-path': 'url(#corte-grafico)', fill: 'none',
                    'stroke-linejoin': 'round', 'stroke-linecap': 'round'};

    // Faixas primeiro, depois projeção, por fim o apurado — que fica por cima.
    for (const l of linhas) {
      const comFaixa = l.pts.filter(q => q.pct_q100 != null
                                         && q.pct_q900 != null);
      if (comFaixa.length > 1) {
        no('path', {'clip-path': 'url(#corte-grafico)', fill: '#b98ae8',
          'fill-opacity': .16, stroke: 'none',
          d: caminho(comFaixa, 'pct_q900') + comFaixa.slice().reverse()
            .map(q => 'L' + X(q.cobertura_pct).toFixed(1) + ' '
                      + Y(q.pct_q100).toFixed(1)).join('') + 'Z'});
      }
    }
    for (const l of linhas) {
      no('path', {...dentro, d: caminho(l.pts, 'pct_projetado'),
                  stroke: l.c.cor, 'stroke-width': .16 * f,
                  'stroke-dasharray': `${.5 * f} ${.4 * f}`});
    }
    for (const l of linhas) {
      no('path', {...dentro, d: caminho(l.pts, 'pct_apurado'),
                  stroke: l.c.cor, 'stroke-width': .26 * f});
    }

    // Pontas: bolinha no último apurado e, na margem, os dois números. Os
    // rótulos se afastam um do outro quando as linhas terminam juntas.
    const pontas = linhas.map(l => {
      const u = l.pts[l.pts.length - 1];
      return {l, u, y: Y(u.pct_apurado)};
    }).sort((a, b) => a.y - b.y);
    const alto = 2.5 * f;
    for (let i = 1; i < pontas.length; i++) {
      if (pontas[i].y - pontas[i - 1].y < alto) {
        const meio = (pontas[i].y + pontas[i - 1].y) / 2;
        pontas[i - 1].y = meio - alto / 2; pontas[i].y = meio + alto / 2;
      }
    }
    for (const q of pontas) {
      q.y = Math.max(m.t + .9 * f, Math.min(H - m.b - 1.4 * f, q.y));
      no('circle', {cx: X(q.u.cobertura_pct), cy: Y(q.u.pct_apurado),
                    r: .42 * f, fill: q.l.c.cor, stroke: '#141922',
                    'stroke-width': .14 * f});
      const x = W - m.d + .7 * f;
      no('circle', {cx: x, cy: q.y - .3 * f, r: .3 * f, fill: q.l.c.cor});
      no('text', {x: x + .7 * f, y: q.y, 'font-size': 1.15 * f,
                  'font-weight': 800, fill: '#f2f5f9'},
         pct(q.u.pct_apurado, 1));
      if (q.u.pct_projetado != null) {
        no('text', {x: x + .7 * f, y: q.y + 1.15 * f, 'font-size': .85 * f,
                    fill: '#b98ae8'}, 'proj. ' + pct(q.u.pct_projetado, 1));
      }
    }
    poe(alvo, svg);
  }

  const ESTILO = `
.pk-raiz .placar{background:var(--panel);border:1px solid var(--line); border-radius:1vw;padding:1.2vh 1.4vw}
.pk-raiz .titulo{font-size:.95vw;text-transform:uppercase;letter-spacing:.1em; color:var(--muted);font-weight:700;margin-bottom:.5vh}
.pk-raiz .titulo b{color:var(--fg)}
.pk-raiz .duelo{display:grid;grid-template-columns:1fr auto 1fr;align-items:center; gap:2vw}
.pk-raiz .cand{display:flex;align-items:center;gap:1vw;min-width:0}
.pk-raiz .cand.b{flex-direction:row-reverse;text-align:right}
.pk-raiz .cand .foto,.pk-raiz .cand .num{width:5.4vw;height:5.4vw;border-radius:50%; flex:0 0 auto;background:#1d2430;border:.25vw solid var(--cor)}
.pk-raiz .cand .foto{object-fit:cover}
.pk-raiz .cand .num{display:flex;align-items:center;justify-content:center; font-size:2.1vw;font-weight:800;color:var(--fg); font-variant-numeric:tabular-nums}
.pk-raiz .cand .nome{font-size:2vw;font-weight:750;letter-spacing:-.02em; line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pk-raiz .cand .part{font-size:1.05vw;color:var(--muted)}
.pk-raiz .cand .ident{min-width:0}
.pk-raiz .pk-selo{font-size:.85vw;padding:.1vh .6vw;border-radius:2vw; border:1px solid var(--ok);color:var(--ok);white-space:nowrap}
.pk-raiz .numeros{display:flex;align-items:baseline;gap:2.4vw}
.pk-raiz .numeros .lado-n{text-align:center}
.pk-raiz .numeros .p{font-size:4.6vw;font-weight:800;letter-spacing:-.04em; line-height:1;font-variant-numeric:tabular-nums}
.pk-raiz .numeros .v{font-size:1vw;color:var(--muted); font-variant-numeric:tabular-nums}
.pk-raiz .cabo{position:relative;height:1.5vh;background:#2a313d;border-radius:1vh; margin-top:1.1vh;display:flex;overflow:visible}
.pk-raiz .cabo i{display:block;height:100%}
.pk-raiz .cabo i.a{background:var(--a);border-radius:1vh 0 0 1vh}
.pk-raiz .cabo i.o{flex:1}
.pk-raiz .cabo i.b{background:var(--b);border-radius:0 1vh 1vh 0}
.pk-raiz .cabo .meio{position:absolute;left:50%;top:-.6vh;bottom:-.6vh;width:.18vw; background:var(--fg);transform:translateX(-50%)}
.pk-raiz .cabo .pj{position:absolute;top:-.5vh;bottom:-.5vh;width:.3vw; background:var(--proj);border-radius:.2vw;transform:translateX(-50%)}
.pk-raiz .veredito{display:flex;align-items:baseline;gap:.9vw;margin-top:1vh; font-size:1.3vw;flex-wrap:wrap}
.pk-raiz .veredito .forte{font-weight:800;white-space:nowrap}
.pk-raiz .veredito.aberto .forte{color:var(--alerta)}
.pk-raiz .veredito.definido .forte,.pk-raiz .veredito.encerrado .forte{color:var(--ok)}
.pk-raiz .veredito .txt{color:#d5dbe4}
.pk-raiz .veredito .pj-txt{color:var(--proj);margin-left:auto;white-space:nowrap; font-variant-numeric:tabular-nums}
.pk-raiz .pk-corpo{flex:1;min-height:0;display:grid; grid-template-columns:.78fr 1.2fr .9fr;gap:1.2vw}
.pk-raiz .col{display:flex;flex-direction:column;gap:1vh;min-height:0;min-width:0}
.pk-raiz .caixa{background:var(--panel);border:1px solid var(--line); border-radius:.8vw;padding:1vh 1vw;min-height:0}
.pk-raiz .rot{font-size:.85vw;text-transform:uppercase;letter-spacing:.09em; color:var(--muted);font-weight:700;margin-bottom:.6vh}
.pk-raiz .rot small{text-transform:none;letter-spacing:0;font-weight:400; font-size:.8vw}
.pk-raiz .pk-kpis{display:grid;grid-template-columns:1fr 1fr;gap:.7vw}
.pk-raiz .pk-kpi .k{font-size:.8vw;text-transform:uppercase;letter-spacing:.08em; color:var(--muted)}
.pk-raiz .pk-kpi .v{font-size:2vw;font-weight:700;letter-spacing:-.03em;line-height:1.1; font-variant-numeric:tabular-nums}
.pk-raiz .pk-kpi .n{font-size:.8vw;color:var(--muted)}
.pk-raiz .projecao{border-color:#3d2b55;background:#1c1528}
.pk-raiz .projecao .rot{color:var(--proj)}
.pk-raiz .pl{display:flex;justify-content:space-between;gap:.8vw;font-size:1.15vw; font-variant-numeric:tabular-nums;padding:.2vh 0}
.pk-raiz .pl b{font-weight:700}
.pk-raiz .pl span{color:#e0cdf5}
.pk-raiz .linhas{display:grid;gap:.5vh;flex:1;min-height:0}
.pk-raiz .dl{display:grid;align-items:center;gap:.6vw;min-height:0; grid-template-columns:var(--c1) 3.4vw 1fr 3.4vw}
.pk-raiz .dl .onde{min-width:0;line-height:1.15}
.pk-raiz .dl .onde b{display:block;font-size:1.05vw;font-weight:650;white-space:nowrap; overflow:hidden;text-overflow:ellipsis}
.pk-raiz .dl .onde small{font-size:.72vw;color:var(--muted);white-space:nowrap}
.pk-raiz .dl .pa,.pk-raiz .dl .pb{font-size:1.1vw;font-weight:700; font-variant-numeric:tabular-nums}
.pk-raiz .dl .pa{text-align:right}
.pk-raiz .dl .perde{opacity:.6;font-weight:500}
.pk-raiz .dl .br{position:relative;display:flex;height:1.1vh;border-radius:.3vh; overflow:hidden;background:#2a313d}
.pk-raiz .dl .br i{display:block}
.pk-raiz .dl .br i.o{flex:1}
.pk-raiz .dl .br::after{content:"";position:absolute;left:50%;top:0;bottom:0; width:1px;background:rgba(255,255,255,.7)}
.pk-raiz .sg{display:inline-block;min-width:2.3vw;text-align:center;font-size:.85vw; font-weight:800;border-radius:.3vw;padding:.05vh .3vw;color:#fff; margin-right:.4vw}
.pk-raiz .pk-mapa{flex:1;display:flex;align-items:center;justify-content:center; gap:.8vw;overflow:hidden;padding:.5vw}
.pk-raiz .mapa-svg{flex:1;min-width:0;height:100%;display:flex;align-items:center; justify-content:center}
.pk-raiz .mapa-svg svg{max-height:100%}
.pk-raiz .mapa-svg text{font-weight:800;fill:#fff;stroke:rgba(8,11,16,.75); stroke-width:5px;paint-order:stroke;stroke-linejoin:round; text-anchor:middle;font-variant-numeric:tabular-nums}
.pk-raiz .mapa-svg{position:relative}
.pk-raiz .sobre{position:absolute;inset:0;display:flex;align-items:center; justify-content:center;pointer-events:none}
.pk-raiz .sobre svg{max-height:100%}
.pk-raiz .etiquetas{flex:none;display:flex;flex-direction:column;gap:.45vh}
.pk-raiz .et{font-size:1vw;font-weight:800;color:#fff;border-radius:.35vw; padding:.15vh .55vw;white-space:nowrap;font-variant-numeric:tabular-nums}
.pk-raiz .et.vazio{background:#2a313d;color:var(--muted)}
.pk-raiz .ponderada{font-size:.85vw;color:var(--muted)}
.pk-raiz .ponderada .legend{display:flex;flex-wrap:wrap;gap:.3vh .9vw;font-size:.85vw; color:#c7ced9}
.pk-raiz .ponderada .legend span{display:inline-flex;align-items:center;gap:.3vw}
.pk-raiz .ponderada .legend .sw{display:inline-block;width:.9vw;height:.9vw; border-radius:.2vw}
.pk-raiz .grafico{flex:1;min-height:0;position:relative}
.pk-raiz .grafico svg{position:absolute;inset:0;width:100%;height:100%}
.pk-raiz .leg{display:flex;flex-wrap:wrap;gap:.2vh 1vw;font-size:.9vw;color:#c7ced9; margin-bottom:.5vh}
.pk-raiz .leg span{display:inline-flex;align-items:center;gap:.35vw;white-space:nowrap}
.pk-raiz .leg i{display:inline-block;width:1.6vw;height:0;border-top:.22vw solid}
.pk-raiz .leg i.tr{border-top-style:dashed;border-top-width:.16vw}
.pk-raiz .leg i.fx{height:.8vw;border:0;background:var(--proj);opacity:.3; border-radius:.15vw}
.pk-raiz .sem-serie{position:absolute;inset:0;display:flex;align-items:center; justify-content:center;text-align:center;color:var(--muted);font-size:1vw; padding:1vw}
.pk-raiz .vazio-tela{grid-column:1/-1;display:flex;align-items:center; justify-content:center;font-size:1.6vw;color:var(--muted);text-align:center}
.pk-raiz{height:100%;min-height:0;display:flex;flex-direction:column;gap:1.1vh;--a:#5aa2f0;--b:#e2574c}
`;
  function estilo() {
    if (document.getElementById('pk-estilo')) return;
    const s = document.createElement('style');
    s.id = 'pk-estilo';
    s.textContent = ESTILO;
    document.head.append(s);
  }

  /* Uma instância por disputa e tipo de mapa: cada uma com os próprios
   * caches e a própria contagem de cargas. Num roteiro com presidente e
   * governador alternando, a carga atrasada de uma não pode escrever com os
   * dados da outra. */
  function criar(CARGO, UF, MAPA_MUN) {
    const GOV = CARGO === '3';
    const QS = `cargo=${CARGO}` + (GOV ? `&uf=${UF}` : '');
    const NOME_CARGO = GOV ? 'Governador' : 'Presidente';
    let get = null;

    /* Projeção em cache de 60 s e sem segurar a tela, como no telão: cada pedido
     * roda 1.200 simulações no servidor, e pedi-la a cada releitura de 10 s
     * empilharia cálculo em cima da coleta. Se não respondeu em 1,5 s, o apurado
     * entra sem ela e a resposta fica guardada para a releitura seguinte. */
    let cacheProj = null;
    function projecaoEmCache() {
      if (!cacheProj || Date.now() - cacheProj.t >= 60000) {
        cacheProj = {t: Date.now(),
          p: get(`/projection?${QS}&sims=1200&registrar=false`)
            .catch(() => null)};
      }
      return Promise.race([cacheProj.p,
        new Promise(ok => setTimeout(() => ok(null), 1500))]);
    }

    /* Série da noite: apurado e projetado de cada candidato a cada rodada de
     * projeção, como foram registrados (§5.2 — nada é recalculado). Vem da
     * tabela pronta (`/serie-apuracao`), alimentada a cada projeção gravada:
     * a rota antiga relia o diário inteiro e levava de 5 a 7 s. Mesmo assim
     * fica fora da carga da tela e em cache de 60 s — o gráfico é acessório e
     * o diário só ganha linha a cada 2 minutos. */
    let cacheSerie = null;
    let serieAtual;
    function serieEmCache() {
      if (!cacheSerie || Date.now() - cacheSerie.t >= 60000) {
        cacheSerie = {t: Date.now(),
          p: get(`/serie-apuracao?${QS}&top=2`).then(r => r.dados || null)
            .catch(() => null)};
      }
      return cacheSerie.p;
    }

    /* Mapas por município em cache de 60 s: são centenas de KB (o do país,
     * alguns MB) e o desenho só muda quando o lote muda. O mapa por estado é
     * leve e vai sempre fresco. */
    const cacheMapa = new Map();
    function mapaEmCache(u) {
      let c = cacheMapa.get(u);
      if (!c || Date.now() - c.t >= 60000) {
        c = {t: Date.now(), p: get(u)};
        c.p.catch(() => cacheMapa.delete(u));
        cacheMapa.set(u, c);
      }
      return c.p;
    }

    let cargaAtual = 0;
    let assinaturaMapa = null;
    let caixaMapa = null;
    let pintura = Promise.resolve();

    async function carregar(raiz, o) {
      get = o.get;
      const minha = ++cargaAtual;
      const velha = () => minha !== cargaAtual || !!(o.velha && o.velha());
      // A casca (placar em cima, três colunas embaixo) só entra em `raiz`
      // quando há o que mostrar: até lá fica o que estava na tela.
      let casca = raiz.querySelector(':scope > .pk-raiz');
      if (!casca) {
        casca = el('div', {cls: 'pk-raiz'}, el('div', {cls: 'placar'}),
                   el('div', {cls: 'pk-corpo'}));
      }
      const caixaPlacar = casca.children[0], corpo = casca.children[1];
      const entrar = () => { if (!casca.isConnected) poe(raiz, casca); };
      if (!['1', '3'].includes(CARGO) || (GOV && !UF)) {
        poe(caixaPlacar);
        poe(corpo, el('div', {cls: 'vazio-tela'}, GOV
          ? 'Governador é decidido em cada estado: informe a UF.'
          : 'Esta tela só vale para presidente e governador (uma vaga).'));
        entrar();
        return null;
      }
      try {
        const res = await get(
          `/results?abrangencia=${GOV ? 'uf' : 'br'}&${QS}`);
        const u0 = res.dados[0];
        // Apuração encerrada: não pede projeção. Sem registro recente o
        // servidor calcularia e GRAVARIA uma no diário (toda projeção exibida é
        // gravada) — e abrir esta tela dias depois deixava registros
        // pós-eleição no diário oficial (visto em 07/10).
        const acabou = !!u0 && (u0.andamento === 'f'
                                || (u0.pendente && u0.pendente.max === 0));
        const mu = `/map-layers?cargo=${CARGO}&abrangencia=mu`
          + (GOV ? `&uf=${UF}` : '');
        const uf = `/map-layers?cargo=${CARGO}&abrangencia=uf`;
        // `base`: o que alimenta regiões e estados (ou municípios, em
        // governador). `mapa`: o que se desenha. São o mesmo pedido quando o
        // mapa pedido é o da própria base.
        const [cands, def, base, mapa, par, proj] = await Promise.all([
          get(`/candidates?${QS}`),
          get(`/decided?${QS}`).catch(() => null),
          GOV ? mapaEmCache(mu) : get(uf),
          MAPA_MUN ? mapaEmCache(mu) : get(uf),
          get('/partidos').catch(() => null),
          acabou ? null : projecaoEmCache(),
        ]);
        if (velha()) return null;
        if (o.aoAmbiente) o.aoAmbiente(res.ambiente);

        const u = res.dados[0];
        const lista = (((cands.dados || [])[0] || {}).candidatos || [])
          .filter(c => !c.oficial.destinacao_voto
                       || c.oficial.destinacao_voto === 'Válido')
          .slice().sort((x, y) => y.oficial.votos - x.oficial.votos);
        if (!u || lista.length < 2) {
          poe(caixaPlacar);
          poe(corpo, el('div', {cls: 'vazio-tela'},
            res.status_motivo || 'Sem dois candidatos com voto para '
              + `${NOME_CARGO.toLowerCase()}.`));
          entrar();
          return res;
        }

        const cores = {};
        for (const x of ((par && par.dados.partidos) || [])) cores[x.sigla] = x.cor;
        const de = (c, reserva) => {
          const o = c.oficial, e = c.enriquecimento;
          return {sq: o.sqcand, numero: o.numero, votos: o.votos, pct: o.pct,
                  nome: nomeProprio((e && e.nome) || o.nome_urna),
                  partido: o.partido, foto: e && e.foto_url,
                  cor: cores[o.partido] || reserva, eleito: o.eleito_segundo_tse};
        };
        const A = de(lista[0], '#5aa2f0'), B = de(lista[1], '#e2574c');
        casca.style.setProperty('--a', A.cor);
        casca.style.setProperty('--b', B.cor);

        const dfn = def && def.dados && def.dados[0];
        const est = {};
        if (dfn) for (const c of dfn.candidatos) est[c.sqcand] = c.estado;
        const cObs = u.votos && u.votos.validos_computados;
        const restoMax = dfn ? dfn.resto_maximo_total : null;
        // A mesma conta do telão: 2º turno certo quando nem o melhor caso de
        // alguém passa de 50%.
        const turnoCerto = !!(dfn && restoMax != null && cObs > 0
          && !lista.some(c => 2 * c.oficial.votos + restoMax > cObs));
        const e = u.eleitorado;
        const cob = e.total ? 100 * e.apurado / e.total : null;
        const fr = frasePlacar({
          a: A, b: B, nCandidatos: lista.length,
          faltam: u.pendente.max, taxa: e.apurado ? cObs / e.apurado : 0,
          encerrada: u.andamento === 'f' || u.pendente.max === 0,
          eleitoTse: A.eleito === true,
          matematicamenteEleito: est[A.sq] === 'matematicamente_eleito',
          turnoCerto});

        const pj = {};
        if (proj && proj.dados) for (const x of proj.dados) pj[x.sqcand] = x;
        const faixaPj = x => {
          const f = x.projecao.faixa;
          const lo = f ? f.pct_min : x.projecao.pct_q100;
          const hi = f ? f.pct_max : x.projecao.pct_q900;
          return `${lo.toFixed(1).replace('.', ',')}–`
               + `${hi.toFixed(1).replace('.', ',')}, ${f ? f.nivel : 80}%`;
        };
        const temPj = !!(proj && proj.publicavel && pj[A.sq] && pj[B.sq]);

        // ---- placar
        const lado = (c, qual) => el('div', {cls: 'cand ' + qual,
            style: `--cor:${c.cor}`},
          c.foto ? el('img', {cls: 'foto', src: c.foto, alt: ''})
                 : el('div', {cls: 'num'}, c.numero),
          el('div', {cls: 'ident'},
            el('div', {cls: 'nome'}, c.nome),
            el('div', {cls: 'part'}, `${c.partido} ${c.numero}`,
              c.eleito === true ? [' ', el('span', {cls: 'pk-selo'}, 'eleito')]
                                : null)));
        const numero = c => el('div', {cls: 'lado-n'},
          el('div', {cls: 'p'}, pct(c.pct, 2)),
          el('div', {cls: 'v'}, fmt(c.votos) + ' votos'));
        const lugar = GOV ? ((u.geografia && u.geografia.nome) || UF.toUpperCase())
                          : 'Brasil';
        poe(caixaPlacar,
          // No telão o cargo e o lugar já estão no cabeçalho da tela.
          o.semTitulo ? null
            : el('div', {cls: 'titulo'}, el('b', {}, NOME_CARGO), ' · ', lugar),
          el('div', {cls: 'duelo'}, lado(A, 'a'),
            el('div', {cls: 'numeros'}, numero(A), numero(B)), lado(B, 'b')),
          el('div', {cls: 'cabo'},
            el('i', {cls: 'a', style: `width:${A.pct || 0}%`}),
            el('i', {cls: 'o'}),
            el('i', {cls: 'b', style: `width:${B.pct || 0}%`}),
            el('span', {cls: 'meio'}),
            // Marca roxa: onde a projeção põe o candidato da esquerda.
            temPj ? el('span', {cls: 'pj', style:
              `left:${Math.max(0, Math.min(100,
                pj[A.sq].projecao.pct_mediana))}%`}) : null),
          el('div', {cls: 'veredito ' + fr.tom},
            el('span', {cls: 'forte'}, fr.forte),
            el('span', {cls: 'txt'}, fr.texto),
            temPj ? el('span', {cls: 'pj-txt'},
              `projeção ${A.nome.split(' ')[0]} `
              + `${pct(pj[A.sq].projecao.pct_mediana, 1)} (${faixaPj(pj[A.sq])})`)
              : null));

        // ---- coluna esquerda: números e o gráfico da noite
        const unidades = base.dados || [];
        const regioes = REGIOES.map(r => {
          let va = 0, vb = 0, tot = 0, falta = 0, eleit = 0, completo = true;
          for (const x of unidades.filter(d => d.regiao === r)) {
            const a = daUF(x, A.sq), b = daUF(x, B.sq);
            if (!a || !b) { completo = false; continue; }
            va += a.votos; vb += b.votos; tot += validosDaUF(x);
            eleit += x.eleitorado || 0;
            falta += (x.eleitorado || 0) * (1 - (x.cobertura_pct || 0) / 100);
          }
          return {r, pa: tot && completo ? 100 * va / tot : null,
                  pb: tot && completo ? 100 * vb / tot : null,
                  cob: eleit ? 100 * (1 - falta / eleit) : null, falta};
        });
        const eleitTotal = Math.max(1, unidades.reduce(
          (t, x) => t + (x.eleitorado || 0), 0));
        const maiores = unidades.filter(x => x.lider_sqcand && !x.aguardando)
          .sort((x, y) => (y.eleitorado || 0) - (x.eleitorado || 0));
        const alvoGrafico = el('div', {cls: 'grafico'});
        const colEsq = el('div', {cls: 'col'},
          el('div', {cls: 'caixa'},
            el('div', {cls: 'pk-kpis'},
              el('div', {cls: 'pk-kpi'}, el('div', {cls: 'k'}, 'Apurado'),
                el('div', {cls: 'v'}, pct(cob, 2)),
                el('div', {cls: 'n'}, `${fmt(e.apurado)} eleitores`)),
              el('div', {cls: 'pk-kpi'},
                el('div', {cls: 'k'}, u.pendente.max > 0 ? 'Faltam entrar'
                                                          : 'Situação'),
                el('div', {cls: 'v'}, u.pendente.max > 0 ? falado(u.pendente.max)
                  : (u.andamento === 'f' ? 'encerrada' : 'em andamento')),
                el('div', {cls: 'n'}, u.pendente.max > 0 ? 'eleitores no máximo'
                                                          : 'apuração')),
              el('div', {cls: 'pk-kpi'}, el('div', {cls: 'k'}, 'Comparecimento'),
                el('div', {cls: 'v'}, pct(e.comparecimento_pct, 1)),
                el('div', {cls: 'n'}, 'do eleitorado apurado')),
              el('div', {cls: 'pk-kpi'}, el('div', {cls: 'k'}, 'Brancos e nulos'),
                el('div', {cls: 'v'}, pct(u.votos.total ? 100
                  * (u.votos.brancos + u.votos.nulos) / u.votos.total : null, 1)),
                el('div', {cls: 'n'}, 'dos votos')))),
          temPj ? el('div', {cls: 'caixa projecao'},
            el('div', {cls: 'rot'}, 'Projeção ',
              el('small', {}, `${(proj.modelo || '').toUpperCase()} · mediana e `
                + 'intervalo')),
            ...[A, B].map(c => el('div', {cls: 'pl'}, el('b', {}, c.nome),
              el('span', {}, `${pct(pj[c.sq].projecao.pct_mediana, 1)} `
                + `(${faixaPj(pj[c.sq])})`)))) : null,
          el('div', {cls: 'caixa', style: 'flex:1;display:flex;'
              + 'flex-direction:column'},
            el('div', {cls: 'rot'}, 'Ao longo da apuração ',
              el('small', {}, '% dos válidos, conforme o % apurado')),
            el('div', {cls: 'leg'},
              ...[A, B].map(c => el('span', {},
                el('i', {style: `border-color:${c.cor}`}), c.nome)),
              el('span', {}, el('i', {cls: 'tr', style: 'border-color:#c7ced9'}),
                 'nossa projeção'),
              el('span', {}, el('i', {cls: 'fx'}), 'intervalo')),
            alvoGrafico));

        // ---- coluna direita: regiões em cima, estados mais apertados embaixo
        const caixaRegioes = el('div', {cls: 'caixa', style: 'flex:5;display:flex;'
              + 'flex-direction:column'},
            GOV ? [
              el('div', {cls: 'rot'}, 'Maiores municípios ',
                el('small', {}, '% dos votos válidos')),
              el('div', {cls: 'linhas'}, ...maiores.slice(0, 5).map(x => {
                const a = daUF(x, A.sq), b = daUF(x, B.sq);
                return linhaDupla(nomeProprio(x.nome_extenso || x.nome),
                  pct(100 * x.eleitorado / eleitTotal, 1) + ' dos eleitores'
                  + (x.cobertura_pct != null && x.cobertura_pct < 99.95
                     ? ` · ${pct(x.cobertura_pct, 1)} apurado` : ''),
                  a && a.pct, b && b.pct, '9.5vw');
              }))] : [
              el('div', {cls: 'rot'}, 'Regiões ',
                el('small', {}, '% dos votos válidos')),
              el('div', {cls: 'linhas'}, ...regioes.map(g => linhaDupla(g.r,
                g.cob == null ? null : g.cob >= 99.995 ? '100% apurado'
                  : `${pct(g.cob, 1)} · faltam ${falado(g.falta)}`,
                g.pa, g.pb, '8.5vw')))]);
        const comDois = (GOV ? maiores.slice(0, MAIORES_GOV) : unidades)
          .filter(x => x.lider_sqcand && !x.aguardando && x.margem_pp != null);
        const apertados = comDois.slice()
          .sort((x, y) => x.margem_pp - y.margem_pp).slice(0, QUANTOS_APERTADOS);
        const corDe = x => x.lider_sqcand === A.sq ? A.cor
          : x.lider_sqcand === B.sq ? B.cor : (x.lider_partido_cor || '#59636e');
        const colDir = el('div', {cls: 'col'},
          caixaRegioes,
          el('div', {cls: 'caixa', style: 'flex:8;display:flex;'
              + 'flex-direction:column'},
            el('div', {cls: 'rot'}, GOV ? 'Mais apertados entre os maiores '
                                        : 'Estados mais apertados ',
              el('small', {}, GOV
                ? `${apertados.length} dos ${comDois.length} maiores municípios `
                  + '· município não elege governador'
                : `${apertados.length} de ${comDois.length} · `
                  + 'estado não elege presidente')),
            el('div', {cls: 'linhas'}, ...apertados.map(x => {
              const a = daUF(x, A.sq), b = daUF(x, B.sq);
              const nome = GOV ? nomeProprio(x.nome_extenso || x.nome)
                : x.uf === 'zz' ? 'Exterior' : (x.nome_extenso || x.nome);
              const linha = linhaDupla(nome,
                `${x.margem_pp.toFixed(1).replace('.', ',')} pp`
                + (GOV ? ` · ${pct(100 * x.eleitorado / eleitTotal, 1)} dos `
                         + 'eleitores' : '')
                + (x.cobertura_pct != null && x.cobertura_pct < 99.95
                   ? ` · ${pct(x.cobertura_pct, 1)} apurado` : ''),
                a && a.pct, b && b.pct, '12.5vw');
              // Selo com a cor de quem lidera ali: a sigla do estado ou, em
              // governador, só a cor.
              linha.querySelector('.onde b').prepend(el('span', {cls: 'sg',
                style: `background:${corDe(x)}` + (GOV ? ';min-width:1vw' : '')},
                GOV ? '\u00a0' : x.uf.toUpperCase()));
              return linha;
            }))));

        // ---- centro: mapa. Só redesenha quando o lote de dados muda.
        const assin = [QS, MAPA_MUN, mapa.snapshot_set_id, base.snapshot_set_id,
                       A.sq, B.sq].join('|');
        if (assin !== assinaturaMapa || !caixaMapa || !caixaMapa.isConnected) {
          const alvo = el('div', {cls: 'mapa-svg'});
          const pond = el('div', {cls: 'ponderada'});
          const etiquetas = GOV ? null
            : el('div', {cls: 'etiquetas'}, ...PEQUENOS.map(s => {
            const x = unidades.find(d => d.uf === s);
            return (x && x.lider_sqcand && !x.aguardando)
              ? el('div', {cls: 'et', style: `background:${corDe(x)}`},
                  `${s.toUpperCase()} ${pct(x.lider_pct, 0)}`)
              : el('div', {cls: 'et vazio'}, s.toUpperCase());
          }));
          caixaMapa = el('div', {cls: 'col'},
            el('div', {cls: 'caixa pk-mapa'}, alvo, etiquetas),
            el('div', {cls: 'caixa'},
              el('div', {cls: 'rot'}, 'Na proporção dos eleitores ',
                el('small', {}, 'o mapa mostra área; quem decide é eleitor')),
              pond));
          const desenhados = mapa.dados || [];
          // Governador sem municípios: o mapa dos estados, enquadrado na UF e
          // com ela destacada, como o telão faz quando falta dado municipal.
          const daUf = GOV && !MAPA_MUN
            ? desenhados.find(d => d.uf === UF) : null;
          pintura = MAPA.desenhar(alvo, desenhados, {
            malha: mapa.malha, camada: 'partido', recorte: mapa.recorte_ibge,
            foco: daUf && daUf.cd_ibge, destaque: daUf && daUf.cd_ibge,
            largura: 900, altura: 900,
          }).then(r => {
              // A barra dos eleitores é sempre a da disputa: em governador sem
              // municípios o mapa mostra os 27 estados, mas a barra continua
              // sendo a dos municípios daquele estado.
              const daBarra = GOV ? unidades : desenhados;
              poe(pond, MAPA.barraPonderada(daBarra, r.camada, r.ctx),
                  MAPA.legenda(r.camada, r.ctx, daBarra));
              if (GOV) return;
              if (!MAPA_MUN) return rotularEstados(alvo, unidades);
              // Presidente por município: as divisas e os percentuais dos
              // estados vão numa camada por cima, só com o contorno.
              const sobre = el('div', {cls: 'sobre'});
              return MAPA.desenhar(sobre, unidades, {
                malha: base.malha, camada: 'partido', largura: 900, altura: 900,
              }).then(() => {
                for (const t of sobre.querySelectorAll('path[data-cd]')) {
                  t.setAttribute('fill', 'none');
                  t.setAttribute('stroke', 'rgba(255,255,255,.6)');
                  t.setAttribute('stroke-width', '1.2');
                  t.removeAttribute('stroke-dasharray');
                }
                alvo.append(sobre);
                rotularEstados(sobre, unidades);
              });
            })
            .catch(() => poe(alvo, el('div', {cls: 'vazio-tela'},
                                      'mapa indisponível')));
          assinaturaMapa = assin;
        }

        if (velha()) return null;
        poe(corpo, colEsq, caixaMapa, colDir);
        entrar();
        // Depois de entrar na página: o gráfico mede a própria caixa.
        graficoApuracao(alvoGrafico, serieAtual, [A, B]);
        const aSerie = serieEmCache().then(sr => {
          const nova = sr !== serieAtual;
          serieAtual = sr;
          if (nova && alvoGrafico.isConnected) {
            graficoApuracao(alvoGrafico, sr, [A, B]);
          }
        });
        // Captura de imagem: só devolve com o mapa e o gráfico desenhados.
        if (o.esperar) await Promise.all([pintura, aSerie]);
        return res;
      } catch (err) {
        if (velha()) return null;
        assinaturaMapa = null;
        if (o.propagar) throw err;
        poe(corpo, el('div', {cls: 'vazio-tela'}, 'erro: ' + err.message));
        entrar();
        return null;
      }
    }
    return carregar;
  }

  const instancias = new Map();

  /* Desenha (ou atualiza) o placar dentro de `raiz`.
   *
   *   o.cargo       '1' ou '3'
   *   o.uf          sigla, obrigatória em governador
   *   o.municipios  mapa com municípios; padrão: sim em governador
   *   o.get         função que busca na API e devolve o JSON
   *   o.velha       () => true se outra tela já assumiu
   *   o.semTitulo   não repete cargo e lugar (o telão já os mostra)
   *   o.esperar     só devolve com mapa e gráfico prontos (captura)
   *   o.propagar    lança o erro em vez de escrevê-lo na tela
   *   o.aoAmbiente  recebe o `ambiente` da resposta (faixa de aviso)
   *
   * Devolve a resposta de /results (para o rodapé de quem chamou) ou null. */
  function desenhar(raiz, o) {
    estilo();
    const cargo = String(o.cargo || '1');
    const uf = String(o.uf || '').toLowerCase();
    const mun = o.municipios == null ? cargo === '3' : !!o.municipios;
    const chave = [cargo, uf, mun].join('|');
    if (!instancias.has(chave)) instancias.set(chave, criar(cargo, uf, mun));
    return instancias.get(chave)(raiz, o);
  }

  return {desenhar, frasePlacar};
})();
