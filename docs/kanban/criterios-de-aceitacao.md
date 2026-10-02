> **Homologado por Anderson em 29/09/2026** (Telegram). Os critérios abaixo valem como régua de aceite;
> card cujo critério não bater não fecha. Documento antes chamado `criterios-de-aceitacao-propostos.md`.

# Critérios de aceitação propostos (fila imediata)

Escritos por mim no papel de **Analista de Requisitos** do fluxo (`docs/architecture/fluxo-de-desenvolvimento-e-perfis.md`). **Não valem até o Anderson homologar** — e quem rotula/valida é ele.

Cards: bloco JEV (W0-E04) + W1 (PostgreSQL) + W2 (Odoo). O resto do board segue com o mesmo padrão.

## TRE-W0-E04-T02 — Integrar JEV ao Hermes Dev Harness

- O roteador lê `hermes/jev/policy_v1.yaml` e recusa iniciar com versão de política desconhecida.
- Para um conjunto de tarefas de exemplo, a lane atribuída (small/medium/high/critical) bate com a política e fica registrada no recibo.
- Confiança < 0,65 gera abstenção e escalação — nunca execução.
- Nenhuma das 8 ações 'nunca decidido por máquina' passa: teste com uma delas (primeiro contato) termina em bloqueio/escalação.
- O recibo grava os 13 campos previstos, sem segredo.
- Teste negativo: política ausente ou ilegível → modo degradado (lane high), nunca execução silenciosa.

**Test plan:** Suíte automatizada no repo rodando contra o roteador + verbete de recibo de exemplo; evidência = saída do teste e recibo versionado.
**Rollback:** Reverter o commit. O fluxo manual pelo board continua funcionando sem o roteador.
**Risco:** Médio — mexe no encadeamento de execução, não em dado.

## TRE-W0-E04-T03 — Criar benchmark anotado de routing

- Corpus com casos anotados por lane, com a anotação final homologada pelo Anderson (quem rotula é ele, não a máquina).
- Métricas calculadas e versionadas: accuracy, false downgrade (crítico tratado como lane barata), escalation rate, custo e latência.
- Nenhum caso rotulado por mim entra sem revisão dele; caso não revisado fica marcado como pendente, não como válido.
- Resultado gravado em `hermes/jev/benchmarks/` com data e versão da política usada.

**Test plan:** Script de benchmark reprodutível: roda o roteador sobre o corpus e recalcula as métricas; evidência = saída do script + arquivo de resultado.
**Rollback:** Remover o corpus e o resultado; nada em produção depende dele.
**Risco:** Baixo — é medição, não mudança de comportamento.

## TRE-W0-E04-T04 — Validar JEV guardrails e fallback

- Cada guardrail tem teste que reprova o caminho proibido e aprova o permitido: segredo em prompt/log, contato com `do_not_contact`, DDL em produção, Sales AI com credencial de deploy, e fail-closed.
- Fallback exercitado: JEV indisponível → lane conservadora (high) + `degraded_mode` no recibo + Human Approval nunca contornado.
- Cada uma das 8 ações proibidas tem teste de bloqueio.
- Evidência = relatório OK/FALHOU item a item, não narrativa.

**Test plan:** Verificador com saída OK/FALHOU por item, no mesmo padrão dos verificadores existentes do repo.
**Rollback:** Reverter o commit do verificador; a política permanece válida.
**Risco:** Baixo — validação, sem efeito em dado.

## TRE-W1-E01-T01 — Criar database/schema Sales Intelligence

- Schema `sales_intelligence` criado no ambiente **dev** por migration versionada no repo.
- `psql \dt` lista as 12 tabelas do Data Contract V1.0.
- `verificar_contrato_dados.py` passa contra o banco do ambiente (não só contra a migration em container descartável).
- Nenhuma DDL aplicada em produção (ADR-005).

**Test plan:** Aplicar a migration em dev, listar tabelas e rodar o verificador do contrato; evidência = saída dos três comandos.
**Rollback:** Dropar o schema em dev e reaplicar; ambientes superiores intocados.
**Risco:** Médio — primeira aplicação real do contrato.

## TRE-W1-E02-T01 — Criar tabelas core

- As 12 tabelas existem com colunas, PK, FK e NOT NULL conforme o contrato.
- `db/fixtures/smoke_dev.sql` carrega sem erro em dev.
- Contagem por tabela confere com o esperado do fixture.

**Test plan:** Aplicar fixture e comparar contagens tabela a tabela; evidência = saída + contagens.
**Rollback:** Rollback da migration em dev.
**Risco:** Baixo.

## TRE-W1-E03-T01 — Criar constraints e índices

- Os 30 índices do contrato existem e aparecem no `pg_indexes`.
- Constraints (PK/FK/unique) conferem com o contrato, item a item.
- `verificar_contrato_dados.py` passa contra o banco do ambiente.

**Test plan:** Consulta a `pg_indexes`/`information_schema` comparada com o contrato; evidência = listagem + verificador.
**Rollback:** Rollback da migration.
**Risco:** Baixo.

## TRE-W1-E04-T01 — Implementar deduplicação strong identifiers

- Mesmo CNPJ, mesmo domínio ou mesmo LinkedIn ⇒ duplicidade detectada.
- Merge só com similaridade ≥ 0,95: teste com 0,94 mantém separado e sinaliza; com 0,95 faz merge.
- Nenhum merge silencioso: todo merge deixa registro auditável.
- Limiar não é ajustável por código de produção sem mudar o contrato/política.

**Test plan:** Casos sintéticos cobrindo o limite 0,94/0,95 e cada identificador forte, isoladamente e em conjunto.
**Rollback:** Reverter código; dados já mesclados exigem desfazer com registro — por isso o merge é auditável.
**Risco:** Alto — mexe em identidade de dado.
**Componentes afetados:** `scripts/dedup/` (motor + teste sintético + teste de ambiente, novos);
`sales_intelligence.organizations` (identidade e soft delete) e as tabelas filhas que a referenciam
(reapontamento no merge); `sales_intelligence.sync_events` (trilha auditável do merge);
`sales_intelligence.human_approvals` (fila `REVIEW_REQUIRED`); `docs/data/data_contract_v1.json`
(limiar lido, não editado); `scripts/verificar_estrutura.sh` (artefatos versionados).

**Decisões de implementação registradas (não mudam o contrato):**
D1 o limiar de merge é lido do contrato a cada chamada — não há parâmetro, constante ajustável nem
variável de ambiente que o mude (mudar exige mudar o contrato/política); D2 evidência fraca nunca alcança
a faixa de merge (teto `limiar − 0,01` = 0,94) e vai para a fila humana; D3 CNPJ igual porém inválido
(dígito verificador) detecta e vai para revisão, nunca mergeia automático; D4 `organizations` não tem
telefone nem endereço na V1 — fracos "nome + telefone"/"nome + endereço" são lacuna declarada; D5
auditoria em `sync_events` e fila em `human_approvals` (nenhuma coluna/tabela nova — exigiria nova versão
do contrato); D6 `--desfazer-merge` reverte o merge com registro `UNMERGE` (rollback executável).

## TRE-W1-E04-T02 — Implementar entity_match_confidence

- O campo `entity_match_confidence` é calculado e persistido.
- Faixas de confiança documentadas e coerentes com o limiar de merge do E04-T01.
- Teste de faixa: 0,94 não mescla; 0,95 mescla.

**Test plan:** Teste unitário por faixa + registro persistido conferido.
**Rollback:** Reverter o cálculo; coluna fica nula em vez de mentir valor.
**Risco:** Médio.

**Componentes afetados:** `scripts/dedup/deduplicar_organizacoes.py` (modelo de faixas, score canônico e
persistência do campo no registro auditado); `scripts/dedup/teste_entity_match_confidence.sh` (novo, prova
em um comando); `docs/data/entity-match-confidence.md` (novo, o modelo e as faixas documentadas);
`docs/runbooks/deduplicacao-strong-identifiers.md` (limite declarado que este card fecha);
`scripts/verificar_estrutura.sh` (artefatos versionados); `CHANGELOG.md`;
`docs/operations/registro-de-execucoes.md`; tabelas existentes **sem mudança de schema**:
`sales_intelligence.sync_events` (merge) e `sales_intelligence.human_approvals` (fila humana).

**Decisões de implementação registradas (não mudam o contrato):** D-T02-1 nome canônico
`entity_match_confidence` com `confianca` mantido como alias de mesmo valor no mesmo registro (compatibilidade
com o E04-T01); D-T02-2 a decisão do par passou a ser a decisão da faixa do score (3 estados — `MERGE` /
`REVIEW_REQUIRED` / `SEM_DUPLICIDADE`; o "abaixo do limiar → REVIEW_REQUIRED" do contrato vale para candidato
a deduplicação); D-T02-3 persistência no registro auditado das decisões, **nenhuma coluna nova** (coluna é
gatilho de nova versão do contrato + aprovação humana; o rollback proposto "coluna fica nula" não se aplica
porque não existe coluna e a trilha de auditoria é imutável por contrato §9); D-T02-4 sem evidência
qualificada o score é 0,00 e não a similaridade bruta de nome (que segue auditável em `evidencias.fracos`);
D-T02-5 o registro leva a tabela de faixas vigente e a versão do modelo. Detalhe em
`docs/data/entity-match-confidence.md` §6.

**Defeito medido na revisão independente (D02) e corrigido:** a linha de detalhe do `--faixas` tinha os nomes
das faixas fixos no código e se contradizia com a tabela quando o limiar do contrato não era 0,95 (reproduzido
pela revisão independente em cópia com `auto_merge_threshold=0.90`, exit 0, e reconfirmado aqui na correção); o teste do projeto asseria a string
constante como evidência do critério. Correção: `linha_detalhe_faixas()` derivada de `faixa_de_confianca()`;
a verificação compara com o modelo (não com constante) e exige que tabela e detalhe se movam juntos numa cópia
com o limiar em 0,90; a suíte ganhou a sabotagem `detalhe`. Registrado como **D-T02-6** em
`docs/data/entity-match-confidence.md` §6 e no `CHANGELOG.md` (Fixed).

## TRE-W1-E05-T01 — Criar database test suite

- Suíte roda com um comando único e falha (exit ≠ 0) se o schema divergir do contrato.
- Teste de tenant/RLS: consulta sem filtro de tenant devolve vazio ou erro — **nunca** dado de outro cliente.

> **NOTA DATADA (30/09/2026) — o 2º critério acima foi REFORMULADO pelo dono; a forma vigente é a desta nota.**
> Decisão do dono (opção A — isolamento **físico**, um banco por cliente), card `t_e340c29b`, registrada em
> `docs/operations/registro-de-aprovacoes.md` e em `docs/data/DATA_CONTRACT_V1.md`:
>
> **forma vigente: "não existem dois clientes no mesmo banco"** (medida por
> `scripts/db/teste_isolamento_clientes.sh`, etapa 5 da suíte) — 0 dimensão de cliente no schema, 1 base de
> aplicação na instância do alvo, 1 base provisionada (`pg-*`) servindo o schema no host.
>
> A forma antiga ("consulta sem filtro de tenant devolve vazio ou erro") **não é decidível** contra o Data
> Contract V1.0 (0 coluna de cliente, RLS desligada nas 12 tabelas, 0 policy, papel `sales_ai`
> superuser+bypassrls): não há o que filtrar — foi medido (`NAO_TESTAVEL`, exit 3) e devolvido ao
> requisito, que decidiu pela forma nova. Tenant/RLS como dimensão de primeira classe fica para o **V2**,
> com o instrumento já versionado (`scripts/db/teste_tenant_rls.sh`, fora da suíte).

- Exit code é confiável: suíte verde = exit 0 com os testes efetivamente executados (não 'sem output').

**Test plan:** Executar a suíte em dev e em homolog; evidência = saída completa + exit code.
**Rollback:** Reverter o commit da suíte.
**Risco:** Médio — é a prova de isolamento entre clientes.

## TRE-W1-E06-T01 — Testar backup/restore

- `teste-backup-restore.sh` roda contra o banco do **ambiente dev** (não container descartável).
- Restauração reproduz as contagens tabela a tabela.
- Artefato sobe ao bucket e o manifesto registra `externo: enviado`.
- Teste negativo (dump truncado) é reprovado.

**Test plan:** Ciclo completo de backup → restore → comparação + teste negativo; evidência = TESTE_OK com itens e falhas.
**Rollback:** Restaurar do último artefato válido (runbook de backup/restore).
**Risco:** Médio.

## TRE-W2-E01-T01 — Instalar Odoo Community

- Odoo Community instalado no ambiente **dev** do TRE (VPS Contabo), com compose versionado no repo.
- A versão escolhida está registrada no runbook e no `compose` (reprodutível, não 'a última de hoje').
- Serviço sobe e responde; banco do Odoo é separado do `sales_intelligence`.
- Nenhuma porta exposta publicamente (UFW) — exposição só depois do card de TLS/proxy.
- **Decisão do Anderson:** versão do Odoo, portas e onde hospedar antes de aplicar.

**Test plan:** `docker compose config` válido + subida do serviço + resposta HTTP + conferência de portas no UFW.
**Rollback:** Derrubar os containers e remover volumes do ambiente dev; nada em homolog/produção.
**Risco:** Alto — serviço novo em ambiente do TRE.

## TRE-W2-E01-T02 — Configurar TLS/reverse proxy/security

- Acesso por HTTPS com certificado válido (sem bypass de aviso).
- Porta administrativa não exposta publicamente.
- UFW com regras mínimas documentadas; teste de acesso externo nega o que não deve ser exposto.
- **Decisão do Anderson:** domínio, portas e exposição.

**Test plan:** Chamada externa HTTPS + varredura de portas abertas + `ufw status`; evidência = saída dos três.
**Rollback:** Reverter regras de proxy/UFW para o estado anterior.
**Risco:** Alto — exposição de serviço.

## TRE-W2-E02-T01 — Configurar CRM básico

- Pipeline e etapas do CRM configurados conforme o processo comercial da Transformativa, validado pelo Anderson.
- Nenhuma etapa ou regra inventada por mim sem aprovação dele.

**Test plan:** Print/consulta das etapas configuradas + validação explícita do Anderson.
**Rollback:** Reconfigurar as etapas para o padrão anterior.
**Risco:** Médio.

## TRE-W2-E03-T01 — Criar módulo transformativa_sales_ai

- Módulo `transformativa_sales_ai` instala e desinstala limpo em banco limpo (idempotente).
- Manifesto e versão corretos; dependências declaradas.
- `--test-enable` do Odoo sem erro no módulo.

**Test plan:** Instalação em banco limpo, teste do Odoo, desinstalação e reinstalação; evidência = log dos quatro passos.
**Rollback:** Desinstalar o módulo.
**Risco:** Médio.

## TRE-W2-E04-T01 — Customizar res.partner

- Campos de dedup (CNPJ, domínio, LinkedIn) presentes e indexados em `res.partner`.
- IDs canônicos seguem o Data Contract V1.0.
- Teste de criação e consulta com dado sintético.

**Test plan:** Teste do Odoo criando/consultando parceiro com identificadores fortes.
**Rollback:** Desinstalar/campo removido pelo módulo.
**Risco:** Médio.

## TRE-W2-E04-T02 — Customizar crm.lead

- Campos previstos no contrato presentes em `crm.lead`.
- CRM padrão não quebra: criação/consulta de lead funcionam.
- Teste de criação e consulta com dado sintético.

**Test plan:** Teste do Odoo criando lead com os campos novos.
**Rollback:** Reverter pelo módulo.
**Risco:** Médio.

## TRE-W2-E05-T01 — Criar tf.process.opportunity

- Modelo `tf.process.opportunity` criado com os campos do contrato.
- A oportunidade canônica vive no Odoo (regra do contrato), com vínculo a `res.partner`.
- Teste de criação, consulta e relação com parceiro.

**Test plan:** Teste do Odoo + consulta da relação.
**Rollback:** Desinstalar o modelo pelo módulo.
**Risco:** Médio.

## TRE-W2-E06-T01 — Criar views Sales AI

- Views exibem os campos das entidades customizadas, sem erro de renderização.
- Acesso respeita o perfil de usuário (quem não pode ver, não vê).
- Evidência de abertura das views (lista/formulário).

**Test plan:** Abrir cada view com usuário de teste e com usuário restrito.
**Rollback:** Reverter pelo módulo.
**Risco:** Baixo.

## TRE-W2-E07-T01 — Configurar ACLs/security

- Regras de acesso por carteira/tenant aplicadas.
- Teste negativo: usuário de um tenant **não** vê dado de outro — vazio ou erro, nunca material alheio.
- Aprovação humana jamais concedida por máquina.

**Test plan:** Teste negativo por tenant + conferência das regras do módulo.
**Rollback:** Reverter as ACLs pelo módulo.
**Risco:** Alto — isolamento entre clientes.

## TRE-W5-E08-T01 — Test scoring/NBA (aceite E2E da cadeia W5)

- A cadeia **ICP → AUTOMATION_FIT → BUYING_SIGNAL → DATA_QUALITY → PRIORITY → TIER → NBA** roda em
  sequência no **mesmo** banco, e o artefato de cada etapa é o **insumo** da seguinte: o `PRIORITY` é
  a fórmula do contrato sobre os **quatro scores gravados**; o registro `TIER` cita o `score_id` do
  `PRIORITY` lido; a recomendação cita o `tier` gravado pelo tiering.
- **Fail-closed na cadeia**: empresa sem lastro (sem sinal e sem hipótese) não ganha score, tier nem
  recomendação — cada etapa do caminho curto **RECUSA** com motivo nominal (`SEM_LASTRO`,
  `SEM_LASTRO_COMPLETO`, `SEM_PRIORITY`, `SEM_TIER`) e **nada** é gravado.
- **Escopo**: as tabelas de negócio saem idênticas (foto md5 antes/depois), a `outbox` fica 0 e
  nenhuma rodada chama LLM (`model`/tokens/custo NULL).
- **Replay** da cadeia com as mesmas entradas **não duplica** linha nenhuma.
- `prod` é recusado (**exit 4**) sem escrita nos sete componentes; `--planejar`/`--regras` exit 0 sem
  conexão; `--desfazer` é dry-run até o `--confirmo`.
- Evidência OK/FALHOU item a item com exit code e prova de dente (cada mutação reprova o item
  esperado); nada em produção (ADR-005).

**Test plan:** `bash scripts/e2e/verificar-e2e-scoring-nba.sh` na VPS do ambiente (container
descartável `pg-w5-acc`, migration 0001, 3 empresas sintéticas: duas com lastro completo e uma sem) +
`--prova-de-dente` (4 mutações) + as sete suítes offline no mesmo commit; evidência = saída completa
com exit code. Runbook: `docs/runbooks/e2e-scoring-nba.md`.
**Rollback:** Reverter o commit do aceite (sem DDL, sem schema novo; nada em produção). O container
descartável é removido pelo próprio aceite e o rollback operacional dos dados é o `--desfazer` de cada
componente.
**Risco:** Baixo-médio — é medição. O risco real é o instrumento (verde falso sobre cadeia quebrada),
endereçado pelos itens de composição e pelos dentes.

## TRE-W6-E02-T01 — Criar GPT outreach generator (`gerador-abordagem-v1`)

**Acceptance:** a evidência lida (recomendação `NEXT_BEST_ACTION` OPEN + organização + contato + pesquisa +
dores + sinais + PRIORITY + registro TIER) vira uma abordagem validada e um **pedido de aprovação humana**
`PENDING` em `human_approvals` (`action_type` = ação recomendada, `proposed_action` com canal/tipo/assunto/
corpo/cta/citações/hash/recomendação), auditado em `agent_runs` com provider/modelo/`prompt_version`/
`entrada_hash` **estruturados**; nada é enviado; nada fora dessas duas tabelas é tocado; replay não duplica;
evidência citável nova gera pedido novo com o anterior `EXPIRED`; contato bloqueado, ausência de contato,
ausência de evidência, empresa inexistente e abordagem inválida **recusam sem gravar**; `prod` recusado (exit 4).
**Test plan:** `python3 scripts/agentes/verificar_gerador_abordagem.py` (100 OK / 0) + `--autoteste` (12/12) +
`bash scripts/agentes/teste_gerador_abordagem_aceite.sh` na VPS do ambiente (PostgreSQL descartável
`pg-outreach-acc`, migration 0001, 8 empresas sintéticas, stub HTTP local do provedor) + `--prova-de-dente`
(4 mutações, cada uma reprovando o item esperado). Evidência = saída completa com exit code e o veredito
`ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)`. Runbook: `docs/runbooks/gerador-abordagem.md`.
**Rollback:** `--desfazer <correlation_id> [--confirmo]` apaga só os pedidos de aprovação da rodada,
preservando a auditoria; reverter o merge do branch (sem DDL, sem migration, sem estado externo criado —
nenhum e-mail enviado, nenhuma atividade no Odoo, nenhum evento de outbox).
**Risco:** médio — é o primeiro componente que chama modelo. Endereçado por: validação determinística de fato
sustentado com citação obrigatória, lista declarada de afirmações proibidas, guarda de compliance antes da
geração, id determinístico (sem duplicação em retry), provedor sem credencial recusando **sem abrir conexão**,
e o default `offline` (sem rede, sem custo) — cada um com item e dente próprios. O que **não** está coberto
nesta v1: qualidade da abordagem medida por resposta real (W9) e a decisão humana (W6-E03).
