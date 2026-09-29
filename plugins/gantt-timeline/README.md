# Plugin: linha do tempo (Gantt) do board — [ASR]

Pagina web do dashboard do Hermes com a linha do tempo do board: barras do **REAL** (nascimento, execucao,
fechamento, lidos do board) e barras **PLANEJADAS** (janelas do cronograma versionado), agrupadas por onda.

- **Nao e o plugin `kanban-gantt` do catalogo.** Aquele entrega API + pagina que carrega apenas no **app
  desktop**; no dashboard web o arquivo da pagina nem existe (medido: `entry_ok=False`). Este plugin entrega
  a pagina web e consome o backend daquele (`/api/plugins/kanban-gantt/gantt`) para o real.
- **Planejado x real em fontes separadas, de proposito:** o board guarda o que aconteceu (e nao tem campo de
  data planejada); o cronograma `hermes/plan/cronograma.yaml` guarda o que se pretendia. Somar as duas coisas
  numa tela so e o que revela o desvio.
- Sem build: IIFE puro com `window.__HERMES_PLUGIN_SDK__` (`fetchJSON` aplica auth e o prefixo do proxy
  reverso). Registra com `window.__HERMES_PLUGINS__.register("gantt-timeline", Pagina)`.

## Arquivos
- `dashboard/manifest.json` — `tab` (`/gantt`), `entry`, `css` e `api`.
- `dashboard/dist/index.js` — a pagina (real + planejado, filtros, zoom, detalhe do card).
- `dashboard/dist/style.css` — estilo (cinzas em alfa, funcionam em tema claro e escuro).
- `dashboard/plugin_api.py` — `/api/plugins/gantt-timeline/planejado`: le o cronograma (caminho
  configuravel por `TRE_CRONOGRAMA`) e devolve janelas por onda/card; sem cronograma responde
  `disponivel: false` e a pagina segue so com o real, sem inventar data.

## Instalar / atualizar
```bash
cp -r plugins/gantt-timeline "$HERMES_HOME/plugins/"       # no container: /opt/data/plugins
hermes config set plugins.enabled '[...com "gantt-timeline"...]' --force   # lista, nao string
/command/s6-svc -r /run/service/dashboard                  # a api so monta apos reiniciar
```
Conferir por codigo (nao por HTTP: tudo redireciona para o login):
```bash
/opt/hermes/.venv/bin/python -c "from hermes_cli.web_server_dashboard import _discover_dashboard_plugins as d; print([(p['name'], p['tab']['path'], p['has_api']) for p in d()])"
grep -i "Mounted plugin API routes" /opt/data/logs/agent.log | tail -4
```

## Verificado em 29/09/2026
- Descoberta: `gantt-timeline tab=/gantt entry=dist/index.js has_api=True fonte=user`.
- Dados reais: `/api/plugins/kanban-gantt/gantt?board=transformativa-revenue-engine` (95 cards com
  `created_at`/`started_at`/`completed_at` e `parents`/`children`).
- Planejado: `planejado.planejado()` -> `disponivel: True`, `cronograma-tre-v1.0`, 10 ondas.
- Pagina: `node --check` exit 0 e, executada com SDK simulado, registra o componente.
- O que **nao** esta provado por mim: o desenho na tela (verificacao humana, na URL do dashboard).

## Armadilha da sincronizacao
Copiar a pasta live para o repo com `rmtree` **apaga** o que so existe num dos lados (aconteceu com este
README). Mantenha os arquivos presentes nos dois lados, ou sincronize com `--delete` consciente.
