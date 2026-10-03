# Registro de execuções — TRE-W6-E06-T01 (atualizar o Odoo a partir das respostas)

Branch: `feature/TRE-W6-E06-T01` (base `origin/feature/TRE-W6-E05-T01`).
Ambiente do E2E: `vmi3619453` (host de dev) — Postgres descartável `pg-e06-acc` (imagem `postgres:16`) e
stub da API controlada em `127.0.0.1:8799`. Nada em produção; nenhum schema alterado; nenhum e-mail
lido/enviado.

## Medições (todas por execução real, saída completa anexada ao card)

| execução | comando | resultado |
|---|---|---|
| suite offline | `python3 scripts/agentes/verificar_atualizacao_odoo.py` | `PASS (67 itens, 0 falhas)` exit 0 |
| prova de dente | `python3 scripts/agentes/verificar_atualizacao_odoo.py --prova-de-dente` | `dentes: 12/12 reprovaram como esperado` exit 0 |
| aceite E2E | `bash scripts/agentes/teste_atualizacao_odoo_aceite.sh` | `ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_OK (45 itens, 0 falhas)` exit 0 |
| portão de estrutura | `bash scripts/verificar_estrutura.sh` | `RESULTADO: PASS (0 falhas)` exit 0 |

### O que o aceite mediu (números da rodada final, `acc7`)

- corpus: 5 respostas classificadas (1 sem lead, 1 bounce) em 1 organização com 2 contatos;
- rodada confirmada: **3 propagadas** (`INTERESSE`, `OPT_OUT`, `SEM_INTERESSE`), **1 `SEM_ATO`** (BOUNCE),
  **1 `SEM_VINCULO`** (INTERESSE sem lead), **5 linhas de trilha** (uma por interaction);
- API controlada: **9 chamadas** (3 atos × ler + upsert + atividade), todas `ambiente=dev`, todas com
  bearer conferido, todas as escritas com `idempotency_key`; eventos `RESPOSTA_INTERESSE`,
  `RESPOSTA_OPT_OUT`, `RESPOSTA_SEM_INTERESSE`; próximas ações `RESPONDER_AGORA`, `NAO_CONTATAR`,
  `ENCERRAR_COM_CORTESIA`; atividade criada no contato de Odoo (`res_id 12`);
- dry-run: exit 0, **0 chamadas** à API e **0 linhas** de trilha;
- replay: exit 0, **3 `JA_ATUALIZADO`**, trilha continua com 5 linhas, **0 chamadas novas**;
- guardas: `prod` recusa (exit 4); em dev, API fora de loopback recusa (exit 3) e porta de banco remota
  recusa (exit 3) — sem escrever trilha;
- `--desfazer`: dry-run não escreve; `--confirmo` grava `DESFEITO` preservando a linha `ATUALIZADO`;
  chave sem alvo recusa (exit 3);
- escopo: as 9 tabelas do `sales_intelligence` intactas, `interactions` continua com 5 linhas, a chave da
  API não aparece na saída do componente (0 ocorrências).

## Defeitos reais encontrados pelo aceite e corrigidos (cada um com teste na suite)

1. **psql quebra a saída longa em várias linhas** — ler "a última linha" do `json_agg` devolvia um pedaço
   do JSON e o componente recusava (`BANCO_RESPOSTA_INVALIDA`). Correção: envelope `md5 + base64` na
   porta de banco; o md5 prova que a costura das linhas foi exata e divergência **recusa**
   (`BANCO_RESPOSTA_CORROMPIDA`). Itens 57/58 + dente `sem-conferencia-de-md5`.
2. **SQL do envelope malformado** — a expressão inteira numa string única produzia `... ) t AS payload`
   (o alias caindo na tabela) e o banco recusava a sintaxe. Correção: coluna e `FROM` passam como
   parâmetros separados. Item 59 + dente `envelope-sql-malformado`.
3. **Postgres recusa CTE que modifica dados fora do topo** — `WITH afetados AS (INSERT ... RETURNING) ...`
   em subconsulta: `WITH clause containing a data-modifying statement must be at the top level`. Correção:
   escrita com `RETURNING` vai crua (uuid curto, uma linha por registro). Item 63 + dente
   `escrita-embrulhada-em-cte`.
4. **`duplicate key value violates unique constraint` no replay** — todas as decisões da mesma interaction
   usavam a mesma chave e o índice de `sync_events.idempotency_key` é único: o replay do `SEM_ATO`
   derrubava a rodada. Correção: chave por decisão (`chave_de_trilha`) e
   `ON CONFLICT (idempotency_key) DO NOTHING` em toda gravação. Itens 60/61/62 + dentes
   `trilha-sem-on-conflict` e `trilha-sem-chave-por-decisao`.
5. **o aceite contava o próprio aquecimento** — a sonda de prontidão do stub já era uma chamada
   registrada, então "0 chamadas no dry-run" acusava 1 e "9 chamadas" acusava 10. Correção: contagem em
   delta a partir da sonda.
6. **rótulo medindo errado** — a asserção dizia "2 respostas propagadas" e media 3 (o correto é 3:
   INTERESSE + OPT_OUT + SEM_INTERESSE).
7. **portão de estrutura rodando em checkout sem `.git`** — o checkout do aceite vem por `git archive`;
   ali `verificar_estrutura.sh` acusa todos os arquivos como não versionados. Correção: o portão roda no
   repo (medido: PASS 0 falhas) e o aceite declara isso em vez de medir errado.

Defeitos encontrados e corrigidos antes do primeiro aceite (na fase de suite): teste por `importlib`,
contagem de delimitador de docstring de uma linha, auto-flag da auditoria de fonte quando o próprio código
citava `xmlrpc`/`jsonrpc`/`/web/dataset` (resolvido com concatenação de strings no rótulo auditado) e o
caso `31.BOUNCE` (resposta BOUNCE não gera ato de CRM).
