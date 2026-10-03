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

### TRE-W6-E03-T01 — Implementar Human Approval workflow

**Acceptance:** `--fila` notifica cada pedido `PENDING` uma unica vez (codigo curto `APR-`, empresa, contato,
acao, canal, texto e os tres comandos; sem `{{marcador}}` pendurado) e a segunda rodada e `JA_NOTIFICADO` com
0 novos; `--decidir aprovar` grava `APPROVED` + operador humano canonico + `decided_at` + nota + hash do texto
aprovado, e o `entrada_hash` do gerador fica intacto; replay do mesmo voto = `JA_DECIDIDO` (nada reescrito) e
voto diferente = `CONFLITO_DE_VOTO`; `--decidir editar` so aprova texto que passa a validacao do gerador irmao
(fato inventado RECUSA `EDICAO_INVALIDA` **sem gravar**), preservando o original; `--decidir rejeitar` fecha o
pedido e ele nao reabre; compliance em vigor na hora da decisao (`do_not_contact`/`opt_out_*`) e recomendacao
fora de `OPEN` RECUSAM; operador ausente/nao autorizado/maquina RECUSAM (exit 3) sem escrever; `--expirar`
marca `EXPIRED` o que passou do TTL com `decided_by` **vazio** e nao mexe no resto; pedido `EXPIRED` nao aceita
decisao; `--consultar` libera **so** `APPROVED` com hash conferido e contato limpo; `--desfazer` dry-run x
`--confirmo` (motivo obrigatorio) reabre o pedido preservando a auditoria; `prod` exit 4; escrita so em
`human_approvals`/`agent_runs` (DDL e DELETE sem `--confirmo` recusam) e nada fora delas e tocado em rodada
nenhuma.
**Test plan:** `python3 scripts/agentes/verificar_fluxo_aprovacao.py` (79 OK / 0) + `--autoteste` (20/20) +
`bash scripts/agentes/teste_fluxo_aprovacao_aceite.sh --prova-de-dente` na VPS do ambiente (PostgreSQL
descartavel `pg-aprovacao-acc`, migration 0001, 6 empresas sinteticas; os pedidos sao criados pelo **gerador do
card irmao**, cadeia real) -> `ACEITE_APROVACAO_001_OK (104 OK / 0 FALHOU)` + 4/4 dentes. Evidencia = saida
completa com exit code e veredito. Doc: `docs/architecture/aprovacao-humana-v1.md`; runbook:
`docs/runbooks/aprovacao-humana.md`.
**Rollback:** `--desfazer <correlation_id> [--confirmo --por --motivo]` devolve os pedidos da rodada a
`PENDING`, limpa `decided_at`/`decided_by`, restaura o texto original da edicao e preserva a auditoria; reverter
o merge do branch (sem DDL, sem migration; nenhum envio, nenhuma atividade no Odoo, nenhum evento de outbox).
**Risk:** medio — e o portao que autoriza abordagem a pessoa real. Enderecado por: decisao so de `PENDING`,
operador humano obrigatorio e canonico, hash do texto aprovado carimbado e reconferido no portao, compliance
reavaliada na hora da decisao, contexto do pedido reconferido contra o banco (recomendacao `OPEN`, contato e
empresa atuais), escrita restrita a duas tabelas com guarda anti-DDL, `prod` recusado e idempotencia testada
nos dois sentidos (replay nao reescreve, rodada repetida nao renotifica). O que **nao** esta coberto nesta v1:
transporte real da notificacao (Telegram/e-mail e do W6-E04) e a qualidade da abordagem medida por resposta
real (W9).
## TRE-W6-E01-T01 — Configurar Titan SMTP

- **Componente:** `hermes/integracoes/titan/smtp_titan.py` (versão `titan-smtp-v1`) + contrato
  `hermes/integracoes/titan/titan-smtp-v1.json` + doc `docs/integrations/titan-smtp-v1.md`.
- A configuração `TRE_TITAN_*` é **validada** antes de qualquer conexão: completude (faltantes
  nomeados), **matriz porta × TLS do provedor** (`465` implicit_tls · `587` starttls · `25` recusada ·
  outra porta recusada) e coerência entre porta e segurança declarada — divergência **RECUSA**
  (`CONFIG_INCOERENTE`), nunca "conserta sozinho".
- **Guardas de ambiente (ADR-005):** em `dev` só sink local e remetente/destino do domínio de dev
  (`HOST_NAO_E_DEV`, `REMETENTE_NAO_DEV`, `DESTINO_NAO_PERMITIDO`); `homolog` exige aprovação
  registrada (`HOMOLOG_SEM_APROVACAO`) e lista explícita de destinos; **`prod` RECUSA (exit 4)**.
- **Segredo:** senha só por `TRE_TITAN_PASSWORD`, mascarada em todo relatório/trilha, sem caminho por
  linha de comando, e **checagem fail-closed** de vazamento na gravação (exit 5 `SENHA_VAZADA`).
- **Idempotência:** `--chave-idempotencia` obrigatória no envio; replay da mesma chave → `JA_ENVIADO`
  sem novo envio e sem conexão; `--desfazer <chave>` é dry-run até `--confirmo` e preserva a auditoria.
- `--planejar`/`--conferir` nunca abrem conexão; `--enviar` sem `--confirmo` é `DRY_RUN`.

**Test plan:** `python3 scripts/integracoes/verificar_smtp_titan.py` (suite offline — config, matriz,
guardas, segredo, trilha; **49 itens**) + `bash scripts/integracoes/teste_smtp_titan_aceite.sh`
(sink SMTP descartável em `127.0.0.1` com TLS próprio: EHLO/TLS/AUTH/NOOP, entrega medida na captura,
guardas, replay, desfazer; **23 itens**) + `--prova-de-dente` (5 mutações, cada uma reprovando o item
esperado; **29 itens**). Evidência = saída completa com exit code. Runbook: `docs/runbooks/titan-smtp.md`.
**Rollback:** `git revert` do commit do card — sem DDL, sem migration, sem tabela e sem ato em
produção; chaves registradas podem ser marcadas com `--desfazer <chave> --confirmo` (um e-mail
entregue não volta — o `--confirmo` antes do envio é a barreira).
**Risco:** Médio-alto — é canal **outbound** (e-mail em nome da Transformativa). Mitigado por: guarda
de dev (host loopback), remetente/destino de dev, aprovação registrada em homolog, `--confirmo`
obrigatório, idempotência e ausência de credencial Titan no papel `dev-harness`. A prova contra
`smtp.titan.email` é de **homolog**, com credencial do Sales AI e aprovação do dono (fora deste card).
**Components afetados:** `hermes/integracoes/titan/`, `scripts/integracoes/`,
`deploy/environments/dev-smtp.env`, `.env.example`, `docs/integrations/titan-smtp-v1.md`,
`docs/runbooks/titan-smtp.md`, `scripts/verificar_estrutura.sh`.
**Depends on:** W5-E08-T01 (fechado e medido) · **Destrava:** W6-E01-T02 (IMAP), W6-E04-T01 (send
workflow) e, por consequência, o E2E Outbound #002.

## TRE-W6-E01-T02 — Configurar Titan IMAP

- **Componente:** `hermes/integracoes/titan/imap_titan.py` (versão `titan-imap-v1`) + contrato
  `hermes/integracoes/titan/titan-imap-v1.json` + doc `docs/integrations/titan-imap-v1.md`.
- A configuração `TRE_TITAN_*` é **validada** antes de qualquer conexão: completude (faltantes
  nomeados), **matriz porta × TLS do provedor** (`993` implicit_tls · `143` starttls · portas de
  outro protocolo — `25`/`465`/`587` SMTP e `110`/`995` POP3 — recusadas em
  `PORTA_DE_OUTRO_PROTOCOLO` · outra porta recusada) e coerência entre porta e segurança declarada —
  divergência **RECUSA** (`CONFIG_INCOERENTE`), nunca "conserta sozinho".
- **Invariante de leitura (o traço deste card):** a caixa é aberta **só com `EXAMINE`**
  (`readonly=True`), todo conteúdo vem de **`BODY.PEEK`** e o componente **audita a própria fonte
  antes de qualquer conexão** — achando comando de escrita (STORE, EXPUNGE, DELETE, COPY, MOVE,
  APPEND) ou busca sem `PEEK`, **RECUSA** com `ESCRITA_NO_CODIGO` (exit 3) e não conecta. Medição de
  ponta no sink: todas as seleções em `EXAMINE`, `total_buscas_sem_peek = 0`,
  `total_comandos_de_escrita = []` e nenhuma mensagem marcada `\Seen`.
- **Guardas de ambiente (ADR-005):** em `dev` só sink local e login do domínio de dev
  (`HOST_NAO_E_DEV`, `USUARIO_NAO_DEV`); `homolog` exige aprovação registrada
  (`HOMOLOG_SEM_APROVACAO`) e caixa na lista explícita (`CAIXA_NAO_PERMITIDA`); **`prod` RECUSA
  (exit 4)**.
- **Segredo:** senha só por `TRE_TITAN_PASSWORD`, mascarada em todo relatório/trilha/arquivo de
  mensagem, sem caminho por linha de comando, e **checagem fail-closed** de vazamento na gravação
  (exit 5 `SENHA_VAZADA`); o sink do aceite guarda `LOGIN <usuario> <senha-oculta>`.
- **Idempotência:** identidade da mensagem `UIDVALIDITY:UID`; `--chave-idempotencia` e `--saida`
  obrigatórias; replay → `JA_INGERIDO` **sem buscar o corpo de novo**; `--desfazer <identidade>` é
  dry-run até `--confirmo`, preserva a auditoria e só aceita identidade com **ingesta registrada**.
- `--planejar`/`--conferir` nunca abrem conexão; `--ingerir` sem `--confirmo` é `DRY_RUN` e não
  conecta.

**Test plan:** `python3 scripts/integracoes/verificar_imap_titan.py` (suite offline — config, matriz,
guardas, invariante de leitura **inclusive a violação injetada numa cópia do módulo**, segredo,
trilha; **67 itens**) + `bash scripts/integracoes/teste_imap_titan_aceite.sh` (sink IMAP descartável em
`127.0.0.1` com TLS próprio, nos modos `implicit_tls` e `starttls`: TLS/LOGIN/EXAMINE/NOOP, leitura de
envelopes sem marcar lido, ingesta medida na captura, replay que não relê o corpo, invariante medido no
sink, guardas, segredo, desfazer, escopo; **33 itens**) + `--prova-de-dente` (6 mutações, cada uma
reprovando o item esperado; **40 itens**). Evidência = saída completa com exit code. Runbook:
`docs/runbooks/titan-imap.md`.
**Rollback:** `git revert` do commit do card — sem DDL, sem migration, sem tabela e sem ato em
produção; a ingesta grava apenas arquivos locais (`--saida`) e a trilha JSONL, e `--desfazer
<identidade> --confirmo` marca `DESFEITO` preservando a auditoria. Nada é apagado do servidor em
nenhum caso.
**Risco:** Alto — é credencial + canal externo de e-mail, e a leitura de IMAP é o ponto em que "ler"
costuma **escrever** (flag, remoção, pasta). Mitigado por: invariante de leitura em três camadas
(EXAMINE, `BODY.PEEK`, auditoria da própria fonte com prova de dente), guarda de dev (host loopback +
login de dev), lista explícita de caixas em homolog, aprovação registrada, `--confirmo` obrigatório,
idempotência por `UIDVALIDITY:UID` e ausência de credencial Titan no papel `dev-harness`. A prova
contra `imap.titan.email` é de **homolog**, com credencial do Sales AI e aprovação do dono (fora deste
card).
**Components afetados:** `hermes/integracoes/titan/`, `scripts/integracoes/`,
`deploy/environments/dev-imap.env`, `.env.example`, `docs/integrations/titan-imap-v1.md`,
`docs/runbooks/titan-imap.md`, `scripts/verificar_estrutura.sh`.
**Depends on:** W6-E01-T01 (SMTP, fechado e medido — a branch do card ramifica dele) · **Destrava:**
W6-E05-T01 (reply ingestion/classification) e, por consequência, o E2E Outbound #002.

---

## TRE-W6-E05-T01 — ingestão e classificação de respostas (v1)

**Entregue:** `hermes/agentes/respostas/ingestao_respostas.py` (`ingestao-respostas-v1`) + contrato
`ingestao-respostas-v1.json` (vocabulário fechado, 6 regras com `ordem` explícita, 2 exclusões e as
lacunas declaradas) + `--ingerir/--conferir/--desfazer/--classificar`; leitura pelo primitivo do
`TRE-W6-E01-T02` (mudanças **aditivas**: `cabecalhos_completos`, `corpo_html`); gravação só por `INSERT`
em `interactions` + `sync_events` (idempotência `resposta:<UIDVALIDITY:UID>`, `SEM_VINCULO` quando não há
vínculo, `DESFEITO` preservando a linha).
**Test plan:** `python3 scripts/agentes/verificar_ingestao_respostas.py` (suite offline sem rede, banco e
credencial — **50 itens**) + `--prova-de-dente` (6 mutações, cada uma reprovando o item que nomeia —
**56 itens**) + `bash scripts/agentes/teste_ingestao_respostas_aceite.sh` (E2E em host com Docker:
Postgres descartável com a migration 0001 + sink IMAP local com TLS próprio + corpus de 10 respostas;
mede 12 tabelas antes/depois, categorias gravadas, replay sem duplicata, invariante de leitura no sink,
guardas, `--desfazer`, escopo — **43 itens**) + regressão do pai
(`verificar_imap_titan.py`, **67 OK**). Evidência = saída completa com exit code, anexada ao card.
Runbooks: `docs/runbooks/respostas-ingestao.md`, `docs/integrations/respostas-titan-v1.md`,
`docs/validation/registro-de-execucoes-e05-t01.md`.
**Rollback:** `git revert` do commit do card — sem DDL, sem migration e sem ato em produção; a única
escrita é `INSERT` em `interactions`/`sync_events` do banco do ambiente, e `--desfazer <identidade>` marca
`DESFEITO` sem apagar nada. Nada é escrito na fonte IMAP em nenhum caso.
**Risco:** Alto — mexe em canal externo de e-mail (leitura de caixa) **e** grava no banco. Mitigado por:
`EXAMINE` + `BODY.PEEK` obrigatórios, auditoria da fonte executada no caminho de ingesta com prova de
dente, invariante medido no sink (zero escrita, zero flag, zero lido), guarda de dev (loopback + login de
dev), `--confirmo` obrigatório, idempotência por `UIDVALIDITY:UID` e fail-closed de segredo.
**Components afetados:** `hermes/agentes/respostas/`, `hermes/integracoes/titan/imap_titan.py`,
`scripts/agentes/`, `scripts/integracoes/sink-imap-dev.py`, `deploy/environments/dev-respostas.env`,
`.env.example`, `scripts/verificar_estrutura.sh`, `docs/`.
**Depends on:** W6-E01-T02 (IMAP, fechado e medido — a branch do card ramifica dele) · **Destrava:**
W6-E06 (fluxo de resposta) e o E2E Outbound #002.

## TRE-W6-E06-T01 — atualizar o Odoo a partir das respostas (v1)

**Entregue:** `hermes/agentes/respostas/atualizacao_odoo.py` (`atualizacao-odoo-respostas-v1`) + contrato
`hermes/agentes/respostas/atualizacao-odoo-respostas-v1.json` (ato por categoria, campos escritos, limites
e **lacunas** declaradas) + `--planejar/--conferir/--regras/--propagar/--desfazer`; atualização **somente
pela API controlada** do card `TRE-W3-E01-T05` (POST `/tf/api/v1/<operacao>` com `idempotency_key`,
`correlation_id` e `dry_run` — não existe XML-RPC/JSON-RPC direto nem SQL no banco do Odoo); stub local
`scripts/agentes/stub-odoo-api-dev.py` (mesmo envelope da política v1.3.0) para medir em dev; trilha de
idempotência em `sales_intelligence.sync_events` (`odoo-resposta:<interaction_id>`), só `INSERT` na
tabela de trilha e `SEM_ATO`/`SEM_VINCULO` em vez de inventar ato ou vínculo.
**Acceptance:** `bash scripts/agentes/teste_atualizacao_odoo_aceite.sh` termina em
`ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_OK` com **0 falhas** — Postgres descartável `pg-e06-acc` com a
migration 0001 + corpus de 5 respostas já classificadas (INTERESSE com lead, OPT_OUT, SEM_INTERESSE,
BOUNCE, INTERESSE sem lead) + stub loopback; mede guardas, trilha, chamadas à API, escopo das 9 tabelas
intocadas e a chave da API ausente da saída.
**Test plan:** `python3 scripts/agentes/verificar_atualizacao_odoo.py` (suite offline sem rede, banco e
credencial — **67 itens**) + `--prova-de-dente` (**12 mutações**, cada uma reprovando o item que nomeia) +
aceite E2E acima (**45 itens**) + portão de estrutura do repo (`scripts/verificar_estrutura.sh`, PASS 0
falhas). Evidência = saída completa com exit code, anexada ao card.
Runbooks: `docs/runbooks/atualizacao-odoo-respostas.md`, `docs/validation/registro-de-execucoes-e06-t01.md`.
**Rollback:** `git revert` do commit do card — sem DDL, sem migration e sem ato em produção; a única
escrita é `INSERT` em `sync_events` (trilha) do banco do ambiente e `--desfazer <chave>` marca `DESFEITO`
preservando a linha do ato. A trilha é append-only: não existe UPDATE/DELETE no módulo (auditoria de
fonte roda antes de qualquer conexão e recusa, exit 3).
**Risco:** Alto — escreve no CRM (Odoo) e no banco canônico. Mitigado por: ausência total de acesso direto
ao Odoo (só API controlada, verificado por item de suite), guardas de ambiente por medição (prod recusa
exit 4; dev exige API em loopback e porta de banco em container local de dev/aceite), `--confirmo`
obrigatório, dry-run que não escreve nem chama a API, idempotência por chave única por decisão
(`ON CONFLICT DO NOTHING`), `VINCULO_NAO_SE_INVENTA` (`SEM_VINCULO`), auditoria da própria fonte e
fail-closed de segredo (`SENHA_VAZADA`, exit 5).
**Components afetados:** `hermes/agentes/respostas/`, `scripts/agentes/` (novo
`stub-odoo-api-dev.py`, `verificar_atualizacao_odoo.py`, `teste_atualizacao_odoo_aceite.sh`),
`docs/runbooks/`, `docs/validation/`, `CHANGELOG.md`, `.env.example`.
**Depends on:** W6-E05-T01 (respostas classificadas em `interactions`) + W3-E01-T05 (API controlada) ·
**Destrava:** o E2E Outbound #002 contra o Odoo de dev (`TRE-W6-E07`).

### TRE-W6-E04-T01 — Implementar o envio (send workflow)

**Acceptance:** o envelope consome **so** o que o portao do card irmao libera: `--enviar <approval_id>` sem
`--confirmo` e `PLANO` (nenhuma escrita, nenhum e-mail); com `--confirmo` o pedido `APPROVED` e entregue uma
unica vez pelo primitivo de SMTP e o que fica no banco e `interactions` (canal/direcao/tipo do contrato, com
`content_reference = envio:<approval_id>:<texto_hash>`) + `sync_events` (claim `ENVIANDO` antes do SMTP ->
`ENVIADO` ligado a `interaction_id`; ou `FALHOU` com a trilha do primitivo, sem fato gravado). O corpo entregue
e o **texto aprovado + CTA** e o destinatario e o **do pedido** (nao ha argumento de destino na linha de
comando). Replay da mesma chave = `JA_ENVIADO` (nao reenvia); chave em voo = `ENVIO_EM_VOO`; chave `FALHOU`
permite retentativa com `tentativas` incrementado. Pedido fora de `APPROVED`, com hash divergente, contato
bloqueado ou inexistente recusa no portao (`PORTAO_NAO_LIBEROU`) sem enviar; `dev` so entrega para host loopback
e dominio de dev (`DESTINO_NAO_DEV`) e `homolog` exige a aprovacao registrada (`HOMOLOG_SEM_APROVACAO`); `prod`
exit 4 sem escrever. Escrita so em `interactions` e `sync_events` — DDL, `DELETE` e `UPDATE` fora das rodadas
recusam antes de executar (`validar_sql`), e nenhuma outra tabela e tocada em rodada nenhuma. `--desfazer` em
dry-run conta e nao escreve; `--confirmo` exige `--por` e `--motivo`, marca `DESFEITO` e **preserva o fato**
(e um e-mail entregue nao volta: a barreira e o `--confirmo` antes do envio).

**Test plan:** `python3 scripts/agentes/verificar_envio_outbound.py` (suite offline com duble de porta de banco
e primitivo falso: 60 itens) + `--autoteste` (7 mutacoes no modulo, cada uma reprovando o item que nomeia) +
`bash scripts/agentes/teste_envio_outbound_aceite.sh --prova-de-dente` na VPS do ambiente (PostgreSQL
descartavel `pg-envio-acc` com a migration 0001 + **sink SMTP local** em `127.0.0.1` com certificado proprio;
o pedido nasce do gerador irmao W6-E02 e e aprovado pelo workflow irmao W6-E03 — cadeia real) ->
`ACEITE_ENVIO_OUTBOUND_001_OK (44 itens, 0 falhas)` + 3/3 dentes. Evidencia = saida completa com exit code.
Doc: `docs/architecture/envio-outbound-v1.md`; runbook: `docs/runbooks/envio-outbound.md`.

**Rollback:** `--desfazer <correlation_id> --confirmo --por --motivo` marca `DESFEITO` preservando o fato (para
impedir envio futuro, rejeitar/expirar o pedido no card irmao); reverter o merge do branch — sem DDL, sem
migration, sem credencial Titan e nenhum ato em producao. Mensagem ja entregue **nao** e recuperavel: o
`--confirmo` antes do envio e a barreira.

**Risk:** medio-alto — e o ato de outbound (falar com pessoa real em nome da Transformativa). Enderecado por:
portao do card irmao obrigatorio (status + hash + compliance reavaliada), `--confirmo` explicito, guarda de
ambiente/destino por ambiente, claim exatamente-uma-vez antes do SMTP com `ENVIO_EM_VOO` para nao repetir
entrega, falha do primitivo sem fato gravado, escrita restrita a duas tabelas com guarda anti-DDL, `prod`
recusado e a prova E2E inteira em banco descartavel com sink local (nenhuma credencial Titan, nenhum destino
real). O que **nao** esta nesta v1: producao (decisao do dono), ingestao de resposta (W6-E05/IMAP) e o aceite
com o `smtp.titan.email` real (homolog, com credencial do Sales AI e aprovacao do dono).

**Components afetados:** `hermes/agents/outreach/` (novo `send_workflow.py`, `politica-envio-v1.json`,
`envio-outbound-v1.json`), `scripts/agentes/` (novo verificador, duble de porta, aceite),
`docs/architecture/envio-outbound-v1.md`, `docs/runbooks/envio-outbound.md`, `scripts/verificar_estrutura.sh`.
**Depends on:** W6-E01-T01 (primitivo SMTP Titan, fechado e medido) e W6-E03-T01 (aprovacao humana, fechado e
medido) — os dois pais integrados na base deste card. **Destrava:** o E2E Outbound #002 e o W6-E05-T01 (ingestao
de resposta), que passa a ter envio registrado para casar.

## TRE-W6-E07-T01 — Executar E2E Outbound #002

- **O que e':** o cenario do doc 08 §4 (10 passos do outbound) encadeado **num unico trio descartavel**,
  com as pecas REAIS de cada card da onda W6 (nao dubles): PostgreSQL + **sink SMTP** local + **sink
  IMAP** local + **stub da API controlada do Odoo**, todos em `127.0.0.1`. Aceite:
  `scripts/e2e/verificar-e2e-outbound-002.sh`. Runbook: `docs/runbooks/e2e-outbound-002.md`.
- **Cadeia medida:** lead A+ elegivel (registro TIER do W5) -> NBA `SEND_EMAIL` `OPEN` (W5-E07) ->
  pedido de aprovacao `PENDING` com rascunho citando a recomendacao (W6-E02) -> `APPROVED` por operador
  humano canonico com hash do texto (W6-E03) -> envio unico sob TLS ao contato do pedido (W6-E04) ->
  `interactions` + `sync_events ENVIADO` -> resposta do lead ingerida e classificada `INTERESSE`
  (W6-E05) -> CRM atualizado pela API controlada com `RESPOSTA_INTERESSE`/`RESPONDER_AGORA` (W6-E06) ->
  NBA de novo: `CREATE_MEETING` SUPERSEDE a recomendacao anterior.
- **ACCEPTANCE:** `ACEITE_E2E_OUTBOUND_002_OK` — **56 itens, 0 falhas**, exit 0. Cobre os 10 passos,
  os invariantes herdados (leitura `EXAMINE`+`BODY.PEEK` re-medida de ponta, escrita restrita as duas
  tabelas, idempotencia de envio e de ingesta, portao da aprovacao, hash do texto aprovado = texto
  entregue), as **guardas de ambiente** (`prod` RECUSA exit 4 nos 4 componentes, exit 0) e o escopo
  (nenhuma tabela nova; senha do sink e chave da API ausentes da saida).
- **TEST:** `bash scripts/e2e/verificar-e2e-outbound-002.sh` (o aceite; ~1 min na VPS do ambiente) +
  `--prova-de-dente` (**3 mutacoes** em copia de componente, cada uma reprovando o item que nomeia:
  `sem-cta-na-mensagem` -> 5.7, `sem-supersessao` -> 10.3, `primeira-regra-sempre` -> 2.2) com o
  controle verde. Evidencia = saida completa com exit code, anexada ao card.
- **ROLLBACK:** o aceite **nao deixa nada**: o container do trio e' removido e os processos locais
  (sink SMTP, sink IMAP, stub) sao mortos pelo proprio script (`--manter` existe so' para investigar).
  Nao ha DDL nem migration. O que o card adiciona ao repositorio e' o aceite + runbook + docs; reverter
  e' `git revert` do commit do card. A unica escrita fora do container descartavel e' o `UPDATE`
  declarado da ponte do lead (item 9.2), que vive e morre no banco descartavel.
- **RISK:** **medio-alto** — o aceite exercita a cadeia que fala com pessoa real (outbound por e-mail) e
  escreve no CRM, ainda que contra pontas locais. Mitigado por: trio descartavel e **loopback only**
  (nenhuma credencial Titan, nenhum destino real, nenhuma chave de API do Odoo), guarda que ABORTA se o
  container ou as 3 portas ja' estiverem em uso (nao mede contra sobra de rodada), `prod` recusado por
  medição nos 4 componentes, aprovacao humana obrigatoria no meio da cadeia, dry-run que nao entrega e
  `--confirmo` explicito. O que **nao** esta medido (pontas Titan/Odoo reais, draft por LLM, o vinculo
  `interactions.odoo_lead_id`) esta declarado no runbook §5.
- **Lacuna medida (nao escondida):** **nenhum componente da onda W6 grava `interactions.odoo_lead_id`**.
  O aceite mede o efeito (item 9.1: sem o vinculo o CRM responde `SEM_VINCULO`) e so' entao aplica a
  **ponte declarada** do harness (item 9.2), que representa o papel do E2E #001 / fundacao W3-W4.
  Fechar essa lacuna e' do caminho de fundacao/sync, nao deste card.
- **Components afetados:** `scripts/e2e/verificar-e2e-outbound-002.sh` (novo),
  `docs/runbooks/e2e-outbound-002.md` (novo), `docs/kanban/criterios-de-aceitacao.md`,
  `docs/operations/registro-de-execucoes.md`, `CHANGELOG.md`, `scripts/verificar_estrutura.sh`.
- **Depends on:** todos os W6 anteriores (E01-T01, E01-T02, E02, E03, E04, E05, E06) + W5-E07/E08 (NBA)
  — todos fechados e medidos. **Destrava:** W7 (inbound/multicanal) e W8 (analytics), que dependem do
  caminho outbound provado ponta a ponta.

## TRE-W7-E02-T01 — Meta lead ingestion

- Webhook sem `X-Hub-Signature-256` válida (HMAC-SHA256 do corpo cru com o app secret) é RECUSADO antes de
  qualquer chamada à Graph API; o replay da mesma entrega inválida é `JA_INGERIDO` e não derruba a rodada.
- Lead sem e-mail **e** sem telefone não vira linha em `interactions` (`DADOS_INSUFICIENTES`).
- Contato desconhecido em `contacts` não inventa organização nem contato (`SEM_VINCULO`, 0 interação).
- Idempotência por `meta-lead:<page_id>:<leadgen_id>`: replay é `JA_INGERIDO`, sem linha nova e sem nova
  chamada à Graph API.
- Escrita restrita a `interactions` e `sync_events`, só INSERT; campo fora do mapa entra apenas pelo nome
  em `campos_desconhecidos`; `content_summary` não expõe e-mail/telefone em claro.
- Guardas ADR-005 medidas por exit code: `prod` recusa (4), porta de banco remota recusa (`BANCO_NAO_E_DEV`, 3),
  Graph fora de loopback recusa (`GRAPH_NAO_E_DEV`, 3), `--ingerir` sem `--confirmo` é DRY_RUN e não grava.
- Segredos (token/app secret) ausentes de toda saída; se aparecerem na gravação, `SENHA_VAZADA` (exit 5).

**Acceptance:** os oito critérios acima, medidos item a item pelo aceite E2E e pela suíte offline.
**Test plan:** `python3 scripts/agentes/verificar_ingestao_leads_meta.py --autoteste` (51 itens + 7
mutações) e `bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh [--prova-de-dente]` (53 itens) na VPS
do ambiente, com Postgres descartável (`pg-meta-acc`) e stub local da Graph API em loopback.
**Rollback:** reverter o commit da branch `feature/TRE-W7-E02-T01`; em runtime, `--desfazer <chave>` marca a
trilha `DESFEITO` por INSERT (trilha imutável, contrato §9).
**Risco:** Médio — ponta externa e segredo de aplicação; fail-closed na assinatura, idempotência por chave
única e escrita em 2 tabelas.
**Componentes afetados:** `hermes/agentes/inbound/ingestao_leads_meta.py` e o contrato
`meta-lead-ingestion-v1.json` (novos); `scripts/agentes/verificar_ingestao_leads_meta.py`,
`scripts/agentes/stub-meta-graph-dev.py`, `scripts/agentes/teste_ingestao_leads_meta_aceite.sh` (novos);
`deploy/environments/dev-meta.env`; `docs/integrations/meta-leads-v1.md`; `docs/runbooks/ingestao-leads-meta.md`;
`scripts/verificar_estrutura.sh`; tabelas existentes **sem mudança de schema** (`interactions`, `sync_events`;
somente INSERT).
