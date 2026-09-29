/**
 * Hermes Gantt — plugin de dashboard (pagina web)
 *
 * Linha do tempo dos cards do board: quando cada card nasceu, quando comecou e quando fechou.
 * Le o backend JA montado do plugin kanban-gantt (/api/plugins/kanban-gantt/gantt).
 *
 * IIFE puro, sem build: usa window.__HERMES_PLUGIN_SDK__ (React + primitivas + fetchJSON autenticado
 * com prefixo de base-path do proxy reverso).
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { Card, CardContent, Badge, Select, SelectOption } = SDK.components;
  const { useState, useEffect, useMemo } = SDK.hooks;

  const API = "/api/plugins/kanban-gantt";
  const API_PLANO = "/api/plugins/gantt-timeline";
  const COR_ATIVA = "#E63946"; // vermelho Transformativa: trabalho em curso

  const CORES = {
    running: COR_ATIVA,
    ready: "#F08A5D",
    review: "#C99A2E",
    blocked: "#8C1D18",
    rejected: "#8C1D18",
    done: "#2F9E62",
    verified: "#2F9E62",
    todo: "#9AA0A6",
    backlog: "#9AA0A6",
  };
  const ROTULO_STATUS = {
    done: "concluído", verified: "verificado", running: "em execução", ready: "pronto",
    review: "em revisão", blocked: "bloqueado", rejected: "rejeitado",
    todo: "a fazer", backlog: "backlog",
  };

  function ms(v) {
    if (v === null || v === undefined) return null;
    const n = Number(v);
    if (!isFinite(n) || n <= 0) return null;
    return n > 1e12 ? n : n * 1000; // segundos ou milissegundos, tanto faz
  }
  function dia(ts) {
    return new Date(ts).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }
  function diaLongo(ts) {
    return new Date(ts).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
  }
  function onda(titulo) {
    const m = /^(TRE-[A-Z]+\d*)/i.exec(titulo || "");
    if (m) return m[1].toUpperCase();
    const m2 = /^(TRE)-W(\d+)/i.exec(titulo || "");
    if (m2) return "TRE-W" + m2[2];
    const m3 = /(W\d+)/.exec(titulo || "");
    return m3 ? "W" + m3[1].replace(/\D/g, "") : "outros";
  }

  function GanttPage() {
    const [boards, setBoards] = useState([]);
    const [board, setBoard] = useState(null);
    const [snap, setSnap] = useState(null);
    const [erro, setErro] = useState(null);
    const [zoom, setZoom] = useState("ajustar");
    const [ocultarArquivados, setOcultarArquivados] = useState(true);
    const [ondaFiltro, setOndaFiltro] = useState("todas");
    const [selecionado, setSelecionado] = useState(null);
    const [plano, setPlano] = useState(null);

    useEffect(function () {
      SDK.fetchJSON(API + "/boards")
        .then(function (d) {
          const lista = (d && d.boards) || [];
          setBoards(lista);
          if (lista.length && !board) {
            const alvo = lista.filter(function (b) { return b.slug === "transformativa-revenue-engine"; })[0] || lista[0];
            setBoard(alvo.slug);
          }
        })
        .catch(function (e) { setErro("Não consegui listar os boards: " + e.message); });
    }, []);

    useEffect(function () {
      if (!board) return;
      setErro(null);
      SDK.fetchJSON(API + "/gantt?board=" + encodeURIComponent(board))
        .then(setSnap)
        .catch(function (e) { setErro("Não consegui ler o board " + board + ": " + e.message); });
    }, [board]);

    useEffect(function () {
      // O cronograma e opcional: sem ele a linha do tempo segue so com o REAL, sem inventar planejado.
      SDK.fetchJSON(API_PLANO + "/planejado")
        .then(function (p) { setPlano(p && p.disponivel ? p : null); })
        .catch(function () { setPlano(null); });
    }, []);

    const dados = useMemo(function () {
      if (!snap || !snap.tasks) return null;
      const todos = snap.tasks;
      const visiveis = todos.filter(function (t) {
        if (ocultarArquivados && t.archived) return false;
        if (ondaFiltro !== "todas" && onda(t.title) !== ondaFiltro) return false;
        return true;
      });
      const agora = Date.now();
      const planoDe = function (t) {
        if (!plano) return null;
        const p = (plano.cards && plano.cards[t.id]) || (plano.ondas && plano.ondas[onda(t.title)]);
        if (!p) return null;
        const ini = ms(p.inicio), fim = ms(p.fim);
        return (ini || fim) ? { inicio: ini, fim: fim, estado: p.estado, daOnda: !(plano.cards && plano.cards[t.id]) } : null;
      };
      let min = null, max = null;
      visiveis.forEach(function (t) {
        const c = ms(t.created_at), s = ms(t.started_at) || c, f = ms(t.completed_at) || (s ? agora : null);
        const pl = planoDe(t);
        [c, s, f, pl && pl.inicio, pl && pl.fim].forEach(function (v) { if (v) { if (min === null || v < min) min = v; if (max === null || v > max) max = v; } });
      });
      if (min === null) { min = agora - 7 * 864e5; max = agora; }

      // janela: presets ancorados em hoje, ou "ajustar" = dados + folga
      if (zoom !== "ajustar") {
        const dias = { "30 dias": 30, "90 dias": 90, "180 dias": 180 }[zoom] || 30;
        max = Math.max(max, agora);
        min = Math.min(min, agora - dias * 864e5);
      } else {
        const folga = Math.max(864e5, (max - min) * 0.04);
        min -= folga; max += folga;
      }
      const span = Math.max(1, max - min);

      const grupos = {};
      visiveis.forEach(function (t) {
        const g = onda(t.title);
        (grupos[g] = grupos[g] || []).push(t);
      });
      const ordem = Object.keys(grupos).sort(function (a, b) {
        if (a === "outros") return 1;
        if (b === "outros") return -1;
        return a.localeCompare(b, "pt-BR", { numeric: true });
      });

      const passo = span <= 45 * 864e5 ? 864e5 : span <= 400 * 864e5 ? 7 * 864e5 : 30 * 864e5;
      const marcos = [];
      for (let v = Math.ceil(min / passo) * passo; v <= max; v += passo) marcos.push(v);

      const feito = visiveis.filter(function (t) { return t.status === "done" || t.status === "verified"; }).length;

      const planejadoDaOnda = {};
      Object.keys(grupos).forEach(function (g) {
        const pl = plano && plano.ondas && plano.ondas[g];
        planejadoDaOnda[g] = pl ? { inicio: ms(pl.inicio), fim: ms(pl.fim) } : null;
      });

      return { visiveis: visiveis, grupos: grupos, ordem: ordem, min: min, max: max, span: span,
               marcos: marcos, passo: passo, agora: agora, total: todos.length,
               feito: feito, ocultos: todos.length - visiveis.length,
               planoDe: planoDe, planejadoDaOnda: planejadoDaOnda,
               deps: (snap.tasks || []).reduce(function (acc, t) { acc[t.id] = t; return acc; }, {}) };
    }, [snap, zoom, ocultarArquivados, ondaFiltro, plano]);

    const ondas = useMemo(function () {
      if (!snap || !snap.tasks) return [];
      const s = {};
      snap.tasks.forEach(function (t) { const g = onda(t.title); s[g] = (s[g] || 0) + 1; });
      return Object.keys(s).sort(function (a, b) {
        if (a === "outros") return 1; if (b === "outros") return -1;
        return a.localeCompare(b, "pt-BR", { numeric: true });
      });
    }, [snap]);

    function pct(v) { return ((v - dados.min) / dados.span) * 100; }

    function barra(t) {
      const c = ms(t.created_at), s = ms(t.started_at) || c, f = ms(t.completed_at);
      const cor = CORES[t.status] || CORES.backlog;
      const concluido = t.status === "done" || t.status === "verified";
      const corr = concluido ? CORES.done : cor;
      const dadosT = { id: t.id, status: t.status, assignee: t.assignee, titulo: t.title,
                       criado: c, iniciado: ms(t.started_at), fim: f, plano: pl,
                       pais: (t.parents || []).length, filhos: (t.children || []).length };
      const titulo = t.title + "\n" + (ROTULO_STATUS[t.status] || t.status) +
        "\ncriado " + diaLongo(c) +
        (dadosT.iniciado ? "\niniciado " + diaLongo(dadosT.iniciado) : "\nnão iniciado") +
        (f ? "\nfechado " + diaLongo(f) : "") +
        (dadosT.pais ? "\ndepende de " + dadosT.pais + " card(s)" : "");
      const elementos = [];
      const pl = dados.planoDe(t);

      // PLANEJADO primeiro (fundo): barra fina com contorno tracejado — o real fica por cima, cheio.
      if (pl && pl.fim) {
        elementos.push(h("div", {
          key: "p", className: "hg-plano",
          title: "planejado" + (pl.daOnda ? " (janela da onda)" : " (do card)") + ": " + diaLongo(pl.inicio) + " a " + diaLongo(pl.fim),
          style: { left: pct(pl.inicio) + "%", width: Math.max(0.4, ((pl.fim - pl.inicio) / dados.span) * 100) + "%" },
        }));
      }

      if (s && f) {
        elementos.push(h("div", {
          key: "b", className: "hg-bar", title: titulo,
          style: { left: pct(s) + "%", width: Math.max(0.4, ((f - s) / dados.span) * 100) + "%", background: corr },
          onClick: function () { setSelecionado(dadosT); },
        }));
      } else if (s) {
        // em aberto: do inicio ate agora, com hachura para nao mentir sobre o fim
        elementos.push(h("div", {
          key: "b", className: "hg-bar hg-bar--aberta", title: titulo,
          style: { left: pct(s) + "%", width: Math.max(0.4, ((dados.agora - s) / dados.span) * 100) + "%", background: corr },
          onClick: function () { setSelecionado(dadosT); },
        }));
      } else if (c) {
        // nunca iniciado: marco, sem inventar duracao
        elementos.push(h("div", {
          key: "m", className: "hg-marco", title: titulo,
          style: { left: pct(c) + "%", borderColor: corr },
          onClick: function () { setSelecionado(dadosT); },
        }));
      }
      return elementos;
    }

    function linha(t) {
      const cor = CORES[t.status] || CORES.backlog;
      return h("div", { key: t.id, className: "hg-linha" + (selecionado && selecionado.id === t.id ? " hg-linha--sel" : "") },
        h("div", { className: "hg-rotulo", title: t.title },
          h("span", { className: "hg-ponto", style: { background: cor } }),
          h("span", { className: "hg-titulo" }, t.title),
          h("span", { className: "hg-atrib" }, t.assignee ? "· " + t.assignee : ""),
        ),
        h("div", { className: "hg-trilha" }, barra(t)),
      );
    }

    if (erro) return h(Card, null, h(CardContent, null, h("div", { className: "hg-erro" }, erro)));
    if (!snap || !dados) return h("div", { className: "hg-vazio" }, "Carregando a linha do tempo...");

    const progresso = dados.visiveis.length && dados.total ? Math.round((dados.feito / dados.visiveis.length) * 100) : 0;

    return h("div", { className: "hg-pagina" },
      h("div", { className: "hg-topo" },
        h("div", null,
          h("h1", { className: "hg-titulo-pagina" }, "Linha do tempo"),
          h("div", { className: "hg-sub" },
            (plano && plano.versao ? "cronograma " + plano.versao + " · " : "") +
            dados.visiveis.length + " de " + dados.total + " cards visíveis · " +
            dados.feito + " concluídos (" + progresso + "%)" +
            (dados.ocultos ? " · " + dados.ocultos + " fora do filtro" : "")),
        ),
        h("div", { className: "hg-controles" },
          h(Select, { value: board || "", onValueChange: setBoard },
            boards.map(function (b) { return h(SelectOption, { key: b.slug, value: b.slug }, b.label); })),
          h(Select, { value: ondaFiltro, onValueChange: setOndaFiltro },
            [h(SelectOption, { key: "todas", value: "todas" }, "todas as ondas")].concat(
              ondas.map(function (o) { return h(SelectOption, { key: o, value: o }, o + " (" + (dados.grupos[o] || []).length + ")"); }))),
          h(Select, { value: zoom, onValueChange: setZoom },
            ["ajustar", "30 dias", "90 dias", "180 dias"].map(function (z) { return h(SelectOption, { key: z, value: z }, z); })),
          h("label", { className: "hg-check" },
            h("input", { type: "checkbox", checked: ocultarArquivados, onChange: function (e) { setOcultarArquivados(e.target.checked); } }),
            "ocultar arquivados"),
        ),
      ),

      h("div", { className: "hg-legenda" },
        Object.keys(CORES).filter(function (s) { return ["done", "running", "ready", "review", "blocked", "todo"].indexOf(s) >= 0; })
          .map(function (s) {
            return h("span", { key: s, className: "hg-legenda-item" },
              h("span", { className: "hg-ponto", style: { background: CORES[s] } }), ROTULO_STATUS[s] || s);
          }),
        h("span", { className: "hg-legenda-item" }, h("span", { className: "hg-marco-mini" }), "não iniciado (sem duração)"),
        h("span", { className: "hg-legenda-item" }, h("span", { className: "hg-plano-mini" }), "planejado (cronograma)"),
      ),

      selecionado ? h("div", { className: "hg-detalhe" },
        h("strong", null, selecionado.titulo),
        h("div", null, (ROTULO_STATUS[selecionado.status] || selecionado.status) + " · " + (selecionado.assignee || "sem perfil")),
        h("div", null, "criado " + diaLongo(selecionado.criado) +
          (selecionado.iniciado ? " · iniciado " + diaLongo(selecionado.iniciado) : " · não iniciado") +
          (selecionado.fim ? " · fechado " + diaLongo(selecionado.fim) : "")),
        (selecionado.pais || selecionado.filhos) ? h("div", { className: "hg-sub" }, "depende de " + selecionado.pais + " · libera " + selecionado.filhos) : null,
        selecionado.plano
          ? h("div", { className: "hg-sub" },
              "planejado: " + diaLongo(selecionado.plano.inicio) + " a " + diaLongo(selecionado.plano.fim) +
              (selecionado.plano.daOnda ? " (janela da onda)" : " (do card)") +
              (selecionado.fim ? (selecionado.fim <= selecionado.plano.fim ? " · fechou dentro do planejado" : " · fechou DEPOIS do planejado") : ""))
          : h("div", { className: "hg-sub" }, "sem data planejada no cronograma"),
      ) : null,

      h("div", { className: "hg-quadro" },
        h("div", { className: "hg-escala" },
          h("div", { className: "hg-escala-rotulo" }, "card"),
          h("div", { className: "hg-escala-trilha" },
            dados.marcos.map(function (m) {
              return h("div", { key: m, className: "hg-marco-tempo", style: { left: pct(m) + "%" } },
                h("span", null, dados.passo <= 864e5 ? dia(m) : diaLongo(m).slice(0, 5)));
            }))),
        dados.ordem.map(function (g) {
          return h("div", { key: g, className: "hg-grupo" },
            h("div", { className: "hg-grupo-titulo" },
              g + "  ·  " + dados.grupos[g].length + " cards" +
              (dados.planejadoDaOnda[g] && dados.planejadoDaOnda[g].fim
                ? "  ·  planejado " + dia(dados.planejadoDaOnda[g].inicio) + " a " + dia(dados.planejadoDaOnda[g].fim)
                : "  ·  sem planejado no cronograma")),
            dados.grupos[g].map(linha));
        }),
        !dados.visiveis.length ? h("div", { className: "hg-vazio" }, "Nenhum card com esses filtros.") : null,
      ),
    );
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("gantt-timeline", GanttPage);
  }
})();
