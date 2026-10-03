# Runbook — atualizar o Odoo a partir das respostas (W6-E06-T01)

Componente: `hermes/agentes/respostas/atualizacao_odoo.py` (`atualizacao-odoo-respostas-v1`).
Contrato: `hermes/agentes/respostas/atualizacao-odoo-respostas-v1.json`.
Entrada: respostas já classificadas em `sales_intelligence.interactions` (card `TRE-W6-E05-T01`).
Saída: atos na API controlada do Odoo (card `TRE-W3-E01-T05`) + uma linha de trilha por interaction
em `sales_intelligence.sync_events`.

## 1. O que ele faz, em uma linha por categoria

| categoria da resposta | ato no CRM | próxima ação |
|---|---|---|
| `INTERESSE` | `RESPOSTA_INTERESSE` + atividade no contato | `RESPONDER_AGORA` |
| `OPT_OUT` | `RESPOSTA_OPT_OUT` + atividade | `NAO_CONTATAR` |
| `SEM_INTERESSE` | `RESPOSTA_SEM_INTERESSE` + atividade | `ENCERRAR_COM_CORTESIA` |
| `BOUNCE` | **nenhum** — `SEM_ATO` na trilha | — |

Categoria sem ato declarado no contrato: `SEM_ATO` (registrado, elegível a reprocesso).
Sem `odoo_lead_id` (ou sem contato quando a categoria exige): `SEM_VINCULO` — **o vínculo não se inventa**.

## 2. Variáveis (bloco DEV do `.env.example`)

```
TRE_AMBIENTE=dev                                   # dev | homolog | prod
TRE_ODOO_API_URL=http://127.0.0.1:8799             # em dev: LOOPBACK obrigatório
TRE_ODOO_API_KEY=<usuario de integracao>           # nunca em log/stdout/trilha
TRE_ODOO_RESPOSTAS_PORTA_BANCO="docker exec -i pg-e06-acc psql -U sales_ai -d sales_intelligence"
TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA=<id do tipo de atividade>
TRE_ODOO_USUARIO_ATIVIDADE=<id do usuario>         # opcional
TRE_ODOO_RESPOSTAS_CORRELACAO=<rotulo da rodada>   # opcional (vai no correlation_id)
TRE_ODOO_RESPOSTAS_LIMITE=50                       # opcional
TRE_ODOO_RESPOSTAS_TIMEOUT=15                      # opcional
TRE_ODOO_RESPOSTAS_APROVACAO_HUMANA=<registro>     # obrigatório em homolog
```

## 3. Operação

```bash
# 1) o que o contrato declara (sem rede e sem banco)
python3 hermes/agentes/respostas/atualizacao_odoo.py --regras
python3 hermes/agentes/respostas/atualizacao_odoo.py --conferir

# 2) o que ele FARIA (não escreve na API, não escreve trilha)
python3 hermes/agentes/respostas/atualizacao_odoo.py --propagar

# 3) rodada de verdade (escreve na API controlada e na trilha)
python3 hermes/agentes/respostas/atualizacao_odoo.py --propagar --confirmo --saida <dir>

# 4) desfazer um ato (marca DESFEITO, preserva a linha do ato)
python3 hermes/agentes/respostas/atualizacao_odoo.py --desfazer "odoo-resposta:<interaction_id>" --confirmo
```

Exit: `0` OK/dry-run/replay · `1` falha de execução (HTTP/banco) · `2` uso · `3` recusa de
guarda/contrato/fonte · `4` recusa de produção · `5` segredo vazado na gravação.

### Códigos de recusa que você pode ver

| motivo | o que significa | o que fazer |
|---|---|---|
| `CONFIG_INCOMPLETA` | falta `TRE_ODOO_API_URL` ou `TRE_ODOO_API_KEY` (o motivo nomeia a chave) | exportar a variável |
| `BANCO_NAO_DECLARADO` | em dev não há porta de banco declarada | usar `--porta-banco` ou a env |
| `BANCO_NAO_E_DEV` | a porta aponta para banco remoto | apontar para container local de dev/aceite |
| `HOST_NAO_E_DEV` | em dev a API não está em loopback | usar o stub/Odoo local |
| `PRODUCAO_RECUSADA` | `TRE_AMBIENTE=prod` | promover por decisão humana, com aprovação registrada |
| `DDL_NO_CODIGO` | a fonte do módulo tem UPDATE/DELETE/DDL | investigar: a trilha é append-only |
| `BANCO_RESPOSTA_CORROMPIDA` | o md5 do payload não confere | repetir a leitura; nada foi escrito |
| `API_INDISPONIVEL` / `API_RECUSOU` | a API controlada não respondeu ou recusou | ver a trilha `FALHA` e o `correlation_id` |
| `SENHA_VAZADA` | o valor da chave apareceu na gravação | gravação recusada; trocar a chave |

## 4. Como saber que rodou

```bash
docker exec -i pg-e06-acc psql -U sales_ai -d sales_intelligence -c \
  "select status, idempotency_key, entity_id, completed_at from sales_intelligence.sync_events order by completed_at;"
```

- `ATUALIZADO` → ato aplicado (chave `odoo-resposta:<interaction_id>`).
- `SEM_ATO` / `SEM_VINCULO` / `SEM_CONFIG_DE_ATIVIDADE` → decisão registrada com sufixo próprio na chave.
- `FALHA` → erro de API/banco, com `error_message`.
- `DESFEITO` → marca de desfazer (a linha `ATUALIZADO` continua lá: a trilha não se reescreve).

## 5. Idempotência (por que rodar duas vezes é seguro)

- A chave do ato é `odoo-resposta:<interaction_id>`; replay devolve `JA_ATUALIZADO`, não chama a API e
  não grava de novo.
- Cada decisão tem chave própria (`:sem_ato`, `:sem_vinculo`, `:sem_config`, `:falha`) porque
  `sync_events.idempotency_key` é **único**: a mesma interaction pode ter `SEM_VINCULO` hoje e
  `ATUALIZADO` amanhã.
- Toda gravação é `ON CONFLICT (idempotency_key) DO NOTHING`: replay de decisão já registrada não colide
  nem duplica.
- Na API, a escrita leva `idempotency_key` (por ato) e `correlation_id` (a rodada) no corpo.

## 6. Limites declarados (não faz, por desenho)

- **não marca supressão** (`OPT_OUT`) em campo do Odoo: a política da API v1.3.0 não declara campo de
  supressão — o evento e a atividade são registrados e a lacuna fica nomeada no contrato;
- **não envia e-mail** (isso é W6-E04) e **não decide nada humano** (W6-E03);
- **não escreve no `sales_intelligence`** além da trilha, e **não altera `interactions`**;
- **nada em produção sem decisão humana registrada** (ADR-005).

## 7. Aceite reproduzível

```bash
# em host com Docker (o aceite cria um Postgres descartável e um stub em loopback)
bash scripts/agentes/teste_atualizacao_odoo_aceite.sh /tmp/tre-e06t01-aceite
# offline, sem rede/banco/credencial
python3 scripts/agentes/verificar_atualizacao_odoo.py
python3 scripts/agentes/verificar_atualizacao_odoo.py --prova-de-dente
```
