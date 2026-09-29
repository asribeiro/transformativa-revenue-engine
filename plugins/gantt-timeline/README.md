# Plugin: linha do tempo (Gantt) do board — [ASR]

Pagina web do dashboard do Hermes que mostra os cards do board numa linha do tempo: quando nasceram,
quando comecaram e quando fecharam, agrupados por onda, com filtro por onda/board e zoom de janela.

- **Nao e o plugin `kanban-gantt` do catalogo.** Aquele so entrega a *API* (`dashboard/plugin_api.py`)
  mais uma pagina que carrega **apenas no app desktop** (`desktop/plugin.js`) — no navegador ele nao
  aparece. Este plugin entrega a **pagina web** e consome o backend daquele (`/api/plugins/kanban-gantt/gantt`),
  sem duplicar a leitura do banco.
- Sem build: IIFE puro usando `window.__HERMES_PLUGIN_SDK__` (`fetchJSON` aplica auth e o prefixo do
  proxy reverso; `authedFetch`/`buildWsUrl` para o resto).
- Nomes de arquivo importam: `dashboard/manifest.json` + `entry` apontando para um arquivo existente.

## Instalar
```bash
cp -r plugins/gantt-timeline "$HERMES_HOME/plugins/"     # no container: /opt/data/plugins
hermes config set plugins.enabled '[...com "gantt-timeline"...]' --force   # lista, nao string
/command/s6-svc -r /run/service/dashboard
```
Conferir a descoberta (prova, nao suposicao):
```bash
/opt/hermes/.venv/bin/python -c "from hermes_cli.web_server_dashboard import _discover_dashboard_plugins as d; print([(p['name'], p['tab']['path']) for p in d()])"
```

## Verificado em 29/09/2026
- Descoberta: `gantt-timeline tab=/gantt entry_ok=True fonte=user`.
- Dados: `/api/plugins/kanban-gantt/gantt?board=transformativa-revenue-engine` → 95 cards com
  `created_at`/`started_at`/`completed_at` (epoch em segundos) e `parents`/`children`.
- Carregamento da pagina: executada com SDK simulado → registra componente React `gantt-timeline`.
- O que **nao** esta provado por mim: o desenho na tela (isso e olho humano, na URL do dashboard).
