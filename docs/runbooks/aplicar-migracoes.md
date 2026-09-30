# Runbook — aplicar as migrations do Data Contract nos ambientes

**Card que criou isto:** `TRE-W1-E01-T01` (W1 · E01) · **Ambiente aplicado até agora:** `dev`.
**Regra que manda:** ADR-005 — nenhuma DDL nasce em produção. Ordem obrigatória: **dev → homologação → produção**,
esta última só com aprovação humana registrada (`docs/operations/registro-de-aprovacoes.md`).

## 1. Onde roda

O runner roda **na VPS do ambiente** (ADR-0008): o Hermes não tem rota de rede até o banco do TRE nem
cliente `psql`; ele orquestra por SSH e o trabalho acontece via `docker exec` no container do ambiente.

```bash
# da VPS do TRE (usuário tre-deploy)
bash /opt/tre/repo/scripts/db/aplicar_migracoes.sh dev
```

O par **ambiente → container/usuário/banco** é versionado e não-secreto em
`deploy/environments/<ambiente>.env`. Variável de ambiente do operador **vence** o arquivo versionado:

```bash
TRE_PG_SERVICO=outro-container TRE_PG_USER=outro TRE_PG_DB=outro_banco \
  bash scripts/db/aplicar_migracoes.sh dev
```

## 2. O que o runner faz

1. Lista `db/migrations/*.sql` em ordem lexicográfica e recusa arquivo sem prefixo de versão de 4 dígitos.
2. Confere que o container existe e que o PostgreSQL responde **duas vezes** (`SELECT 1` com intervalo) —
   a imagem oficial sobe um servidor temporário durante a inicialização e `pg_isready` mente nesse momento.
3. Cria/atualiza a tabela de controle `public.tre_schema_migrations`
   (`versao`, `arquivo`, `sha256`, `aplicada_em`, `aplicada_por`) — controle operacional, **fora** do schema
   do contrato.
4. Para cada migration:
   - já aplicada com o **mesmo sha256** → `PULADO` (idempotente);
   - já aplicada com **sha256 diferente** → `FALHOU` e para: migration aplicada é imutável (`BRANCHING.md`);
   - pendente → aplica (`docker cp` + `psql -f`, sem depender de stdin) e registra.
5. Fecha com `RESULTADO: MIGRACAO_OK …` (exit 0) ou `RESULTADO: MIGRACAO_FALHOU …` (exit 1).

`--somente-checar` mostra o que seria aplicado sem executar DDL.

## 3. Guardrail de produção (fail-closed)

`aplicar_migracoes.sh prod` **recusa** por padrão. Só passa com as três condições, verificadas antes de
qualquer comando no ambiente:

1. `TRE_APROVACAO_HUMANA=<arquivo do registro de aprovacao>` existente e não-vazio;
2. container de homologação (`pg-homolog`, ou `TRE_PG_SERVICO_HOMOLOG`) existente;
3. **toda** versão pendente já registrada no ambiente de homologação (sequência imposta pelo script,
   não por combinado verbal).

Sem isso: mensagem explícita e `RESULTADO: MIGRACAO_FALHOU`, com produção intocada.

## 4. Como saber como está o ambiente (read-only)

```bash
bash scripts/db/estado_do_ambiente.sh dev
```

Imprime: contagem de tabelas e índices do schema `sales_intelligence`, o `psql \dt`
(`search_path=sales_intelligence`) e o conteúdo de `public.tre_schema_migrations`.

## 5. Rollback

Dev (o plano do card `TRE-W1-E01-T01`, já exercitado de verdade):

```bash
docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA IF EXISTS sales_intelligence CASCADE' \
  -c "DELETE FROM public.tre_schema_migrations WHERE versao='0001'"
bash /opt/tre/repo/scripts/db/aplicar_migracoes.sh dev    # reaplica do zero
```

Ambientes superiores não são tocados por esse rollback. Rollback de backup/restore está em
`docs/runbooks/backup-restore-rollback.md`.

## 6. Evidência de aceite (TRE-W1-E01-T01)

- `scripts/db/estado_do_ambiente.sh dev` → **12 tabelas | 30 índices** e o `\dt` com as 12 tabelas;
- `python3 scripts/verificar_contrato_dados.py --banco 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'`
  → **PASS (37 itens)** — 26 itens dos artefatos do repo + 11 itens contra o **banco do ambiente**;
- `aplicar_migracoes.sh prod` → recusa por ADR-005 (exit 1) e produção sem nenhum arquivo/container.

## 7. Divergência aberta (não resolvida por este runbook)

O Data Contract V1.0 declara o banco de inteligência como `transformativa_ai`; o ambiente **dev** foi
provisionado com banco `sales_intelligence` (container `pg-sales-dev`, usuário `sales_ai`), e os scripts de
backup já assumem `sales_intelligence` como padrão. O runner aplica no par que está em
`deploy/environments/dev.env`. Alinhar nome (contrato ou ambiente) é decisão do dono — registrada no card.

## Nomenclatura de banco e schema (decidido por Anderson, 30/09/2026)

- **Nome de exibição:** *Sales Intelligence* — em documentação, telas e conversa.
- **Identificador técnico:** `sales_intelligence`, em **minúsculo e sem aspas**. Decisão: em
  Postgres, identificador sem aspas é achatado para minúsculo, então `Sales_Intelligence` sem
  aspas **é** `sales_intelligence`; criar com aspas (`"Sales_Intelligence"`) obrigaria toda query,
  conexão, DSN, backup e script futuros a carregar aspas, e quem esquecesse receberia
  `relation does not exist`. O custo é permanente, a fidelidade é só visual.
- **Divergência conhecida e a reconciliar:** a migration 0001 registra que, nos ambientes de
  operação, a base é `transformativa_ai` com schema `sales_intelligence`, enquanto no ambiente de
  desenvolvimento o banco se chama `sales_intelligence`. Não é defeito desta entrega; é
  nomenclatura a unificar antes de promover qualquer ambiente (card próprio).

## 8. Constraints e índices conferidos item a item (TRE-W1-E03-T01)

O modo `--banco` do `verificar_contrato_dados.py` confere o schema do ambiente e **conta** os índices (30).
Para a prova **item a item** de constraints e índices existe um verificador dedicado, também read-only:

```bash
# na VPS do ambiente (ADR-0008)
python3 scripts/db/verificar_constraints_indices.py --banco 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'
python3 scripts/db/verificar_constraints_indices.py --esperado   # imprime o esperado, sem tocar banco
```

- O esperado **não é lista escrita à mão**: sai da própria migration (PK inline, UNIQUE inline, `REFERENCES`
  e os `CREATE INDEX`) e do `docs/data/data_contract_v1.json` (os 16 itens de índice do contrato). Mudou a
  migration, mudou o esperado — o verificador não envelhece nem vira carimbo.
- Conta: **30 índices** = 12 PK (`<tabela>_pkey`) + 15 `CREATE INDEX` nomeados + 3 UNIQUE declaradas inline
  (`organizations.odoo_partner_id`, `contacts.odoo_partner_id`, `sync_events.idempotency_key`).
- Compara o conjunto **exato** (nome, tabela, unicidade e colunas com direção `DESC`): índice faltando **e**
  índice sobrando reprovam. E compara item a item: PK (uma por tabela, coluna `id`), FK (par
  tabela.coluna → tabela.coluna de destino) e UNIQUE (tabela + colunas) — além de provar que os vínculos
  lógicos declarados (`signals.research_run_id`, `recommendations.opportunity_id`, `interactions.campaign_id`)
  continuam **sem** FK.
- Só leitura (`pg_indexes`/`pg_constraint`); sem `--banco` o script sai 2: não existe veredito sem alvo.

A prova de que o verificador tem dente roda em **container descartável** (não toca ambiente nenhum):

```bash
bash scripts/db/teste-constraints-indices.sh
```

Ele sobe um `postgres:16`, aplica a migration congelada, confirma `PASS` no alvo íntegro, aplica sete
mutações — índice removido, índice renomeado, mesmo nome com coluna errada, FK removida, UNIQUE removida, PK
removida, índice a mais — exige **reprovação apontando o motivo** em cada uma, desfaz a mutação e reverifica.

### 8.1 Evidência medida — 30/09/2026 (dev, VPS Contabo `vmi3619453`)

- `bash scripts/db/estado_do_ambiente.sh dev` → `12 tabelas | 30 índices`;
- `python3 scripts/db/verificar_constraints_indices.py --banco 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'`
  → `RESULTADO: PASS (16 itens, 0 falhas)`, exit 0: `pg_indexes` com os 30 índices (12 PK + 15 nomeados + 3
  UNIQUE) e `pg_constraint` com 12 PK, 10 FK e 3 UNIQUE, iguais ao contrato item a item;
- `python3 scripts/verificar_contrato_dados.py --banco '…'` → `PASS (37 itens, 0 falhas)`, exit 0;
- `bash scripts/db/teste-constraints-indices.sh` → `TESTE_OK (18 itens, 0 falhas)`, exit 0 (sete mutações
  reprovadas pelo motivo certo, todas reversíveis);
- produção e homologação intocadas: `aplicar_migracoes.sh prod` recusa por ADR-005 (exit 1), `docker ps -a`
  só com `pg-sales-dev` e `/opt/tre/{prod,homolog}` sem arquivo.
