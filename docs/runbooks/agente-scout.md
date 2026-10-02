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
# suíte offline + autoteste por mutação (roda em qualquer máquina e de qualquer diretório,
# sem banco: a raiz do repo é achada por marcador, não pela profundidade do arquivo)
python3 scripts/agentes/verificar_agente_scout.py --autoteste

# aceite E2E em container PostgreSQL descartável (na VPS)
bash scripts/agentes/teste_scout_aceite.sh
bash scripts/agentes/teste_scout_aceite.sh --prova-de-dente

# portão de estrutura do repositório
bash scripts/verificar_estrutura.sh
```

O aceite cria um container **novo** (`pg-scout-acc`) e se recusa a rodar se o nome já existir — ele
nunca mexe em `pg-sales-dev`, `pg-odoo-dev` ou em qualquer container que não seja dele. Ao terminar, o
container é removido (inclusive em falha). São **37 itens**: três rodadas de ingestão (criação, retry e
replay com a chave já reivindicada), carimbos e id casado das criadas, `prod` recusado sem escrita,
`--planejar` sem porta e o ciclo do `--desfazer`. `--prova-de-dente` roda o aceite sobre cópias mutadas
do agente (**3 mutações**) e, para **cada uma**, exige que o aceite reprove **o item esperado** daquela
mutação (não basta "o aceite falhou": mutação que só quebra a importação não conta como detectada) —
baseline verde antes e depois, contagem **medida** no veredito. Mutação não detectada, ou não aplicada
na âncora, é falha do aceite, não do agente. `--manter` preserva container e diretório de trabalho para
inspeção (o diretório e os relatórios são apagados por padrão).

> Nota de manutenção: o corpo do laço das mutações usa `docker exec -i`, que **consome** o stdin de
> quem o chamou. Por isso as mutações são lidas numa lista **antes** do laço e todo `docker exec` que não
> lê stdin leva `</dev/null`. Sem isso o laço morre na primeira iteração e o dente mente.

## 7. Problemas conhecidos

- **`PortaIndisponivel: porta psql falhou`** — SSH/Docker da VPS fora, ou prefixo errado. Nada foi
  escrito: o agente aborta antes de inserir e registra `agent_runs` com `status=FAILED` quando
  consegue falar com o banco.
- **`RECUSADO_AMBIENTE`** — `--ambiente` ausente, `prod` ou valor desconhecido. É o comportamento
  esperado (fail-closed), não defeito.
- **`FONTE_DESCONHECIDA`** — a fonte não está no vocabulário do agente (`agente-scout-v1.json`).
  Incluir uma fonte nova é mudança de contrato do agente, não ajuste local.
- **`GuardaDeEscritaViolada`** — alguma instrução tentou DDL ou tabela fora das 4 declaradas. É a
  guarda funcionando; investigue a alteração de código, não afrouxe a guarda. A guarda olha o **código
  SQL** (o conteúdo dos literais é ignorado), então nome de empresa com `Drop`/`Create`/`Alter` no meio
  não dispara — se disparar com a instrução legítima, o defeito está na guarda.
- **Veredito `ERRO` em candidata ambígua** — a escrita em `human_approvals` falhou (`fila humana nao
  registrada`): sem a linha, a ambiguidade **não** foi reportada, então o agente não afirma
  `REVISAO_IDENTIDADE`. Idem para a auditoria: se o `INSERT` em `agent_runs` falhar, o motivo sai como
  `AUDITORIA_NAO_REGISTRADA` e a execução **não** é reportada como concluída. Os dois são fail-closed,
  não defeito.
- **`sync_events` preso em `PENDING`** — o fechamento foi colocado dentro da CTE de escrita: as CTEs e a
  instrução principal rodam no **mesmo snapshot**, então ela não enxerga a linha que acabou de inserir.
  O fechamento tem de ser uma instrução própria (ver `sql_ingerir`); a suíte offline reprova a regressão.
- **Saída da porta com carimbos** — o `psql` imprime `BEGIN`/`COMMIT` junto do resultado. Confira o
  resultado **linha a linha** (é o que o agente faz com a marca `SCOUT_CRIADA`); comparar a saída inteira
  com uma string única faz uma criação legítima parecer replay.
