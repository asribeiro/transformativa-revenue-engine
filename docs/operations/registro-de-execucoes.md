# Registro de execuções do agente nas máquinas

Regra (decisão 5 do desenho `docs/architecture/aprovacao-humana-com-executor.md`): toda
execução do agente em máquina operada por ele entra aqui com **comando e saída reais**, para
a auditoria não depender da narrativa do agente. Uma linha por execução, datada, com o
identificador da máquina. Segredos nunca aparecem aqui.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102)

- **Chave de acesso instalada** (por Anderson, no terminal dele): `authorized_keys` do root
  recebeu `hermes-ops@transformativa` (fingerprint `SHA256:oUy0ikl21DXxRDrONQlLD4l/jl1p+KJ92ffnYZAA4uA`).
- **Teste de conexão (agente):** `ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102` →
  `CONECTOU`, host `vmi3619453`, `Docker version 29.8.1, build 4a63305`.
- **Tentativa de subir o banco dev (agente):** script `tre_pg_dev_up_v3.cmd` enviado por stdin
  → guarda fail-closed respondeu `container pg-sales-dev: JA EXISTE - nao mexo nele,
  pare aqui e me chame` / `fim. abort=1`. **Nada foi criado nem alterado por mim.**
- **Medição read-only do que já existia (agente):** `pg-sales-dev | Up 4 minutes |
  127.0.0.1:5433->5432/tcp`; `image=postgres:16 restart=unless-stopped`;
  `POSTGRES_DB=sales_intelligence POSTGRES_USER=sales_ai`; volume `pgdata-sales-dev`;
  `/root/.tre_pg_dev.env` 600 (51 bytes); `pg_isready` → `accepting connections`;
  `SHOW server_version` → `16.15 (Debian 16.15-1.pgdg13+2)`.
- Origem do container existente: as tentativas anteriores no terminal do dono (o colar
  quebrado abortou por sintaxe, mas uma delas completou). Estado é o esperado pelo desenho.
## 2026-09-30 — host do Hermes (Hostinger srv1912562, 187.127.56.17)

- **Chave de acesso instalada** (por Anderson, no terminal dele): `authorized_keys` do root
  recebeu `hermes-ops@transformativa`.
- **Teste (agente):** `ssh -i ~/.ssh/id_ed25519_ops root@187.127.56.17` -> host `srv1912562`,
  **16 containers** rodando.
- **Janela aprovada pelo dono:** "podemos rodar os 8 pacotes com reboot".
- **Dry-run e upgrade (agente):** `apt-get -qq update` + `apt-get -y -qq upgrade` ->
  **1 pacote subiu** (`linux-libc-dev` 6.8.0-142 -> 6.8.0-146). O `needrestart` reiniciou
  `cron`, `ssh`, `systemd-journald`, `systemd-networkd`, `systemd-resolved`,
  `systemd-timesyncd`, `systemd-udevd` (a conexao do agente nao caiu) e **diferiu**
  `systemd-logind`, `dbus`, `unattended-upgrades`. Saida explicita: "No containers need to
  be restarted". Medicao posterior: **16 containers no ar** e **6 servicos
  transformativa-\* ativos** — nada perdido.
- **Reboot NAO executado, por medicao:** kernel em uso `6.8.0-142-generic` e o maior
  instalado em `/boot` e o mesmo `6.8.0-142`; `needrestart` reportou "Running kernel seems
  to be up-to-date"; sem `/var/run/reboot-required`. Reiniciar derrubaria 16 containers (e o
  proprio agente) sem ganho medido. Decisao devolvida ao dono.
- **7 pacotes segurados (kept back)**, nao tocados: `libegl-mesa0`, `libgbm1`,
  `libgl1-mesa-dri`, `libglx-mesa0`, `mesa-libgallium`, `mesa-vulkan-drivers`
  (bibliotecas GL, tipicamente dependencia de navegador headless) e `linux-image-virtual`
  (metapacote que hoje aponta para o kernel ja instalado — bump cosmetico). Subir exige
  `apt-get dist-upgrade` (mudanca de dependencias), nao executado.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — W1/E01: schema `sales_intelligence` em dev

- **Sincronizacao da copia operacional (agente):** `tar -cz db scripts deploy docs/{data,runbooks,operations} | ssh tre-deploy@… 'tar -xz -C /opt/tre/repo'` → `SYNC_OK`; sha256 da migration igual nos dois lados (`bc766a818943…`) e do runner (`1eb97c92a321…`).
- **Estado ANTES (read-only):** `bash /opt/tre/repo/scripts/db/estado_do_ambiente.sh dev` → `0 tabelas | 0 indices`, `Did not find any relations.`, tabela de controle ausente — nada de DDL existia no ambiente.
- **Aplicacao (agente):** `bash /opt/tre/repo/scripts/db/aplicar_migracoes.sh dev` → `OK migrations encontradas: 1 arquivo(s)`, `OK postgres responde em 'pg-sales-dev' (servidor definitivo, confirmado duas vezes)`, `OK tabela de controle pronta (public.tre_schema_migrations)`, `OK versao 0001 (0001_sales_intelligence_v1.sql) aplicada e registrada (bc766a818943…)`, `RESULTADO: MIGRACAO_OK …`, exit 0.
- **Idempotencia:** segunda execucao → `PULADO versao 0001 … ja aplicada com o mesmo sha256`, `RESULTADO: MIGRACAO_OK (… 0 aplicada(s), 1 pulada(s))`, exit 0.
- **Rollback exercitado de verdade:** `DROP SCHEMA IF EXISTS sales_intelligence CASCADE` (12 objetos) + `DELETE FROM public.tre_schema_migrations` → `DROP SCHEMA`, `DELETE 1`; reaplicacao pelo runner → `MIGRACAO_OK`, 12 tabelas de volta.
- **Listagem psql:** `estado_do_ambiente.sh dev` → `12 tabelas | 30 indices` e `\dt` com as 12 tabelas (agent_runs … sync_events), owner `sales_ai`.
- **Verificador do contrato CONTRA o banco do ambiente (agente):** `python3 scripts/verificar_contrato_dados.py --banco "docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence"` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0 (26 itens dos artefatos + 11 itens do banco real).
- **Provas negativas em container descartavel `tre-neg-7015-31853`** (criado e removido pelo agente, mesmo padrao do `scripts/backup/teste-backup-restore.sh`): runner do zero → `MIGRACAO_OK`; verificador → `PASS (37 itens)`; `DROP TABLE sales_intelligence.agent_runs` → `FALHOU (4 de 37)`, exit 1; coluna inventada em `scores` → `FALHOU (4 de 37)`; `DROP INDEX idx_organizations_domain` → `FALHOU (5 de 37)`; migration editada depois de aplicada (sha diferente) → `FALHOU … migration aplicada e IMUTAVEL`, exit 1.
- **Guardrail de producao:** `aplicar_migracoes.sh prod` → `FALHOU ADR-005: DDL nao nasce em producao…`, exit 1; com `TRE_APROVACAO_HUMANA` declarado → `FALHOU sequencia dev -> homolog -> producao: container de homolog 'pg-homolog' nao existe`, exit 1; ambiente inventado (`staging`) → exit 2. **Nada foi tocado em producao.**
- **Producao intocada (medido):** `docker ps -a` → so `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f` → nenhum arquivo.
- **Defeito encontrado e corrigido no mesmo card (detectado por teste, nao por leitura):** o runner deixava o arquivo versionado `deploy/environments/dev.env` sobrescrever a variavel do operador (a primeira prova negativa acabou rodando no ambiente dev) e usava `docker exec -i`, que consumia o stdin de quem orquestra por SSH (o script remoto morreu no meio). Correcao: variavel de ambiente passa a vencer o arquivo e a migration entra no container por `docker cp` + `psql -f`. O ambiente dev foi reparado pelo proprio plano de rollback (drop + reaplicacao) e reverificado: 12 tabelas, 30 indices, `PASS (37 itens)`.
- **Divergencia medida (decisao do dono, nao do agente):** o Data Contract V1.0 declara o banco de inteligencia como `transformativa_ai`; o ambiente dev provisionado usa banco `sales_intelligence`, container `pg-sales-dev` e usuario `sales_ai` — e os scripts de backup ja assumem `sales_intelligence` como padrao. O runner aplica no par declarado em `deploy/environments/dev.env`.
- Segredos: nenhum valor nesta entrada; a conexao usa o socket local do container (sem senha em argumento, arquivo ou log).

## 2026-09-30 — licoes de engenharia desta rodada (commit, recibo, git com worker)

1. **Contrato do recibo e fechado em 13 campos** (`hermes/jev/policy_v1_2.yaml`): rastro novo
   NUNCA vira campo novo no recibo — entra DENTRO de `override` (que existe para registrar
   excecao com razao). O motivo de uma aprovacao NAO aplicavel viaja na **resposta** do gate
   (que o board grava), nao no recibo.
2. **Nao commitar com suite vermelha** — inclusive a suite que "nao tem a ver" com a mudanca.
   Nesta rodada a suite nova passou (18 itens) e o `verificar_gate_jev.py` ficou vermelho por
   causa da mudanca; o commit saiu antes de ver. Regra: mudanca no gate roda, no minimo,
   `verificar_gate_jev.py` + `verificar_aprovacao_humana.py` ANTES do commit.
3. **Repo com worker rodando troca de branch sozinho**: o worker do card cria e faz checkout de
   `feature/<codigo-do-card>` no MESMO diretorio. Commit feito nesse momento cai na branch do
   worker, e `git push origin develop` vira no-op silencioso (`origin/develop` nao anda e o
   `git log` local engana). Procedimento correto: conferir `git branch --show-current` antes de
   commitar e **empurrar pelo SHA** (`git push origin <sha>:develop`), sem `checkout` — trocar
   de branch com worker em execucao atropela o worker.

## 2026-09-30 — Decisão 8 (dono): deixar os 7 pacotes segurados no host do Hermes

Depois da janela aprovada dos 8 pacotes (1 subiu: `linux-libc-dev` 6.8.0-142 -> 146), restaram
7 segurados (`libegl-mesa0`, `libgbm1`, `libgl1-mesa-dri`, `libglx-mesa0`, `mesa-libgallium`,
`mesa-vulkan-drivers`, `linux-image-virtual`). Anderson decidiu **deixar como está** (opção A),
com o racional medido: o kernel em uso e o maior instalado sao o mesmo (`6.8.0-142`), sem
`/var/run/reboot-required`, e `linux-image-virtual` e metapacote que ja aponta para ele — ganho
de zero; as bibliotecas GL so afetariam servico que use navegador headless no HOST. Subir os 7
exigiria `apt-get dist-upgrade` (mudanca de dependencia, com remocao possivel) num host que
sustenta 16 containers. Nada foi executado.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — W1/E02: massa de smoke nas 12 tabelas em dev

- **Sincronizacao da copia operacional (agente):** `tar -cz db scripts deploy docs/{data,runbooks,operations} | ssh root@… 'tar -xz -C /opt/tre/repo && chown -R tre-deploy:tre-deploy'` → `SYNC_OK`; sha256 igual nos dois lados para os artefatos novos (`aplicar_fixture_smoke.sh cef34f4a43cc…`, `verificar_fixture_smoke.sh 6a83c167bf17…`, `teste-fixture-smoke.sh aaa94e4991e8…`, `smoke_dev_rollback.sql 9a4592f99e38…`, `smoke_dev.sql ed5700b29c2e…`).
- **Estado ANTES (read-only):** `bash scripts/db/estado_do_ambiente.sh dev` → `12 tabelas | 30 indices`, `\dt` com as 12 tabelas, `public.tre_schema_migrations` com a versao 0001 (`bc766a818943…`), **0 linhas** em todas as tabelas.
- **Critério 1 (colunas/PK/FK/NOT NULL conforme o contrato):** `python3 scripts/verificar_contrato_dados.py --banco "docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence"` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0 (alvo `sales_intelligence @ sales_ai`; 11 itens conferidos contra o banco real).
- **Critério 2 + 3 (fixture carrega em dev e contagem confere):** `bash scripts/db/aplicar_fixture_smoke.sh dev` → `OK fixture aplicado sem erro (psql -v ON_ERROR_STOP=1, exit 0)`, `OK linha de base contada: 12 tabelas, 14 linhas no total`, uma linha `OK` por tabela com `alvo=N == esperado do fixture=N` (organizations 2, scores 2, as outras 10 com 1), `RESULTADO: FIXTURE_OK (20 itens, 0 falhas)` e `RESULTADO: FIXTURE_APLICADO_OK (7 itens, 0 falhas)`, exit 0.
- **Idempotencia:** segunda execucao com o schema ja em 14 linhas → `-- linhas no schema ANTES da aplicacao: 14` e 14 depois (nada duplicado, exit 0).
- **Prova negativa (container descartavel, nao toca ambiente real):** `bash scripts/db/teste-fixture-smoke.sh` → `TESTE_OK (12 itens, 0 falhas)`: alvo integro aprova (20 itens OK); **linha a mais** reprovada (2 falhas apontadas); **linha a menos** reprovada; reaplicar o fixture restaura a contagem; rollback da massa (`db/fixtures/smoke_dev_rollback.sql`) → `0 linhas nas 12 tabelas`; reaplicar o fixture → 14 de volta (rollback reversivel).
- **Guardrail de ambiente:** `bash scripts/db/aplicar_fixture_smoke.sh prod` → `FALHOU ADR-005: massa de smoke nao nasce em producao`, exit 1; `docker ps -a` → so `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f | wc -l` → `0`. **Nada foi tocado em producao nem em homologacao.**
- **Defeitos achados por teste no proprio card (2, corrigidos e medidos):** (i) o item que confere "toda tabela tem INSERT no fixture" exigia o `(` na mesma linha do nome — o fixture quebra linha antes das colunas, entao as 12 tabelas foram reprovadas na primeira execucao (o item existe de verdade); corrigido para casar o nome com fronteira; (ii) o teste negativo deixava o alvo mutado antes da reverificacao final; corrigido reaplicando o fixture, o que virou a prova de que a massa restaura a contagem.
- **Verificadores do repo depois da mudanca:** `verificar_estrutura.sh` PASS; `secret_scan.sh` PASS; `verificar_papeis.sh` PASS; `verificar_contrato_dados.py` (arquivos) `PASS (26 itens)`.
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — W1/E03: constraints e indices conferidos item a item em dev

- **Campos do card definidos ANTES de executar** (doc 11 §2): AC de referência (homologados por Anderson em 29/09/2026), TEST PLAN, ROLLBACK PLAN, AFFECTED COMPONENTS e RISK LEVEL (baixo) registrados no card `t_49e8e2e3` antes da primeira medição.
- **Estado ANTES (read-only):** `bash scripts/db/estado_do_ambiente.sh dev` → `12 tabelas | 30 indices`; migration 0001 registrada em `public.tre_schema_migrations` com sha `bc766a818943…` (imutável, não tocada nesta rodada).
- **Critério 1 (os 30 índices existem e aparecem no `pg_indexes`):** `python3 scripts/db/verificar_constraints_indices.py --banco "docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence"` → `RESULTADO: PASS (16 itens, 0 falhas)`, exit 0 — o log traz a listagem completa dos 30 índices (12 `<tabela>_pkey` UNIQUE + 15 `CREATE INDEX` nomeados + 3 UNIQUE inline), conferidos por conjunto exato (nome, tabela, unicidade e colunas com direção `DESC`): nada faltando, nada sobrando; os 16 itens de índice do contrato, todos presentes.
- **Critério 2 (constraints PK/FK/unique item a item):** `pg_constraint` do alvo → 12 PK (uma por tabela, coluna `id`), 10 FK e 3 UNIQUE, exatamente o contrato; os vínculos lógicos declarados (`signals.research_run_id`, `recommendations.opportunity_id`, `interactions.campaign_id`) continuam SEM FK. O `information_schema` mostra ainda 35 linhas `CHECK`, que são as 35 colunas `NOT NULL` (constraint `_not_null` por coluna, do padrão) — `pg_constraint` do schema só tem p/f/u, ou seja, não há CHECK inventado.
- **Critério 3 (verificador do contrato passa no banco do ambiente):** `python3 scripts/verificar_contrato_dados.py --banco "…"` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0.
- **Prova negativa (container descartável `tre-constraints-teste-71908-12394`, criado e removido pelo próprio teste):** `bash scripts/db/teste-constraints-indices.sh` → `TESTE_OK (18 itens, 0 falhas)`, exit 0. Alvo íntegro APROVA (16 itens); sete mutações REPROVAM apontando o motivo (índice removido → 3 falhas; índice renomeado → 2; mesmo nome com coluna errada → 3; FK removida → 1; UNIQUE removida → 5; PK removida → 4; índice a mais → 2); cada mutação desfeita volta a APROVAR.
- **Defeito achado por teste no próprio card (corrigido com evidência):** a mutação `DROP CONSTRAINT organizations_pkey` não podia ser aplicada — a PK é referenciada por 7 FKs e o Postgres exige `CASCADE`; o teste reprovou a si mesmo (`FALHOU f. …: nao consegui aplicar a mutacao no container descartavel`). Primeira execução: `TESTE_FALHOU (17 itens, 1 falha)`; a mutação passou a usar `human_approvals_pkey` (tabela sem FK de entrada) e a segunda execução fechou `TESTE_OK (18 itens, 0 falhas)`.
- **Verificadores do repo depois da mudança:** `verificar_estrutura.sh` PASS; `secret_scan.sh` PASS (`nenhum segredo versionado`); `verificar_papeis.sh` PASS; `verificar_contrato_dados.py` (arquivos) `PASS (26 itens)`.
- **Guardrail de ambiente:** `bash scripts/db/aplicar_migracoes.sh prod` → `FALHOU ADR-005: DDL nao nasce em producao`, exit 1; `docker ps -a` → só `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f | wc -l` → `0`. **Nada foi tocado em produção nem em homologação.**
- Segredos: nenhum valor nesta entrada; conexão pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453) — W1/E04: deduplicacao por identificadores fortes em dev

- **Sincronizacao da copia operacional (agente):** `tar -cz scripts docs | ssh root@… 'tar -xz -C /opt/tre/repo && chown -R tre-deploy:tre-deploy'` → `SYNC_OK`; sha256 do motor igual nos dois lados (`deduplicar_organizacoes.py a16c680bf84a…` antes da correcao do defeito; o sha final esta no commit do card).
- **Estado ANTES (read-only):** `contar_tabelas` pelo motor → 12 tabelas do contrato com `organizations 2, scores 2, signals 1, …` (contagem do fixture de smoke intacta: 14 linhas).
- **Suite sintetica (agente, host do Hermes e VPS):** `python3 scripts/dedup/deduplicar_organizacoes.py --autoteste` → `TESTE_OK (30 itens, 0 falhas)`, exit 0. Cobre: normalizacao (CNPJ/dominio/LinkedIn), cada identificador forte **isolado** (CNPJ, dominio, LinkedIn) e os tres **em conjunto** (com a prioridade do contrato na evidencia), o limite **0,94 vs 0,95** (0,94 mantem separado e sinaliza; 0,95 e inclusivo e mergeia), negativos (sem identificador comum; nome igual com cidade diferente), auditoria (merge grava `sync_events`; chave de idempotencia deterministica) e **governanca do limiar** (sem parametro, sem variavel de ambiente e sem atributo de modulo que mude o valor lido do contrato).
- **Prova negativa da propria suite:** `--autoteste --sabotar limiar|auditoria|identificadores|fraco` → as quatro reprovam (`TESTE_FALHOU`, 2/3/5/2 falhas, exit 1). Sabotagem que nao reprovasse seria teste passando por construcao — o wrapper `teste_dedup_sintetico.sh` transforma isso em falha: `TESTE_DEDUP_SINTETICO_OK (7 itens, 0 falhas)`, exit 0.
- **Varredura somente-leitura no dev real:** `--detectar --ambiente dev` → `candidatos a duplicidade: 0` (as duas organizacoes do fixture nao sao duplicatas), exit 0.
- **Cenario real no ambiente (agente):** `bash scripts/dedup/teste_dedup_ambiente.sh dev` → `TESTE_DEDUP_AMBIENTE_OK (4 itens, 0 falhas)`, com o cenario em `CENARIO_OK (17 itens, 0 falhas)`, exit 0: par com mesmo CNPJ detectado (confianca 1,0, decisao MERGE) e **mesclado de verdade** (filho `signals` reapontado para o sobrevivente, duplicado com `deleted_at`, auditoria lida de volta do banco com `operation=MERGE`, confianca 1,0 e `limiar_vigente=0.95`); merge repetido **recusado por idempotencia** (1 registro so); `--desfazer-merge` devolveu os vinculos e registrou `UNMERGE`; par fraco (nome + cidade) **parado em 0,94** e mandado para `human_approvals` PENDING **sem nenhuma organizacao mesclada**.
- **Estado DEPOIS identico ao ANTES:** contagem por tabela igual (`organizations 2, scores 2, …, sync_events 1`), `nenhuma linha sintetica sobrou no ambiente` — a limpeza filtra pelo marcador `cenario=tre-w1-e04-t01`, nunca por `LIKE` generico, para nao apagar auditoria real.
- **Guardrail de ambiente:** `teste_dedup_ambiente.sh` roda primeiro `--ambiente prod` → `FALHOU ADR-005: deduplicacao nao nasce em producao…`, exit 1. **Producao intocada (medido):** `docker ps -a` → so `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f | wc -l` → `0`.
- **Defeito achado por teste no proprio card (corrigido):** o SQL de contagem por tabela montava as linhas de `VALUES` sem parenteses por linha e o PostgreSQL reprovou a primeira execucao real no dev (`ERROR: syntax error at or near "'organizations'"`; o cenario nem comecou, ambiente intacto). Corrigido montando `(tabela, (SELECT count(*)))` por linha; a execucao seguinte passou e o estado do dev foi conferido igual ao anterior. Aprendizado: leitura de contagem que so roda contra o banco real nao aparece em teste de arquivo — o cenario no ambiente e que pegou.
- **Defeito pre-existente achado no proprio verificador (corrigido):** `scripts/verificar_estrutura.sh` tinha `exit` no MEIO do script (o resumo `RESULTADO: PASS/FALHOU` ficava na linha 30) — todo o resto do arquivo era codigo morto: os blocos de artefato versionado (backup/T03, JEV policy, dedup novo, processo de defeitos) **nunca rodavam** e o script imprimia PASS mesmo com artefato fora do git (aceite falso). Corrigido movendo o resumo/exit para o FIM do arquivo. **Provado com dente:** `git rm --cached scripts/dedup/deduplicar_organizacoes.py` → `FALHOU nao versionado …`, `RESULTADO: FALHOU (1)`, exit 1; re-adicionado → `PASS (0 falhas)`, exit 0. Aprendizado: verificador que "sempre passou" precisa de prova negativa — a leitura do fluxo pegou o que nenhuma execucao tinha pegado.
- **Verificadores do repo depois da mudanca:** `verificar_estrutura.sh` → `PASS (0 falhas)`, 52 itens OK (agora incluindo os 4 artefatos do E04, que antes eram codigo morto); `secret_scan.sh` → `PASS (nenhum segredo versionado)`; `verificar_papeis.sh` → `PASS (0 falhas)`; `verificar_contrato_dados.py` (arquivos) → `PASS (26 itens, 0 falhas)`; `verificar_gate_jev.py` → `PASS (30 itens, 0 falhas)`; `verificar_aprovacao_humana.py` → `PASS (18 itens, 0 falhas)`. Nota de ambiente: o `python3` do host do Hermes nao tem PyYAML, entao o gate roda por `uv run --quiet --with pyyaml python scripts/verificar_gate_jev.py` (na VPS o modulo tambem nao esta no python do sistema).

- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453) — W1/E04-T02: campo `entity_match_confidence` calculado, por faixa e persistido (dev)

- **Campos do card definidos ANTES de executar** (doc 11 §2): AC de referência (homologados por Anderson em 29/09/2026), TEST PLAN, ROLLBACK PLAN, AFFECTED COMPONENTS e RISK LEVEL (médio) registrados no card `t_430ba4cc` **antes** da primeira medição (comentário do card, 30/09/2026).
- **Sincronizacao da copia operacional (agente):** `tar -cz scripts docs CHANGELOG.md | ssh root@… 'tar -xz -C /opt/tre/repo && chown -R tre-deploy:tre-deploy …'` → `SYNC_OK`.
- **Modelo de faixas (critério 2 — faixas documentadas e coerentes com o limiar do E04-T01):** `python3 scripts/dedup/deduplicar_organizacoes.py --faixas` → exit 0: `MERGE_AUTOMATICO [0.95, 1.00] -> MERGE`, `REVISAO_HUMANA [0.80, 0.95) -> REVIEW_REQUIRED`, `SEM_DUPLICIDADE [0.00, 0.80) -> SEM_DUPLICIDADE`, com a origem da fronteira impressa (`docs/data/data_contract_v1.json`, `dedup.auto_merge_threshold=0.9500` + piso de candidatura 0,80). As faixas são derivadas do contrato a cada chamada e o modelo **recusa operar** com contrato incompatível (falha fechada, sem faixa sobreposta).
- **Suíte do motor (critérios 1 e 3):** `python3 scripts/dedup/deduplicar_organizacoes.py --autoteste` → `TESTE_OK (47 itens, 0 falhas)`, exit 0 (eram 30 itens no E04-T01; +17 itens novos de faixa/score/coerência/persistência). Inclui o **teste de faixa do aceite** em três lugares: decisão (`0,94 → REVIEW_REQUIRED`, `0,95 → MERGE`), caminho do merge no ponto exato (`gerar_sql_merge` **recusa** 0,94 e **gera** o SQL auditável de 0,95) e cenário real no dev.
- **Prova negativa da própria suíte (novas sabotagens):** `--autoteste --sabotar persistencia|coerencia|limiar` → `TESTE_FALHOU (47 itens, 3/8/15 falhas)`, exit 1 nas três. Sabotagem que não reprovasse seria teste passando por construção.
- **Sem regressão no E04-T01:** `bash scripts/dedup/teste_dedup_sintetico.sh` → `RESULTADO: TESTE_DEDUP_SINTETICO_OK (7 itens, 0 falhas)`, exit 0 (as quatro sabotagens do T01 continuam reprovando; a suíte do T01 deixou de estourar traceback quando uma inconsistência de contrato é forçada — exceção virou item reprovado com veredito).
- **Cenario real no ambiente (critério 1 — campo CALCULADO e PERSISTIDO):** `bash scripts/dedup/teste_entity_match_confidence.sh dev` → `RESULTADO: TESTE_ENTITY_MATCH_CONFIDENCE_OK (18 itens, 0 falhas)`, exit 0, com `CENARIO_OK (21 itens, 0 falhas)`. O campo é lido **de volta do banco**: `sync_events.request_payload` traz `entity_match_confidence=1.0`, `entity_match_confidence_faixa=MERGE_AUTOMATICO`, `entity_match_confidence_modelo=entity-match-confidence-v1` e a tabela de faixas vigente; `human_approvals.proposed_action` traz `entity_match_confidence=0.94` na faixa `REVISAO_HUMANA` (par fraco, nome + cidade, **sem** nenhuma organização mesclada). O alias `confianca` do E04-T01 ficou no mesmo registro com o mesmo valor (compatibilidade).
- **Estado DEPOIS identico ao ANTES:** contagem por tabela igual (`organizations 2, scores 2, …, sync_events 1`) e `nenhuma linha sintetica sobrou no ambiente` — a limpeza continua filtrando pelo marcador `cenario=tre-w1-e04-t01`, nunca por `LIKE` genérico.
- **Guardrail de ambiente:** `--cenario-ambiente --ambiente prod` → `FALHOU ADR-005: deduplicacao nao nasce em producao…`, exit 1. **Produção e homologação intocadas (medido):** `docker ps` → só `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f | wc -l` → `0`.
- **Verificadores do repo depois da mudanca:** `verificar_estrutura.sh` → `PASS (0 falhas)` (agora com os 2 artefatos do T02); `secret_scan.sh` → `PASS (nenhum segredo versionado)`; `verificar_papeis.sh` → `PASS (0 falhas)`; `verificar_contrato_dados.py` (arquivos) → `PASS (26 itens, 0 falhas)`; `verificar_gate_jev.py` (por `uv`, PyYAML) → `PASS (30 itens, 0 falhas)`; `verificar_aprovacao_humana.py` → `PASS (18 itens, 0 falhas)`.
- **Prova negativa do verificador de estrutura (novo bloco do T02 tem dente):** `git rm --cached docs/data/entity-match-confidence.md` → `FALHOU nao versionado docs/data/entity-match-confidence.md` + `RESULTADO: FALHOU (1)`; re-adicionado → `RESULTADO: PASS (0 falhas)`. Aprendizado do D04 do E04-T01 aplicado: bloco novo de verificador nasce com a prova de que reprova.
- **Decisão de execução declarada (não é mudança de contrato):** não há coluna nova. O score é do **par** (decisão de match) e vive no registro auditado da decisão; criar coluna é gatilho de nova versão do contrato (governança §10) + aprovação humana (ADR-0004), fora do escopo de um card de execução. O rollback proposto no card ("coluna fica nula") não se aplica por isso — registrado no card, no CHANGELOG e em `docs/data/entity-match-confidence.md` §6/§7.
- **Entrega:** commits `8e9d3f1` (implementação), `559fb76` (evidência neste registro) e `6645ba1` (limpeza do registro de evidência) empurrados para `feature/TRE-W1-E04-T02` e `develop` (fast-forward a partir de `5102370`).
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453) — W1/E04-T02-D02: `--faixas` deriva a linha de detalhe do modelo

- **Origem:** revisão independente do `TRE-W1-E04-T02` (perfil `tester`, rodada 1, lente artefato) → `changes_requested`: a linha de detalhe do `--faixas` tinha os **nomes das faixas fixos no código** enquanto as decisões eram calculadas, e o teste do projeto (`scripts/dedup/teste_entity_match_confidence.sh:49-50`) asseria essa string constante como evidência do critério 2 (mesma classe do D04 do E04-T01). Card de defeito do board registrado, ligado como pré-requisito da origem.
- **Reprodução (autor, cópia isolada; contrato do repo intocado):** cópia da árvore com `dedup.auto_merge_threshold=0.90` → `--faixas` imprimia `MERGE_AUTOMATICO  [0.90, 1.00]  -> MERGE` na tabela e `detalhe: 0,94 cai em REVISAO_HUMANA (MERGE); 0,95 cai em MERGE_AUTOMATICO (MERGE); limiar inclusivo`, **exit 0** — 0,94 está dentro de `[0.90, 1.00]`: o próprio artefato do critério se contradizia.
- **Correção:** `linha_detalhe_faixas()` (nova) derivada de `faixa_de_confianca()` — a mesma fonte que decide o merge; `main()` passou a imprimi-la. Suíte ganhou o item da linha de detalhe e a **sabotagem `detalhe`** (reproduz o defeito: nome fixo + decisão calculada). O `.sh` deixou de asserir constante: calcula a expectativa de 0,94/0,95 **pelo próprio modelo** (`faixa_de_confianca`) e, numa cópia com o limiar em 0,90, exige que tabela e detalhe **se movam juntos** (mutação só na cópia temporária; contrato do repo intocado).
- **Prova de dois lados (antes/depois), mesma verificação nova:** motor **anterior** (`f437072`, sha256 `e58058469a0631e4…`) com o limiar copiado em 0,90 → `FALHOU teste de faixa do aceite DERIVADO do modelo` e `FALHOU e a linha de detalhe move JUNTO`; motor **corrigido** (sha256 `54dcc0b46252574f…`) na mesma cópia → os dois itens `OK`. Ou seja: a verificação tem dente e o defeito era medível por ela.
- **Medição na VPS (dev, ADR-0008; árvore própria em `/tmp/d02_fix_*`, nada publicado):** sha256 do motor e do teste iguais aos do worktree (`54dcc0b46252574f…`, `741f253c5c1f56f5…`); `--faixas` → exit 0 com as faixas do contrato (`auto_merge_threshold=0.9500`) e o detalhe derivado.
- **`bash scripts/dedup/teste_entity_match_confidence.sh dev` → `RESULTADO: TESTE_ENTITY_MATCH_CONFIDENCE_OK (22 itens, 0 falhas)`, exit 0** (eram 18; +4 itens de detalhe/mutação). Seção 1: `OK teste de faixa do aceite DERIVADO do modelo`; seção 1b (cópia com limiar 0,90): `OK a tabela move a faixa de merge para [0.90, 1.00]` e `OK e a linha de detalhe move JUNTO`. Cenário real em dev: `CENARIO_OK (21 itens, 0 falhas)` + `TESTE_DEDUP_AMBIENTE_OK (4 itens, 0 falhas)`, com o campo lido **de volta do banco** (merge `entity_match_confidence=1,0`/`MERGE_AUTOMATICO`; fila humana `0,94`/`REVISAO_HUMANA`) e o alias do E04-T01 com o mesmo valor.
- **Suíte do motor:** `--autoteste` → `TESTE_OK (48 itens, 0 falhas)`, exit 0 (era 47). **Provas negativas (todas com exit 1):** `persistencia` 3 falhas, `coerencia` 8, `limiar` 15, **`detalhe` 1 (nova)**, `identificadores` 5, `auditoria` 7. **Sem regressão no E04-T01:** `teste_dedup_sintetico.sh` → `TESTE_DEDUP_SINTETICO_OK (7 itens, 0 falhas)`, exit 0.
- **Guardrail e estado do ambiente:** `--cenario-ambiente --ambiente prod` → `FALHOU ADR-005…`, exit 1; `docker ps` só `pg-sales-dev`; `/opt/tre/{prod,homolog}` com 0 arquivos; dev conferido no fim → `organizations=2 sync_events=1 human_approvals=1` (idêntico ao estado anterior; a limpeza do cenário não deixou massa).
- **Verificadores do repo depois da mudança:** `verificar_estrutura.sh` → `PASS (0 falhas)`; `secret_scan.sh` → `PASS`; `verificar_papeis.sh` → `PASS (0 falhas)`; `verificar_contrato_dados.py` → `PASS (26 itens)`; `verificar_gate_jev.py` (por `uv`, PyYAML) → `PASS (30 itens)`; `verificar_aprovacao_humana.py` → `PASS (18 itens)`. **Prova negativa do verificador de estrutura:** `git rm --cached scripts/dedup/teste_entity_match_confidence.sh` → `RESULTADO: FALHOU (1)`, exit 1; re-adicionado → `PASS (0 falhas)`, exit 0.
- **Entrega:** commits `7a6a270` (correção: motor, teste, `CHANGELOG.md` Fixed, `docs/kanban/criterios-de-aceitacao.md`, `docs/data/entity-match-confidence.md` — D-T02-6) e `17c905f` (esta entrada) em `feature/TRE-W1-E04-T02` e `develop`.
- **Aprendizado:** artefato de prova que imprime o **modelo** e a **expectativa escrita à mão** lado a lado pode se contradizer quando a régua se move — e o teste que assere a constante não vê. Verificação de modelo derivado tem de derivar a expectativa da mesma fonte, e o cenário com a régua movida é o que dá dente a ela. (Mesma classe do D04 do E04-T01: verificador que prova por constante.)
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

