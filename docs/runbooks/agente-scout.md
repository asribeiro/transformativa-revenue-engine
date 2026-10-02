# Runbook — Agente Scout (TRE-W4-E01-T01)

Contrato do agente: `docs/architecture/agente-scout-v1.md` · contrato legível por máquina:
`hermes/agents/scout/agente-scout-v1.json` · código: `hermes/agents/scout/scout.py`.

## 1. Onde roda

O PostgreSQL do TRE vive na **VPS** do ambiente (ADR-0008) e o container do Hermes **não tem rota**
até ele. Logo: o SQL sai pelo **prefixo psql** que aponta para o container do ambiente na VPS.

```bash
# na VPS (ou por SSH, do container do Hermes)
cd /opt/tre/repo
PREFIXO="docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

`--prefixo` é o **único** caminho de escrita do agente. Ele é validado linha a linha pela guarda
`validar_sql` antes de sair (só as 4 tabelas declaradas, sem DDL).

## 2. Rodada de descoberta

```bash
# 1) planejar (NÃO abre conexão; serve para ver o que faria)
python3 hermes/agents/scout/scout.py --planejar --fonte /caminho/candidatas.jsonl

# 2) ingerir no dev
python3 hermes/agents/scout/scout.py --ambiente dev \
  --fonte /caminho/candidatas.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/scout-$(date -u +%Y%m%dT%H%M%SZ).json
```

Saída: um JSON de resumo (agente, ambiente, `correlation_id`, identidade do alvo, contagem por
veredito) seguido de uma linha por candidata. **Guarde o `correlation_id`**: é o que permite desfazer
a rodada.

Vereditos: `CRIADA` · `JA_EXISTE` · `REVISAO_IDENTIDADE` · `RECUSADA` · `ERRO`.
Exit codes: `0` OK · `1` houve `ERRO` · `2` uso incorreto · `4` ambiente recusado · `5` fonte ilegível.

## 3. Depois da rodada — o que conferir

```bash
PSQL='docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence -tA'

# criadas na rodada
$PSQL -c "SELECT count(*) FROM sales_intelligence.organizations WHERE status='DISCOVERED';"

# fila humana aberta pelo Scout (identidade ambígua ou sem identificador forte)
$PSQL -c "SELECT id, proposed_action->>'motivo' FROM sales_intelligence.human_approvals \
          WHERE action_type='SCOUT_IDENTITY_REVIEW' AND status='PENDING';"

# auditoria da rodada
$PSQL -c "SELECT status, count(*) FROM sales_intelligence.agent_runs \
          WHERE agent_name='scout' AND correlation_id='<correlation_id>' GROUP BY status;"
```

**Retry é seguro:** rodar a mesma fonte de novo não cria duplicata (a chave de idempotência é a
identidade, `sync_events.idempotency_key` é UNIQUE). As candidatas voltam como `JA_EXISTE` com o
motivo `IDEMPOTENCIA_REPLAY`.

## 4. Desfazer uma rodada

```bash
# dry-run (padrão): mostra o que seria apagado, sem apagar
python3 hermes/agents/scout/scout.py --desfazer <correlation_id> --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# aplica
python3 hermes/agents/scout/scout.py --desfazer <correlation_id> --ambiente dev --confirmo \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

O desfazer apaga **somente** as organizações criadas por aquela correlação (`veredito=Criada` em
`agent_runs`) e ainda com `status='DISCOVERED'`, mais os `sync_events` dessas identidades; registra um
`sync_events` de `ROLLBACK`. Não toca organização pré-existente, `agent_runs` nem `human_approvals`.

## 5. Homologação e produção

- `--ambiente prod` é **recusado** (exit 4). Promover o Scout a produção é card próprio, com aprovação
  humana registrada (`docs/operations/registro-de-aprovacoes.md`) e a sequência dev → homologação →
  produção do ADR-005.
- A fila `SCOUT_IDENTITY_REVIEW` é decisão humana: revisar a candidata e as organizações casadas em
  `human_approvals.proposed_action`. O Scout **não** decide sozinho.

## 6. Verificação (antes de dizer que está certo)

```bash
# suíte offline + autoteste por mutação (roda em qualquer máquina, sem banco)
python3 scripts/agentes/verificar_agente_scout.py --autoteste

# aceite E2E em container PostgreSQL descartável (na VPS)
bash scripts/agentes/teste_scout_aceite.sh
bash scripts/agentes/teste_scout_aceite.sh --prova-de-dente

# portão de estrutura do repositório
bash scripts/verificar_estrutura.sh
```

O aceite cria um container **novo** (`pg-scout-acc`) e se recusa a rodar se o nome já existir — ele
nunca mexe em `pg-sales-dev`, `pg-odoo-dev` ou em qualquer container que não seja dele. Ao terminar, o
container é removido (inclusive em falha).

## 7. Problemas conhecidos

- **`PortaIndisponivel: porta psql falhou`** — SSH/Docker da VPS fora, ou prefixo errado. Nada foi
  escrito: o agente aborta antes de inserir e registra `agent_runs` com `status=FAILED` quando
  consegue falar com o banco.
- **`RECUSADO_AMBIENTE`** — `--ambiente` ausente, `prod` ou valor desconhecido. É o comportamento
  esperado (fail-closed), não defeito.
- **`FONTE_DESCONHECIDA`** — a fonte não está no vocabulário do agente (`agente-scout-v1.json`).
  Incluir uma fonte nova é mudança de contrato do agente, não ajuste local.
- **`GuardaDeEscritaViolada`** — alguma instrução tentou DDL ou tabela fora das 4 declaradas. É a
  guarda funcionando; investigue a alteração de código, não afrouxe a guarda.
