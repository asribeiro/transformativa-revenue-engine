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

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E06-T01: ciclo de backup/restore contra o banco do DEV

- **Linha de base (read-only):** 12 tabelas, 30 indices, 14 linhas (`organizations 2`, `scores 2`, as outras 10 com 1); `docker ps -a` so `pg-sales-dev`.
- **Ciclo em modo ambiente real (agente, como `root`):** `set -a; . /etc/tre/backup.env; set +a; /opt/tre/repo/scripts/backup/teste-backup-restore.sh /opt/tre/repo --ambiente dev` -> `origem: pg-sales-dev`, `OK origem e o container do ambiente dev ... (nenhum container descartavel de origem)`, artefato `tre_dev_20260930T193704Z`, `OK manifesto registra o envio externo (externo: enviado (contabo:tre-backup))`, `RESULTADO: TESTE_OK (14 itens, 0 falhas)`, **exit 0**.
- **Producao intocada / dev intacto:** `docker inspect pg-sales-dev` com o **mesmo** `Id` (`396ace5637103224a550ab0b9687744e43ccdb32cb242ffc8e0dd6087a68a152`) e `StartedAt` (`2026-09-30T17:05:15.574220813Z`) antes e depois; `docker ps -a` sem container `tre-smoke-src`; so `pg-sales-dev` mais descartaveis de outros cards.
- **Verificacao independente:** `sha256sum` do dump == manifesto == `.sha256` (`f92f924d…`); `pg_restore --list` dentro de container -> 68 objetos; restore proprio (`pg_restore --no-owner --no-privileges`) -> contagens **identicas** ao `contagens.txt` (diff vazio, 14 linhas); `rclone lsl contabo:tre-backup/tre_dev_20260930T193704Z` -> 6 objetos; **prova de volta** (`rclone copyto` do dump + `sha256sum` + `cmp`) -> `IDENTICOS`.
- **Negativos (todos reprovados como deviam):** contagem mutada -> `RESTORE_FALHOU (1 falha)` exit 1; dump de 0 byte -> `RESTORE_FALHOU` exit 1; dump valido de outro banco -> `RESTORE_FALHOU (5 falhas)` exit 1; dump truncado a 2 KB -> `RESTORE_FALHOU (8 falhas)` exit 1; destino externo inexistente -> `BACKUP_FALHOU`, manifesto `externo: falhou`, exit 1. Fronteiras: base com 0 linhas -> `RESTORE_OK` com `NOTA backup sem linhas`; destino externo vazio -> `externo: pendente` exit 0; `--ambiente prod` -> recusado, exit 2; container inexistente -> exit 1 sem cair para descartavel.
- **TESTE NEGATIVO DO PROPRIO TIMER (achado 1):** `systemctl start tre-backup.service` -> `Job for tre-backup.service failed … code=exited, status=203/EXEC`; journal: `Unable to locate executable '/opt/tre/repo/scripts/backup/backup-tre.sh': Permission denied`. Causa raiz medida: `git ls-files -s scripts/backup/` -> **100644** (sem bit executavel) e a copia operacional `-rw-r--r--`; o unit tem `ExecStart=…/backup-tre.sh todos`. `systemctl reset-failed` aplicado depois.
- **TESTE NEGATIVO DA ROTINA (achado 2):** com a config real do timer (`. /etc/tre/backup.env`), `bash /opt/tre/repo/scripts/backup/backup-tre.sh todos` -> `PULADO ambiente dev/homolog/prod: container 'pg-dev'/'pg-homolog'/'pg-prod' nao existe`, `RESULTADO: BACKUP_OK (todos)`, exit 0, **zero artefato novo** em `/opt/tre/backup` (o trio real do dev e `pg-sales-dev`/`sales_ai`, declarado so em `deploy/environments/dev.env`, que nenhum timer le).
- **Concorrencia medida:** a copia operacional `/opt/tre/repo` foi sobrescrita por outro card durante a rodada (sha do driver `d29c9c97…` -> `9f24572a…`); o ciclo principal ja havia rodado com o driver correto (sha impresso no inicio da execucao).
- **Restricao de execucao registrada:** o ciclo rodou como `root` (acesso do agente); a execucao do ciclo sob `tre-deploy` com as variaveis do timer nao foi possivel nesta sessao (encadeamento de privilegio bloqueado pelo guard de comandos do harness). Provado por partes: `sudo -u tre-deploy test -w /opt/tre/backup` -> OK; `sudo -u tre-deploy rclone lsl --config /etc/tre/rclone.conf contabo:tre-backup/…` -> OK.
- **O que NAO foi tocado:** producao (nao existe na maquina), `homolog`, os scripts sob teste (`backup-tre.sh 1a430637…`, `verificar-backup.sh 885ad388…`, `restore-tre.sh c1750401…`), o `.env`/segredos (nenhum valor em argumento, arquivo ou log).

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E06-T01-D01: bit executavel dos scripts de unit (defeito `t_22c27625`, o `203/EXEC`)

- **Objeto do defeito:** `tre-backup.service` morria em `status=203/EXEC` (achado 1 do card E06) porque `scripts/backup/*.sh` estavam `100644` no git e o `ExecStart=` chama o arquivo direto. Correcao no git (`100755`, commit `6a580ee`, branch `fix/TRE-W1-E06-T01-D01`) + reinstalacao da copia operacional por `install -m 755`.
- **ANTES (medido no commit `16c31f0`, estado intacto):** `git ls-files -s scripts/backup/` -> `100644` em **6 dos 7** scripts existentes (`teste-backup-restore.sh` ja era `100755`; `verificar-modos-executaveis.sh` nao existia — contagem conferida com `git ls-tree -r 16c31f0 -- scripts/backup/ | awk '{print $1}' | sort | uniq -c` -> `6 100644` + `1 100755`); `ls -l /opt/tre/repo/scripts/backup/` -> `-rw-r--r-- tre-deploy`; `sudo -u tre-deploy test -x …/backup-tre.sh` -> **exit 1**; `systemctl start tre-backup.service` -> `START_EXIT=1`, `systemctl show` -> `Result=exit-code ExecMainStatus=203 ExecMainCode=1 User=tre-deploy`, journal `Failed at step EXEC spawning /opt/tre/repo/scripts/backup/backup-tre.sh: Permission denied`.
- **Verificador de regressao com dente (novo):** `scripts/backup/verificar-modos-executaveis.sh` le os `ExecStart=` dos units e confere modo no indice do git + bit no disco. Contra a arvore congelada de `16c31f0` -> `RESULTADO: MODOS_FALHOU (4 itens, 2 falhas, 2 provas efetivas)`, **exit 1**; contra o branch corrigido -> `RESULTADO: MODOS_OK (4 itens, 0 falhas, 2 provas efetivas)`, **exit 0**. Na copia operacional (`RAIZ_REPO=/opt/tre/repo`, sem `.git`) -> `MODOS_OK (4 itens, 0 falhas, 2 provas efetivas)` com `AVISO modo no git nao conferido aqui`.
- **Instalacao da copia operacional (agente, `root`):** 8 arquivos para staging `/tmp/tre-fix-d01` e `install -o tre-deploy -g tre-deploy -m 755` -> `-rwxr-xr-x tre-deploy` nos 8; staging removido. **Conteudo provado por sha256**: 8/8 identicos ao repositorio (`diff` vazio entre a lista local e a da VPS), incluindo `teste-backup-restore.sh d29c9c97…` (o driver do modo `--ambiente`, que estava revertido para `9f24572a…` por sync ad-hoc).
- **DEPOIS:** `sudo -u tre-deploy test -x …/backup-tre.sh` -> **exit 0** (idem para `verificar-ultimo-backup.sh`); `systemctl start tre-backup.service` -> **`START_EXIT=0`**, `Result=success ExecMainStatus=0`, journal de 20:03:37 UTC com `backup-tre.sh[182191]` imprimindo os tres blocos de ambiente e `RESULTADO: BACKUP_OK (todos)`, `Deactivated successfully` — nenhum `203/EXEC`.
- **Segundo unit exercitado de ponta a ponta sob `tre-deploy`** (`tre-backup-verify.service`, `ExecStart=…/verificar-ultimo-backup.sh todos`): `systemctl start` -> exit 0; journal com `verificar-ultimo-backup.sh[182935]` -> `tre_dev_20260930T193704Z`, `OK dump legivel pelo pg_restore (68 objetos)`, `OK tabelas: 12`, `OK indices: 30`, `OK contagens por tabela: todas as 12 batem (linha a linha)`, `RESULTADO: RESTORE_OK (11 itens, 0 falhas)`, `VERIFICACAO_OK (1 itens)`. E a primeira vez que o ciclo do timer roda como o usuario do unit (o card E06 registrou como nao exercitado).
- **Guarda do `instalar-timers.sh` (harness isolado, `sudo`/`systemctl` dublados, nada tocado no sistema):** alvo do `ExecStart=` em 644 -> `ABORTADO: script de unit sem bit executavel — timer NAO habilitado`, **exit 1**, e o log de `systemctl` ficou **ausente** (o `enable` nunca foi chamado); alvo em 755 -> passo 4 aprovado e `RESULTADO: TIMERS_OK`, exit 0.
- **Estado dos timers:** `tre-backup.timer` `enabled` + `active` (proxima execucao 01/10 02:33 -03); a geracao diaria do artefato continua pendente do ACHADO ABERTO 2 (`t_1b2ab418`), nao deste defeito.
- **RODADA 2 (pos revisao que reprovou os criterios 2 e 3):** a instalacao acima foi **revertida** as 20:04:15Z/20:06:19Z pela publicacao de teste do card `t_091cfea9` com `commit=16c31f0` (arvore **anterior** a correcao) — prova em `/opt/tre/.publicacoes.log` (linhas de 20:02:46Z, 20:03:59Z e 20:06:19Z, `card=t_091cfea9-TESTE`); `digest_antes=787205e1…` da publicacao de 20:06:19Z e o digest do estado deixado pelo `install`. As medicoes `203/EXEC` do journal de **20:10:21Z e 20:11:59Z sao da revisao**, nao desta rodada.
- **Restauracao por caminho versionado (nao por install ad-hoc):** as 20:12:35Z o card `t_091cfea9` publicou por caminho versionado (`commit=f1f1cb6b…`, 306 arquivos, `execstart_sem_bit: 0`, `concorrencia: (nenhuma)`) e a copia voltou a `-rwxrwxr-x tre-deploy` nos 8 scripts; `/opt/tre/repo/.publicado` passou a registrar o commit publicado. Conteudo conferido por **sha256 8/8 identico** ao repositorio (`origin/develop` = `d2a2640`) e `git ls-tree -r` de `scripts/backup/` identico entre `origin/develop` e `f1f1cb6b` (mesmos blobs). Eu **nao** reinstalei manualmente — sobrepor a publicacao versionada seria repetir o padrao que causou o revert.
- **REMEDICAO COM HORARIO (20:13:36Z):** `sudo -u tre-deploy test -x /opt/tre/repo/scripts/backup/backup-tre.sh` -> **`TEST_X_EXIT=0`**; `systemctl start tre-backup.service` -> **`START_EXIT=0`**, `Result=success ExecMainStatus=0`, `ExecStart pid=230506 code=exited status=0`; journal de 17:13:36-03 (20:13:36Z) com `backup-tre.sh[230506]` nos tres blocos de ambiente, `PULADO` em dev/homolog/prod e `RESULTADO: BACKUP_OK (todos)`, `Deactivated successfully` — nenhum `203/EXEC` novo. `grep -c verificar-modos-executaveis /opt/tre/repo/scripts/backup/instalar-timers.sh` -> `1` (a guarda esta na copia publicada).
- **REPUBLICACAO IDEMPOTENTE (20:13:40Z):** o mesmo card publicou de novo o mesmo commit `f1f1cb6b…` (`digest_antes = digest = e4e1f05d…`) e os modos continuaram `755`; conferido depois: `test -x` exit 0, `sha256 backup-tre.sh` = `1a430637…` (identico ao repo), `Result=success ExecMainStatus=0` como ultima execucao. A publicacao versionada preserva modo **e** e idempotente.
- **O que NAO foi tocado:** o banco (`pg-sales-dev` intacto — o unico trabalho de escrita foi o container descartavel do proprio teste de restore, removido), `/opt/tre/backup` (nenhum artefato apagado ou criado por esta correcao), producao/homolog (nao provisionados), os units em `/etc/systemd/system` (nenhuma alteracao: os arquivos instalados sao os mesmos do repo) e segredos (nenhum valor em argumento, arquivo ou log).
## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E05-T01: suite de teste do banco (schema, constraints/indices, dedup e tenant/RLS)

- **Campos do card definidos ANTES de executar** (doc 11 §2): ACCEPTANCE CRITERIA (os 3 homologados por Anderson em 29/09/2026, com o teste positivo, o negativo e a fronteira de cada um), TEST PLAN, ROLLBACK PLAN, AFFECTED COMPONENTS e RISK LEVEL (medio — e a prova de isolamento entre clientes) registrados no card `t_c7281fce` antes da primeira medicao.
- **Sincronizacao da copia operacional (agente):** `tar -cz scripts docs db | ssh root@… 'tar -xz -C /opt/tre/repo && chown -R tre-deploy:tre-deploy …'` -> `SYNC_OK`, e sha256 **igual nos dois lados** (repo e `/opt/tre/repo`) para os artefatos sob teste (`suite_banco.sh f7cbcfa7…`, `teste_tenant_rls.sh 1f870a76…`) e para os reusados (`estado_do_ambiente.sh 7f9a12a5…`, `deduplicar_organizacoes.py e5805846…`, `verificar_contrato_dados.py dfb8ad79…`, `verificar_constraints_indices.py 68cc57cb…`). Antes dessa ressincronizacao o motor de dedup da copia operacional estava uma versao atras (`b009a8b9…`, sem as mudancas do E04-T02): a bateria foi **refeita** depois da ressincronizacao para que a evidencia venha do artefato atual.
- **Comando unico, exit code como resposta:** `bash /opt/tre/repo/scripts/db/suite_banco.sh dev` -> `RESULTADO: SUITE_FALHOU (85 itens, 1 falha(s), 1 nao testavel(is))`, **exit 1**; por etapa: ambiente OK, `contrato 37 itens`, `constraints 16 itens`, `dedup_sintetico 7 itens`, `dedup_ambiente 21 itens` (`CENARIO_OK`, com o estado voltando ao anterior), `tenant_rls NAO_TESTAVEL (2 itens)`. `--somente-leitura` -> `SUITE_FALHOU (65 itens, 1 falha(s), 1 nao testavel(is))`, exit 1, sem escrever no alvo (a etapa de dedup vira varredura `--detectar`).
- **AC1 provado com dente (container descartavel):** `suite_banco.sh --prova-de-dente` -> `RESULTADO: SUITE_DENTE_OK (14 itens, 0 falhas)`, **exit 0**: alvo integro -> exit 3; **coluna do contrato removida** (`DROP COLUMN organizations.cnpj`) -> exit 1 apontando `contrato`; **indice a mais** (`CREATE INDEX idx_intruso_suite`) -> exit 1 apontando `constraints`; cada divergencia desfeita -> volta ao veredito sem reprovacao (exit 3).
- **AC3 provado com dente (guarda do exit code):** na mesma prova, a suite foi rodada com a etapa sabotada: `TRE_SUITE_SABOTAGEM=sem-saida` -> exit 1 com `etapa terminou SEM linha RESULTADO`; `TRE_SUITE_SABOTAGEM=zero-itens` -> exit 1 com `etapa nao executou item nenhum (0 itens)`. "Sem output" nunca vira verde.
- **AC2 (tenant/RLS) — NAO TESTAVEL contra o Data Contract V1.0, medido:** `teste_tenant_rls.sh dev` -> `RESULTADO: TENANT_RLS_NAO_TESTAVEL (2 itens, 0 reprovacoes, 1 criterio(s) nao testavel(is))`, **exit 3**: 12 tabelas no schema com **0 coluna de cliente/tenant**, **RLS desabilitada nas 12**, **0 policy** em `pg_policies` e papel `sales_ai` com `superuser=true` e `bypassrls=true` (superuser contorna RLS por definicao). Sem dimensao de cliente, a consulta "sem filtro de tenant" nao e nem expressavel — o criterio nao tem o que provar, e a ausencia e o proprio risco.
- **Dente do teste de tenant/RLS (fixture multi-cliente descartavel, 2 clientes A/B + RLS + papel sem bypass):** `teste_tenant_rls.sh --prova-de-dente` -> `RESULTADO: TENANT_RLS_DENTE_OK (18 itens, 0 falhas)`, **exit 0**: alvo sem dimensao -> exit 3; alvo protegido -> exit 0; **policy permissiva `USING (true)`** -> exit 1 apontando vazamento; **`ALTER ROLE app_cliente BYPASSRLS`** -> exit 1 (`contorna RLS`); **`DISABLE ROW LEVEL SECURITY`** em tabela com coluna de cliente -> exit 1 apontando vazamento; cada mutacao desfeita volta a aprovar. O teste aceita os dois resultados que o criterio permite: sessao sem cliente -> **vazio** (`current_setting(..., true)` -> NULL -> nenhuma linha) e, na variante estrita, **erro** (`unrecognized configuration parameter`).
- **Guarda de ambiente (ADR-005):** `suite_banco.sh prod` -> `FALHOU ADR-005: a suite escreve no alvo (etapa 4) — em producao so com --somente-leitura`, exit 1; `suite_banco.sh prod --somente-leitura` nao foi executado (nao existe ambiente de producao provisionado). **Homologacao nao provisionada (medido):** `suite_banco.sh homolog` -> `FALHOU ambiente: alvo nao respondeu (… No such container: pg-homolog)`, exit 1 — falha visivel, nunca silenciosa. `docker ps -a` ao fim da bateria: so `pg-sales-dev`; `/opt/tre/prod` e `/opt/tre/homolog` com **0 arquivo**.
- **Estado do dev antes x depois:** `12 tabelas | 30 indices` nas duas pontas; a massa sintetica da etapa de dedup e limpa e conferida pela propria etapa (`ambiente volta ao estado anterior`).
- **DEFEITO ACHADO PELA SUITE (aberto como card de defeito, nao corrigido neste card):** a etapa de ambiente acusa que a migration registrada pelo runner em dev (`public.tre_schema_migrations.sha256 = bc766a818943…`) **diverge** do arquivo do repo (`0484a3701b8c…`). Causa medida: o commit `c795677` (decisao do dono, opcao 3 — trio canonico) acrescentou 7 linhas de **comentario** ao fim de `db/migrations/0001_sales_intelligence_v1.sql` **depois** de ele ter sido aplicado em dev (`aplicada_em 2026-09-30 17:29:22+00`); o diff entre os dois commits e so o bloco de comentario (nenhuma DDL muda). Impacto medido: `bash scripts/db/aplicar_migracoes.sh dev --somente-checar` -> `FALHOU versao 0001 … ja aplicada com sha256 bc766a818943… e o arquivo atual tem 0484a3701b8c… — migration aplicada e IMUTAVEL (BRANCHING.md). Nada aplicado.`, `RESULTADO: MIGRACAO_FALHOU`, exit 1 — o runner de dev esta **parado** para qualquer versao nova.
- **Defeitos do proprio roteiro de prova (achados pela prova de dente e corrigidos no card):** (i) desfazer `DROP COLUMN cnpj` recriava a coluna mas nao o indice `idx_organizations_cnpj`, que o PostgreSQL derruba junto com a coluna — a suite continuava reprovando depois da "restauracao" (o roteiro estava incompleto, o schema nao); (ii) a checagem da guarda de 0 item esperava a string `0 item`, que **nao** e prefixo de `0 itens`, entao a prova dizia que a guarda falhava quando ela estava funcionando. Corrigidos: a restauracao recria coluna **e** indice; a checagem casa a mensagem real. Primeira execucao: `SUITE_DENTE_FALHOU (12 itens, 3 falhas)`; segunda: `SUITE_DENTE_OK (14 itens, 0 falhas)`.
- **Relatorio de teste versionado:** `docs/testing/TRE-W1-E05-T01-relatorio-de-teste.md` (criterio x teste positivo/negativo/fronteira, tabela comando+saida+exit code, proveniencia das shas e o que NAO foi feito).
- **Verificadores do repo depois da mudanca:** `verificar_estrutura.sh` PASS (inclui os 3 artefatos novos do E05 versionados e executaveis); `secret_scan.sh` PASS; `verificar_papeis.sh` PASS; `verificar_contrato_dados.py` (arquivos) PASS (26 itens).
- **Concorrencia medida (mesmo checkout):** durante a rodada outro card (E06) commitou e empurrou no mesmo diretorio; o `pull --rebase --autostash` dele deixou os edits do E05 em `CHANGELOG.md` e `scripts/verificar_estrutura.sh` em conflito de stash-pop. Resolvido **mantendo os dois lados** (diff contra HEAD so com insercoes, 0 linhas removidas) e commitado pelo SHA no `develop`.
- **O que NAO foi tocado:** producao (nao provisionada), homologacao (`/opt/tre/homolog` sem arquivo, sem container), `db/migrations/0001_sales_intelligence_v1.sql` (sha `0484a370…` inalterado), Odoo, n8n, os scripts reusados (conferidos por sha256) e segredos (nenhum valor em argumento, arquivo ou log).

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E01-T01-D02: log da migracao por execucao (defeito F1 do runner)

- **Defeito de origem (achado pela revisao independente, nao pelo autor):** o card de revisao `t_2cc57d80` (revisao independente do `TRE-W1-E01-T01`) registrou o defeito `F1`: o runner `scripts/db/aplicar_migracoes.sh` gravava o log da aplicacao em caminho **FIXO** `/tmp/tre_migracao_<versao>.log` (linhas 227/236), deixava o arquivo no host e a execucao seguinte — de **outro** usuario — morria com diagnostico **VAZIO**. Card de correcao: `t_41d17c27`.
- **Estado ANTES (read-only, medido):** `pg-sales-dev` com `Id 396ace5637103224a550ab0b9687744e43ccdb32cb242ffc8e0dd6087a68a152` / `StartedAt 2026-09-30T17:05:15.574220813Z`; `estado_do_ambiente.sh dev` → `12 tabelas | 30 indices`; `public.tre_schema_migrations` → `0001 | bc766a818943fbe2b6d5d8b292bdd1bda6e4aaced13d71e0257bffb085d2d03e | 2026-09-30 17:29:22.843171+00 | tre-deploy@vmi3619453`; `sha256 /opt/tre/repo/scripts/db/aplicar_migracoes.sh` → `1eb97c92a321…` (runner anterior); `stat /tmp/tre_migracao_0001.log` → `-rw-rw-r-- tre-deploy tre-deploy 0` (a sobra do defeito); `sysctl fs.protected_regular` → `2`; `stat -c '%a %A %U' /tmp` → `1777 drwxrwxrwt root`; `docker ps -a` → so `pg-sales-dev`.
- **Correcao (arquivos):** `scripts/db/aplicar_migracoes.sh` — o log passa a viver em diretorio **por execucao** (`mktemp -d "${TMPDIR:-/tmp}/tre_migracao.XXXXXXXXXX"`), criado **antes** do `psql` e removido no fim, inclusive em falha (`trap … EXIT INT TERM HUP`); o destino **dentro** do container tambem e unico por execucao (`/tmp/tre_aplicar_$$_<arquivo>`); falha sem log agora diz explicitamente que a causa NAO esta no SQL; `TMPDIR` nao gravavel (ou diretorio de log nao gravavel) **recusa** com causa explicita, exit 1. `sha256` do runner: `1eb97c92a321…` → **`d0baf1e15fd6292e48ade158f1f08249ab0cb89a525f4cf48bb57e6c662671d5`**. Novo teste versionado `scripts/db/teste-log-migracao.sh` (`6a16b50ac7bbdb87a51a8384df8086348609f08cc8054b99d1dc3b6c4bf9e34f`, `100755`).
- **Evidencia de teste (container descartavel PROPRIO do teste, criado e removido por ele; nunca o dev real):** `TRE_D02_RUNNER_ANTIGO=/tmp/tre-d02-wt/runner_antigo.sh bash /opt/tre/repo/scripts/db/teste-log-migracao.sh /opt/tre/repo` → `RESULTADO: TESTE_OK (19 itens, 0 falhas)`, **exit 0**. O que a execucao mediu, item a item: (i) com `/tmp/tre_migracao_0001.log` VAZIO e de OUTRO dono, abrir o caminho da `Permission denied` — o cenario morde; (ii) o runner ATUAL, no mesmo cenario, `MIGRACAO_OK` exit 0 e o arquivo plantado **INTACTO** (conteudo, dono, tamanho e mtime conferidos); (iii) o runner ANTERIOR (`1eb97c92…`, extraido do `origin/develop`) no MESMO cenario → `FALHOU versao 0001 (0001_sales_intelligence_v1.sql) falhou: ` com a mensagem **VAZIA**, exit 1, e **fail-closed**: `0 tabela no schema e 0 linha de versao` (nenhum aceite falso); (iv) `TMPDIR` nao gravavel → `FALHOU nao consegui criar o diretorio de log por execucao …`, exit 1; (v) migration invalida → diagnostico EXPLICITO (`ERROR: syntax error at…`) e fail-closed (0 linha para a versao, nenhuma tabela criada por ela); (vi) duas execucoes **CONCORRENTES** no mesmo host → `MIGRACAO_OK` nas duas, **diretorios de log diferentes** (`/tmp/tre_migracao.…` != `/tmp/tre_migracao.…`), ambos removidos no fim. O mesmo teste ja havia passado a partir da area de trabalho (`/tmp/tre-d02-wt`) antes da instalacao.
- **Sincronizacao da copia operacional (agente):** `install -o tre-deploy -g tre-deploy -m 755` dos dois artefatos para `/opt/tre/repo` → `INSTALL_OK`; `sha256` **igual nos dois lados** (`aplicar_migracoes.sh d0baf1e1…`, `teste-log-migracao.sh 6a16b50a…`). A evidencia de teste acima foi colhida **depois** dessa instalacao, a partir do proprio `/opt/tre/repo`.
- **Guardrail (regressao, somente-leitura):** `bash /opt/tre/repo/scripts/db/aplicar_migracoes.sh dev --somente-checar` → `FALHOU versao 0001 … ja aplicada com sha256 bc766a818943… e o arquivo atual tem 0484a3701b8c… — migration aplicada e IMUTAVEL`, `RESULTADO: MIGRACAO_FALHOU`, exit 1. **E a divergencia PRE-EXISTENTE ja aberta pelo `TRE-W1-E05-T01` (entrada W1/E05 acima) — nao e deste card**; ela nasce do `sha` do ARQUIVO e nada foi aplicado. Depois da correcao o guardrail continua fail-closed, sem regressao.
- **Estado DEPOIS (read-only):** mesmo `Id`/`StartedAt` do container; `12 tabelas | 30 indices`; registro `0001 | bc766a818943… | 2026-09-30 17:29:22.843171+00` inalterado; `python3 scripts/verificar_contrato_dados.py --banco 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0; `docker ps -a` → so `pg-sales-dev` (nenhum `tre-d02-*` sobrou); **nenhum** diretorio `/tmp/tre_migracao.*` sobrou no host; `/tmp/tre_migracao_0001.log` devolvido ao estado anterior (`tre-deploy`, 0 bytes, mtime preservado); `/opt/tre/prod` e `/opt/tre/homolog` com `0` arquivo.
- **Verificadores do repo depois da mudanca:** `verificar_estrutura.sh` → `PASS (0 falhas)`; `secret_scan.sh` → `PASS (nenhum segredo versionado)`; `verificar_papeis.sh` → `PASS (0 falhas)`; `verificar_contrato_dados.py` (arquivos) → `PASS (26 itens, 0 falhas)`; `verificar_gate_jev.py` (por `uv`+PyYAML) → `PASS (30 itens, 0 falhas)`; `verificar_aprovacao_humana.py` → `PASS (18 itens, 0 falhas)`.
- **Verificacao independente:** a correcao **nao** e homologada por quem corrige — fica **pendente de verificacao independente** pelo perfil `tester` (card proprio, com o log bruto do teste e o runner anterior para o comparativo antes/depois).
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E05-T01 (rodada 2): AC2 na forma reformulada e defeito do rotulo do RESUMO

- **O que destravou a rodada:** os dois pre-requisitos do card fecharam — `t_39838c5b` (defeito D01: registro da migration 0001 realinhado ao sha do arquivo em dev) e `t_e340c29b` (requisito D02: decisao do dono, opcao A — isolamento FISICO, um banco por cliente; AC2 reformulado para "nao existem dois clientes no mesmo banco", registrado em `docs/data/DATA_CONTRACT_V1.md` e em `docs/operations/registro-de-aprovacoes.md`).
- **Publicacao do artefato testado (caminho versionado, destino isolado de ensaio — t_091cfea9, decisao 2):** `TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao-t_c7281fce deploy/publicar.sh --commit e74ec02 --card t_c7281fce` -> `PUBLICACAO_OK commit=e74ec02… digest=3b431df75535b5d42e81d237332866504fa78c9207c43fd71ddcd5c3683d187f arquivos=307`. **A copia operacional `/opt/tre/repo` NAO foi escrita** por esta rodada (foi apenas **conferida** ao fim, a partir de um checkout git local — o `--conferir` sai do git, nao da arvore de trabalho: `deploy/publicar.sh --conferir` -> `PUBLICACAO_OK commit=c7972ca… digest=89729f5d… arquivos=310`, exit 0). **Correcao de rota registrada:** a primeira bateria desta rodada usou `tar -cz … | ssh … 'tar -xz -C /opt/tre/repo'` (padrao da rodada 1) e isso sobrescreveu a arvore publicada — o card `t_1b2ab418` (DEFEITO F2) mediu o dano (`deploy/publicar.sh --conferir` -> `PUBLICACAO_DIVERGENTE`, exit 5; `scripts/backup/backup-tre.sh` de volta a versao pre-correcao, com a rotina de backup voltando a imprimir `BACKUP_OK` sem cobrir ambiente); o dano foi reparado pelo proprio caminho versionado (republicacao as 21:52:24Z). Aquela bateria foi **parada** e os resultados dela **nao foram usados**: a bateria final (itens 1–10 + estado final) rodou no destino de ensaio publicado. O runbook ganhou a secao 1.1 declarando que `tar` para a copia e proibido.
- **sha256 dos artefatos sob teste no destino de ensaio (conferidos tambem no repo local, iguais):** `suite_banco.sh 5fb644a2375e…`, `teste_isolamento_clientes.sh f3586c102e30…`, `teste_tenant_rls.sh 217bfd050a14…`, `suite-de-teste-do-banco.md 3bbdc1214fa7…`; reusados: `estado_do_ambiente.sh 7f9a12a50481…`, `deduplicar_organizacoes.py e58058469a06…`, `verificar_contrato_dados.py dfb8ad79…`, `verificar_constraints_indices.py 68cc57cb…`, `0001_sales_intelligence_v1.sql 0484a3701b8c…` (inalterado).
- **Comando unico, exit code como resposta (no destino de ensaio publicado):** `bash /opt/tre/.teste-publicacao-t_c7281fce/scripts/db/suite_banco.sh dev` -> `RESULTADO: SUITE_OK (89 itens, 0 falhas)`, **exit 0**; por etapa: ambiente OK (3 itens — identidade, estado do schema `12 tabelas | 30 indices` e sha da migration registrada igual ao repo), `contrato 37 itens`, `constraints 16 itens`, `dedup_sintetico 7 itens`, `dedup_ambiente 21 itens` (`CENARIO_OK`, estado restaurado), `isolamento 5 itens`. `--somente-leitura` -> `SUITE_OK (69 itens, 0 falhas)`, **exit 0**, sem escrever no alvo (a etapa de dedup vira varredura `--detectar`). `prod` -> `FALHOU ADR-005: a suite escreve no alvo (etapa 4)`, **exit 1** (recusado antes de tocar no alvo). `homolog` -> `FALHOU ambiente: alvo nao respondeu (… No such container: pg-homolog)`, **exit 1**.
- **AC2 (forma reformulada) medido no ambiente do card:** `bash scripts/db/teste_isolamento_clientes.sh dev` -> `RESULTADO: ISOLAMENTO_OK (5 itens, 0 falhas)`, **exit 0**: alvo responde; schema `sales_intelligence` com 12 tabelas; **0 coluna de cliente/tenant** (regex `(^|_)(tenant|cliente|client)(_id)?$` em 12 tabelas); **1 base de aplicacao na instancia** (`sales_intelligence`, sem templates); **1 base provisionada `pg-*` no host servindo o schema** (`pg-sales-dev`). Com a medicao de provisionamento impossivel (`TRE_ISOLAMENTO_SEM_DOCKER=1`) -> `ISOLAMENTO_NAO_TESTAVEL (5 itens, 0 reprovacoes, 1 item nao medido)`, **exit 3** — nunca verde.
- **Prova de dente do AC2 (containers descartaveis; o caminho proibido tem de REPROVAR):** `bash scripts/db/teste_isolamento_clientes.sh --prova-de-dente` -> `RESULTADO: ISOLAMENTO_DENTE_OK (17 itens, 0 falhas)`, **exit 0**: base integra -> exit 0; **dimensao de cliente com linhas de 2 clientes** -> **exit 1** (`dimensao de cliente no schema`); **segunda base de aplicacao na mesma instancia** -> **exit 1**; **segundo servico `pg-*` com o schema no host** -> **exit 1**; **docker ausente** -> exit 3. Cada mutacao desfeita volta a `ISOLAMENTO_OK` (exit 0). Containers removidos pelo proprio teste.
- **DEFEITO CORRIGIDO — rotulo do RESUMO mentia (achado pelo card de D01):** a etapa `ambiente` reprovava e o resumo imprimia `ambiente ............. OK` (linha era texto fixo no script). Agora a linha sai do veredito CONTADO e entrou a guarda de consistencia do resumo (nº de linhas `FALHOU` no resumo >= nº de etapas reprovadas; desvio reprova a suite e imprime `resumo ............... INCONSISTENTE`). **Prova negativa:** `bash scripts/db/suite_banco.sh --prova-de-dente` -> `RESULTADO: SUITE_DENTE_OK (19 itens, 0 falhas)`, **exit 0**, com o alvo descartavel de registro divergido -> suite **exit 1**, `FALHOU ambiente: migration do alvo … DIVERGE`, resumo com `ambiente ... FALHOU` e **sem** linha `ambiente ... OK`; registro removido -> suite volta a `SUITE_OK` (exit 0).
- **AC1/AC3 (dente):** na mesma prova, alvo integro -> exit 0 (`SUITE_OK`); `DROP COLUMN organizations.cnpj` -> exit 1 apontando `contrato`; `CREATE INDEX idx_intruso_suite` -> exit 1 apontando `constraints`; cada divergencia desfeita -> volta ao verde; `TRE_SUITE_SABOTAGEM=sem-saida` -> exit 1 (`etapa terminou SEM linha RESULTADO`); `TRE_SUITE_SABOTAGEM=zero-itens` -> exit 1 (`nao executou item nenhum`).
- **Instrumento do V2 fora da suite:** `bash scripts/db/teste_tenant_rls.sh dev` -> `TENANT_RLS_NAO_TESTAVEL (2 itens, 0 reprovacoes, 1 criterio nao testavel)`, **exit 3** — o script mede a forma ANTIGA do criterio e fica versionado para o dia em que houver multi-cliente no mesmo banco (nao e mais chamado pela suite).
- **Runner de migrations em dev (D01 fechado, medido nesta rodada):** `bash scripts/db/aplicar_migracoes.sh dev --somente-checar` -> `MIGRACAO_OK (--somente-checar; 0 aplicada(s)/pendente(s), 1 pulada(s), 4 itens, 0 falhas)`, **exit 0**.
- **Estado DEPOIS (read-only):** `12 tabelas | 30 indices`; `psql \dt` com as 12 tabelas do contrato; registro `0001 | 0484a3701b8c… | 2026-09-30 17:29:22+00`; `docker ps -a` -> so `pg-sales-dev` (nenhum descartavel orfao); `/opt/tre/prod` e `/opt/tre/homolog` com `0` arquivo.
- **Logs brutos na VPS:** `/tmp/e05r2_bateria.log` (itens 1–8 + estado final, com `### EXIT=` por comando), `/tmp/e05r2_suite_dente.log` (dente da suite), `/tmp/e05r2_isolamento_dente.log` (dente do isolamento).
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de teste/analise (estagio 6); a homologacao (estagio 7) e do Anderson, com esta evidencia na mao.
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E05-T01 (rodada 3): os 3 itens da revisao independente, corrigidos e medidos

- **O que disparou a rodada:** a revisao independente do E05 **reproduziu** a bateria (`SUITE_OK (89)`, `ISOLAMENTO_OK (5)`, os dentes e a guarda ADR-005), deu **AC1/AC2/AC3 = PASS** e **reprovou 3 itens** de texto x medicao: (1) a regua `docs/kanban/criterios-de-aceitacao.md` ainda trazia o AC2 na forma antiga, sem nota; (2) o item 3 de `teste_isolamento_clientes.sh` falhava ABERTO (leitura de catalogo vazia lida como "0 coluna") e o detector so pegava nomes terminando em `tenant|cliente|client`, case-sensitive; (3) o texto do item 5 (provisionamento) afirmava mais do que a medicao por convencao de nome `pg-*` cobria.
- **Commit da correcao:** `git push` do `21ed325` para `origin/develop` (4 arquivos: `scripts/db/teste_isolamento_clientes.sh`, `scripts/db/teste_tenant_rls.sh`, `docs/runbooks/suite-de-teste-do-banco.md`, `docs/kanban/criterios-de-aceitacao.md`). `git diff e74ec02 21ed325 -- scripts/` mostra que **nenhum** script de dedup/backup foi tocado por este card (a diferenca de 47 para 48 itens no dedup sintetico vem do `7a6a270`, TRE-W1-E04-T02).
- **Prova do defeito e da correcao, lado a lado (container descartavel `e05r3-probe`, `postgres:16`, migration congelada aplicada, medicao por `--prefixo`; nada em dev):**
  - co-locacao `ALTER TABLE sales_intelligence.organizations ADD COLUMN tenant_uuid uuid` -> artefato da rodada 2 (`f3586c102e30…`): `-- dimensao de cliente/tenant no schema: 0 ((nenhuma))` / `RESULTADO: ISOLAMENTO_OK (5 itens, 0 falhas)`, **exit 0 (verde falso)**; artefato desta rodada (`a69e08d1779c…`): `FALHOU dimensao de cliente no schema: 1 coluna(s) … (organizations.tenant_uuid )` / `ISOLAMENTO_FALHOU`, **exit 1**. Mutacao desfeita -> `ISOLAMENTO_OK`, exit 0.
  - co-locacao com grafia mista `ALTER TABLE sales_intelligence.contacts ADD COLUMN "conta_Cliente" uuid` -> rodada 2: `ISOLAMENTO_OK`, **exit 0 (verde falso)**; rodada 3: `FALHOU … (contacts.conta_Cliente )`, **exit 1**.
  - **catalogo ilegivel** (o alvo responde, mas a leitura de `information_schema.columns` falha — wrapper de prova por `--prefixo`, que responde todo o resto) -> rodada 2: `ISOLAMENTO_OK (5 itens)`, **exit 0 (verde falso)**; rodada 3: `NAO_TESTAVEL nao consegui medir a dimensao de cliente no schema (leitura vazia/erro NAO e '0 coluna')` / `ISOLAMENTO_NAO_TESTAVEL (5 itens, 0 reprovacoes, 1 item nao medido)`, **exit 3 (nunca verde)**, com o item 4 tambem fail-closed (leitura que falha REPROVA, nao vira "0 bases").
  - **dente do AC2:** rodada 2 -> `ISOLAMENTO_DENTE_OK (17 itens)` (nenhum caso cobria `tenant_uuid`/grafia mista/catalogo mudo); rodada 3 -> `ISOLAMENTO_DENTE_OK (26 itens, 0 falhas)`, **exit 0**, com os novos: grafia `tenant_uuid` -> exit 1, grafia mista `conta_Cliente` -> exit 1, catalogo ilegivel -> exit 3, cada mutacao desfeita volta a `ISOLAMENTO_OK`.
  - **sem falso positivo no contrato:** `SELECT count(*)` com a regex nova (`~*`, token em qualquer posicao) na base dev -> **0** (igual a regex antiga); as 203 colunas do schema foram conferidas.
  - **limite do item 5 declarado e visivel:** em vez de esconder a convencao de nome, a saida ganhou a linha informativa com os containers de pe FORA de `pg-*` que servem o schema. Medido: no dente, `e05r3-probe` (fora da convencao, servindo o schema) aparece nessa linha e o item permanece `OK`; em dev, a linha saiu `nenhum`. O texto do veredito passou a dizer exatamente o que a medicao cobre; runbook 8 atualizado (superficie do detector, o que fica fora — coluna de cliente com outra grafia e pega pela **etapa 1** — e limite de provisionamento).
- **Publicacao do artefato testado (caminho versionado, destino isolado de ensaio — t_091cfea9, decisao 2):** `TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao-t_c7281fce deploy/publicar.sh --commit 21ed325 --card t_c7281fce` -> `PUBLICACAO_OK commit=21ed325… digest=c35ecba3457ffc1c396c1c68b407d576ce360e9db471388f2baff0e7308ed0ba arquivos=307` (antes: copia identica ao commit `e74ec02…` registrado — `digest_antes=3b431df7…`). **A copia operacional `/opt/tre/repo` NAO foi escrita** nesta rodada.
- **sha256 dos artefatos sob teste no destino publicado:** `teste_isolamento_clientes.sh a69e08d1779c…`, `teste_tenant_rls.sh d4ade21185c0…`, `suite_banco.sh 5fb644a2375e…` (nao alterado nesta rodada), `estado_do_ambiente.sh 7f9a12a50481…`, `aplicar_migracoes.sh d0baf1e15fd6…`; reusados: `verificar_contrato_dados.py dfb8ad79…`, `verificar_constraints_indices.py 68cc57cb…`, `0001_sales_intelligence_v1.sql 0484a3701b8c…` (inalterado).
- **Bateria (11 itens, com `### EXIT=` por comando; nada "passou" sem comando, saida e exit code):** `suite_banco.sh dev` -> `SUITE_OK (89 itens, 0 falhas)`, **exit 0**; `--somente-leitura` -> `SUITE_OK (69 itens)`, **exit 0**; `prod` -> `FALHOU ADR-005`, **exit 1**; `homolog` -> `FALHOU ambiente` (`pg-homolog` inexistente), **exit 1**; `teste_isolamento_clientes.sh dev` -> `ISOLAMENTO_OK (5 itens, 0 falhas)`, **exit 0**; `TRE_ISOLAMENTO_SEM_DOCKER=1` -> `ISOLAMENTO_NAO_TESTAVEL`, **exit 3**; `teste_tenant_rls.sh dev` -> `TENANT_RLS_NAO_TESTAVEL (2 itens)`, **exit 3**; `aplicar_migracoes.sh dev --somente-checar` -> `MIGRACAO_OK (4 itens, 0 falhas)`, **exit 0**; `suite_banco.sh --prova-de-dente` -> `SUITE_DENTE_OK (19 itens, 0 falhas)`, **exit 0**; `teste_isolamento_clientes.sh --prova-de-dente` -> `ISOLAMENTO_DENTE_OK (26 itens, 0 falhas)`, **exit 0**; `teste_tenant_rls.sh --prova-de-dente` -> `TENANT_RLS_DENTE_OK (18 itens, 0 falhas)`, **exit 0** (regex nova, mesmo comportamento).
- **Item 4 fail-closed — a outra ponta do par, medida com envelope (read-only no dev real, 22:48Z; re-executada em 23:24Z pelo follow-up `t_8253ad1f`):** as duas guardas de leitura do teste de isolamento sao **de proposito** diferentes — leitura do **catalogo de colunas** que falha -> item 3 `NAO_TESTAVEL` (**exit 3**, nunca verde); leitura de **`pg_database`** que falha -> item 4 **REPROVA** (**exit 1**, nunca vira "0 bases"). Comando, no destino publicado (commit `58ec9fb`, `teste_isolamento_clientes.sh a69e08d1…`; envelope `psql_bases_mudo.sh be0cec01…`, que responde todo o resto pelo dev e falha so a consulta a `pg_database`): `bash scripts/db/teste_isolamento_clientes.sh dev --prefixo "bash /tmp/e05r3b/psql_bases_mudo.sh"` -> `-- bases de aplicacao na instancia: 0 (nenhuma)` / `FALHOU nao consegui medir as bases de aplicacao da instancia (leitura do catalogo falhou) — sem medicao o criterio nao pode ser dado como cumprido` / `RESULTADO: ISOLAMENTO_FALHOU (5 itens, 1 falha(s))`, **exit 1** (os itens 3 e 5 do mesmo alvo sairam `OK`). E o par da guarda **(e)** do dente do AC2 e era a unica afirmacao do relatorio §10 que ainda nao tinha medição versionada; log bruto `a_item4_envelope.log` (anexo do card `t_8253ad1f`).
- **Estado DEPOIS (read-only):** `12 tabelas | 30 indices`; registro `0001 | 0484a3701b8c…` == arquivo do repo; contagens `organizations=2 / contacts=1 / interactions=1`; `/opt/tre/prod` e `/opt/tre/homolog` com `0` arquivo; `docker ps -a` com `pg-sales-dev` e — de **outro card** rodando em paralelo (teste de backup/restore, E06) — `tre-restore-637931-27223`, que ja havia sido removida quando fui inspeciona-la (caso concreto do limite declarado do item 5); meus descartaveis `tre-isolamento-*` foram removidos pelos proprios testes.
- **Logs brutos na VPS:** `/tmp/e05r3b/bateria_r3.log` (11 itens + estado final, com `### EXIT=` por comando) e `/tmp/e05r3b/prova_r3.log` (rodada 2 x rodada 3, lado a lado, no mesmo alvo mutado). Copias anexadas ao card (conferidas byte a byte).
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de teste/analise (estagio 6); a homologacao (estagio 7) e do Anderson, com esta evidencia na mao.
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E05-T01 (follow-up pos-revisao, card `t_8253ad1f`): precisao documental — o exemplo do runbook §8, medido

- **O que este card e:** follow-up **docs-only** do veredito APROVADO da revisao independente da rodada 3 (`t_c7281fce`); os sha256 sob teste continuam os do veredito (`suite_banco.sh 5fb644a2…`, `teste_isolamento_clientes.sh a69e08d1…`, `teste_tenant_rls.sh d4ade211…`) — **nenhum byte de script mudou** (o diff deste card toca so `.md`). Artefato medido: destino publicado `/opt/tre/.teste-publicacao-t_c7281fce`, commit `58ec9fb1b3ef89ddfe87f008f0413227cabc821f` (`publicado_em 2026-09-30T22:46:17Z`, `digest 36ed9261b8258ff7c66519f3969eca80cb2362378c8fc869bf521d4ad70b8027`).
- **A medicao do item 4 (fail-closed) entra no registro versionado** — esta na entrada da rodada 3 (bullet "Item 4 fail-closed — a outra ponta do par"), com comando, saida e exit code, re-executada as 23:24Z no mesmo artefato publicado (`ISOLAMENTO_FALHOU (5 itens, 1 falha(s))`, **exit 1**); log bruto `a_item4_envelope.log`. Ate este card, a medicao existia so no fio de comentarios do board.
- **O exemplo do runbook §8 (grafia FORA da superficie do item 3) foi medido:** o texto citava `organizations.tenant_uuid`, que com a regex nova (`~*`, token em qualquer posicao) **casa** a superficie do item 3 — deixou de ilustrar "fora da superficie". Caso medido em alvo **descartavel** proprio (`e05r4-probe`, `postgres:16`, migration congelada do repo aplicada): `ALTER TABLE sales_intelligence.organizations ADD COLUMN customer_id text` (grafia que a regex nao casa — conferido nome a nome pela revisao) e `TRE_PG_SERVICO=e05r4-probe TRE_PG_USER=tre TRE_PG_DB=sales_intelligence bash scripts/db/suite_banco.sh dev` -> `FALHOU as colunas do banco sao exatamente as do contrato (nem sobra, nem falta)  -> faltam=[] sobram=['organizations.customer_id']`; `FALHOU contrato: RESULTADO: FALHOU (1 de 37 itens) (exit 1)`; `RESULTADO: SUITE_FALHOU (88 itens, 1 falha(s), 0 nao testavel(is))`, **exit 1**. Quem reprova e a **etapa 1** (`contrato`); no mesmo alvo mutado o item 3 saiu `ISOLAMENTO_OK (5 itens, 0 falhas)`, **exit 0** — o limite declarado, medido.
  - **Controles do roteiro (mesmo container):** alvo integro (antes da mutacao) -> `SUITE_OK (88 itens, 0 falhas)`, **exit 0**; mutacao desfeita -> `SUITE_OK (88 itens, 0 falhas)`, **exit 0**. Sem isso, o exit 1 poderia ser do alvo e nao da divergencia.
- **Estado do dev real:** nao foi mutado em momento nenhum — a mutacao viveu so no container descartavel (removido pelo proprio roteiro) e a medicao do item 4 e read-only no dev. `docker ps -a` ao fim: so `pg-sales-dev`.
- **Logs brutos:** `a_item4_envelope.log`, `b0_integro.log`, `b2_suite_customer_id.log`, `b3_isolamento_customer_id.log`, `b4_volta_verde.log` — anexos do card `t_8253ad1f`.
- **Verificacao independente:** quem entrega nao homologa; a homologacao (estagio 7) e do Anderson.
- Segredos: nenhum valor nesta entrada; conexao pelo socket local do container, sem senha em argumento, arquivo ou log.

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E01-T01 (card `t_d6dc5a4c`): Odoo Community 19.0 instalado no **dev**

- **Campos do card definidos ANTES de executar** (doc 11 §2): ACCEPTANCE CRITERIA (os 4 homologados por Anderson em 29/09/2026), TEST PLAN, ROLLBACK PLAN, AFFECTED COMPONENTS e RISK LEVEL (alto) registrados em comentario no card `t_d6dc5a4c` **antes da primeira medicao**, junto com a decisao de deploy (versao/portas/onde hospedar).
- **Autorizacao:** declaracao de acao do dono para este card (`hermes/jev/acoes-declaradas.yaml`, 01/10/2026, `ambiente_alvo: desenvolvimento`, `producao=false`, `credencial=false`) sob a onda dev homologada em 30/09/2026 (validade 07/10/2026); recibo JEV `dec-c6650746cbb56e76` = PASS. Nada em producao.
- **Guardas medidos (VPS, antes de criar qualquer coisa):** docker `29.8.1` + compose `5.5.1`; `docker ps -a` so com `pg-sales-dev` (`Up 20 hours`); `/opt/tre/{homolog,prod}` com **0 arquivo**; UFW ativo com **so 22/tcp**; `ss -lnt` sem nada em 8069.
- **Transferencia do par + scripts (agente):** `cat > …` por SSH do repo para a VPS em `/opt/tre/dev/compose/{odoo.yml,odoo.env}` e `/opt/tre/dev/scripts/{instalar,verificar,remover}-odoo-dev.sh` -> **sha256 igual nos dois lados** nos 5 arquivos: `odoo.yml e4f2632d695e…`, `odoo.env 86206a6a0037…`, `verificar-odoo-dev.sh 7db09de011c9…`, `remover-odoo-dev.sh 241694fd3285…`. O `instalar-odoo-dev.sh` foi editado duas vezes depois da primeira transferencia (defeitos 4 e 5 abaixo) e o sha final, medido igual nos dois lados ao fim: `instalar-odoo-dev.sh b6b483e61b53…`.
- **Instalacao (agente, na VPS):** `bash /opt/tre/dev/scripts/instalar-odoo-dev.sh` -> `OK guardas`, `OK segredos gerados /etc/tre/odoo-dev/{pg.env,odoo.conf} (600)`, `OK compose valido … (versao 19.0, porta 127.0.0.1:8069)`, `OK pg-odoo-dev healthy (postgres:16)`, `OK banco odoo_dev inicializado (195 linhas de log; sem demo)`, `RESULTADO: ODOO_DEV_INSTALADO versao=19.0 porta=127.0.0.1:8069 http=200`, exit 0. Evidencia: imagem `odoo:19.0` / digest `odoo@sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (confere com o par), versao interna `Odoo Server 19.0-20260926`, `pg-odoo-dev=id=c7cb12f75eb9…`, `odoo-dev=id=12cf65a3c1c6…`, **iniciado_em 2026-10-01T12:42:30.380559934Z**.
- **Aceite (agente, na VPS):** `bash /opt/tre/dev/scripts/verificar-odoo-dev.sh` -> `RESULTADO: ODOO_DEV_OK (19 itens, 0 falhas) versao=19.0 porta=127.0.0.1:8069`, **exit 0**. Itens: compose valido e imagem declarada; os dois containers de pe com `restart=unless-stopped`; imagem do container == tag local == digest registrado; `HTTP 200` em `127.0.0.1:8069/web/login` (5535 bytes, pagina do Odoo) e binario respondendo `19.0-20260926`; banco `odoo_dev` presente e **separacao medida nos dois lados** (o Postgres do Odoo nao tem `sales_intelligence`; o `pg-sales-dev` nao tem `odoo_dev`; volumes distintos); nenhuma porta publica (`odoo-dev` so em `8069/tcp -> 127.0.0.1:8069`, `pg-odoo-dev` sem porta publicada, `ss` com `127.0.0.1:8069`, UFW com regras `[22/tcp]`).
- **Dentes do aceite (provas negativas medidas, na VPS):** (a) com um `docker` falso no PATH respondendo `docker port odoo-dev` = `8069/tcp -> 0.0.0.0:8069` -> `FALHOU odoo-dev publica endereco publico` e `RESULTADO: ODOO_DEV_FALHOU (19 itens, 1 falha(s))`, **exit 1** (as demais 18 seguem OK — o item reprova por comportamento, nao por cascata); (b) `remover-odoo-dev.sh` sem `TRE_ODOO_CONFIRMAR_REMOCAO=1` -> `FALHOU remocao exige confirmacao explicita`, **exit 1**, nada tocado; (c) verificador rodado **depois** do rollback -> `RESULTADO: ODOO_DEV_FALHOU (19 itens, 13 falha(s))`, **exit 1**.
- **Rollback executado (agente, na VPS) e reinstalacao limpa:** `TRE_ODOO_CONFIRMAR_REMOCAO=1 bash /opt/tre/dev/scripts/remover-odoo-dev.sh` -> `RESULTADO: ODOO_DEV_REMOVIDO`, exit 0; removidos `odoo-dev`, `pg-odoo-dev`, rede `tre-odoo-dev`, volumes `pgdata-odoo-dev` e `odoo-data-dev` e `/etc/tre/odoo-dev`; **`pg-sales-dev` intocado** (`integrity: running` no DEPOIS). Em seguida `bash instalar-odoo-dev.sh` do **zero** (segredos novos, volumes novos, banco inicializado com 195 linhas de log, `odoo-dev` novo id `12cf65a3c1c6…`) -> exit 0, e o aceite voltou a `ODOO_DEV_OK (19 itens, 0 falhas)`, exit 0.
- **Defeitos encontrados e consertados nesta execucao (5):** (1) o `POSTGRES_DB=odoo_dev` do `postgres:16` cria o banco **vazio** e o check por `pg_database` pulou a inicializacao -> o Odoo respondia **HTTP 500** em `/web/login` (medido: `FALHOU Odoo nao respondeu 200 … (ultimo codigo: 500)`); conserto: o que prova inicializacao e a tabela `ir_module_module`; (2) `docker compose run` **consome o stdin** de quem o executa e, orquestrado por `ssh 'bash -s' < script`, matou o script remoto no meio (a rodada 2 parou depois do `OK banco odoo_dev inicializado`, exit 0 sem subir o servico) — a MESMA armadilha ja registrada neste repositorio; conserto: `-T` + `< /dev/null` no `compose run` e execucao por arquivo na VPS; (3) o item 6 do proprio verificador reprovava o **formato** real do `docker port` em vez do comportamento — conserto com a prova por mutacao (a) acima; (4) o script de instalacao reprovava o proprio `secret_scan.sh` do repo por escrever a chave `db_password` na forma literal `"<chave> = <variavel>"` (falso positivo) — conserto no codigo (nome da chave por variavel + `awk` na leitura), **nao** no scanner, e `secret_scan.sh` -> `PASS (nenhum segredo versionado)`; (5) a guarda de "porta em uso" reprovava a **reexecucao idempotente** (o proprio `odoo-dev` de pe segurava a 8069) — conserto: a guarda so vale quando o container `odoo-dev` ainda nao existe.
- **Reexecucao idempotente com o script final (agente, na VPS):** `TRE_ODOO_RECRIAR=1 bash instalar-odoo-dev.sh` -> `OK segredos: reaproveitando …`, `OK banco odoo_dev ja inicializado pelo Odoo — inicializacao sera pulada`, `OK digest confere com o par`, `RESULTADO: ODOO_DEV_INSTALADO … http=200`, **exit 0**, com o `odoo-dev` **no mesmo id e no mesmo `iniciado_em 12:42:30.380559934Z`** (nada foi recriado) e o aceite de novo em `ODOO_DEV_OK (19 itens, 0 falhas)`, exit 0. Sem `TRE_ODOO_RECRIAR=1` o script **recusa** (`FALHOU container 'odoo-dev' JA EXISTE — nao mexo nele`, exit 1) — medido.
- **Auditoria de segredo no artefato:** nenhum valor de senha em arquivo do repo (as senhas nascem na VPS, em `/etc/tre/odoo-dev/`, 600); o `verificar_estrutura.sh` ganhou item que reprova par de ambiente com valor de senha. Logs desta execucao nao carregam senha (o verificador le o par sem imprimir valor).
- **O que NAO foi tocado:** `pg-sales-dev` e o banco `sales_intelligence` (medido antes/depois), a UFW (regras `[22/tcp]`), `/opt/tre/{homolog,prod}` (0 arquivo, 0 container), os scripts e o compose de outros cards, e a copia operacional `/opt/tre/repo` (**nenhuma escrita ad-hoc**: o dev do Odoo roda do par em `/opt/tre/dev/compose/`, e o motivo esta no runbook §2 — a copia esta numa linha divergente do `develop`).
- **Logs brutos (agente):** `instalar-odoo-dev-3.out`, `aceite-1.out` (aceite 19/19 + mutacao da porta), `rollback-1.out`, `rollback-e-reinstalacao.out` (recusa do rollback + verificador pos-rollback + reinstalacao + aceite final), no scratch do perfil `devops`.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de revisao/teste (estagio 6); a homologacao (estagio 7) e do Anderson, com esta evidencia na mao. A **ratificacao da versao escolhida** (19.0) tambem e dele, antes de homologacao/producao.
- Segredos: nenhum valor nesta entrada.

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E03-T01 (card `t_c536ce86`): modulo Odoo `transformativa_sales_ai` (base do Sales AI)

- **Campos do card definidos e registrados ANTES da primeira medicao** (doc 11 §2): ACCEPTANCE CRITERIA (os 3 homologados por Anderson em 29/09/2026), TEST PLAN (os 4 passos), ROLLBACK PLAN (desinstalar o modulo), AFFECTED COMPONENTS, RISK LEVEL (medio) e as 7 decisoes de implementacao (D1..D7) na **runbook §1** e no fio do card. Nada em producao (ADR-005); a execucao inteira e em dev.
- **Transferencia (agente):** modulo `odoo/addons/transformativa_sales_ai` -> `/opt/tre/dev/modulos/transformativa_sales_ai` e `scripts/odoo/*` -> `/opt/tre/dev/scripts/odoo/` por `tar` sobre `ssh` (sem scp): **sha256 igual nos dois lados** nos 6 arquivos do modulo (`__manifest__.py cd84f4ec…`, `__init__.py 04812263…`, `tests/__init__.py 6c4150e5…`, `tests/test_modulo_base.py ea75d283…`, `README.md abaa206b…`, `.gitkeep e3b0c442…`) e nos 3 scripts (`verificar-modulo-odoo.sh 72d00aa1…`, `manifesto_do_modulo.py f314948c…`, `desinstalar_modulo.py b859b3c6…`).
- **Bateria reexecutada com o script FINAL depois de cada edicao do verificador** (`/opt/tre/dev/scripts/odoo/bateria-e03t01.sh`, sha256 `f4aefd57…`, so na VPS — e orquestracao, nao artefato do repo): a ultima rodada, com `verificar-modulo-odoo.sh` em `72d00aa1…`, deu `--apenas-manifesto` -> `MODULO_ODOO_OK (19 itens, 0 falhas)` **exit 0**, `--prova-de-dente` -> `MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)` **exit 0** e aceite completo -> `MODULO_ODOO_OK (51 itens, 0 falhas)` **exit 0**.
- **Aceite (agente, na VPS, a partir de ARQUIVO — nunca por stdin):** `TRE_LOG_DIR=/opt/tre/dev/evidencias/t_c536ce86/logs bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh` -> `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e03_t01_modulo imagens=odoo:19.0+postgres:16`, **exit 0**. Medido: **passo 1** banco `tre_e03_t01_modulo` nao existia e foi criado do zero pelo Odoo (`odoo --init exit 0`, 554 linhas de log, **0 ERROR/CRITICAL**, `'Modules loaded.'`, `state=installed`, `latest_version=19.0.1.0.0`, `license=LGPL-3`, dependencias gravadas `base crm` == declaradas e todas instaladas, 0 modulo pendurado); **passo 2** `odoo -u transformativa_sales_ai --test-enable exit 0` com `odoo.tests.result: 0 failed, 0 error(s) of 6 tests when loading database 'tre_e03_t01_modulo'` (6 testes `TestModuloBase.test_01..test_06`), **0 linha `FAIL:`/`ERROR:`**; **passo 3** `odoo shell` + ORM -> `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled` (`state=uninstalled`, **0 resquicio** em `ir_model_data`/`ir_ui_view`/`ir_model_fields` e 0 tabela com prefixo); **passo 4** `odoo --init` de novo **exit 0**, 0 ERROR/CRITICAL, `state=installed` com `latest_version=19.0.1.0.0`.
- **Identidade do alvo medido:** `odoo:19.0` digest `odoo@sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (o mesmo do par de dev) e `postgres:16` digest `postgres@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`; rede, postgres e o diretorio de configuracao **descartaveis** criados nesta execucao (senha gerada com `openssl rand -hex 24`, arquivo 600 dono uid 100 — nunca em argumento, log ou artefato) e removidos no fim.
- **Provas de dente (agente, na VPS):** `bash verificar-modulo-odoo.sh --prova-de-dente` -> `RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`, **exit 0**. Dente 1 (copia do modulo com `version` mutada para `18.0.1.0.0`) -> `FALHOU versao do manifesto (18.0.1.0.0) diferente da esperada (19.0.1.0.0)`, `FALHOU serie da versao (18.0) != serie do Odoo na imagem (19.0)`, `FALHOU serie da versao (18.0) diferente da esperada (19.0)` e `RESULTADO: MODULO_ODOO_FALHOU (19 itens, 3 falha(s))`, **exit 1**. Dente 2 (copia com teste que falha de proposito) -> `FALHOU odoo --test-enable exit 1`, `FALHOU runner do Odoo: 1 failed, 0 error(s) of 7 tests`, log com `Starting TestModuloBase.test_99_prova_de_dente` + `FAIL:` e `RESULTADO: MODULO_ODOO_FALHOU (36 itens, 2 falha(s))`, **exit 1**.
- **Bateria completa (3 alvos, com `EXIT=` por comando — nada "passou" sem comando, saida e exit code):** `--apenas-manifesto` -> `MODULO_ODOO_OK (19 itens, 0 falhas)`, **exit 0**; `--prova-de-dente` -> `MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`, **exit 0**; aceite completo -> `MODULO_ODOO_OK (51 itens, 0 falhas)`, **exit 0**.
- **Defeitos encontrados e consertados nesta execucao (6, todos achados executando e cada conserto remedido):** (1) o nome tecnico do modulo saia sempre errado porque o manifesto era lido com o modulo montado em `/modulo` (o item acusava `diretorio (modulo) diferente do modulo`) — conserto: montar em `/leitura/<nome-real-do-modulo>`; (2) o Odoo do dev **abre sessao em qualquer banco novo da instancia `pg-odoo-dev`**: medido com banco probe `tre_probe_cron_<pid>` criado do zero -> sessao de `client_addr 172.18.0.3` (= IP do `odoo-dev`), `application_name=odoo-1`, `state=idle`, em ~30s, sem ninguem pedir — e o `dropdb` do banco de teste morreu com `database "tre_e03_t01_modulo" is being accessed by other users / There are 3 other sessions`; conserto: o aceite passou a rodar numa **dupla descartavel propria** (postgres + odoo, imagens do par) e a limpeza usa `dropdb --force`; (3) o item "dependencias gravadas == declaradas" rodava `select name … join ir_module_module` -> `ERROR: column reference "name" is ambiguous` (com o `stderr` descartado no helper, o item **reprovava sempre** — falso negativo que bloquearia o aceite) — conserto: `select d.name`; (4) `ir_module_module_dependency.state` e campo **calculado** (nao tem coluna no banco) e o item "todas as dependencias instaladas" consultava `d.state` em SQL — conserto: conferir o estado no modulo dependente (`join ir_module_module dep on dep.name = d.name`); (5) o `scripts/secret_scan.sh` do repo reprovou o verificador por escrever a chave da senha na forma literal (`"<chave> = <variavel>"` — falso positivo, o valor e variavel) — conserto **no codigo, nao no scanner** (chave por variavel + `printf`), `secret_scan.sh` -> `PASS`; (6) **regressao da correcao (5), pega por reexecutar**: sobrou o `echo` antigo da chave mestra no bloco, o `odoo.conf` ficou com a opcao **duas vezes** e a instalacao morreu com `configparser.DuplicateOptionError: option 'admin_passwd' in section 'options' already exists` (`MODULO_ODOO_FALHOU (24 itens, 3 falhas)`, exit 1) — conserto: remover a linha antiga, e o aceite voltou a `MODULO_ODOO_OK (51 itens, 0 falhas)`, exit 0. Por isso a bateria inteira (manifesto + dentes + aceite) e reexecutada depois de **qualquer** edicao do verificador.
- **Estado DEPOIS / o que NAO foi tocado (medido pelo proprio verificador e por mim):** instancia do dev com **os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1` — nenhum banco do card nasce na instancia do dev); `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev` e `proxy-dev` de pe; `/opt/tre/{homolog,prod}` com **0 arquivo** antes e depois; **0 container, 0 rede e 0 diretorio temporario residual** da dupla descartavel; `/opt/tre/repo` (copia operacional) **sem nenhuma escrita**; `odoo_dev` intocado (o aceite **nao** instala o modulo nele — o AC pede banco **limpo**, e o `odoo_dev` carrega o funil do `TRE-W2-E02-T01`).
- **Logs brutos (agente, na VPS):** `/opt/tre/dev/evidencias/t_c536ce86/` — `verificacao-completa.out` (51 itens + `EXIT=0`), `manifesto.out` (`EXIT=0`), `prova-de-dente.out` (`EXIT=0`), `bateria.out`, `logs/{1-instalacao,2-teste,3-desinstalacao,4-reinstalacao}.log` (554/38/39/23 linhas, 0 `ERROR|CRITICAL` nos quatro) e `logs-dente/` (prova de dente, incluindo o log do teste mutado).
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de teste/revisao (estagio 6, perfil `tester`); a homologacao (estagio 7) e do Anderson, com esta evidencia na mao. A **publicacao do modulo na copia operacional** `/opt/tre/repo` (linha `fix/t_daca4bda-enforcement`, divergente do `develop`) fica declarada como pendencia de outro caminho — runbook §9.
- Segredos: nenhum valor nesta entrada (a senha da dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E05-T01 (card `t_9c91ecce`): modelo Odoo `tf.process.opportunity` (oportunidade canonica)

- **Campos do card definidos e registrados ANTES da primeira medicao** (doc 11 §2): ACCEPTANCE CRITERIA (os 3 homologados por Anderson em 29/09/2026), TEST PLAN (testes `post_install` + os 4 passos do aceite), ROLLBACK PLAN (desinstalar o modulo = passo 3 do aceite), AFFECTED COMPONENTS, RISK LEVEL (medio) e as decisoes D1..D10 na **runbook §1** (`docs/runbooks/odoo-oportunidade-canonica.md`) e **em comentario no fio do card**. Nada em producao (ADR-005); a execucao inteira e em dev.
- **Sonda read-only da imagem antes de escrever o modelo** (`odoo:19.0`, `19.0-20260926`): `crm.stage` tem `is_won`/`sequence`/`fold` (`crm_stage.py:26,27,32`), `crm.lost.reason` existe (`crm_lost_reason.py:8`), `crm.lead.won_status` e **calculado** a partir do estagio (`crm_lead.py:228-233,613-620`), `models.Constraint` e a API de constraint do Odoo 19 (194 arquivos do core a usam; `_sql_constraints` aparece em 1) e **`fields.Uuid` nao existe** nesta versao (identidade canonica em `Char(36)`). Sem essa medicao a escolha de API seria chute.
- **Transferencia (agente):** modulo `odoo/addons/transformativa_sales_ai` -> **`/opt/tre/dev/modulos-e05t01/transformativa_sales_ai`** (diretorio PROPRIO do card: E04-T01/T02 editam o mesmo modulo em paralelo no caminho compartilhado do E03) por `tar` sobre `ssh` (sem scp): **sha256 igual nos dois lados nos 9 arquivos** do modulo (`README.md eda86a9b…`, `__init__.py 711c51dd…`, `__manifest__.py 9827f894…`, `models/__init__.py 82db8c34…`, `models/tf_process_opportunity.py 2045dd0a…`, `tests/__init__.py 09f8f5e8…`, `tests/test_modulo_base.py ea75d283…` (inalterado do E03), `tests/test_oportunidade_canonica.py 56dc0865…`, `.gitkeep e3b0c442…`). Scripts do aceite na VPS = os do E03 (`verificar-modulo-odoo.sh 72d00aa1…`).
- **Aceite (agente, na VPS, a partir de ARQUIVO):** `bash /opt/tre/dev/baterias/bateria-e05t01.sh` (sha256 `eab932ef…`, so na VPS — orquestracao, nao artefato do repo) -> **`EXIT_BATERIA=0`**: `--apenas-manifesto` -> `MODULO_ODOO_OK (19 itens, 0 falhas)` **exit 0**; aceite completo -> `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e05_t01_oportunidade imagens=odoo:19.0+postgres:16` **exit 0**; `--prova-de-dente` (herdado do E03) -> `MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)` **exit 0**.
- **Medido no aceite:** banco `tre_e05_t01_oportunidade` **nao existia** e foi criado do zero pelo Odoo (`odoo --init exit 0`, **0 ERROR/CRITICAL**, `'Modules loaded.'`, `state=installed`, `latest_version=19.0.1.0.0`, `license=LGPL-3`, deps `base crm` == declaradas e instaladas, 0 pendurado); `odoo -u transformativa_sales_ai --test-enable exit 0` com **`0 failed, 0 error(s) of 15 tests`** (6 herdados do E03 + 9 deste card, `TestOportunidadeCanonica.test_01..test_09`), **0 linha `FAIL:`/`ERROR:`**, 0 ERROR/CRITICAL no log; **passo 3** `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled` com **0 resquicio** (`ir_model_data`/`ir_ui_view`/`ir_model_fields`) e **0 tabela** com prefixo do modulo; **passo 4** reinstalacao `exit 0` com `latest_version=19.0.1.0.0`. Prova da relacao com o parceiro impressa pelo proprio teste no log: `test_06: recusa da exclusao do parceiro = ForeignKeyViolation: update or delete on table "res_partner" violates foreign key constraint "tf_process_opportunity_partner_id_fkey" on table "tf_process_opportunity"`. Aviso esperado (nao falha): `WARNING … The models ['tf.process.opportunity'] have no access rules` — ACL e do card E07 (decisao D8).
- **Provas negativas PROPRIAS do card (agente, na VPS):** `bash /opt/tre/dev/baterias/dentes-e05t01.sh` (sha256 `17dd154b…`, so na VPS) -> **3/3 `DENTE_OK`**, cada uma mutando **uma copia** do modulo em diretorio e banco proprios (o modulo real nao foi tocado): `tf_uuid` sem `required` -> `FAIL: TestOportunidadeCanonica.test_01_modelo_criado_com_os_campos_do_contrato` (`AssertionError: False is not true : tf_uuid tem de ser obrigatorio`); guarda de imutabilidade neutralizada -> `FAIL: …test_08_uuid_canonico_e_imutavel` (`ValidationError not raised`); `unique (tf_uuid)` -> `unique (id)` -> `FAIL: …test_07_uuid_canonico_e_unico` (`IntegrityError not raised`). Nas tres: `MODULO_ODOO_FALHOU (36 itens, 2 falhas)`, **exit 1**.
- **Medicao DIRETA no banco (agente, na VPS — nao se aceita "OK" como prova):** `bash /opt/tre/dev/baterias/medicao-direta-e05t01.sh` (so na VPS, dupla descartavel propria) -> `EXIT_INSTALL=0` e, lido no catalogo com o modulo instalado: tabela `tf_process_opportunity` **existe (14 colunas)**, **15** linhas em `ir_model_fields` para `model='tf.process.opportunity'`, colunas do contrato gravadas (`tf_uuid:char`, `partner_id/stage_id/lost_reason_id/company_id/currency_id:many2one`, `expected_revenue:monetary`, `name:char`, `active:boolean`), constraint **`tf_process_opportunity_tf_uuid_uniq | u`** (unica) e as FKs `partner_id`/`stage_id`/`lost_reason_id`/`company_id`/`currency_id` no `pg_constraint`; depois da desinstalacao pelo ORM (`DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`): **tabela=0, `ir_model_fields`=0, `ir_model_data`(modulo)=0**, `state=uninstalled`.
- **Defeitos encontrados e consertados nesta execucao (6, todos achados executando):** (1) teste com `assertRaises((IntegrityError, UserError))` **derruba** o runner do Odoo 19 (`TypeError: issubclass() arg 1 must be a class` — o `TransactionCase.assertRaises` faz `issubclass(exception, AccessError)`): medido na **primeira rodada do aceite** como `0 failed, 1 error(s) of 15 tests` + `MODULO_ODOO_FALHOU (51 itens, 2 falhas)`, exit 1 (o proprio aceite pegou); conserto com **uma** classe, e a classe certa **medida** (`psycopg2.errors.ForeignKeyViolation`, subclasse de `IntegrityError`) em vez de adivinhada, remedido `0 failed, 0 error(s) of 15 tests`; (2) **o modo `--prova-de-dente` herda o `TRE_LOG_DIR` do chamador e sobrescreve os 4 logs de passo do aceite** — medido: `logs/2-teste.log` passou a citar o banco do dente (`tre_e05_t01_oportunidade_dente, 1 failed`); conserto local (um diretorio de log por alvo) e **card de defeito `t_5c4fc7ac`** (`TRE-W2-E03-T01-D02`, pre-requisito da origem `t_c536ce86`); (3) **item de aceite codigo morto no Odoo 19**: "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" usa `grep -cE '^(FAIL|ERROR): '`, mas a linha de reprovacao vem prefixada (`data pid NIVEL banco logger: FAIL: Test…`) — medido no dente `dente_a`: aceite reprovou, o log tinha `FAIL:` e o item imprimiu `OK`; **card de defeito `t_578a4e4d`** (`TRE-W2-E03-T01-D01`); (4) a guarda de banco recusou o sufixo maiusculo (`FALHOU nome de banco fora do padrao descartavel … tre_e05_t01_denteA`, aceite morreu nas guardas com 6 itens/1 falha) — nomes passaram a minusculos; (5) o meu proprio verificador de dentes usava o mesmo padrao morto do item (3) e imprimia `DENTE_FALHOU` num dente que funcionou — conserto: `grep "FAIL: .*<teste-esperado>"`; (6) **tres dos quatro termos dos itens de resquicio sao codigo morto** (achado pela medicao direta, nao pela leitura): com o modulo instalado, `ir_ui_view where model like 'transformativa_sales_ai%'` = 0, `ir_model_fields where name like 'transformativa_sales_ai%'` = 0 e `tabelas com prefixo transformativa_sales_ai_` = 0, porque em Odoo a tabela tem o nome do MODELO (`tf_process_opportunity`) e o item foi escrito com o nome do MODULO — se a desinstalacao deixasse a tabela, os campos ou uma view para tras, os dois itens de resquicio seguiriam `OK`; **card de defeito `t_9e402411`** (`TRE-W2-E03-T01-D03`). Controle medido na mesma rodada: `ir_model_data` do modulo = **17** com o modulo instalado (esse termo TEM superficie) e 0 depois de desinstalar.
- **Verificadores do projeto (agente, no worktree do card, commit publicado no branch):** `bash scripts/verificar_estrutura.sh` -> `PASS (0 falhas)` RC=0 (bloco dos artefatos do modulo passou a exigir tambem `models/**`, `tests/test_oportunidade_canonica.py` e o runbook do card); `bash scripts/secret_scan.sh` -> `PASS (nenhum segredo versionado)` RC=0; `bash scripts/verificar_papeis.sh` -> `PASS (0 falhas)` RC=0.
- **Estado DEPOIS / o que NAO foi tocado (medido):** instancia do dev com **os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `/opt/tre/{homolog,prod}` com **0 arquivo** antes e depois (ADR-005); nenhum container/rede/diretorio temporario residual **do card** (`docker ps -a` mostrou apenas `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev` **e os pares descartaveis de outros cards em execucao** — `e04t01-pg-*`, `e03t01-pg-*`, que nao foram tocados); `/opt/tre/repo` (copia operacional) **sem nenhuma escrita**; `/opt/tre/dev/modulos/transformativa_sales_ai` (do E03/E04) intocado — o card usou `/opt/tre/dev/modulos-e05t01/`. As copias mutadas dos dentes (`/opt/tre/dev/dentes-e05t01/`) foram removidas ao fim de cada prova (sobra 0); os logs dos dentes ficam como evidencia.
- **Logs brutos (agente, na VPS):** `/opt/tre/dev/evidencias/t_9c91ecce/` — `bateria.out` (3 alvos com `EXIT_*=0`), `manifesto.out` (19 itens), `aceite.out` (51 itens), `dentes.out` (dentes herdados do E03), `dentes-proprios.out` (3 dentes proprios com `DENTE_OK`), `logs-aceite/{1-instalacao,2-teste,3-desinstalacao,4-reinstalacao}.log` (0 `ERROR|CRITICAL` nos quatro), `logs-dente/` e `logs-dente_{a,b,c}/`, `medicao-direta.out` (leitura direta do banco), `medicao-instalacao.log` e `medicao-desinstalacao.log`. `logs/` guarda o execucao em que o defeito (2) foi medido (log do aceite sobrescrito pelo dente) — preservado de proposito.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de teste/revisao (estagio 6, perfil `tester`); a homologacao (estagio 7) e do Anderson. A ratificacao da versao 19.0 tambem e dele. A publicacao do modulo em `/opt/tre/repo` continua pendencia do caminho do E03 (runbook §9 daquele card).
- Segredos: nenhum valor nesta entrada (a senha de cada dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).


## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E07-T01 (card `t_e0b1bcbf`): ACLs e regras de seguranca do modulo Odoo (carteira x tenant)

- **Campos do card definidos e registrados ANTES da primeira medicao** (doc 11 §2): ACCEPTANCE CRITERIA (os 3 homologados por Anderson em 29/09/2026 — aplicados, teste negativo por tenant, aprovacao humana nunca por maquina), TEST PLAN (testes `post_install` com usuarios de verdade + verificacao das regras lidas no banco + prova negativa independente + provas de dente), ROLLBACK PLAN (desinstalar o modulo / `git revert`), AFFECTED COMPONENTS, RISK LEVEL (alto — isolamento entre clientes) e as decisoes **D1..D11** na **runbook §1** (`docs/runbooks/odoo-acl-seguranca.md`) e no fio do card. Nada em producao (ADR-005); a execucao inteira e em dev.
- **Sonda read-only da imagem ANTES de escrever o XML** (a primeira rodada de instalacao morreu e foi ela que ensinou): `ValueError: Invalid field 'category_id' in 'res.groups'` — no Odoo 19 o agrupamento de grupos passou para **`res.groups.privilege`** (`privilege_id` → `category_id`), e `res.groups` deixou de ter `category_id`; a transitividade de heranca virou **`all_implied_ids`** (`trans_implied_ids` nao existe mais); o campo `global` de `ir.rule` e' adicionado por `setattr` (nome reservado em Python: leitura por dominio de busca); e **`res.partner.user_id`** (vendedor do contato) e' `store=True` — e' o que permite a regra de carteira sem campo novo. Sem a sonda, tres itens do aceite teriam sido escritos com API inexistente.
- **Base da execucao (decisao D1):** branch `feature/TRE-W2-E07-T01` nascida de **`feature/TRE-W2-E05-T01`** (`1614b77`), que traz a base do E03 **e** o modelo `tf.process.opportunity` — o caminho declarado do card e' o E03, e o E05 e' o descendente direto que traz o modelo sob protecao.
- **Transferencia (agente):** modulo `odoo/addons/transformativa_sales_ai` -> `/opt/tre/dev/e07t01/modulos/` e `scripts/odoo/{verificar-acl-modulo.sh,provar_acl_modulo.py}` -> `/opt/tre/dev/e07t01/scripts/odoo/` por `tar` sobre `ssh` (sem scp): **sha256 identico nos tres pontos** (worktree do repo, copia do card e copia do harness de medicao) nos 12 arquivos do modulo — `security/transformativa_sales_ai_security.xml c5d89ba3…`, `security/ir.model.access.csv 1066372f…`, `tests/test_acl_seguranca.py 3dca5720…`, `tests/__init__.py 9183eb85…`, `__manifest__.py 015399f6…`, `README.md 06806487…`, `models/tf_process_opportunity.py 2045dd0a…` (intocado do E05), `tests/test_modulo_base.py ea75d283…` (intocado do E03), `tests/test_oportunidade_canonica.py 56dc0865…` (intocado do E05) — **12/12 identicos**, reconferidos arquivo a arquivo **depois do commit** (`060c369`).
- **Aceite (agente, na VPS, a partir de ARQUIVO — nunca por stdin):** `TRE_MODULO_DIR=/opt/tre/dev/e07t01/modulos/transformativa_sales_ai TRE_LOG_DIR=/opt/tre/dev/e07t01/evidencias/logs bash /opt/tre/dev/e07t01/scripts/odoo/verificar-acl-modulo.sh` -> **`RESULTADO: ACL_OK (51 itens, 0 falhas)`, exit 0**. Medido: **passo 1** banco `tre_e07t01_acl` nao existia e foi criado do zero (`odoo --init exit 0`, **0 ERROR/CRITICAL**, `'Modules loaded.'`, `state=installed`, 0 pendurado); **passo 2** `odoo -u transformativa_sales_ai --test-enable exit 0` com **`0 failed, 0 error(s) of 26 tests`** (6 do E03 + 9 do E05 + **11 deste card**) e **`TestAclSeguranca` rodando (11 metodos no log)** — classe verde ausente nao valeria como aceite; **passo 3** lido NO BANCO: 2 grupos no privilegio/categoria do modulo, gestor implica vendedor, nenhum grupo alcanca `group_system`/`group_erp_manager`, **superficie de ACL = so `tf.process.opportunity`**, matriz `t|t|t|f` (vendedor) e `t|t|t|t` (gestor), 3 regras ativas, todas no modelo, todas com as 4 permissoes, dominios exatamente os declarados e a regra de tenant **global**; **passo 4** prova negativa independente -> `ACL_ITENS=22 ACL_FALHAS=0`, `ACL_RESULTADO: OK`, **0 acusacao de material alheio**.
- **A prova negativa e' INDEPENDENTE dos testes do autor** (`scripts/odoo/provar_acl_modulo.py`, via `odoo shell`): monta a cena do zero (2 tenants = 2 companhias, 3 carteiras = 3 vendedores, usuarios de verdade) e mede o ataque por **busca** E por **leitura direta de id** com `with_user`: cada vendedor ve so a propria carteira; vendedor sem carteira devolve **VAZIO**; leitura de carteira/tenant alheio -> **AccessError**; gestor ve as duas carteiras do SEU tenant e nenhuma do outro; sem o grupo do modulo -> **AccessError** (fail-closed); criacao em carteira alheia recusada e na propria aceita; AC3: superficie de ACL = 1 modelo, nenhum grupo alcanca administracao, nao administra OUTRO usuario (`AccessError`), nao cria `ir.rule` (`AccessError`) e a tentativa de autopromocao a administrador **nao promove** (medido no grupo do usuario depois da tentativa, nao suposto).
- **Provas de dente PROPRIAS do card (agente, na VPS), cada uma mutando uma COPIA do modulo em banco proprio:** `--prova-de-dente` -> **`RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)`, exit 0**. Dente 1 (dominio da regra de carteira trocado por `[(1, '=', 1)]`) -> `FALHOU dominio da regra de carteira: '[(1, '=', 1)]'` + `FALHOU runner do Odoo: 4 failed … of 26 tests` + `FALHOU prova negativa: 22 itens, 6 falha(s)` com **acusacao de material alheio**, `RESULTADO: ACL_FALHOU`, exit 1. Dente 2 (ACL plantada no CSV dando escrita em `res.users` ao grupo do vendedor) -> `FALHOU superficie de ACL do modulo: 'res.users, tf.process.opportunity'` + `FALHOU ACLs do modulo: 3 (esperado 2)` + `FALHOU runner do Odoo: 1 failed` + `FALHOU prova negativa: 22 itens, 2 falha(s)`, `RESULTADO: ACL_FALHOU`, exit 1. O modulo real nao foi tocado em nenhuma das duas.
- **Defeitos encontrados e consertados nesta execucao (3, todos achados executando e remedidos):** (1) **API de grupos do Odoo 19 escrita por suposicao** — a instalacao morreu com `ValueError: Invalid field 'category_id' in 'res.groups'` (`odoo.registry: Failed to load registry` / `CRITICAL`), reprovando o aceite inteiro; conserto medido na imagem (`res.groups.privilege` + `privilege_id`) e a instalacao voltou a `install rc=0`, **0 ERROR/CRITICAL**; (2) **teste do autor errado (e por isso um falso negativo de aceite)**: o item de AC3 exigia `AccessError` ao escrever o **proprio** `res.users`, mas o Odoo permite ao usuario editar o proprio cadastro — o teste reprovava o artefato correto; conserto: o ataque passou a ser em **OUTRO** usuario (recusa real) e a autopromocao passou a ser **medida no estado final** do usuario (tentativa + verificacao do grupo), que e' o que o criterio quer; (3) **o proprio `--prova-de-dente` nao tinha dente**: as duas mutacoes chamavam `"$0"` num arquivo sem bit de execucao na VPS e as duas provas falhavam em silencio reportando "prova sem dente" — reprovava o artefato por um erro do verificador, nao do modulo; conserto: `bash "$0"` no dente **e** bit `100755` nos dois scripts (verificado pelo `verificar_estrutura.sh`, que passou a ter item de bit executavel para o verificador novo). Depois do conserto, os dois dentes morderam (acima).
- **Verificadores do projeto (agente, no worktree do card):** `bash scripts/verificar_estrutura.sh` -> `PASS (0 falhas)` RC=0 (bloco do modulo passou a exigir `security/**`, `tests/test_acl_seguranca.py`, `scripts/odoo/verificar-acl-modulo.sh`, `scripts/odoo/provar_acl_modulo.py`, `docs/runbooks/odoo-acl-seguranca.md`, alem do item de bit executavel dos tres scripts); `bash scripts/secret_scan.sh` -> `PASS` RC=0; `bash scripts/verificar_papeis.sh` -> `PASS (0 falhas)` RC=0.
- **Estado DEPOIS / o que NAO foi tocado (medido pelo proprio verificador):** **instancia do dev intacta** — mesmos 4 bancos antes e depois (`odoo_dev, postgres, template0, template1`); `/opt/tre/{homolog,prod}` com **0 arquivo** antes e depois (ADR-005); banco, `postgres`, rede e diretorio de configuracao descartaveis (`e07t01-*`, senha gerada na hora em arquivo 600 dono uid 100) **removidos no fim**; `/opt/tre/repo` (copia operacional) **sem escrita**; `/opt/tre/dev/modulos/` (do E03/E04) e `/opt/tre/dev/modulos-e05t01/` **intocados** — o card usou `/opt/tre/dev/e07t01/`. Os pares descartaveis de outros cards em execucao (`e03t01-pg-*`, `e04t01-*`) nao foram tocados.
- **Logs brutos (agente, na VPS):** `/opt/tre/dev/e07t01/evidencias/logs/` — `1-instalacao.log`, `2-teste.log` (relatorio do runner), `4-prova-negativa.log` (a prova negativa item a item) — e `/opt/tre/dev/e07t01/evidencias/dentes-final.out` (as duas rodadas mutadas, com a saida completa dos filhos, terminando em `RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)`). O aceite completo e as provas foram capturados tambem no scratch do perfil `desenvolvedor`. A dupla de ITERACAO e os diretorios de sonda foram removidos no fim (`harness-e07t01-*`, `probe-e07t01`, `harness-e07t01`): **0 container e 0 rede do card** depois.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de teste/revisao (estagio 6, perfil `tester`); a homologacao (estagio 7) e do Anderson. A ratificacao da versao 19.0 tambem e dele. A publicacao do modulo em `/opt/tre/repo` continua pendencia do caminho do E03.
- Segredos: nenhum valor nesta entrada (a senha de cada dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).
## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E04-T01 (card `t_adee6ad7`): campos de dedup e IDs canonicos em `res.partner`

- **Transferencia (agente):** modulo -> `/opt/tre/dev/cards/t_adee6ad7/modulos/transformativa_sales_ai`, scripts -> `/opt/tre/dev/cards/t_adee6ad7/scripts/odoo/`, contrato -> `/opt/tre/dev/cards/t_adee6ad7/docs/data/data_contract_v1.json`, por `tar` sobre `ssh` (sem scp). **Diretorio por card de proposito**: `E04-T02` e `E05-T01` rodam em paralelo na mesma VPS e escrevem no mesmo modulo — usar `/opt/tre/dev/modulos/` faria um aceite medir o arquivo do outro. **sha256 igual nos dois lados**: `models/res_partner.py 37a93372…`, `models/__init__.py 932f2686…`, `__init__.py f5e43b96…`, `tests/test_res_partner_dedup.py 76c10063…`, `tests/__init__.py d8c51368…`, `verificar-res-partner.sh 1bc9e1b8…`, `data_contract_v1.json dfc74c95…`.
- **Aceite (agente, na VPS, a partir de ARQUIVO):** `TRE_LOG_DIR=/opt/tre/dev/evidencias/t_adee6ad7/logs-round2 bash /opt/tre/dev/cards/t_adee6ad7/scripts/odoo/verificar-res-partner.sh` -> `RESULTADO: RES_PARTNER_OK (64 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e04_t01_res_partner imagens=odoo:19.0+postgres:16`, **exit 0**. Medido: **contrato** `CONTRATO_RES_PARTNER_OK (9 itens, 0 falhas)` contra o sha256 `dfc74c95…` do contrato congelado; **passo 1** banco `tre_e04_t01_res_partner` criado do zero (`odoo --init exit 0`, 0 ERROR/CRITICAL, `'Modules loaded.'`, `state=installed`, `latest_version=19.0.1.0.0`); **passo 2** `odoo -u transformativa_sales_ai --test-enable exit 0` com `0 failed, 0 error(s) of 13 tests`, **7 testes de `TestResPartnerDedup` (este card) e 6 de `TestModuloBase` efetivamente rodados** no log; **passo 3** catalogo do PostgreSQL: `tf_cnpj`/`tf_domain`/`tf_linkedin_url`/`tf_company_id` = `character varying`, `tf_priority_score` = `double precision`, indices **btree reais** `res_partner__tf_cnpj_index` / `res_partner__tf_domain_index` / `res_partner__tf_linkedin_url_index`, `ir_model_fields.index = true` nos tres; **passo 4** `odoo shell` (instrumento independente dos testes) criou e consultou o parceiro sintetico — `MEDICAO_BUSCA` **n=1** por `tf_cnpj`, `tf_domain`, `tf_linkedin_url` e `tf_company_id`, identificador diferente **n=0**, `MEDICAO_ROLLBACK_OK` e **0** parceiro sobrando; **passo 5** `DESINSTALACAO_OK`, `state=uninstalled`, **as 5 colunas `tf_*` removidas** de `res_partner`, **0** indice `tf_*` e **0** resquicio.
- **Identidade do alvo medido:** `odoo:19.0` digest `sha256:77bac5cd…` e `postgres:16` digest `sha256:1a6ab3f5…` (as mesmas imagens do par de dev); rede/postgres/config **descartaveis** criados nesta execucao (senha por `openssl rand -hex 24`, arquivo 600 dono uid 100) e removidos no fim.
- **Provas de dente (agente, na VPS):** `bash verificar-res-partner.sh --prova-de-dente` -> `RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas)`, **exit 0**. Dente 1 (`index=True` retirado do `tf_cnpj` numa copia) -> `FALHOU dedup.strong "cnpj" -> tf_cnpj NAO esta indexado` e `RES_PARTNER_FALHOU (13 itens, 1 falha)`, exit 1. Dente 2 (`tf_domain` -> `tf_dominio` numa copia) -> `FALHOU dedup.strong "domain" -> campo tf_domain AUSENTE` + ordem divergente, `RES_PARTNER_FALHOU`, exit 1. Dente 3 (teste plantado que falha) -> `odoo --test-enable exit 1`, `1 failed` no relatorio, `FALHOU 1 linha(s) de teste reprovado(a) no log: … FAIL: TestResPartnerDedup.test_99_prova_de_dente`, exit 1. Prova negativa extra do conferidor de contrato: contrato com um forte a mais (`telefone`) -> `FALHOU dedup.strong "telefone" -> campo tf_telefone AUSENTE`.
- **Defeitos encontrados executando e consertados (2, cada conserto remedido — runbook §6):** (1) **`ir.model.fields.index` e booleano no Odoo 19** e o teste/o item do verificador exigiam a string `btree`: a rodada 1 morreu com `FALHOU runner do Odoo: 1 failed, 0 error(s) of 13 tests` e `FALHOU ir_model_fields.index de res.partner.tf_cnpj: 't' (esperado btree)` (log: `AssertionError: True != 'btree'`); conserto: o teste exige indice declarado no ORM (`is True`) e o **tipo** btree continua provado no catalogo (`pg_indexes`), onde e medido de verdade. (2) **O item "nenhuma linha de teste FAIL:/ERROR:" imprimia OK com um teste reprovado** — o `grep` estava ancorado no inicio da linha e o Odoo 19 escreve `<hora> <pid> ERROR <banco> <modulo>: FAIL: TestX.test_y` (mesma classe do defeito D04 do verificador de estrutura: aceite falso); prova no proprio log da rodada 1: padrao antigo **0** casamentos, padrao novo **1**; conserto com `grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]'`, provado no dente 3. Achado adicional, registrado sem conserto possivel: o marcador legado `'At least one test failed when loading the modules.'` **nao aparece no Odoo 19** nem com teste reprovado (medido), entao o item ficou apenas como ausencia e os dentes reais do passo 2 sao o exit code, o relatorio do runner e as linhas `FAIL:`.
- **Estado DEPOIS / o que NAO foi tocado:** instancia do dev com **os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev` de pe; `/opt/tre/homolog` e `/opt/tre/prod` com **0 arquivo** antes e depois; **0 container, 0 rede, 0 banco e 0 diretorio temporario residual**; `odoo_dev` intocado (o modulo **nao** foi instalado nele — o AC pede banco limpo e o `odoo_dev` carrega o funil de `TRE-W2-E02-T01`); `/opt/tre/repo` (copia operacional) **sem escrita**.
- **Logs brutos (agente, na VPS):** `/opt/tre/dev/evidencias/t_adee6ad7/` — **rodada final (commit `a569ece`, modulo identico por sha256):** `verificacao-completa-final.out` (64 itens + `EXIT=0`), `prova-de-dente-final.out` (`EXIT=0`) e `logs-final/{0-contrato.out,1-instalacao.log,2-teste.log,4-medicao-orm.log,5-desinstalacao.log}`; ficam preservadas a **rodada 1** com o defeito (`verificacao-completa.out`, `logs/`) — a rodada 1 fica de proposito: e a prova de que o falso OK existia e foi consertado — e a rodada 2 (`verificacao-completa-round2.out`), refeita porque o modulo medido diferia do commit apenas no `README.md`. Na ultima conferencia, sha256 do modulo **igual nos dois lados** em 9 arquivos (`__init__.py f5e43b96…`, `models/res_partner.py 37a93372…`, `tests/test_res_partner_dedup.py 76c10063…`, `README.md 6afc51bc…`, entre outros) e no contrato (`dfc74c95…`).
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e do estagio 6 (perfil `tester`); ratificacao da versao do Odoo (19.0) e homologacao (estagio 7) seguem com o Anderson. Pendencia herdada de `TRE-W2-E03-T01`: a **publicacao do modulo na copia operacional** `/opt/tre/repo` (linha `fix/t_daca4bda-enforcement`, divergente do `develop`) — runbook §8.
- Segredos: nenhum valor nesta entrada (a senha da dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).
## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E04-T02 (card `t_d3bd6660`): campos de rastreio `tf_*` em `crm.lead` (13 campos do Data Contract V1.0)

- **Campos do card definidos e registrados ANTES da primeira medicao** (doc 11 §2): ACCEPTANCE CRITERIA (os homologados por Anderson em 29/09/2026, inalterados), TEST PLAN (6 passos), ROLLBACK PLAN (desinstalar o modulo), AFFECTED COMPONENTS, RISK LEVEL e as decisoes de implementacao na **runbook §1** e no fio do card (comment 187). Nada em producao (ADR-005); execucao inteira em dev.
- **Isolamento entre cards paralelos (nao usei caminho compartilhado):** o modulo e as ferramentas foram medidos de `/opt/tre/evid-t_d3bd6660/{modulo,scripts,contrato,logs}` — diretorio proprio do card — com `TRE_MODULO_DIR`/`TRE_LOG_DIR`/`TRE_*` apontando para la, para nao clobber a rodada do card irmao `TRE-W2-E04-T01`, que tambem escreve em `/opt/tre/dev/`. Transferencia por `tar` sobre `ssh` (sem `scp`): **sha256 igual nos dois lados** em 10 arquivos (`models/crm_lead.py bc18a74e…`, `tests/test_crm_lead_rastreio.py 64bbceee…`, `models/__init__.py 40516ddf…`, `__init__.py faa0bf14…`, `tests/__init__.py 61625721…`, `README.md 3dd18241…`, `__manifest__.py cd84f4ec…` (inalterado), `conferir_crm_lead_no_contrato.py c90d7e7f…`, `medir_crm_lead.py 193d1ab8…`, `verificar-crm-lead-odoo.sh 161b512e…`, `docs/data/data_contract_v1.json dfc74c95…`).
- **Aceite (agente, na VPS, a partir de ARQUIVO — nunca por stdin):** `TRE_MODULO_DIR=… TRE_LOG_DIR=… TRE_CONTRATO_JSON=… bash verificar-crm-lead-odoo.sh` -> `RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e04_t02_crm_lead imagens=odoo:19.0+postgres:16`, **exit 0**. Medido: **passo 0** `CONFERIDOR_CRM_LEAD_OK (18 itens, 0 falhas)` (modulo x contrato congelado: 2 campos nomeados, 5 score types, 5 faixas, vocabulario §7 de 9 valores, 13 eventos §6, inventario == implementado, indices == declarados); **passo 1** banco `tre_e04_t02_crm_lead` nao existia e foi criado do zero (`odoo --init exit 0`, **0 ERROR/CRITICAL**, `state=installed`); **passo 2** os **13** campos em `ir_model_fields` (`name like 'tf_%'` = 13, nenhum a mais, 0 `state=manual`), **indice real** em `pg_indexes` para `tf_opportunity_id`, `tf_correlation_id` e `tf_idempotency_key`; **passo 3** `odoo --test-enable exit 0` com `0 failed, 0 error(s) of 13 tests` (6 do modulo base + 7 do card, contados pelo runner: `Starting TestCrmLeadRastreio.test_01..test_07`) e **0** linha de teste reprovado; **passo 4** `odoo shell` + ORM -> `MEDICAO_CRM_LEAD_OK (58 itens, 0 falhas)` (lead com os 13 campos criado e **lido de volta campo a campo**, achado por `tf_opportunity_id` e por `tf_idempotency_key`+`tf_next_best_action`, `tf_priority_tier` derivado 87,5 -> `A` e apos `write` de 40,0 -> `Nurture`, UUID invalido recusado e UUID v4 aceito, lead comum criado/renomeado/buscado **sem valor artificial** nos campos novos, campos padrao intactos), conferido **tambem por SQL fora da sessao do Odoo** (1 linha sintetica com `Nurture|NURTURE|PRIORITY_SCORE_CHANGED|91.00`, 0 lead comum residual, 4/4 colunas padrao do `crm_lead`); **passo 5** rollback por desinstalacao -> `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`, **0** campo `tf_`, **0** coluna `tf_`, **0** indice dos campos de rastreio, colunas padrao intactas, dados do CRM preservados (1 linha em `crm_lead`); **passo 6** limpeza e dev intocado (mesmos 4 bancos antes e depois: `odoo_dev, postgres, template0, template1`; `/opt/tre/{homolog,prod}` com 0 arquivo; **nenhuma escrita** em `/opt/tre/repo`).
- **Identidade do alvo medido:** `odoo:19.0` digest `odoo@sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (o mesmo do par de dev) e `postgres:16` digest `postgres@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`; rede, postgres e diretorio de configuracao **descartaveis** criados nesta execucao (senha por `openssl rand -hex 24`, arquivo 600 dono uid 100 — nunca em argumento, log ou artefato) e removidos no fim.
- **Regressao do modulo base (com este card em cima):** o aceite do `TRE-W2-E03-T01` (`/opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh`, sha256 `72d00aa1…`, o mesmo do card base) rodado contra **este** modulo (banco `tre_e04_t02_base`) deu `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas)`, **exit 0** — instalacao em banco limpo, teste do Odoo, desinstalacao e reinstalacao seguem passando com os campos novos dentro.
- **Provas de dente (agente, na VPS):** `bash verificar-crm-lead-odoo.sh --prova-de-dente` -> `RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, **exit 0**. Dente 1 (copia com `tf_opportunity_id` renomeado para `tf_opp_id`), medido **no banco** com `TRE_PULAR_CONFRONTO=1` -> `FALHOU so' 12 de 13 campos do inventario…`, `FALHOU sem indice no banco para tf_opportunity_id`, `FALHOU campo nomeado pelo contrato AUSENTE`, `CRM_LEAD_FALHOU (35 itens, 3 falha(s))`, **exit 1**. Dente 2 (copia com `index=True` removido do `tf_idempotency_key`) -> `CRM_LEAD_FALHOU (35 itens, 1 falha(s))`, exit 1. Dente 3 (copia sem a declaracao do `tf_next_best_action`) -> `CRM_LEAD_FALHOU (35 itens, 2 falha(s))`, exit 1. Dente 5 (mesma renomeacao do dente 1, medida **so pelo confronto estatico**) -> `FALHOU campo nomeado pelo contrato AUSENTE: tf_opportunity_id`, `CRM_LEAD_FALHOU (13 itens, 1 falha(s))`, exit 1. Dente 4 (copia com teste plantado que falha) -> `FALHOU odoo --test-enable exit 1`, `FALHOU runner do Odoo: 1 failed, 0 error(s) of 14 tests (minimo 13)`, `FALHOU 1 linha(s) de teste reprovado(a) no log`, `CRM_LEAD_FALHOU (43 itens, 3 falha(s))`, exit 1.
- **Bateria completa (com `EXIT=` por comando — nada "passou" sem comando, saida e exit code):** `--apenas-confronto` -> `CONFERIDOR_CRM_LEAD_OK (18 itens, 0 falhas)`, exit 0; aceite completo -> `CRM_LEAD_OK (64 itens, 0 falhas)`, exit 0; `--prova-de-dente` -> `CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, exit 0; regressao do modulo base -> `MODULO_ODOO_OK (51 itens, 0 falhas)`, exit 0. Reexecutada **inteira** com o script final (`161b512e…`) depois de cada edicao do verificador.
- **Defeitos encontrados e consertados nesta execucao (4, todos achados executando e cada conserto remedido):** (1) `test_07` do card estourava `ValueError: too many values to unpack (expected 2)` — desempacotava a constante do vocabulario como pares, e ela e' a lista de VALORES do contrato (`1 error(s) of 13 tests`, exit 1) — conserto: comparar `set(...)` direto; (2) o item "nenhuma linha de teste `FAIL:`/`ERROR:`" usava `grep -cE '^(FAIL|ERROR): '` e o log do Odoo prefixa com data/hora/nivel: **com 1 erro real o item dizia "nenhuma linha"** — conserto: padrao sem ancora de inicio, provado pelo dente 4 (`3 falha(s)` no passo 3); (3) as provas de dente reexecutavam o verificador por `"$0"`, que so' funciona com caminho contendo barra — invocado como `bash verificar-crm-lead-odoo.sh` as 4 provas morriam com `command not found` e o comando devolvia `CRM_LEAD_DENTE_FALHOU (4 prova(s) sem dente)` — conserto: `SELF="$(readlink -f "$0")"`; (4) os dentes de campo/indice reprovavam **so no passo 0** (confronto estatico) e o caminho de **banco** nunca era exercitado (`16 itens, 1 falha`) — conserto: `TRE_PULAR_CONFRONTO=1` nos dentes 1-3 (que passaram a reprovar pelo banco, `35 itens`) + **dente 5** medindo o confronto estatico (`13 itens, 1 falha`). Por isso a bateria inteira e reexecutada depois de **qualquer** edicao do verificador.
- **O que NAO foi tocado (medido pelo proprio verificador):** `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev` e `proxy-dev` de pe; bancos do dev **os mesmos 4 antes e depois** (o aceite **nao** instala o modulo no `odoo_dev`, que carrega o funil do `TRE-W2-E02-T01`); `/opt/tre/{homolog,prod}` com 0 arquivo antes e depois; **0 container, 0 rede e 0 diretorio temporario residual**; `/opt/tre/repo` (copia operacional) **sem nenhuma escrita**; os scripts de `/opt/tre/dev/scripts/odoo/` de outros cards nao foram sobrescritos (o aceite roda das minhas copias em `/opt/tre/evid-t_d3bd6660/scripts/`).
- **Logs brutos (agente, na VPS):** `/opt/tre/evid-t_d3bd6660/` — `aceite.out` (64 itens + `EXIT=0`), `prova-de-dente.out` (`EXIT=0`), `regressao-modulo-base.out` (`EXIT=0`), `sha256-ferramentas.txt`, `logs/{0-confronto-contrato,0-confronto-smoke,1-instalacao,3-teste,4-dado-sintetico,5-desinstalacao}.out`, `logs/dente-{1,2,3,4,5}-*.out` e `logs-base/`.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e de revisao/teste (estagio 6, perfil `tester`); a homologacao (estagio 7) e do Anderson, com esta evidencia na mao. A **publicacao do modulo na copia operacional** `/opt/tre/repo` segue como pendencia herdada do E03-T01 (o `odoo-dev` monta essa copia) — runbook §7.
- Segredos: nenhum valor nesta entrada (a senha da dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E04-T02 (card `t_d3bd6660`), RODADA 2: defeito 5 (modo dente false-verde) consertado e remedido

- **O que esta rodada e:** remediacao do **unico defeito bloqueante** da revisao independente (perfil `tester`, rodada 1): o modo `--prova-de-dente` dava `CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, **exit 0**, sem exercitar dente nenhum (fail-open). Os 3 AC homologados ja haviam sido aprovados na rodada 1 e **nenhum byte do modulo mudou** (`models/crm_lead.py bc18a74e…` igual nos dois lados e ao da rodada 1): o diff desta rodada e' no verificador, na runbook, no CHANGELOG e neste registro.
- **Transferencia (agente):** modulo + ferramentas + contrato para `/opt/tre/evid-t_d3bd6660-r2/{modulo,scripts,contrato}` por `tar` sobre `ssh` (sem scp): **sha256 igual nos dois lados** nos 14 arquivos medidos (`verificar-crm-lead-odoo.sh 0fbaa3c5…` **novo**, `verificar-modulo-odoo.sh 72d00aa1…`, `conferir_crm_lead_no_contrato.py c90d7e7f…`, `medir_crm_lead.py 193d1ab8…`, `desinstalar_modulo.py b859b3c6…`, `manifesto_do_modulo.py f314948c…`, `data_contract_v1.json dfc74c95…`, `crm_lead.py bc18a74e…`, `test_crm_lead_rastreio.py 64bbceee…`, `__init__.py faa0bf14…`, `models/__init__.py 40516ddf…`, `tests/__init__.py 61625721…`, `README.md 3dd18241…`, `__manifest__.py cd84f4ec…`).
- **ANTES (o defeito, reproduzido por mim com o artefato da rodada 1):** `bash /opt/tre/evid-t_d3bd6660/scripts/verificar-crm-lead-odoo.sh --prova-de-dente` (sha256 `161b512e…`, **nada exportado**) -> as 5 provas morreram na guarda (`FALHOU contrato ausente em /opt/tre/dev/contrato/data_contract_v1.json (sem contrato nao ha confronto)`, `CRM_LEAD_FALHOU (6 itens, 1 falha(s))` cada) e o comando terminou em `RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, **EXIT_ANTES=0**. Log bruto: `r2-bis-antes-falso-verde.out`; os `.out` que o modo default deixou em `/tmp/verificacao-crm-lead-odoo` foram movidos para `logdir-default-antes/`.
- **DEPOIS — o caminho que dava verde agora RECUSA (script novo `0fbaa3c5…`):** (A) defaults, nada exportado -> `FALHOU contrato congelado ausente em /opt/tre/dev/contrato/data_contract_v1.json (sem contrato nao ha confronto) — exporte TRE_CONTRATO_JSON=/caminho/docs/data/data_contract_v1.json` e `RESULTADO: CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, **EXIT_A=1** (`r2-a-defaults.out`); (B) so `TRE_MODULO_DIR` exportado (o que o bloco da runbook exportava) -> identico, **EXIT_B=1** (`r2-b-sem-contrato.out`).
- **DEPOIS — dentes com o ambiente completo:** `TRE_MODULO_DIR=… TRE_CONTRATO_JSON=… TRE_LOG_DIR=… bash verificar-crm-lead-odoo.sh --prova-de-dente` -> **EXIT_C=0**: **baseline** (caminho NAO mutado, passo 0+1+2+3) `RESULTADO: CRM_LEAD_OK (43 itens, 0 falhas)` e os 5 dentes com a contagem da rodada 1 **preservada**: dente 1 `CRM_LEAD_FALHOU (35 itens, 3 falha(s))`, dente 2 `(35, 1)`, dente 3 `(35, 2)`, dente 5 `(13, 1)`, dente 4 `(43, 3)` — cada um julgado pela **sua** assinatura de falha -> `RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)` (`r2-c-dentes.out`).
- **Aceite (rodada 2, script final):** `bash verificar-crm-lead-odoo.sh` -> `RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e04_t02_crm_lead imagens=odoo:19.0+postgres:16`, **EXIT_D=0** (`r2-d-aceite.out`) — os 6 passos seguem medindo o mesmo da rodada 1.
- **Regressao do modulo base:** `verificar-modulo-odoo.sh 72d00aa1…` contra este modulo -> `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas)`, **EXIT_E=0** (`r2-e-regressao.out`).
- **Controle do proprio julgamento (o dente do dente):** o `confere_dente` foi **extraido do artefato sob teste** (`controle-confere_dente.frag`, sha256 `e326a17e…`, 18 linhas) e alimentado com 5 saidas: g1 saida real do dente 1 -> **OK**; g2 saida de **aborto** de guarda -> reprovado; g3 saida real **sem** o passo medido -> reprovado; g4 saida real do dente 1 julgada pela assinatura de **outro** dente -> reprovado; g5 saida **verde** do aceite -> reprovado -> `DENTE_FALHAS=4`, `RESULTADO: CONTROLE_JULGAMENTO_OK (4 provas negativas, 0 falso-OK)`, **EXIT_F=0** (`r2-f-controle-julgamento.out`).
- **Verificadores do projeto (no worktree do commit, depois das edicoes):** `verificar_estrutura.sh` -> `PASS (0 falhas)` RC=0 · `secret_scan.sh` -> `PASS` RC=0 · `verificar_papeis.sh` -> `PASS (0 falhas)` RC=0.
- **O que NAO foi tocado (medido ao fim):** a instancia do dev com **os mesmos 4 bancos** (`odoo_dev, postgres, template0, template1`); `/opt/tre/{homolog,prod}` com **0 arquivo**; **0 arquivo** novo em `/opt/tre/repo` (`find -newermt "2026-10-01 14:20"` = 0); **0** container/rede `e04t02-*` residual; os scripts de `/opt/tre/dev/scripts/odoo/` (de outros cards) **nao** foram sobrescritos — a bateria roda das minhas copias em `/opt/tre/evid-t_d3bd6660-r2/scripts`.
- **Logs brutos (agente, na VPS):** `/opt/tre/evid-t_d3bd6660-r2/` — `r2-bis-antes-falso-verde.out` (o falso-verde do artefato antigo), `r2-a-defaults.out`, `r2-b-sem-contrato.out`, `r2-c-dentes.out`, `r2-d-aceite.out`, `r2-e-regressao.out`, `r2-f-controle-julgamento.out`, `r2-bateria.out` (bateria com `EXIT_*` por bloco), `logs/` (incl. `dente-0-baseline.out` e `dente-{1,2,3,4,5}-*.out`), `logs-b/`, `logs-regressao/`, `logdir-default-antes/`.
- **Verificacao independente:** quem entrega nao homologa — o parecer desta rodada e' de revisao/teste (estagio 6, perfil `tester`); a homologacao (estagio 7) e a ratificacao da versao 19.0 seguem com o Anderson.
- Segredos: nenhum valor nesta entrada.

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E06-T01 (card `t_cf7519c9`): views do Sales AI no modulo `transformativa_sales_ai`

- **Criterios do card definidos e registrados ANTES da primeira medicao** (doc 11 §2): os tres homologados por Anderson em 29/09/2026 — AC1 (as views exibem os campos das entidades customizadas, sem erro de renderizacao), AC2 (o acesso respeita o perfil: quem nao pode ver, nao ve) e AC3 (evidencia de abertura das views: lista/formulario) — publicados no fio do card (**comment 228**) e na **runbook §1/§2** (`docs/runbooks/odoo-views-sales-ai.md`), inalterados. Nada em producao (ADR-005); execucao inteira em dev.
- **Base do card:** branch `feature/TRE-W2-E06-T01` a partir de `129e04a1ae6548701c65a1ce2efe69e99364d4ce`, com os merges das dependencias ja' commitados (`3b0eac31` = E04-T01 `res.partner`, `fac55048` = E04-T02 `crm.lead`) — merge incremental em vez de rebase, para preservar a identidade dos cards. `models/__init__.py`, `tests/__init__.py`, `__init__.py` e os arquivos acumulativos (`README.md`, `CHANGELOG.md`, `registro-de-execucoes.md`) ficaram com as linhas dos tres cards ("keep both"), sem marcador de conflito (`grep` limpo).
- **Isolamento entre cards paralelos (caminho proprio do card):** modulo e ferramentas medidos de `/opt/tre/dev/e06t01/{odoo/addons,scripts,evidencias}` com `TRE_MODULO_DIR`/`TRE_LOG_DIR` apontando para la' (a rodada irma `t_aaaf1558` estava de pe' na mesma VPS em `/opt/tre/evid-t_aaaf1558-r1/`). Transferencia por `tar` sobre `ssh` (**sem `scp`** e **sem `ssh -n`**): sha256 conferido nos 20 arquivos do modulo (`views/tf_process_opportunity_views.xml e43ca70a…`, `views/res_partner_views.xml f4d47bab…`, `views/crm_lead_views.xml 6e798db4…`, `tests/test_views_sales_ai.py a8f7e297…`, `__manifest__.py fa7ce08c…`, `models/res_partner.py 37a93372…`, `models/crm_lead.py bc18a74e…`, `models/tf_process_opportunity.py 2045dd0a…`, `security/*`, `tests/*` e o `.gitkeep`). **CORRECAO (rodada 2):** a afirmacao original desta entrada — *"sha256 igual nos dois lados nos 20 arquivos"* — **nao valia para o `README.md`**: a copia medida carregava o README da rodada anterior ao commit entregue (medido pela revisao independente: `082df8f3…` na copia x `b2e6a02a…` no commit `df9a7e2`; os outros 19 arquivos estavam identicos). O aceite nao mede o README, entao o veredito nao mudou; a identidade registrada estava errada. A rodada 2 re-transferiu do commit e mediu sha256 identico nos 20 — ver a entrada de 01/10 (rodada 2), abaixo.
- **Aceite (agente, na VPS, a partir de ARQUIVO — nunca por stdin):** `TRE_MODULO_DIR=… TRE_LOG_DIR=… bash scripts/odoo/verificar-views-sales-ai.sh` -> `RESULTADO: VIEWS_OK (83 itens, 0 falhas) modulo=transformativa_sales_ai modelo=tf.process.opportunity banco=tre_e06t01_views imagens=odoo:19.0+postgres:16`, **exit 0**. Medido, passo a passo: **1** banco `tre_e06t01_views` nao existia e nasceu do zero (`odoo --init exit 0`, **0 ERROR/CRITICAL**, `state=installed`, nada pendurado em to install/upgrade/remove); **2** `odoo --test-enable exit 0` com `0 failed, 0 error(s) of 50 tests` (a suite do modulo inteira — 40 dos cards anteriores + **10** deste card, contados pelo runner: `Starting TestViewsSalesAi.test_01..test_10`) e **0** linha de teste reprovado; **3** lido **NO BANCO**: 5 views do modulo (3 da oportunidade + 2 herdadas), 2 menus, 1 acao, cada campo do contrato presente na **arch gravada** (4 na lista, 8 no formulario, `tf_uuid` na busca; os 5 campos `tf_*` do parceiro e os 13 do lead nas views herdadas), o recorte por grupo das duas views herdadas e do menu, `view_mode=list,form` da acao e a hierarquia `crm.crm_menu_root -> Sales AI -> Oportunidades`; **4** prova independente `scripts/odoo/provar_views_sales_ai.py` -> `VIEW_ITENS=21`, `VIEW_FALHAS=0`, `VIEW_RESULTADO: OK` (sem traceback); **5** rollback por desinstalacao pelo ORM (`DESINSTALACAO_OK`, `state=uninstalled`) -> **0** view, **0** menu, **0** registro do modulo em `ir_model_data` e **0** view de parceiro carregando a secao "Sales AI"; **6** limpeza e dev/homolog/prod intactos.
- **AC1 (campos das entidades customizadas, sem erro de renderizacao):** a lista e o formulario da entidade canonica abrem pelo ORM para o usuario do Sales AI (`get_view`, o caminho do web client) e trazem os campos do contrato (`ausentes=nenhum`); o formulario traz o contrato **completo** (`company_id` inclusive) para o membro com duas companhias; as secoes "Sales AI" do parceiro (5 campos) e do lead (13 campos) aparecem no formulario **renderizado** do membro. Nenhuma view foi carregada com erro (`log de instalacao sem erro de view`).
- **AC2 (quem nao pode ver, nao ve) — medido com usuarios de verdade que diferem SO' pelo grupo do modulo:** o menu do Sales AI aparece para o membro (`menus=[174,175] visiveis=[174,175]`) e **nao** aparece para o restrito (`visiveis=[]`); a secao "Sales AI" e TODOS os campos `tf_*` do parceiro e do lead **nao** estao na view renderizada do restrito (o Odoo aplica o `groups` no servidor: `tf_sales_ai=False` para o restrito, `True` para o membro); a leitura e a abertura da lista da entidade canonica pelo restrito recusam com `AccessError` (fail-closed do E07). Limite declarado na runbook D6: o grupo do modulo recorta a **superficie de UI** (menu + secoes) e a entidade canonica (ACL); nao e' uma ACL campo-a-campo dos `tf_*` de `res.partner`/`crm.lead`, que seguem os direitos padrao do Odoo nessas duas entidades.
- **AC3 (abertura da lista/formulario):** `acao abre a lista e o formulario do modelo (tf.process.opportunity|list,form)`; o membro abriu lista e formulario pela mesma chamada do web client (`ausentes=nenhum`) e a lista devolveu o dado sintetico (`n=1`, com UUID canonico de volta).
- **Provas de dente (agente, na VPS):** `bash verificar-views-sales-ai.sh --prova-de-dente` -> `RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas)`, **exit 0**. Dente 1 (copia com o `groups` da secao do parceiro removido) -> `VIEWS_FALHOU (83 itens, 6 falha(s))`, exit 1, com as assinaturas esperadas: `FAIL: TestViewsSalesAi.test_09_ac2_o_restrito_nao_ve_a_secao…` no runner, `AC2 a view herdada view_partner_form_tf_sales_ai nao recorta a secao pelo grupo do vendedor (medido 0)` e `prova independente: 21 itens, 2 falha(s)`. Dente 2 (copia com a view do modelo fora do manifesto `data`) -> `VIEWS_FALHOU (83 itens, 27 falha(s))`, exit 1 (`AC3 o menu aponta para ''`, `prova independente sem marcadores VIEW_ITENS/VIEW_FALHAS`). **CORRECAO (rodada 2):** este `VIEWS_DENTE_OK` da rodada 1 **nao provava o que prometia** — a revisao independente mediu o modo dente **fail-open** (qualquer `RESULTADO: VIEWS_FALHOU`, inclusive aborto de guarda de ambiente, era aceito como "dente que mordeu": com `TRE_MODULO_DIR` inexistente ou `DOCKER_HOST` invalido o comando devolvia `VIEWS_DENTE_OK`, exit 0) e, alem disso, os sub-runs do dente herdavam o `TRE_LOG_DIR` e **sobrescreveram os 4 logs do aceite** desta entrada. Conserto e remedicao na rodada 2 (ver a entrada de 01/10 (rodada 2) e a runbook §8).
- **Identidade do alvo medido:** `odoo:19.0` digest `odoo@sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (o mesmo do par de dev) e `postgres:16` digest `postgres@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`; rede, postgres e diretorio de configuracao **descartaveis** criados nesta execucao (senha por `openssl rand -hex 24`, arquivo 600 dono uid 100 — nunca em argumento, log ou artefato) e removidos no fim. O verificador cria o par proprio (postgres+odoo) porque **o Odoo do container de dev abre sessao de cron em qualquer banco novo da instancia `pg-odoo-dev`** — medir em banco descartavel dentro da instancia do dev nao seria limpo.
- **Defeitos encontrados e consertados nesta execucao (5, todos achados executando e cada conserto remedido com a bateria inteira):** (1) a arch de busca com `<group expand="0" string="Agrupar por">` foi **recusada pelo RNG do Odoo 19** (`Invalid attribute expand for element group`) e abortou a instalacao — conserto: `<group>` da busca sem atributo (como no `crm`); (2) `<field name="group_ids">` no **registro** das views herdadas foi recusado (`ParseError: Inherited view cannot have 'groups' defined on the record`) — conserto: o recorte foi para o **no' da arch** (pagina) e para o menu, e o item de banco passou a medir a arch das views herdadas; (3) o item "campo X na arch" do **proprio verificador** dizia FALHOU para os 32 campos na primeira rodada porque `arch_db` e' **jsonb** e o dump escapa as aspas (`name=\"x\"`) — conserto: `arch_db->>'en_US'`; (4) o item do menu media `ir_ui_menu_group_rel.group_id`, coluna que **nao existe** (o par e' `menu_id`+`gid`) — conserto medido em `information_schema.columns`; (5) `company_id` e `currency_id` apareciam como "ausentes" na view renderizada do membro — diagnostico: sao os grupos padrao do Odoo (`base.group_multi_company`/`base.group_multi_currency`) e **`base.group_multi_company` nao se concede na mao** (`UsersMultiCompany` em `res_users.py` o retira de quem tem 1 companhia e concede a quem tem 2+) — conserto: a prova/teste passou a medir esses dois campos com um membro de **duas companhias**. Nenhum conserto ficou sem remedicao: a rodada final (83 itens) e' posterior a todas as cinco correcoes, e as 7 armadilhas ficaram documentadas na runbook §6.
- **Verificadores do projeto (no worktree, depois das edicoes e do commit):** `bash scripts/verificar_estrutura.sh` -> ver saida anexa ao card (RC e contagem `^OK`); `bash scripts/secret_scan.sh` -> PASS. O `scripts/verificar_estrutura.sh` foi estendido para exigir versionados os artefatos deste card (3 views, `test_views_sales_ai.py`, `verificar-views-sales-ai.sh`, `provar_views_sales_ai.py`, `docs/runbooks/odoo-views-sales-ai.md`) e o bit **executavel no git** (`100755`) do verificador — corrigir so' a copia operacional nao sobrevive ao proximo deploy (licao do defeito `t_22c27625`).
- **O que NAO foi tocado (medido pelo proprio verificador):** `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev` e `proxy-dev` de pe'; bancos do dev **os mesmos 4 antes e depois** (`odoo_dev, postgres, template0, template1` — o aceite **nao** instala o modulo no `odoo_dev`); `/opt/tre/{homolog,prod}` com **0 arquivo** antes e depois; **0 container, 0 rede e 0 diretorio temporario residual** `e06t01-*`; `/opt/tre/repo` (copia operacional) **sem nenhuma escrita** (`find -newermt '3 hours ago'` = 0 arquivo); os scripts de `/opt/tre/dev/scripts/odoo/` de outros cards nao foram sobrescritos.
- **Logs brutos (agente, na VPS):** `/opt/tre/dev/e06t01/evidencias/` — `aceite.out` (83 itens + `RESULTADO: VIEWS_OK`), `dente.out` (`VIEWS_DENTE_OK (2 provas, 0 falhas)`), `logs/{1-instalacao,2-teste,4-prova-independente,5-desinstalacao}.log` e `logs/dente-{1,2}-*.out`. **CORRECAO (rodada 2):** os 4 logs de passo citados aqui **nao sao os do aceite**: o modo dente da rodada 1 escreveu nos MESMOS caminhos (defeito D-02 do E03, terceira incidencia) e o que sobrou ali foi a execucao do **mutante** (`tre_e06t01_views_d2`, `2 failed, 5 error(s) of 50 tests`). A evidencia bruta da rodada 2, com caminhos separados e guarda de sha256, esta em `/opt/tre/dev/e06t01-r2b/evidencias/{aceite.out,dente.out,logs/,logs/dente/prova-{1,2}/,controles/}`.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e' de revisao/teste (estagio 6, perfil `tester`); a homologacao (estagio 7) e' do Anderson, com esta evidencia na mao. A publicacao do modulo na copia operacional `/opt/tre/repo` (`deploy/publicar.sh`) segue como pendencia herdada do E03-T01.
- Segredos: nenhum valor nesta entrada (a senha da dupla descartavel nasce na VPS, em arquivo 600 dono uid 100, e morre com o diretorio temporario).

## 2026-10-01 (rodada 2) — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W2-E06-T01 (card `t_cf7519c9`): modo `--prova-de-dente` fail-closed

- **Por que a rodada 2 existe:** a revisao independente da rodada 1 (perfil `tester`) aprovou os 3 AC homologados e **pediu mudancas** em dois defeitos bloqueantes do artefato NOVO deste card, `scripts/odoo/verificar-views-sales-ai.sh`: (A) o modo `--prova-de-dente` era **fail-open** — aceitava qualquer `RESULTADO: VIEWS_FALHOU`, inclusive aborto de guarda de ambiente (medido pela revisao: `TRE_MODULO_DIR` inexistente e `DOCKER_HOST` invalido -> `VIEWS_DENTE_OK`, exit 0); a classe ja' estava consertada no E04-T01 (`a539802`, defeito `t_e1f62fae`) e no E04-T02 (`e8bfe71`); (B) **D-02 do E03 reincidente** — os sub-runs herdavam `TRE_LOG_DIR` e sobrescreviam `1-instalacao.log`/`2-teste.log`/`4-prova-independente.log`/`5-desinstalacao.log` do aceite (terceira incidencia: E03 `t_5c4fc7ac`/`c389223` -> E07 `t_aaaf1558` -> aqui). Achado menor no mesmo pedido: identidade do `README.md` (ver a CORRECAO na entrada da rodada 1).
- **Commit medido:** `1c9380424738ed62b38dadae46106a5499c48ec7` em `feature/TRE-W2-E06-T01` (`HEAD == origin`, conferido). Artefato transferido por `tar` sobre `ssh` (sem `scp`, sem `ssh -n`) para `/opt/tre/dev/e06t01-r2b/{odoo/addons,scripts,evidencias}`: **sha256 identico nos 20 arquivos do modulo e nos 3 scripts do card**, worktree limpo (`git status --porcelain` vazio). Harness `verificar-views-sales-ai.sh f41fe5caa8af92fb9e8209eb68758d33d74bb3452698b88b3d8c1c29c70cca56`, prova `7944fa50…`, desinstalador `b859b3c6…`. O proprio aceite imprime `INFO identidade: o manifesto medido casa com o checkout (...)` — a copia medida e' a do card (o defeito C da revisao nao se repete).
- **Aceite (6 passos) RE-RODADO do zero:** `RESULTADO: VIEWS_OK (83 itens, 0 falhas) modulo=transformativa_sales_ai modelo=tf.process.opportunity banco=tre_e06t01r2_views imagens=odoo:19.0+postgres:16`, **exit 0** — 83 linhas `OK`, **0** `FALHOU`; passo 1 `odoo --init exit 0` sem `ERROR`/`CRITICAL`, `state=installed`; passo 2 `0 failed, 0 error(s) of 50 tests` com `TestViewsSalesAi` (10 metodos); passo 3 lido **no banco** (5 views, 2 menus, 1 acao, campos na arch gravada, grupos, hierarquia do menu); passo 4 `VIEW_ITENS=21`, `VIEW_FALHAS=0`, `VIEW_RESULTADO: OK`; passo 5 desinstalacao pelo ORM -> 0 view, 0 menu, 0 registro do modulo; passo 6 `instancia do dev intacta: mesmos bancos antes e depois (odoo_dev, postgres, template0, template1)`, `homolog/prod sem nenhum arquivo`, **0** container e **0** rede residuais `e06t01-*`.
- **Modo dente fail-closed — `RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas)`, exit 0:**
  - `OK ancora: o artefato em ... e' o do checkout ... — sha256 identico em 20 arquivos`;
  - **baseline** (caminho NAO mutado, os 6 passos): `RESULTADO: VIEWS_OK (83 itens, 0 falhas)` -> `OK baseline: os dentes tem contra o que medir`;
  - dente 1 (`groups` da secao do parceiro removido): `VIEWS_FALHOU (83 itens, 6 falhas)` -> `OK` com as assinaturas exigidas `AC2 a view herdada view_partner_form_tf_sales_ai nao recorta a secao pelo grupo do vendedor` e `prova independente: 21 itens, 2 falha` (mais `FAIL: TestViewsSalesAi.test_09…` no runner);
  - dente 2 (view do modelo fora do manifesto `data`): `VIEWS_FALHOU (83 itens, 27 falhas)` -> `OK` com as assinaturas exigidas `views do modulo no banco: 2 (esperado 5)` e `AC1 a busca do tf.process.opportunity nao traz tf_uuid`;
  - `OK logs do aceite intactos depois das provas (4 arquivo(s) com sha256 identico)` + **guarda externa** (manifesto `sha256sum` dos 4 logs gerado ANTES do dente, conferido depois): `1-instalacao.log: OK`, `2-teste.log: OK`, `4-prova-independente.log: OK`, `5-desinstalacao.log: OK`, `EXIT_GUARDA=0`.
- **Controles fail-closed (na VPS, cada um em `evidencias/controles/<rotulo>.out`)** — **6 de 6** com exit 1 e nenhuma prova de dente aceita: `ctl-a-defaults` (default `/opt/tre/dev/modulos/...`, copia compartilhada de outro card) -> `VIEWS_DENTE_FALHOU (artefato nao ancorado)`; `ctl-b-dockerhost-ruim` (ancora OK, `DOCKER_HOST` invalido) -> `FALHOU docker nao responde` -> `VIEWS_DENTE_FALHOU (baseline nao medido)`; `ctl-c-imagem-ruim` (`TRE_IMAGEM=odoo:nao-existe-mesmo`) -> `FALHOU imagem ... ausente (nada a medir)` -> baseline nao medido; `ctl-d-sem-ancora` (`TRE_ANCORA_DIR=/opt/tre/nao-existe`) -> recusa sem medir nada; `ctl-e-modulo-inexistente` -> recusa na ancora; `ctl-f-readme-divergente` (copia do modulo com uma linha a mais no README) -> recusa apontando `README.md` (`b2e6a02a…` na referencia x `5730c7ee…` no medido). Cada controle com `TRE_LOG_DIR` proprio (nada escrito no diretorio do aceite).
- **Verificadores do projeto (no worktree do commit):** `verificar_estrutura.sh` RC=0 `PASS (0 falhas)`; `secret_scan.sh` RC=0 `PASS`; `verificar_papeis.sh` RC=0 `PASS (0 falhas)` — saida e contagens no comentario da entrega.
- **O que NAO foi tocado (medido por fora, alem do passo 6):** `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev` e `proxy-dev` de pe'; bancos do dev os mesmos 4 antes e depois; `/opt/tre/{homolog,prod}` com **0** arquivo; **0** container/rede/tmp residual `e06t01-*`; `/opt/tre/repo` sem escrita; os defaults compartilhados de `/opt/tre/dev/modulos/` **nao** foram alterados — o harness apenas passou a **recusar** medir contra eles.
- **Evidencia bruta (a revisao tem acesso proprio a VPS):** `/opt/tre/dev/e06t01-r2b/evidencias/` — `aceite.out` (sha256 `3cfa174f3e11…`, 83 itens + `VIEWS_OK`), `dente.out` (`6dc9291598db…`), `logs/{1-instalacao,2-teste,4-prova-independente,5-desinstalacao}.log` (`4ab0180c…`, `df45897e…`, `c239153d…`, `baf5f67f…`), `logs/dente/{dente-0-baseline.out,dente-1-sem-recorte.out,dente-2-view-fora-do-manifesto.out,baseline/,prova-1/,prova-2/}`, `logs-aceite.sha256` (`16ffa5ad…`), `rodada2.log`, `guarda-e-controles.log`, `controles/*.out`; copia local em `/opt/data/profiles/desenvolvedor/cache/scratch/e06t01-r2/`.
- **Registro de processo (por que nao ha card de defeito novo):** as duas classes ja' tem card no board — D-02 em `t_5c4fc7ac` (`done`) e o fail-open do modo dente em `t_e1f62fae` (`done`) — e o rework foi pedido na revisao da rodada 1 do proprio card (`kanban_request_changes`), como no precedente do E04-T02 (rodada 2 na mesma branch, `e8bfe71`). O aprendizado de fim de onda (modo de prova negativa exige baseline verde + assinatura propria + diretorio de log proprio com guarda) esta na runbook §8.
- Segredos: nenhum valor nesta entrada (senha da dupla descartavel gerada na VPS, arquivo 600 dono uid 100, removida com o diretorio temporario).

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T01 (card `t_e0489efc`): API controlada do Odoo, aceite + dentes

- **Card e escopo:** `TRE-W3-E01-T01` — a **API controlada** do Odoo (`POST /tf/api/v1/<operacao>`),
  primeira superfície HTTP do projeto dentro do Odoo e o caminho por onde o n8n vai escrever no CRM.
  Entregue o **mecanismo e o portão** (política versionada, motor de decisão, controlador, suite de
  aceite e verificador com dentes); as operações de negócio são dos cards `TRE-W3-E01-T02..T05`.
- **Os 5 campos que o doc 11 §2 exigia e não detalhava** (DESENHO/ACCEPTANCE/TEST/ROLLBACK/RISK)
  foram definidos **antes de escrever código**, registrados no comentário de abertura do card
  (`comment_id 239`) e ficaram também em `docs/runbooks/odoo-api-controlada.md` §1–§5, conforme a
  convenção da onda.
- **Base do card:** branch `feature/TRE-W3-E01-T01` a partir de `origin/feature/TRE-W2-E06-T01`
  (`b1bbddb`) — o head entregue mais completo do módulo (a API escreve em `res.partner`/`crm.lead` e
  depende das ACLs do E07). `develop` não recebeu o módulo (segue só com `.gitkeep`).
- **Artefato medido:** commit **`1a87f0e65835ce1156d8eb37d4ccb6e162e12350`**, publicado por
  `git archive` sobre `ssh` (sem `scp`/`ssh -n`) em `/opt/tre/evid-t_e0489efc-r1/repo-v2`; sha256
  dos arquivos sob teste registrado na saída do aceite (política `c0b3c44c…`, motor `6a3e52d1…`,
  controlador `9a52f642…`, suíte `dd8dab0d…`, verificador `5b0b677f…`, suite pura `fcdcea9b…`).
- **Aceite (na VPS, a partir de ARQUIVO):** `TRE_LOG_DIR=… bash scripts/odoo/verificar-api-controlada.sh`
  → **`RESULTADO: API_CONTROLADA_OK (75 itens, 0 falhas)`, exit 0**. Medido passo a passo: **passo 0**
  suite pura do motor `MOTOR_API_OK (53 itens, 0 falhas)`; **passo 1** banco `tre_e01_t01_api` nascendo
  limpo, `odoo --init exit 0`, log sem `ERROR`/`CRITICAL`, `state=installed`; **passo 2** `0 failed,
  0 error(s) of 82 tests` (as **32** da suíte nova aparecem nomeadamente no log do runner; piso 82 =
  50 dos cards W2 + 32 novos — sem regressão); **passo 3** servidor HTTP real com `curl` de fora do
  processo: 401 sem token, 401 com token inválido, 200 com envelope (`ok`, `politica_versao 1.0.0`,
  `correlation_id` ecoado), **404** operação não declarada, **405** verbo GET, 200 leitura declarada
  (`total=3`), **422** campo/operador/limite, **400** chave desconhecida, **404** escrita não
  declarada; **passo 3b** `8 linhas TF_API_AUDIT para 8 chamadas autenticadas` (as outras 3 param no
  Odoo antes do controlador), **sem token, sem `Bearer`, sem payload** na trilha e a chave ausente do
  log do servidor; **passo 4** contrato: 1 rota, `auth='bearer'`, só POST, `csrf=False`, **0 SQL** nos
  arquivos da API, motor sem `import odoo`; **limpeza**: banco/rede/dupla/`/tmp` removidos, dev com os
  **mesmos 4 bancos** antes e depois, `/opt/tre/{homolog,prod}` com **0** arquivo.
- **Provas de dente (na VPS):** `bash scripts/odoo/verificar-api-controlada.sh --prova-de-dente` →
  **`RESULTADO: API_CONTROLADA_DENTE_OK (3 provas, 0 falhas)`, exit 0**: dente 1 (cópia da política
  sem `sistema_capacidades`) → `FALHOU (61 itens, 5 falhas)` com a assinatura esperada
  (`operacao 'sistema_capacidades' nao esta declarada na politica 1.0.0 (declaradas: crm_registros_ler)`
  e 404 no lugar de 200); dente 2 (cópia do motor sem a checagem de campo declarado) → `FALHOU (61
  itens, 3 falhas)` **com vazamento medido** do campo não declarado na resposta
  (`{"registros": [{"id": 6, "email": "tf_api_integracao@tre.local"}, …]`); dente 3 (cópia do motor
  sem a checagem de aprovação) → `MOTOR_API_FALHOU (53 itens, 1 falha)` no item de produção sem
  aprovação; e a **guarda externa** por sha256 dos **29 arquivos** do módulo provou que o artefato
  real saiu intacto (`800763cd…`). Cada dente confere antes que a mutação foi de fato aplicada.
- **Defeitos encontrados e consertados nesta execução (6, todos em código meu — 3 no módulo/teste e
  3 no próprio verificador; cada conserto remedido com a bateria inteira):** (1) `res.users.groups_id`
  não existe no Odoo 19 (é `group_ids`) — quebrou o preparo da fase HTTP; (2) o teste de leitura de
  `crm.lead` supunha visão de admin e o usuário de integração é **restrito** (só o lead dele): virou
  item explícito de ACL, medindo também que o lead alheio **não** aparece; (3) `valores: {}` é
  **400** `payload_invalido`, não 422 de campo obrigatório ausente; (4) `res.users.apikeys._generate`
  **exige data de validade** no Odoo 19 (teto = `api_key_duration` do grupo, 1 dia por padrão) — 12h
  em teste e preparo; (5) o diretório montado para o preparo tem de ser gravável pelo **uid 100** do
  container `odoo:19.0`; (6) no verificador: resposta da última chamada referenciada por número fixo
  (off-by-one que faria 3 itens medirem o arquivo errado), `grep -c` multi-arquivo devolvendo
  `arquivo:0` (o item do marcador media sempre FALHOU) e a contagem de auditoria incluindo a chamada
  405 (que o Odoo barra antes do controlador, logo sem trilha). As rodadas anteriores da mesma
  execução (`aceite.out`, `aceite-final.out`, `aceite-v1.out`) ficaram na VPS com os FALHOU originais
  — a rodada que mede o artefato entregue é a `v2`.
- **Verificadores do projeto (no worktree do commit):** `bash scripts/verificar_estrutura.sh` →
  `PASS (0 falhas)` RC=0; `bash scripts/secret_scan.sh` → `PASS` RC=0;
  `bash scripts/verificar_papeis.sh` → `PASS (0 falhas)` RC=0. O `verificar_estrutura.sh` foi
  estendido para exigir versionados os artefatos deste card (api/, controllers/, fixtures de política,
  os 3 scripts de aceite e o runbook) e **executáveis** os dois scripts chamados direto.
- **O que NÃO foi tocado (medido pelo próprio verificador):** `odoo-dev`, `pg-odoo-dev`,
  `pg-sales-dev` e `proxy-dev` de pé; bancos do dev os **mesmos 4 antes e depois** (o aceite **não**
  instala o módulo no `odoo_dev`); `/opt/tre/{homolog,prod}` com **0** arquivo antes e depois;
  **0** container/rede/`/tmp` residual `e01t01-*`; `/opt/tre/repo` sem escrita (a cópia medida é
  `/opt/tre/evid-t_e0489efc-r1/repo-v2`, um `git archive` do commit).
- **Logs brutos (agente, na VPS):** `/opt/tre/evid-t_e0489efc-r1/` — `aceite-v2.out` (75 itens +
  `API_CONTROLADA_OK`), `dente-v2.out` (`DENTE_OK (3 provas, 0 falhas)`), `logs-v2/`
  (`0-motor-puro.out`, `1-instalacao.log`, `2-teste.log`, `3-preparo.log`, `3b-servidor.log`) e
  `logs-dente-v2/` (`dente-{1,2,3}-*.out` + `dente1/`, `dente2/`), além das rodadas anteriores.
- **Segredos:** nenhum valor nesta entrada e nenhum no repositório (`secret_scan.sh` PASS). A chave
  de API da fase HTTP nasceu **na VPS**, em arquivo 600 dentro do diretório descartável do preparo
  (nunca em stdout, log, argumento ou artefato: o `curl` lê os cabeçalhos de um arquivo de
  configuração 600, então o token não aparece nem em `ps`), e morreu com o diretório. A senha da
  dupla descartável é gerada por `openssl rand` na hora, em arquivo 600.
- **Verificação independente:** quem entrega não homologa — o veredito deste card é do estágio 6
  (perfil `tester`) e a ratificação da versão 19.0/homologação (estágio 7) é do Anderson. A
  publicação do módulo na cópia operacional `/opt/tre/repo` (`deploy/publicar.sh`) segue como
  pendência herdada do E03-T01.


---

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T02 (card `t_cdc21b43`): a primeira **escrita de negocio** da API controlada (`empresa_upsert`)

**O que foi executado (por mim, por SSH com a chave de operacoes — o container do agente nao tem
docker daemon, entao toda execucao vira esta linha):**

- **Envio do artefato:** `tar` do worktree `.worktrees/t_cdc21b43` (branch `feature/TRE-W3-E01-T02`,
  empilhada em `feature/TRE-W3-E01-T01` = `afab91c`) para `/opt/tre/evid-t_cdc21b43/repo-r1`, com
  conferencia por `sha256` dos arquivos da entrega contra o worktree (9/9 nas rodadas 1-2, 11/11 na
  rodada final). O que o aceite mede e' uma copia byte a byte, nunca o worktree.
- **Suite pura do motor (na VPS, sem Odoo):** `python3 scripts/odoo/testar_motor_api.py` →
  `MOTOR_API_OK (75 itens, 0 falhas)`.
- **Aceite proprio (`scripts/odoo/verificar-empresa-upsert.sh`), rodada 1:**
  `RESULTADO: EMPRESA_UPSERT_FALHOU (102 itens, 5 falha(s))`. O reprovado era o **preparo**, nao a
  API: as duas linhas do caso ambiguo (AC4) nasceram de SQL cru sem `active`, e o ORM nao as enxerga
  (o default de `active` e' do ORM, nao da coluna) — a API nao viu ambiguidade, criou um terceiro
  registro, e o aceite pegou pelo banco. Correcao: `active` explicito no preparo e `NOME_FINAL`
  passando a vir da chamada por CNPJ.
- **Rodada 2:** `EMPRESA_UPSERT_OK (104 itens, 0 falhas)`, com `0 failed, 0 error(s) of 101 tests` do
  Odoo (19 testes novos + 82 dos cards anteriores) e auditoria lida do log do servidor (`14 linhas
  para 14 chamadas autenticadas`).
- **Dentes, rodada 1:** o harness reprovou a si mesmo — a conferencia da mutacao 1 era `grep` pela
  palavra `empresa_upsert`, que sobrevive na `descricao` da politica. Correcao: conferencia por
  **operacao**.
- **Dentes, rodada 2:** reprovou de novo, por outro motivo tambem real: o `avaliar_dente` exigia o
  texto exato do ramo `ok`, e o `falhou` do mesmo item traz o diagnostico (texto diferente) — o dente
  dependia da redacao do item, nao do comportamento. Correcao: **marcadores multiplos** por dente +
  autoteste do harness (cada marcador tem de existir como texto de item no proprio verificador).
- **Dentes, rodada 3:** as 3 provas e os 2 controles sairam `OK` e o veredito saiu
  `EMPRESA_UPSERT_DENTE_FALHOU (2 prova(s) sem dente)` — **falso vermelho**: os controles usam o
  contador `DENTE_FALHAS` como sinal de que pegaram o caso, e o incremento ficava contado como prova
  reprovada. Correcao: incremento desfeito no ramo de sucesso (mantido quando o controle falha, senao
  o harness vira fail-open).
- **Aceite, rodada 3 (artefato final):** `EMPRESA_UPSERT_OK (104 itens, 0 falhas)`; auditoria
  `15 linhas para 15 chamadas autenticadas` (o numero subiu de 14 porque a correcao do AC4 acrescentou
  a chamada por CNPJ).
- **Dentes, rodada 4 (artefato final):**
  `EMPRESA_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, 0 falhas)` — 19 itens `OK`,
  0 `FALHOU`, com a guarda externa confirmando o mesmo `sha256` do modulo antes e depois das mutacoes.
- **Limpeza medida:** 0 container, 0 rede e 0 diretorio `/tmp` residual do aceite (`e01t02-*`); os 4
  containers do dev (`odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev`) de pe o tempo todo;
  `/opt/tre/homolog` e `/opt/tre/prod` com 0 arquivo antes e depois.
- **Verificadores do projeto (no worktree do commit):** `bash scripts/verificar_estrutura.sh` →
  `PASS (0 falhas)`; `bash scripts/secret_scan.sh` → `PASS (nenhum segredo versionado)`;
  `bash scripts/verificar_papeis.sh` → `PASS (0 falhas)`. O `verificar_estrutura.sh` foi estendido
  para exigir versionados os artefatos deste card (a suite de aceite, o verificador proprio e o
  runbook).
- **Segredos:** nenhum valor nesta entrada e nenhum valor no repositorio. A chave da API nasceu na VPS,
  em arquivo `600` dentro do diretorio descartavel do preparo, lida pelo `curl` por arquivo de
  configuracao (nunca em `ps`, argumento ou log), e morreu com o diretorio.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e' do estagio 6
  (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T03 (card `t_e6e3b0b3`): a escrita de **contato comercial** (pessoa) na API controlada (`contato_upsert`)

**O que foi executado (por mim, por SSH com a chave de operacoes — o container do agente nao tem
docker daemon, entao toda execucao vira esta linha):**

- **Envio do artefato:** `tar` do worktree `.worktrees/t_e6e3b0b3` (branch `feature/TRE-W3-E01-T03`,
  base = commit aprovado do E01-T02 = `8439f7b`) para `/opt/tre/rev-t_e6e3b0b3-r1..r3`; os arquivos da
  entrega foram conferidos por `sha256` contra o worktree. O aceite mede uma copia byte a byte, nunca
  o worktree.
- **Suite pura do motor (na VPS, sem Odoo):** `python3 scripts/odoo/testar_motor_api.py` →
  `MOTOR_API_OK (90 itens, 0 falhas)` (75 do E01-T02 + 15 deste card).
- **Aceite proprio (`scripts/odoo/verificar-contato-upsert.sh`), rodada 1:**
  `RESULTADO: CONTATO_UPSERT_FALHOU (109 itens, 47 falha(s))`. **O defeito era do harness, nao da API:**
  a linha do cabecalho de autorizacao foi escrita a partir da leitura do harness do E01-T02, e a
  leitura devolveu o valor **mascarado** — copiada assim, a chave de API nunca era enviada e as 18
  chamadas autenticadas viraram `401 Invalid apikey`. Correcao: linha reescrita do zero e conferida
  em **bytes** (`od -c`), comparada com o arquivo original (que traz o especificador de formatacao).
  A rodada tambem mostrou o harness reprovando o preparo por permissao de `chown` do diretorio
  descartavel quando roda como usuario nao privilegiado — a dupla descartavel roda como **root**.
- **Rodada 2:** `RESULTADO: CONTATO_UPSERT_FALHOU (109 itens, 6 falha(s))`, os 6 por **uma** causa: o id
  do fixture semeado por SQL vinha com a **etiqueta do comando** colada (`psql` imprime `INSERT 0 1` em
  STDOUT e o `-tA` nao a suprime) — `"63\nINSERT01"`, e as tres medicoes que dependiam dele mediram
  outra coisa. Correcao: o id sai de `select` sobre **CTE** (SELECT nao imprime etiqueta) e passou a
  ser validado como numerico (preparo sujo = falha nomeada, nao cascata de itens reprovados).
- **Rodada 3 (artefato final):** `CONTATO_UPSERT_OK (110 itens, 0 falhas)`, com
  `0 failed, 0 error(s) of 120 tests` do Odoo (19 testes novos deste card + 101 dos cards anteriores,
  sem regressao) e auditoria lida do log do servidor (`15 linhas para 15 chamadas autenticadas`, sem
  token e **sem nenhum e-mail do payload** — dado pessoal fora da trilha).
- **Dentes (rodada 1, o artefato real):** `CONTATO_UPSERT_DENTE_OK (3 provas + 2 controles do proprio
  harness, 0 falhas)` — politica sem a operacao reprova o item de AC1 e o aceite cai junto (96 itens,
  34 falhas); controlador sem o portao de ambiguidade deixa o `409` de ser exigido (96 itens, 4
  falhas); motor sem aplicar o valor fixo deixa o registro-empresa como empresa (96 itens, 1 falha).
  Os 2 controles (sub-run que reprova por ambiente e mutacao inocua) foram reportados como
  **inconclusivo** / **mutacao sem dente**, e a guarda externa confirmou o **mesmo** `sha256` do modulo
  antes e depois das mutacoes.
- **Defeito fechado neste card (codigo, nao harness):** o motor so recusava `parametros.identificador`
  quando a lista de identidade resultante tinha mais de um campo — com identidade declarada por lista
  de **um** elemento (o caso desta operacao) o parametro era **aceito e descartado em silencio**
  (medido: HTTP 200 com o parametro ignorado). Passou a decidir pela **forma da declaracao**:
  `400 payload_invalido` nomeado, com item na suite pura do motor e item HTTP no aceite.
- **Rodada final, sobre o commit congelado:** o aceite e os dentes rodaram de novo sobre o
  `git archive` do commit desta branch (`88323c4`, extraido em `/opt/tre/rev-t_e6e3b0b3-r4`), com os
  **11 arquivos da entrega conferidos por `sha256`** entre o worktree e a copia da VPS (**11/11
  iguais**) e o modulo byte a byte identico ao da rodada 3 (mesmo `sha256` de politica, motor, suite e
  verificador nas arvores r3 e r4): `CONTATO_UPSERT_OK (110 itens, 0 falhas)` e
  `CONTATO_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, 0 falhas)`, com a guarda
  externa do dente confirmando o artefato real intacto (30 arquivos, `sha256` `97feb79b...`).
- **Limpeza medida:** 0 container, 0 rede e 0 diretorio `/tmp` residual do aceite (`e01t03-*`); os 4
  containers do dev (`odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev`) de pe o tempo todo;
  `/opt/tre/homolog` e `/opt/tre/prod` com 0 arquivo antes e depois.
- **Verificadores do projeto (no worktree do commit):** `bash scripts/verificar_estrutura.sh` →
  `PASS (0 falhas)`; `bash scripts/secret_scan.sh` → `PASS (nenhum segredo versionado)`;
  `bash scripts/verificar_papeis.sh` → `PASS (0 falhas)`. O `verificar_estrutura.sh` foi estendido para
  exigir versionados os artefatos deste card (a suite de aceite, o verificador proprio e o runbook).
- **Segredos:** nenhum valor nesta entrada e nenhum valor no repositorio. A chave da API nasceu na VPS,
  em arquivo `600` dentro do diretorio descartavel do preparo, lida pelo `curl` por arquivo de
  configuracao (nunca em `ps`, argumento ou log), e morreu com o diretorio.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e' do estagio 6
  (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T03-D01 (card `t_1062ccc0`): correcao **documental** do registro e do runbook §11 (defeito achado pela revisao independente do T03)

**O que este card e':** defeito **documental**, achado pela revisao independente (estagio 6, perfil
`tester`) do `TRE-W3-E01-T03`. **Nenhum arquivo de codigo muda** — o artefato de comportamento
aprovado (`88323c4`) segue intacto e esta correcao entra **depois** dele, como commit documental no
branch `feature/TRE-W3-E01-T03` (o card preve esse caminho e pede que o runbook §11 cite o commit
medido, o que ele passou a fazer).

- **Item 1 — numero medido errado no registro (unico erro factual):** a frase do dente 3 trazia o
  numero do dente 2 (`96 itens, 4 falha(s)`); passou a `(96 itens, 1 falha)`. Medido por mim, por SSH
  na VPS, nos **dois** `dente.out` do proprio autor e na reproducao da revisao:
  `dente-1-sem-operacao.out` -> `RESULTADO: CONTATO_UPSERT_FALHOU (96 itens, 34 falha(s))
  banco=tre_e01_t03_contato_d1`; `dente-2-sem-portao.out` -> `(96 itens, 4 falha(s))
  banco=..._d2`; `dente-3-sem-valor-fixo.out` -> `(96 itens, 1 falha(s)) banco=..._d3`. Os dentes 1 e 2
  estavam corretos e **nao** foram tocados.
- **Item 2 — lista de logs de etapa inexistentes no runbook §11:** `3-http.log`, `3d-http.log` e
  `4-contrato.log` nao existem em arvore nenhuma de evidencia (`find /opt/tre -name '3-http.log' -o
  -name '3d-http.log' -o -name '4-contrato.log'` -> `0`), e o proprio harness nao os escreve. Os logs
  de etapa que ele realmente escreve (medidos no entregue) sao `0-motor-puro.out`, `1-instalacao.log`,
  `2-teste.log`, `3-preparo.log`, `3b-servidor.log`, `3d-preparo-homolog.log`, `3d-servidor.log` — e
  foi essa a lista que passou a valer no runbook. Os tres nomes errados **saem** de la' (a conferencia
  do card os procura no runbook e espera 0); o registro deles fica **aqui**.
- **Item 3 — enumeracao incompleta dos arquivos da entrega (mesma §11):** a frase listava 9 dos 11
  arquivos conferidos por `sha256`; passou a nomear os **11** — faltavam
  `docs/operations/registro-de-execucoes.md` e `scripts/odoo/testar_motor_api.py`.
- **Convencao da casa (citar o commit medido):** o §11 passou a nomear o commit medido (`88323c4`) e a
  registrar que esta correcao entra depois dele, sem tocar codigo; os `sha256` citados na secao sao os
  do commit medido, e os dois arquivos tocados aqui divergem por construcao (eram justamente os que
  carregavam as duas afirmacoes erradas).
- **Conferencia depois do conserto (os greps do card, medidos nesta rodada):** no registro de
  execucoes, a frase do dente 3 deixou de trazer o numero do dente 2 (agora mede `(96 itens, 1 falha)`)
  e a do dente 2 segue intacta (`96 itens, 4 falha(s)`) — o padrao que o card manda conferir casa uma
  linha so'; no runbook, **0** ocorrencias dos tres nomes de log removidos e **presentes** as linhas
  com os logs reais (`0-motor-puro.out`, `1-instalacao.log`, `2-teste.log`, `3-preparo.log`,
  `3b-servidor.log`, `3d-preparo-homolog.log`, `3d-servidor.log`).
- **Diff:** so' `.md` — `git diff --name-only` = `docs/operations/registro-de-execucoes.md`,
  `docs/runbooks/odoo-contato-upsert.md`.
- **Publicacao para conferencia externa:** o commit documental e' publicado por `git archive` em
  `/opt/tre/rev-t_1062ccc0-d01` (copia do commit, sem build e sem Odoo), para quem quiser conferir
  fora do worktree.
- **Verificacao independente:** quem conserta nao homologa — o veredito deste card e' do estagio 6
  (perfil `tester`); a homologacao (estagio 7) e' do Anderson.

## 2026-10-01 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T04 (card `t_8b2ed1b7`): operacao de escrita de negocio `oportunidade_upsert` (espelho da oportunidade canonica em `crm.lead`)

Rodada executada de 01/10 21h35 a 22h20 na VPS (-03); 02/10 00h35 a 01h20 UTC.

- **O que foi executado:** aceite completo e provas de dente, na VPS do dev, a partir do checkout
  publicado por `git archive` do commit medido — `/opt/tre/evid-t_8b2ed1b7-r7/repo`, commit
  **`c0a248b590a983fc9a2d5e6b1b719285bd58a13b`** (`feature/TRE-W3-E01-T04`). Comando:
  `sudo -n env TRE_LOG_DIR=/opt/tre/evid-t_8b2ed1b7-r7/logs-aceite bash scripts/odoo/verificar-oportunidade-upsert.sh`
  e, em seguida, o mesmo com `--prova-de-dente`. **Tem de rodar como root** — a dupla descartável
  exige `chown` do `odoo.conf` para o uid 100 do container `odoo:19.0` (foi a falha da rodada 1).
- **Aceite (rodada `r7`):** `RESULTADO: OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas)`, exit 0 —
  suíte pura `MOTOR_API_OK (68 itens, 0 falhas)`; instalação em banco limpo
  `tre_e01_t04_oportunidade` (`ir_module_module.state = installed`); `0 failed, 0 error(s) of 109
  tests` (piso 109; os **27** testes novos de `test_oportunidade_upsert.py` presentes no log); fase
  HTTP real medida por `curl` **de fora do processo** (401 sem token e com token inválido; operação
  declarada como `escrita` na `1.1.0`; `criar` na primeira chamada e `atualizar` na segunda, com o
  mesmo `id`; recusas nomeadas `campo_nao_declarado`/`campo_obrigatorio_ausente`/`valor_invalido`/
  `idempotency_key_ausente`/`idempotency_key_invalida`/`payload_invalido`/`operacao_nao_declarada`
  404, todas sem criar registro); **leitura por SQL** no banco (1 registro por UUID depois de 3
  chamadas; espelho com `name`, `tf_idempotency_key`, `tf_correlation_id`, `tf_next_best_action`;
  `stage_id` e `expected_revenue` de dono do Odoo **idênticos antes e depois**; dois leads
  homônimos seguem dois registros e o upsert pelo UUID de um **não** toca o outro; o lead nasce sob
  o usuário de integração); **18 linhas `TF_API_AUDIT` para 18 chamadas autenticadas** (as 2 sem
  token válido param no Odoo antes do controlador), sem chave, sem `Bearer` e sem payload — nem o
  payload plantado, que é medido de volta na resposta; guarda de ambiente do ADR-005 **na escrita**,
  com o ambiente trocado para `homologacao` **pelo ORM** e o servidor **reiniciado depois** da troca
  (`503 ambiente_nao_permitido`, nada escrito); e limpeza com dev/homolog/prod medidos antes e
  depois.
- **Dentes (harness fail-closed):** `RESULTADO: OPORTUNIDADE_UPSERT_DENTE_OK (3 provas, 0 falhas)`,
  exit 0 — dente 1 (cópia da política **sem** a operação) → `FALHOU (108 itens, 47 falhas)`, com o
  item esperado reprovado e **61 itens medidos**; dente 2 (cópia do motor **sem** a checagem de campo
  na escrita) → `FALHOU (108, 10)`, item esperado, **98 medidos**; dente 3 (cópia do controlador
  **sem** o upsert por identidade) → `FALHOU (108, 12)`, item esperado, **96 medidos** — com dois
  registros no mesmo UUID a terceira chamada é recusada com `valor_ambiguo` (a API não escolhe
  registro por conta própria). Guarda externa: o artefato real saiu intacto (29 arquivos,
  `sha256 2f95f8827576e95658e007bc809c36519fc368a7fb1499e39d4e64a95738a6cd`). Cada prova confere
  **antes** que a mutação foi aplicada, roda o aceite de verdade e exige **o item esperado** entre
  os reprovados; prova que mede menos de 20 itens, que não reprova ou que reprova por outro motivo
  **reprova o harness**.
- **Defeitos encontrados e consertados nas rodadas 1..6 (todos em código meu; cada conserto
  remedido com a bateria inteira):** (1) rodada como usuário comum → `chown` negado, `odoo.conf`
  ilegível para o container (`Connection to the database failed`, 13 falhas); (2) `url_open` com
  `json={}` **vira GET** e o roteador responde `405` antes do controlador (2 itens não mediam nada);
  (3) **semente criada pelo ORM da suíte é invisível** para a conexão que serve o HTTP — o upsert
  criava outro registro e 3 itens mediam o oposto do que afirmam; (4) `tf_idempotency_key` é campo
  **declarado** escrito com o valor enviado, não preenchido pelo envelope (2 itens), e o corpo de
  criação do verificador não o enviava (1 item de SQL media campo que nunca foi enviado); (5) o
  extrator de campo `campo()` do verificador não carregava o JSON (`d` indefinido) e **19 itens**
  comparavam vazio; (6) no harness de dentes, o dente 1 acusava falso `FALHOU` (conferia a ausência
  da operação por `grep` no arquivo, e a `descricao` da política citava o nome) e os dentes 1 e 3
  apontavam como item esperado o rótulo do **OK** em vez do rótulo do **FALHOU**. As rodadas
  `r1..r6` ficam na VPS com os `FALHOU` originais; a rodada que mede o artefato entregue é a `r7`.
- **O que NÃO foi tocado (medido pelo próprio verificador):** `api/motor.py` (`6a3e52d1…`) e
  `controllers/api_controlada.py` (`9a52f642…`) com o **mesmo sha256 do E01-T01** — este card não
  mexeu no motor nem no controlador, só declarou a operação na política (`dccf10a7…`, versão
  `1.1.0`); a instância do dev (`odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev`) de pé e com
  os **mesmos 4 bancos** antes e depois; `/opt/tre/homolog` e `/opt/tre/prod` sem nenhum arquivo;
  nenhum container, rede ou `/tmp` residual `e01t04-*`; `/opt/tre/repo` sem escrita (a cópia medida
  é o `git archive` do commit em `evid-t_8b2ed1b7-r7`).
- **Verificadores do projeto (no worktree do commit):** `scripts/verificar_estrutura.sh` → `PASS`
  RC=0; `scripts/secret_scan.sh` → `PASS` RC=0; `scripts/verificar_papeis.sh` → `PASS (0 falhas)`
  RC=0; `python3 scripts/odoo/testar_motor_api.py` → `MOTOR_API_OK (68 itens, 0 falhas)` RC=0. O
  `verificar_estrutura.sh` foi estendido para exigir **versionados** a suíte nova e o runbook, e
  **executável** o verificador do card.
- **Logs brutos (agente, na VPS):** `/opt/tre/evid-t_8b2ed1b7-r7/` — `aceite.out`, `dente.out`,
  `logs-aceite/` (`0-motor-puro.out`, `1-instalacao.log`, `2-teste.log`, `3-preparo.log`,
  `3b-servidor.log`) e `logs-dente/` (`dente-{1,2,3}-*.out`).
- **Segredos:** nenhum valor nesta entrada e nenhum no repositório. A chave de API da fase HTTP
  nasceu **na VPS**, em arquivo `600` dentro do diretório descartável do preparo, lida pelo `curl`
  por arquivo de configuração (não aparece em `ps`, stdout nem log) e morreu com o diretório; a
  chave do passo 3d foi gerada do mesmo jeito depois da troca de ambiente.
- **Verificação independente:** quem entrega não homologa — o veredito deste card é do estágio 6
  (perfil `tester`) e a ratificação da versão 19.0 / homologação (estágio 7) é do Anderson. A
  publicação do módulo na cópia operacional `/opt/tre/repo` segue como pendência herdada do E03-T01.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E01-T05 (card `t_cb615018`): operacao de escrita de negocio `atividade_criar` (a ATIVIDADE comercial em `mail.activity`, na ancora declarada)

Rodada executada em 02/10, 03h20 a 04h55 UTC (00h20 a 01h55 na VPS, -03).

- **O que foi executado:** aceite completo e provas de dente, na VPS do dev, a partir do checkout
  publicado por `git archive` do commit medido — `/opt/tre/evid-t_cb615018-r3/repo`, commit
  **`e9f4c478c9f87c3970d53dd6e0803c9d9e9c97db`** (`feature/TRE-W3-E01-T05`, empilhado em
  `feature/TRE-W3-E01-T03` com o `E01-T04` mesclado). Comando:
  `sudo -n env TRE_LOG_DIR=/opt/tre/evid-t_cb615018-r3/logs-aceite bash scripts/odoo/verificar-atividade-criar.sh`
  e, em seguida, o mesmo com `--prova-de-dente`. **Tem de rodar como root** — a dupla descartavel
  exige `chown` do `odoo.conf` para o uid 100 do container `odoo:19.0`.
- **Aceite (rodada `r3`):** `RESULTADO: ATIVIDADE_CRIAR_OK (119 itens, 0 falhas)`, exit 0 — suite pura
  `MOTOR_API_OK (123 itens, 0 falhas)`; instalacao em banco limpo `tre_e01_t05_atividade`
  (`ir_module_module.state = installed`, e `installed` depois da suite); `0 failed, 0 error(s) of 166
  tests` (piso 147; os **19** testes novos de `test_atividade_criar.py` presentes no log); fase HTTP
  real medida por `curl` **de fora do processo** (401 sem token e com token invalido; operacao
  declarada como `escrita` com chave exigida na politica `1.3.0`; criacao com `acao_efetiva=criar` e o
  `id` na resposta; `correlation_id` ecoado; recusas nomeadas `campo_fixo_divergente` com `crm.lead`
  (**0** atividades em `crm.lead`), `campo_nao_declarado` — inclusive pelo id interno `res_model_id` —,
  `campo_obrigatorio_ausente` sem `res_id`, `idempotency_key_ausente`, `idempotency_key_invalida`;
  `dry_run` que descreve sem criar; **chave sem escrita no documento ancorado -> `403 acesso_negado`
  sem criar**); **leitura por SQL** no banco (a atividade nasceu ancorada em `ir_model.model =
  res.partner` e com o `res_id` informado, resumo/prazo/tipo gravados, o rastro
  `tf_idempotency_key`/`tf_correlation_id` registrado **na atividade** e `create_uid =
  tf_api_integracao`, isto e, o dono da chave); **12 linhas `TF_API_AUDIT` para 12 chamadas
  autenticadas**, sem `Bearer`, sem chave e sem payload de negocio (a recusa por ACL tambem deixa
  linha); guarda de ambiente do ADR-005 **na escrita**, com o ambiente trocado para `homologacao`
  **pelo ORM** e o servidor **reiniciado depois** da troca (`503 ambiente_nao_permitido`, nada
  escrito); contrato do modulo (1 rota, `auth='bearer'`, so `POST`, **0 SQL** na API, motor puro,
  override presente com `_inherit = "mail.activity"`); e limpeza com dev com os **mesmos 4 bancos**
  antes e depois, `homolog`/`prod` com 0 arquivo e `/opt/tre/repo` intocado.
- **Lacunas DECLARADAS e medidas (AC9):** o replay da **mesma** `idempotency_key` cria uma SEGUNDA
  atividade (1 -> 3 registros no fim da fase, contando o replay) — a dedup por chave e' o
  `TRE-W3-E02-T02`; e atividade ancorada em `crm.lead` **nao** e' servida (recusa nomeada, nunca
  registro no lugar errado): aceitar um CONJUNTO declarado de ancoras exige vocabulario novo no
  mecanismo de valor fixo, cujo dono e' o `E02-T02`.
- **Dentes (harness fail-closed):** `RESULTADO: ATIVIDADE_CRIAR_DENTE_OK (4 provas + 2 controles do
  proprio harness, 0 falhas)`, exit 0 — dente 1 (copia da politica **sem** a operacao) ->
  `FALHOU (94 itens, 36 falhas)`, item `atividade_criar nao declarada como escrita com chave`;
  dente 2 (copia da politica **sem o valor fixo** da ancora, `valores_fixos` = `{}`) ->
  `FALHOU (94, 41)`, item `ancora divergente (crm.lead) -> HTTP 422 campo_fixo_divergente`;
  dente 3 (copia do controlador **sem o ramo de criacao**) -> `FALHOU (94, 13)`, item
  `a criacao nao devolveu id de atividade`; dente 4 (copia do **modulo sem a traducao da ancora**) ->
  `FALHOU (94, 18)`, item de SQL `ancora gravada diferente` — este e' o dente desta entrega: prova
  que a unica linha de codigo novo **nao e' decorativa**. Guarda externa: o artefato real saiu
  intacto (33 arquivos, `sha256 6a7ca438…` antes e depois). Cada prova confere **antes** que a
  mutacao foi aplicada e exige **o item esperado** entre os reprovados; prova que nao reprova, que
  reprova por guarda/ambiente ou que mede o artefato intacto **reprova o harness**.
- **Defeitos encontrados e consertados (ambos no PROPRIO aceite, remedidos na `r3`):** (1) rodada
  `r1` (`/opt/tre/evid-t_cb615018-r1`) abortou na suite do Odoo com `1 failed, 0 error(s) of 166
  tests`: o item 16 (AC7) mandava a chave de API no **corpo** em vez do cabecalho `Authorization` e
  media o envelope (`400 payload_invalido`), nao a ACL — o helper `_criar()` misturava o parametro
  `chave` nos campos do payload; (2) rodada `r2` (`/opt/tre/evid-t_cb615018-r2`, dentes verdes)
  mediu `119 itens, 1 falha`: o item de contrato do passo 4 casava o padrao `idempotency=True`
  enquanto o resumo que ele mesmo monta imprime `chave=True` — item que reprovava com a declaracao
  certa na mao, e sem dente que o cobrisse (o modo dente roda so' a fase HTTP). As rodadas `r1` e
  `r2` ficam na VPS com os `FALHOU` originais; a rodada que mede o artefato entregue e' a `r3`.
- **O que NAO foi tocado:** `api/motor.py` (`2a79c952…`) e `controllers/api_controlada.py`
  (`b8c53117…`) — `git diff` contra a base consolidada da onda (`9513335`) e' **vazio** nos dois: este
  card declarou a operacao na politica (`d15f7e0a…`, versao `1.3.0`) e acrescentou **uma** superficie
  de codigo, `models/mail_activity.py` (`33d520ee…`, a traducao do nome declarado para
  `res_model_id`); `scripts/odoo/verificar-atividade-criar.sh` (`8317a501…`),
  `tests/test_atividade_criar.py` (`d2ff36f2…`), `tests/__init__.py` (`15641b44…`),
  `__manifest__.py` (`fc02aedc…`), `models/__init__.py` (`808687cb…`) e
  `scripts/odoo/preparar_api_teste.py` (`435af3c6…`, ganhou `TRE_API_GRUPOS` com o comportamento
  anterior como padrao — a segunda chave da prova de ACL). A instancia do dev (`odoo-dev`,
  `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev`) ficou de pe com os **mesmos 4 bancos** antes e depois.
- **Verificadores do projeto (no worktree do commit):** `scripts/verificar_estrutura.sh` -> `PASS`
  RC=0; `scripts/secret_scan.sh` -> `PASS` RC=0; `scripts/verificar_papeis.sh` -> `PASS (0 falhas)`
  RC=0; `python3 scripts/odoo/testar_motor_api.py` -> `MOTOR_API_OK (123 itens, 0 falhas)` RC=0. O
  `verificar_estrutura.sh` ja' exigia versionados a suite nova e o runbook e executavel o verificador
  do card.
- **Logs brutos (agente, na VPS):** `/opt/tre/evid-t_cb615018-r3/` — `aceite.out`, `dente.out`,
  `logs-aceite/` (`0-motor-puro.out`, `1-instalacao.log`, `2-teste.log`, `3-preparo.log`,
  `3-preparo-sem-escrita.log`, `3b-servidor.log`, `3d-*.log`) e `logs-dente/`
  (`dente-{1,2,3,4}-*.out`); rodadas anteriores em `evid-t_cb615018-r1/` e `evid-t_cb615018-r2/`.
- **Segredos:** nenhum valor nesta entrada e nenhum no repositorio. As duas chaves de API da fase
  HTTP nasceram **na VPS**, em arquivos `600` dentro do diretorio descartavel do preparo, lidas pelo
  `curl` por arquivo de configuracao (nao aparecem em `ps`, stdout nem log) e morreram com o
  diretorio; a chave do passo 3d foi gerada do mesmo jeito depois da troca de ambiente.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e' do estagio 6
  (perfil `tester`) e a ratificacao da versao 19.0 / homologacao (estagio 7) e' do Anderson. A
  publicacao do modulo na copia operacional `/opt/tre/repo` segue como pendencia herdada do E03-T01.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E03-T01 (card `t_85cb2838`): a fila de saída do Odoo e a PORTA ÚNICA de ingestão dos eventos no PostgreSQL

- **O que o card entrega (e o que ele recusa a fazer).** O trecho `Odoo → PostgreSQL` dos eventos do
  contrato (`events.odoo_to_pg`, 7 tipos: 5 de funil + `ACTIVITY_COMPLETED` + `MEETING_CREATED`): o
  fato nasce no ORM (`crm.lead`, `mail.activity`, `calendar.event`), vira linha de uma fila de saída
  do próprio Odoo (`tf.evento.outbox`, `models/tf_evento_outbox.py`) e sai por **uma única porta** —
  webhook autenticado do n8n (`/webhook/tre/odoo-eventos`), núcleo JS puro
  (`n8n/codigo/nucleo-ingest-eventos.js`) que valida o envelope na ordem declarada pelo contrato,
  escreve a trilha em `sales_intelligence.sync_events` e recusa o resto com motivo nomeado. **O
  módulo não tem caminho paralelo para o banco** — provado, não prometido: `test_22` roda uma lente
  AST sobre o pacote do módulo e reprova `psycopg`/`pg8000`/`sales_intelligence`/`cr.execute`/
  `dbname` (lista de exceções explícita, sem afrouxar a regra).
- **Ordem das recusas = contrato.** `envelope.ordem_da_validacao` é executada nessa ordem e cada
  quebra tem nome (`envelope_sem_versao`, `versao_nao_suportada`, `idempotency_key_invalida`,
  `campo_exigido_ausente:<campo>`, `evento_fora_do_contrato`, ...); a recusa nomeada é **decisão
  final, sem retry** (HTTP 422) e deixa rastro na trilha (`status REFUSED`).
- **O cron nasce INATIVO** (`data/ir_cron_tf_eventos.xml`, `active = False`) de propósito: ligar a
  varredura periódica é passo operacional, não efeito colateral de instalação (runbook
  `docs/runbooks/odoo-eventos-para-pg.md`).
- **Aceite (o artefato medido é o commit publicado `597f2dd`).** `bash
  scripts/n8n/verificar-odoo-eventos.sh` na VPS → **`EVENTOS_ODOO_PG_OK (84 itens, 0 falhas)`**, em
  trio descartável próprio (`postgres:16` + `odoo:19.0` + `n8n`) com rede própria, e a suíte do
  módulo rodando dentro da instalação: **`0 failed, 0 error(s) of 22 tests`** da classe do card
  (`TestEventosOdooPg`). Passos: 0 lente estrutural (60 itens) + conferidor de contrato + montador +
  suíte do núcleo (98 itens); 1 schema e módulo instalado; 2 remetente configurado por token de
  arquivo `600`; 3 n8n com cofre, workflow ATIVO e servidor no ar; A porta sem token → 403 e **nada
  escrito** na trilha; B os fatos de negócio viram os 7 tipos na fila (8 eventos, multiplicidade
  declarada); C entrega pela porta e trilha no PostgreSQL (7 `operation`, `entity_type` da origem,
  UUID canônico da oportunidade, `source_version` do envelope, 8 chaves de idempotência distintas);
  D reenvio do mesmo envelope não cria segunda linha; E **4 recusas nomeadas** (sem versão, fora da
  lista fechada, campo exigido ausente, chave fora do formato) + token errado; F porta fora do ar →
  `RETRY` com `last_error` e teto de 3 tentativas e, com a porta de volta, entrega; fecho: o token
  não aparece no repo nem em `request_payload` da trilha, a instância do dev tem os mesmos bancos
  antes/depois e o `sha256` dos 9 artefatos sob teste não muda durante a medição.
- **Prova de dente (fail-closed).** `bash scripts/n8n/verificar-odoo-eventos.sh --prova-de-dente` →
  **`EVENTOS_ODOO_PG_OK (6 itens, 0 falhas)`**: baseline sem mutação verde + 4 mutações nomeadas
  (`sem_versao`, `sem_formato_da_chave`, `sem_campos_exigidos`, `sem_on_conflict`), cada uma exigindo
  que **o item declarado** reprove; o juiz é conferido com saídas sintéticas (cumprido / nada / item
  errado) e mutação que não se aplica (âncora ausente) reprova o harness.
- **Defeitos encontrados e consertados no caminho** (todos medidos): (1) `odoo.conf` `600` não é
  lido pelo uid 100 do container → `644` (o diretório continua `700`); (2) n8n rodando com o `--user`
  do host precisa do **HOME inteiro** montado — ele escreve `~/.n8n` **e** `~/.cache`, e montar só o
  `.n8n` morria com `EACCES mkdir '/home/node/.cache'`; (3) `--without-demo=all` é inválido →
  `True`; (4) o contrato de dados não existe dentro do container (só o diretório do módulo é
  montado) → o teste congela o contrato nele mesmo; (5) `crm.lead.company_currency_id.name` não
  existe na 19.0 → `company_currency.name`; (6) `mail.activity._action_done` **arquiva**
  (`active=False`) e grava `date_done` — o teste media `exists()`, o que dava falso positivo;
  (7) `test_13` (usuário sem ACL na fila) reescrito para o desenho real (o detector grava a fila com
  `sudo`); (8) a guarda fatal pós-postgres abortava a sub-rodada do dente antes do passo que ela
  existe para medir — passou a abortar só com o ambiente quebrado; (9) os trechos declarados no
  mutador não eram os textos dos itens do aceite e o ramo `FALHOU` tinha outra redação — o item que
  uma mutação deve reprovar passou a ter a mesma identidade nos dois ramos, e o dente **achou um
  buraco real**: o aceite não media recusa por **formato de chave de idempotência**, que virou o
  quarto item do passo E.
- **Rodadas anteriores ficam na VPS com os `FALHOU` originais** (`/opt/tre/e03t01-r{6..10}/`,
  `/tmp/verificacao-e03-r*`); as rodadas que medem o artefato entregue são `r14` (aceite) e `r15`
  (dente), em `/opt/tre/e03t01-r11/aceite.log` e `/opt/tre/e03t01-r11/dente.log`.
- **Verificadores do projeto (no worktree do commit):** `scripts/verificar_estrutura.sh` → `PASS (0
  falhas)`; `scripts/secret_scan.sh` → `PASS` (o próprio aceite tinha um falso positivo: a chave
  `db_password` do `odoo.conf` agora sai de variável, com o valor do segredo só em memória).
- **Segredos:** nenhum valor nesta entrada e nenhum no repositório. Senha do Postgres, chave de
  admin do Odoo, token da porta e chave de criptografia do n8n nascem **na VPS** (`openssl rand`),
  vivem em arquivos `600` dentro do diretório descartável `700` e morrem com ele.
- **Verificação independente:** quem entrega não homologa — o veredito deste card é do estágio 6
  (perfil `tester`) e a homologação (estágio 7) é do Anderson.
## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E02-T01 (card `t_ba84b412`): consumidor de outbox em n8n (fila -> porta unica da API controlada)

- **Escopo entregue:** o consumidor da fila `sales_intelligence.outbox_events` declarado em **quatro
  artefatos versionados** — contrato `n8n/contracts/outbox-consumer.v1.json` (esquema 1, versao 1.0.0),
  nucleo `n8n/codigo/nucleo-outbox-consumer.js` (JS puro: roda em `node` e no Code node), dois SQL
  (`n8n/sql/ler-pendentes.sql` so leitura; `n8n/sql/registrar-resultado.sql` grava estado do evento +
  linha de `sync_events` **na mesma transacao**) — e o workflow `n8n/workflows/TRE-outbox-consumer.json`
  **derivado** deles pelo montador `scripts/n8n/montar_workflow.py` (nucleo e contrato embutidos byte a
  byte; workflow nasce inativo; `--conferir` reprova divergencia). O consumidor so alcanca o Odoo pela
  **porta unica** (`POST /tf/api/v1/<operacao>`); grep estrutural reprova XML-RPC, `execute_kw`,
  `/jsonrpc`, `psycopg` e `res_partner` dentro do workflow.
- **Leitura e escrita separadas (single-writer):** a fila e' lida por
  `status = ANY(ARRAY['PENDING','RETRY'])` com `LIMIT` declarado no contrato; a **unica** mutacao e' o
  `registrar-resultado.sql` (UPDATE do evento + INSERT da trilha, uma transacao). Nenhum caminho do
  nucleo faz UPDATE na tabela da fila.
- **Defeito 1 (achado pelo aceite, corrigido):** o no HTTP do workflow nao declarava
  `authentication: genericCredentialType` + `genericAuthType: httpHeaderAuth` — o n8n **nao enviava** a
  credencial e a API respondia `401 use an API Key with a Bearer ...`. Correcao: credencial declarada
  pelo **id/nome** (nunca valor — o valor e' lido pelo preparo e morre com o diretorio), item
  estrutural exigindo o tipo, item no aceite medindo o header e **sonda independente** da chave
  (`dry_run`, 200) para separar "chave invalida" de "n8n nao mandou o header".
- **Defeito 2 (achado pelo aceite, corrigido na raiz — vale para o produto, nao so para o teste):** o
  no HTTP disparava o lote **em paralelo**: dois eventos da MESMA identidade na mesma leva chegavam a
  API antes de o primeiro commitar, a busca de identidade da API nao via o registro vizinho e o
  resultado eram **dois parceiros para a mesma empresa** (`acao_efetiva:"criar"` nas duas respostas,
  E1/E2 com **2 ms** de intervalo na auditoria do servidor — contra 13 linhas de auditoria medidas onde
  se esperava 3). Correcao: entrega **serializada** (`batching.batchSize = 1` + intervalo **declarados
  no contrato** e lidos pelo montador), item no aceite medindo o intervalo entre E1 e E2 (**217 ms**
  medidos no artefato final, duas ordens de grandeza acima do piso paralelo) e item de negocio exigindo
  **um** parceiro com o `name` **atualizado** pelo segundo evento. Limite declarado no runbook: a
  serializacao protege **dentro** de uma instancia de n8n; concorrencia entre instancias (sem
  `FOR UPDATE SKIP LOCKED`) fica como card proprio antes de escalar.
- **Outros defeitos do harness (rodadas 2..8), todos corrigidos:** contagem de auditoria sem base
  (a sonda da chave audita — passou a ser **delta** de uma base medida antes do ciclo 1); verificacao do
  id estavel do workflow dependia do formato de `list:workflow` (trocada por `export:workflow --id`);
  `podar()` vs `limpar()` confundidos na leitura de campos do parceiro (whitespace do `psql`); `chown`
  errado para o uid do container no preparo; `odoo_ci()` precisa de `--stop-after-init
  --log-level=info` (senao a instalacao do modulo pendura); `secret_scan.sh` chamado do diretorio
  errado; controle do juiz dos dentes com 2 saidas sinteticas julgadas errado (fail-closed: o juiz
  devolvia `MUTACAO_SEM_DENTE`/`NAO_CONTA` onde o controle esperava `DENTE_CUMPRIDO`).
- **Aceite (artefato final, sobre copia byte a byte na VPS):** `OUTBOX_CONSUMER_OK (81 itens,
  0 falhas)`, exit 0 — trio descartavel proprio (postgres:16 + odoo:19.0 + n8nio/n8n:latest, banco
  `tre_e02_outbox`, rede propria; **sem tocar** no dev/homolog/prod, medido antes e depois). Inclui:
  suite pura do nucleo `NUCLEO_CONSUMIDOR_OK (87 itens)`; lente estrutural `CONTRATO_WORKFLOW_OK (55
  itens)`; 7 eventos de fila no ciclo 1 medidos item a item (valido / atualizacao da mesma identidade /
  sem `event_version` / fora do contrato / sem `name` / sem identidade / identidade ambigua na API);
  Odoo **parado** -> `RETRY`, `attempts=1`, trilha `FAILED`; Odoo de volta -> o retry entrega
  (`PROCESSED`, `attempts=2`, **uma** linha de trilha, **um** parceiro); evento no teto ->
  `DEAD_LETTER` **sem** chamada e **sem** escrita; sonda da chave 200 em `dry_run`; chamadas
  autenticadas por delta de auditoria (3 no ciclo 1, 4 no total); `sha256` dos 5 artefatos fixado no
  inicio e reconferido no fim.
- **Dentes (prova de que o aceite tem dentes):** `OUTBOX_CONSUMER_DENTE_OK` — as 4 mutações
  nomeadas (`sem_validacao_de_envelope`, `sem_incremento_de_tentativas`, `sem_teto_de_tentativas`,
  `mapeamento_trocado`) saíram **`DENTE_CUMPRIDO`** (o item esperado reprovou em cada sub-run) e o
  **juiz dos dentes** foi conferido por 4 saídas sintéticas (mutação sem efeito → `MUTACAO_SEM_DENTE`,
  mutação cumprida → `DENTE_CUMPRIDO`, ambiente quebrado → `NAO_CONTA`, âncora quebrada → `NAO_CONTA`).
  O juiz é o que separa "dente" de "ambiente quebrado" — sem ele, um aceite abortado passaria por
  prova. Nesta rodada ele próprio reprovou antes de valer (2 de 4 saídas sintéticas julgadas errado)
  e foi corrigido antes de emitir veredito.
- **Lição registrada (a mesma do E01-T02, agora com o item consertado):** o veredito do dente depende
  de o **texto do item** existir tanto no ramo `OK` quanto no ramo `falhou`. Os itens E3/E8/E9/E1
  passaram a repetir o rótulo estável no ramo de falha (com o diagnóstico entre parênteses) — sem
  isso o juiz devolve "âncora quebrada" e o dente vira falso vermelho.
- **Verificadores do projeto (no worktree):** `bash scripts/verificar_estrutura.sh` ->
  `PASS (0 falhas)`; `bash scripts/secret_scan.sh` -> `PASS (nenhum segredo versionado)`. O
  `verificar_estrutura.sh` foi estendido para exigir versionados os 12 artefatos deste card (contrato,
  nucleo, 2 SQL, workflow, montador, lente, suite pura, mutador, massa ambigua, harness e runbook) e
  com permissao de execucao para os 4 scripts.
- **Segredos:** nenhum valor nesta entrada e nenhum valor no repositorio. A chave da API nasceu na VPS
  em arquivo `600` no diretorio descartavel do preparo, lida pelo n8n por arquivo de configuracao, e
  morreu com o diretorio. O contrato referencia a credencial **apenas** por id/nome.
- **Limpeza medida:** 0 container e 0 rede `e02t01-*` no fecho (verificado por mim no servidor, item
  a item: `docker ps -a`/`docker network ls`) + diretório do preparo removido. Ressalva registrada: a
  rodada **`--manter`** (v5, usada para post-mortem de um defeito) preserva de propósito o diretório do
  preparo — e ele contém a chave da API em claro; resíduo encontrado no fecho deste card e removido
  (`rmtree`), com o aviso agora no runbook §5. O diretório default de **logs**
  (`/tmp/verificacao-outbox-consumer`) fica de propósito para leitura posterior. Os containers do dev
  (`odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `proxy-dev`) de pé o tempo todo.
- **Arvore medida:** commit `0eaae57` deste branch (`feature/TRE-W3-E02-T01`) — os 5 artefatos
  derivados foram medidos com os `sha256` do proprio aceite, e o commit seguinte a ele acrescenta
  **apenas documentacao** (este registro e o runbook §5), conferivel por
  `git diff --name-only 0eaae57 HEAD`.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card e' do estagio 6
  (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.

### Rodada 2 — revisao independente pediu mudancas: 3 defeitos do proprio artefato de teste, corrigidos e remedidos (commit `38b539f`)

A revisao da rodada 1 (`tester`, parecer em `evidencia/PARECER-t_ba84b412-r1.md`) **reproduziu o aceite
verde na base do revisor** (`OUTBOX_CONSUMER_OK`, 81 itens, 0 falhas, EXIT=0; dentes 4/4; controle
externo proprio mostrando 2 parceiros ao remover a serializacao) e reprovou **o artefato de teste
entregue**, nao o produto. Os tres achados e a correcao, cada uma remedida:

1. **BLOQUEANTE — `--prova-de-dente` fail-open (verde sem medir dente nenhum).** O laco so imprimia o
   veredito de cada dente e o script fechava com `OUTBOX_CONSUMER_DENTE_OK (...)` + `exit 0`
   **incondicionalmente**; o contador `FALHAS` e o controle do juiz nunca eram lidos naquele ramo.
   Reproducao do revisor: `TRE_IMAGEM=odoo:nao-existe-9.9 ... --prova-de-dente` -> 4x `NAO_CONTA` +
   `DENTE_OK` + `EXIT=0`. **Correcao (fail-closed):** (a) sub-run **NAO mutado** (baseline) tem de ficar
   verde **antes** de contar dente; (b) os vereditos vao para **arquivo** (o laco roda em subshell) e a
   agregacao roda fora dele; (c) qualquer veredito que nao seja `DENTE_CUMPRIDO`
   (`NAO_CONTA`/`MUTACAO_SEM_DENTE`/`MUTACAO_NAO_APLICADA`), baseline vermelho ou juiz com falta fecha
   com `OUTBOX_CONSUMER_DENTE_FALHOU` + `exit 1`. Remedido na VPS: `OUTBOX_CONSUMER_DENTE_OK (4/4
   dentes cumpridos; juiz conferido; baseline nao mutado verde)` **EXIT=0**, com o baseline medindo
   `OUTBOX_CONSUMER_OK (81 itens, 0 falhas)`; e o **mesmo controle do revisor** agora fecha
   `OUTBOX_CONSUMER_DENTE_FALHOU (baseline NAO mutado nao ficou verde; 4 sem dente de 4)` **EXIT=1**.
2. **Afirmacao falsa no registro (sha256 "reconferido no fim" que nao existia).** O harness so imprimia
   os sha256 nas guardas (2 linhas de impressao, no inicio). **Correcao:** item que **fixa** o sha256
   dos 5 artefatos nas guardas + item que **reconfere** no fecho, com juiz proprio de 2 saidas sinteticas
   (`controle do juiz do sha256`) para o item nao ser vacuO. Medido de verdade, com adulteracao real: um
   controle externo (fora do card) adiciona uma linha a `n8n/sql/ler-pendentes.sql` **no meio** da
   medicao e o aceite fecha `OUTBOX_CONSUMER_FALHOU (81 itens, 1 falha)` **EXIT=1** com
   `FALHOU sha256 dos 5 artefatos MUDOU durante a medicao` e os dois digests lado a lado; na rodada
   limpa o item imprime `OK ... reconferido no fecho: identico ao fixado nas guardas`.
3. **ADVISORY — item morto na lente estrutural** ("nenhum host literal no workflow"): reprovava apenas
   o loopback, entao passava com qualquer outro host literal (medido pelo revisor com
   `http://host-literal.example:8069/...` gravado no workflow). **Correcao:** o item passou a medir o
   texto **inteiro** do workflow (fora do parametro `url` das portas) **e** o proprio parametro `url` da
   porta unica. Controles meus: 2 mutantes (host cravado dentro do parametro `url`; host cravado fora das
   portas) -> `FALHOU` nos **2/2**, versionado -> `OK` (2 itens de lente a mais reprovados nos mutantes,
   como esperado, por o workflow divergir do montado).

- **Identidade do que foi medido (na VPS, copia propria do commit):** `/opt/tre/e02t01-r2/repo` =
  `git archive 38b539f`. Os **5 artefatos sob teste NAO mudaram** de sha256 em relacao a rodada 1
  (contrato `3ee87a10...d7b7`, nucleo `be765699...f986`, ler `9234b566...dfb2`, registrar
  `0eac7d7a...d017`, workflow `82212ffd...78e2`) — mudaram so os dois artefatos de teste
  (`scripts/n8n/verificar-outbox-consumer.sh` `cb643d11...375f`;
  `scripts/n8n/conferir_contrato_e_workflow.py` `f5d974df...3ad0`).
- **Aceite remedido:** `OUTBOX_CONSUMER_OK (**83 itens, 0 falhas**)` **EXIT=0** (banco `tre_e02t01_r2`,
  trio descartavel proprio; 83 = 81 da rodada 1 + os 2 itens novos de sha256). Inclui
  `NUCLEO_CONSUMIDOR_OK (87 itens)` e `CONTRATO_WORKFLOW_OK (55 itens)`; entregas em SERIE
  (**E1 -> E2 com 188 ms**; piso paralelo 2 ms); chamadas autenticadas por delta de auditoria = 4; dev
  com os MESMOS bancos antes/depois; `/opt/tre/{homolog,prod}` com 0 arquivo; 0 container e 0 rede
  `e02t01-*` no fecho e nenhum `/tmp/dente-e02t01-*` residual.
- **Controles meus da rodada 2 (fora do card):** (a) fail-open: mesmo cenario do revisor (imagem
  inexistente) -> `DENTE_FALHOU` + exit 1 (na VPS e localmente); (b) dente do item de sha256:
  adulteracao real no meio da medicao -> 1 falha exatamente nesse item, exit 1; (c) dente do item de host
  literal: 2 mutantes reprovados 2/2, versionado OK; (d) juizes conferidos por saidas sinteticas
  (dente: 4; sha256: 2).
- **Verificadores do projeto (no worktree, commit `38b539f`):** `verificar_estrutura.sh` PASS (0
  falhas); `secret_scan.sh` PASS (nenhum segredo versionado); `verificar_papeis.sh` PASS (0 falhas).
- **Arvore medida:** commit `38b539f` deste branch; o commit seguinte acrescenta **apenas documentacao**
  (este registro, o runbook §5 e o CHANGELOG) — conferivel por `git diff --name-only 38b539f HEAD`.
- **Nota:** o diretorio `/tmp/verificacao-outbox-consumer` que existe na VPS e' residuo **da rodada 1**
  (01/10 23:10-23:40 UTC, ja' declarado); a rodada 2 gravou em `/opt/tre/e02t01-r2/logs-*`.
- **Nao e homologacao:** quem entrega nao homologa — o veredito deste card segue com o estagio 6
  (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E02-T02 (card `t_3bde06ab`): dedup por `idempotency_key` no consumidor de outbox (replay por chave)

- **O que foi entregue:** o consumidor do `TRE-W3-E02-T01` ganhou o **dedup por chave**. O contrato
  `n8n/contracts/outbox-consumer.v1.json` passa a **1.1.0** com o bloco `dedup` (criterio de replay,
  ordem da decisao, status final, `incrementa_tentativas: 0`); o nucleo ganhou a decisao **REPLAY**
  (chave ja' entregue) e os helpers `chavesDoLote` / `trilhaPorChave` / `registroDeReplay` /
  `chaveDoEvento`; dois SQL novos — `n8n/sql/ler-trilha.sql` (**uma** consulta por lote, somente
  SELECT) e `n8n/sql/registrar-replay.sql` (finaliza o evento **reaproveitando** a trilha, com guarda
  fail-closed `EXISTS ... t.status='COMPLETED'`); o workflow derivado ganhou os nos `Chaves do lote`,
  `Ler trilha (chaves entregues)` (**`alwaysOutputData`**: sem ele o ciclo sem chave entregue mataria a
  cadeia), `Decisao: replay?` e `Registrar replay (outbox)`. O caminho do replay **nao tem no de HTTP
  alcancavel** — a lente mede que so' o ramo de entrega alimenta a porta unica.
- **Identidade do que foi medido (na VPS, copia propria do working tree do card):** `/opt/tre/e02t02-r3`
  = commit `bdd2aea`; o commit seguinte acrescenta **apenas documentacao** (este registro e o
  CHANGELOG) — conferivel por `git diff --name-only bdd2aea HEAD`. O proprio aceite
  (`scripts/n8n/verificar-outbox-consumer.sh`) tem **o mesmo sha256** no worktree e na VPS
  (`36907773e110782b...`): a medicao e' do script entregue (a rodada anterior, `/opt/tre/e02t02-r2`,
  media `bd144122...`, antes do conserto da ancora descrito abaixo). Os **7 artefatos sob teste**
  (fixados nas guardas e **reconferidos no fecho**): contrato `2b4d9cef...c13af8`,
  nucleo `91ad375a...a5657f2`, ler-pendentes `9234b566...797ddfb2` e registrar-resultado
  `0eac7d7a...1472d017` (**inalterados** desde o T01), ler-trilha `8d788e78...eea0ef86`,
  registrar-replay `fc438d0a...80f14ec30` e workflow `9393b3dc...5de7e30c`.
- **Aceite:** `OUTBOX_CONSUMER_OK (**97 itens, 0 falhas**)` **EXIT=0** (banco `tre_e02_outbox`, trio
  descartavel proprio postgres+odoo+n8n), incluindo `CONTRATO_WORKFLOW_OK (93 itens, 0 falhas)` e
  `NUCLEO_CONSUMIDOR_OK (124 itens, 0 falhas)`.
- **Ciclo 5 (o que este card mede, evento a evento):** E1 (chave ja' entregue, trilha `COMPLETED`) e E7
  (trilha `REFUSED`) voltam a fila com os **IDs originais** -> E1 vira **REPLAY** (`PROCESSED`,
  `attempts` inalterado em 1, `last_error` nulo) e E7 **volta a ser entregue** e e' recusado de novo
  (`DEAD_LETTER/2` com `valor_ambiguo`). O ciclo com **2 eventos na fila chamou a API UMA vez** (so' o
  E7). O replay **nao escreveu no CRM** (1 parceiro, `name` e `score` da entrega original preservados),
  **nao criou linha na trilha** (9 linhas antes e depois) e a linha do E1 e' a **mesma** (mesmo `id`,
  mesmo `completed_at` e mesma resposta `acao_efetiva=criar`); a trilha do E7 continua **1 linha
  `REFUSED`** (o upsert do retry nao duplica). Total de chamadas autenticadas do aceite = **5** (E1, E2,
  E7, retry do E8 e o E7 do ciclo 5). Demais medicoes do T01 preservadas: entregas em SERIE (**E1 -> E2
  com 192 ms**; piso paralelo medido 2 ms) e 7 eventos PENDING medidos no ciclo 1.
- **Prova de dente:** `OUTBOX_CONSUMER_DENTE_OK (**6/6** dentes cumpridos; baseline nao mutado verde;
  juiz conferido)` **EXIT=0** — as 4 mutacoes herdadas (`sem_validacao_de_envelope`,
  `sem_incremento_de_tentativas`, `sem_teto_de_tentativas`, `mapeamento_trocado`) e **2 novas deste
  card**: `sem_consulta_de_trilha` (a consulta da chave some do lote) reprova `E1 reenfileirado` e
  `guarda_de_sucesso_afrouxada` (o nucleo aceita trilha de QUALQUER status) reprova
  `E7 (chave na trilha como REFUSED)`. Os juizes continuam conferidos por saidas sinteticas (dente: 4;
  sha256: 2).
- **Defeito achado pelo proprio aceite e corrigido na raiz:** na primeira rodada do dente (copia
  `/opt/tre/e02t02-r2`) o dente `guarda_de_sucesso_afrouxada` saiu **`NAO_CONTA (ancora quebrada)`**: a
  ancora do juiz (`E7 (chave na trilha como REFUSED)`) existia **so'** na mensagem de sucesso do item, e
  a mensagem de reprovacao dizia outra coisa — o dente nao media nada. Reproduzi o mutante a mao (log
  `/tmp/gda.out` na VPS: `FALHOU E7 esperava DEAD_LETTER/2 com valor_ambiguo (...), medido
  RETRY/1/recusa_da_api:valor_ambiguo`), alinhei as duas mensagens no item e remedi: **6/6**. A mesma
  reproducao mostrou o **segundo cinto**: com a guarda do nucleo afrouxada o evento **nao** e'
  finalizado nem reentregue (a guarda `EXISTS` do `registrar-replay.sql` casa zero linhas e o evento
  fica `RETRY`, sem sucesso inventado) — o dano e' medido pelo item, nao varrido.
- **Residuo:** 0 container e 0 rede `e02t02-*` no fecho e 0 diretorio `/tmp/dente-e02t02-*` (o extrator
  do dente limpa os seus). O `/tmp/verificacao-outbox-consumer` que existe na VPS e' residuo **da
  rodada 1 do T01** (01/10, ja' declarado) — nao e' desta medicao.
- **Verificadores do projeto:** `secret_scan.sh` PASS (nenhum segredo versionado), rodado no worktree do
  card **e** na copia da VPS; a lente estrutural e a suite do nucleo rodam **dentro** do aceite
  (`CONTRATO_WORKFLOW_OK 93`, `NUCLEO_CONSUMIDOR_OK 124`).
- **Evidencia guardada:** `/tmp/e02t02-r3-full.log`, `/tmp/e02t02-r3-dente.log` na VPS e
  `aceite-logs/` (`0-estrutural.out`, `0b-nucleo.out`, `8-ciclo5.out`, `sha256-antes/depois.txt`,
  `8-secret-scan.log`); anexados ao card.
- **Nao e homologacao:** quem entrega nao homologa — o veredito deste card vai para o estagio 6 (perfil
  `tester`) e a homologacao (estagio 7) e' do Anderson.

## 2026-10-02 — repositorio TRE (worktree local `t_a1bed5fa`, sem uso da VPS) — TRE-W3-E02-T02-D01: os dois SQL do dedup por chave entram no verificador de estrutura (defeito `t_a1bed5fa`)

- **Objeto do defeito:** `scripts/verificar_estrutura.sh` nao conhecia nenhum dos dois artefatos novos do `TRE-W3-E02-T02` — `grep -c 'ler-trilha\|registrar-replay'` = **0** no head `a38585d` e `git log --oneline -S'ler-trilha.sql' -- scripts/verificar_estrutura.sh` **vazio** (nunca entrou). O bloco do consumidor de outbox foi criado pelo `TRE-W3-E02-T01` (commit `0eaae57`) para a classe "existe **E** esta versionado"; sem os dois na lista, o verificador (rodado por outras trilhas/CI) imprime `PASS` mesmo se eles sumirem da arvore versionada. Achado de **cobertura**, nao de comportamento: a lente estrutural do card (`scripts/n8n/conferir_contrato_e_workflow.py`, `is_file()` nos caminhos declarados pelo contrato) e o aceite (sha256 dos 7 artefatos) ja' ancoravam a entrega.
- **Correcao (branch `fix/TRE-W3-E02-T02-D01`, commit `a5c3a1f`, nascido de `a38585d`):** os dois caminhos entram na lista do bloco do consumidor de outbox, **um por linha** (o aceite da classe mede `grep -c`, que conta **linhas**: os dois na mesma linha contariam 1). Blob `ff192ad4…`, sha256 do arquivo `103842cc6ca5cfcaab70f66473ae95bdb3d6d16efe79e8c200a6d2d905e66d5c`, modo `100755` no indice e no disco (inalterado); nenhuma outra linha do arquivo mudou (9 insercoes, 3 remocoes — o comentario do bloco e as duas linhas) e a lista de executaveis **nao** foi tocada (os dois sao `.sql`, 644).
- **Controle do defeito (o script ANTES do fix):** com o script anterior (`git show HEAD:scripts/verificar_estrutura.sh`, sha256 `9c4562560ca6d8c39419b799d8c7ee8d9f6f2d0559c9739174a4a440190c6175`) e `n8n/sql/ler-trilha.sql` **ausente** da arvore → `RESULTADO: PASS (0 falhas)`, **exit 0** (e **0** ocorrencia de `ler-trilha` na saida) — o gate era cego, com a saida bruta guardada (`10-ctrl-defeito-antes-do-fix-arquivo-ausente.out`). Este e' o controle que a revisao do `tester` pediu: sem ele o item nao prova nada.
- **Prova negativa (depois do fix; cada mutacao desfeita e remedida, uma saida bruta por linha em `evidencia/` do card):** N-verde `bash scripts/verificar_estrutura.sh` → `RESULTADO: PASS (0 falhas)`, **exit 0** (153 `OK`, 0 `FALHOU`), com `OK versionado n8n/sql/ler-trilha.sql` e `OK versionado n8n/sql/registrar-replay.sql` (`02-verde.out`); **N1** (ausencia) `n8n/sql/ler-trilha.sql` movido para fora da arvore → `FALHOU ausente n8n/sql/ler-trilha.sql` + `RESULTADO: FALHOU (1)`, **exit 1**, restaurado → `PASS (0 falhas)` exit 0 (`11-neg-N1-ausente.out`, `12-neg-N1-restaurado.out`); **N2** (nao versionado) `git rm --cached n8n/sql/registrar-replay.sql` (o arquivo segue no disco) → `FALHOU nao versionado n8n/sql/registrar-replay.sql (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)` + `FALHOU (1)`, **exit 1**, `git reset --` → `PASS (0 falhas)` exit 0 (`13-neg-N2-nao-versionado.out`, `14-neg-N2-restaurado.out`). Estado do indice no fim: so' `scripts/verificar_estrutura.sh` modificado.
- **Aceite do card:** `grep -c 'ler-trilha\|registrar-replay' scripts/verificar_estrutura.sh` → **2** (linhas 193 e 194 do arquivo corrigido).
- **Verificadores do projeto no worktree do fix (comando + exit code):** `bash scripts/verificar_estrutura.sh` → `PASS (0 falhas)` exit 0; `bash scripts/secret_scan.sh` → `PASS (nenhum segredo versionado)` exit 0; `bash scripts/verificar_papeis.sh` → `PASS (0 falhas)` exit 0; `python3 scripts/verificar_contrato_dados.py` → `PASS (26 itens, 0 falhas)` exit 0; `bash -n scripts/verificar_estrutura.sh` exit 0.
- **Integracao (hotspot `scripts/verificar_estrutura.sh`; cherry-pick isolado de `a5c3a1f` em worktrees descartaveis, removidos no fim):** sobre `feature/TRE-W3-E05-T01` (ponta `a38585d`, **com** os dois SQL do T02) → `PICK_EXIT=0`, **0 conflito**, `grep -c` = 2, `RESULTADO: PASS (0 falhas)` exit 0. Sobre `origin/feature/TRE-W3-E02-T01` (`522f6b6`, **sem** os dois SQL do T02) → cherry-pick **limpo** (0 conflito) e o verificador reprova `FALHOU ausente n8n/sql/ler-trilha.sql` + `FALHOU ausente n8n/sql/registrar-replay.sql` (`RESULTADO: FALHOU (2)`, exit 1) — **esperado e medido**: o aceite pede deteccao de **ausencia**, entao a lista e' por arquivo do card e so' fecha na arvore que tem a entrega do T02; **nao** e' conflito de integracao. Limite conhecido declarado: o commit `a5c3a1f` **nao** deve ser aplicado isolado em arvore anterior ao T02.
- **O que NAO foi tocado:** a VPS (**nenhum** comando nesta correcao — e' mudanca de gate no repo), `/opt/tre/repo` e `/opt/tre/{homolog,prod}`, os artefatos do T02 (nenhum arquivo de `n8n/` ou `scripts/n8n/` foi editado), nenhum banco, nenhum container.
- **Aprendizado:** bloco de gate que lista artefato por card so' vale na arvore onde a entrega do card esta — e a vizinhanca se mede (cherry-pick isolado), nao se supoe; o controle do defeito (script anterior + arquivo ausente → `PASS`) e' o que separa "o gate consertou" de "o gate sempre olhou".
- **Verificacao independente:** quem entrega nao homologa — o veredito deste defeito e' do estagio 6 (perfil `tester`); a homologacao (estagio 7) e' do Anderson. A origem (`TRE-W3-E02-T02`, card `t_3bde06ab`) so' fecha de vez com este defeito resolvido.
- Segredos: nenhum valor nesta entrada.

## 2026-10-02 — repositorio TRE (worktree `t_2ee17829`) — TRE-W3-E04-T01: job diario de reconciliacao (PostgreSQL x Odoo pela porta unica)

- **Objeto:** o card `t_2ee17829` (epico W3, E04) pedia o job diario que cruza o que o PostgreSQL
  declara com o que o CRM (Odoo) tem, pela **porta unica** da API controlada. Entrega contract-first:
  contrato versionado (`n8n/contracts/reconciliation-job.v1.json`), nucleo puro
  (`n8n/codigo/nucleo-reconciliacao.js`, o mesmo arquivo que o Code node embute), dois SQL
  (`n8n/sql/reconciliacao-origem.sql`, `reconciliacao-pendentes.sql`), workflow **derivado**
  (`n8n/workflows/TRE-reconciliation.json`), montador, lente estrutural, suite do nucleo, mutador,
  leitor do resultado, massas por estado, aceite e runbook (`docs/runbooks/n8n-reconciliacao.md`).
  Comparacoes de **entidade** (`E1`, `E2`, `E4`, `I1..I4`) e de **fila x trilha** (`P1..P4`); o job **nao
  escreve** (somente leitura, o produto e' o relatorio).
- **Porta unica (modulo Odoo, politica 1.3.0 -> 1.4.0):** a leitura controlada (`crm_registros_ler`)
  passou a poder ver registros **ARQUIVADOS** por declaracao — `leitura_de_arquivados` na operacao,
  parametro `incluir_arquivados` no vocabulario da leitura, `active_test=False` no controlador quando o
  plano pede, `active` obrigatorio entre os campos pedidos e recusa nomeada
  (`parametro_nao_declarado`, 422) em operacao que nao declara. 4 testes novos
  (`test_33..test_36`). Motivo **medido**, nao suposto: no estado *E* do aceite (parceiro arquivado) as
  duas leituras do destino voltavam `0 registro(s)` e o veredito saia `E1 espelho_ausente` em vez de
  `E2` — o job mandaria criar de novo o que ja' existe no CRM.
- **Defeitos achados pelo proprio aceite e corrigidos na raiz (todos com a medicao no log):**
  1. **Booleano do Odoo nao e' valor** — o ORM devolve `false` para campo de caracter vazio e o nucleo
     lia `String(false)` = `'false'`, inventando divergencia de identidade forte (`E4` duplicado nos
     estados C/D). Conserto: `texto()` trata booleano como vazio (+ item de suite com a medicao real).
  2. **Leitura nao medida nao vira divergencia** — com a porta unica **parada** (estado *G*) o job
     reportava `E1` para todo espelho esperado enquanto o veredito ja' era `INDETERMINADO`
     (divergencia inventada a partir de ausencia de medicao). Conserto: as comparacoes de entidade sao
     **puladas** quando qualquer leitura do destino nao foi medida, com o pulo **nomeado**
     (`comparacoes_de_entidade_puladas`) ao lado de `leitura_do_destino_nao_medida` (+ item de suite).
  3. **Porta unica fora do ar abortava a execucao** — sem `onError: continueRegularOutput` o no HTTP
     derrubava a execucao do n8n e a rodada morria sem relatorio (`leitura=ilegivel`). Conserto: a
     declaracao entra no **contrato** (`envelope_da_requisicao.no_que_nao_mede`), o montador a usa e a
     lente confere os tres (`onError`, `neverError`, URL do involucro).
  4. **Dois falsos positivos do PROPRIO aceite**, corrigidos para medir o que diziam medir: (a) a
     checagem de segredo procurava as **palavras** `chave`/`token` — acusava o proprio comentario do
     job e o **nome** da credencial; passou a medir o **valor** (o valor da chave da rodada, lido do
     arquivo 600 do descartavel, nao pode aparecer no cofre/workflow/log, com guarda de medicao vazia);
     (b) `local letra="$1" bloco="...$letra..."` na mesma linha — o bash expande as palavras **antes**
     de atribuir, entao a variavel resolvia o escopo de FORA e, com `set -u`, matava o script na
     chamada direta (estado *G*): `letra: unbound variable`. Dividido em duas linhas (e o padrao foi
     varrido no arquivo inteiro: era a unica ocorrencia).
- **Defeito HERDADO corrigido na raiz:** `tests/test_acl_seguranca.py::test_11` reprovava porque o
  `tf.evento.outbox` entrou com ACL propria no card **TRE-W3-E03-T01** (commit `d0b8d5a`) e a
  expectativa do teste ficou presa em `{tf.process.opportunity}` (`git log -1 -- tests/test_acl_seguranca.py`
  = `060c369`, **anterior**). Nao e' violacao de ACL: e' expectativa que envelheceu. Conserto: a
  superficie de ACL do modulo passa a ser medida contra os **modelos do proprio modulo**, derivados de
  `ir.model.data` (nao da coluna `modules`, que nao filtra em `search` — medido), entao nao envelhece a
  cada modelo novo e continua reprovando ACL para modelo de fora. Com isso a suite do modulo fecha
  **`0 failed, 0 error(s) of 192 tests`** (antes: 1 failed + 1 error).
- **Aceite (VPS `169.58.24.102`, trio descartavel proprio: postgres + odoo + n8n, banco
  `tre_reconc_26756757954`):** **`RESULTADO: RECONCILIACAO_OK (96 itens, 0 falhas)`**, **exit 0** —
  lente estrutural **`RECONCILIACAO_LENTE_OK (150 itens, 0 falhas)`**, suite do nucleo
  **`RECONCILIACAO_NUCLEO_OK (61 itens, 0 falhas)`** (inclui o codigo **embutido** no workflow, medido
  byte a byte antes do marcador), 8 mutacoes nomeadas com o item alvo reprovando e baseline verde, e
  **7 estados em execucao real**: *A* espelho saudavel -> `OK` sem divergencia (linha-resumo com
  `janela_completa, fila_completa`); *B* ausente -> `E1`; *C* ID cruzado -> `I1`; *D* identidade forte ->
  `E4`; *E* **arquivado** -> `E2`; *F* fila x trilha -> `P1,P2,P3,P4`; *G* porta unica **parada** ->
  `INDETERMINADO`, **nenhuma** divergencia e a regra nomeada. **Somente-leitura** medido por digest nas
  tres tabelas do PostgreSQL e nos parceiros do Odoo em **cada** uma das 7 rodadas (inclusive a que nao
  mediu o destino). `sha256` dos **12 artefatos sob teste** fixado nas guardas e **identico no fecho** —
  e reconferido aqui contra o worktree ja' commitado (mesmos 12 digests). Instancia do **dev intocada**
  (mesmos bancos antes/depois); o **valor** da chave nao aparece no cofre do n8n, no workflow nem no log.
- **Verificadores do projeto (worktree do card, comando + exit code):** `bash scripts/verificar_estrutura.sh`
  -> `PASS (0 falhas)` exit 0 (**185** linhas `OK`, incluindo o bloco novo do card: os 14 artefatos
  existem **E** estao versionados, 6 scripts executaveis); `bash scripts/secret_scan.sh` ->
  `PASS (nenhum segredo versionado)` exit 0; `bash scripts/verificar_papeis.sh` -> `PASS (0 falhas)`
  exit 0; `python3 scripts/verificar_contrato_dados.py` -> `PASS (26 itens, 0 falhas)` exit 0.
- **Integracao de branch:** `feature/TRE-W3-E04-T01` nasce de `feature/TRE-W3-E03-T01` e **mergeia**
  `fix/TRE-W3-E02-T02-D01` (conflito em `docs/operations/registro-de-execucoes.md` resolvido
  preservando **as duas** entradas), para o card nao entregar por cima da correcao do gate.
- **O que NAO foi tocado:** `/opt/tre/{homolog,prod}`, `/opt/tre/repo` (a copia do aceite foi
  `/opt/tre/e04t01-repo-r7`, descartavel), a instancia do dev (medida antes/depois), nenhum banco de
  ambiente e nenhum container do dev. O unico efeito fora do descartavel sao os arquivos do repo.
- **Residuo:** trio e rede `e04t01-*` destruidos no fecho (conferido: 0 container e 0 rede).
  Nota de honestidade operacional: uma limpeza minha no meio do caminho filtrou por prefixo e derrubou
  um container `e05t01-pg-*` que nao era deste card (estava no ar havia 15s); declarei o ocorrido no
  card e a limpeza passou a filtrar **so'** `e04t01-*`/`frosty_feynman` do proprio descartavel.
- **Aprendizado:** um aceite que mede de verdade encontra defeito que nenhuma lente de forma encontra —
  os tres defeitos de comportamento deste card (booleano do Odoo, divergencia inventada sem medicao,
  execucao abortada com a porta fora do ar) so' apareceram em **execucao real**; e um item de
  verificacao que procura **vocabulario** (palavra "segredo") em vez de **valor** acusa o proprio
  codigo: medicao que nao mede e' pior que medicao ausente, porque da' verde.
- **Verificacao independente:** quem entrega nao homologa — o veredito deste card vai para o estagio 6
  (perfil `tester`) e a homologacao (estagio 7) e' do Anderson.
- Segredos: nenhum valor nesta entrada.

---
## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E05-T01 (card `t_0b77a689`): observabilidade de sincronizacao (outbox -> trilha), aceite + dentes

- **Publicacao do autor:** `git archive` do head **`a58c0a7`** do worktree `.worktrees/t_0b77a689` (branch
  `feature/TRE-W3-E05-T01`, base `a38585d` = `origin/feature/TRE-W3-E02-T02`), publicado em
  `origin/feature/TRE-W3-E05-T01`; copia propria na VPS em `/opt/tre/evid-t_0b77a689-r6/repo` (nunca a copia
  de rodada anterior). 14 arquivos novos + `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md` e
  `scripts/verificar_estrutura.sh` **aditivos** (nenhum arquivo existente alterado em logica: `git diff --stat`
  da base mostra so' as adicoes).
- **Aceite (medicao real, banco e n8n DESCARTaveis, banco `tre_obs_263498525799`):** `RESULTADO: OBSERVABILIDADE_SYNC_OK (119 itens, 0 falhas) banco=tre_obs_263498525799 imagens=postgres:16+n8nio/n8n:latest workflow=/opt/tre/evid-t_0b77a689-r6/repo/n8n/workflows/TRE-observabilidade-sync.json` —
  **OBSERVABILIDADE_SYNC_NUCLEO_OK (58 itens itens, 0 falhas** — com 8 estados semeados, cada metrica medida por **dois caminhos
  independentes** (`psql` direto e pelo workflow no n8n descartavel) e o **retrato das duas tabelas identico
  antes/depois** de uma rodada completa (somente-leitura provado em execucao real, nao por grep).
- **Prova de dente:** `RESULTADO: OBSERVABILIDADE_SYNC_DENTE_OK (12/12 dentes cumpridos; juiz conferido; baseline nao mutado verde)` (INFO  resumo do dente: 12/12 dentes cumpridos; baseline=verde; juiz=conferido); baseline **nao mutado** verde
  (OK    baseline nao mutado: RESULTADO: OBSERVABILIDADE_SYNC_OK (116 itens, 0 falhas) banco=tre_obs_base_2641163 imagens=postgres:16+n8nio/n8n:latest workflow=/opt/tre/evid-t_0b77a689-r6/repo/n8n/workflows/TRE-observabilidade-sync.json); juiz conferido por 8 saidas sinteticas (dente, sem dente, ambiente, ancora, fase de codigo,
  item indentado, FALHOU vence OK, ambiente quebrado nomeado) + 2 do sha256. Veredito por mutacao:
  DENTE dead_letter_sem_motivo_nao_conta         DENTE_CUMPRIDO
  DENTE processado_sem_trilha_nao_conta          DENTE_CUMPRIDO
  DENTE direcao_nao_declarada_nao_conta          DENTE_CUMPRIDO
  DENTE sem_conclusao_nao_conta                  DENTE_CUMPRIDO
  DENTE falha_da_trilha_nao_conta                DENTE_CUMPRIDO
  DENTE detalhe_perde_o_motivo                   DENTE_CUMPRIDO
  DENTE contrato_afrouxa_o_limiar                DENTE_CUMPRIDO
  DENTE sem_fail_closed_de_metrica_ausente       DENTE_CUMPRIDO
  DENTE sanitizacao_removida                     DENTE_CUMPRIDO
  DENTE sem_conferencia_cruzada                  DENTE_CUMPRIDO
  DENTE metrica_nao_declarada_ignorada           DENTE_CUMPRIDO
  DENTE placeholder_vira_indeterminado           DENTE_CUMPRIDO
- **Defeito achado pelo proprio aceite e corrigido na raiz (1):** a consulta de detalhes rodava **uma vez por
  linha de metrica** (o n8n executa no' com entrada multipla uma vez por item): a primeira rodada mediu
  `outbox_dead_letter (20)` para **um** dead-letter e o estado sem detalhe virou 20 linhas vazias. Corrigido com
  `executeOnce` nos dois nos Postgres, declarado no contrato (`workflow.consulta_uma_vez`), medido pela lente e
  travado por item do aceite (`outbox_dead_letter (1)`).
- **Defeito (2):** `alwaysOutputData` (necessario para a cadeia nao parar sem detalhe) entrega **um item vazio**
  e o nucleo lia isso como "detalhe com tipo nao declarado" — **toda rodada saudavel fechava INDETERMINADO**
  (`tipo_de_detalhe_nao_declarado:(vazio)`), ou seja o relatorio gritava exatamente quando estava tudo bem.
  Corrigido na raiz (linha totalmente vazia = ausencia de detalhe, regra declarada em `detalhes.linha_vazia`),
  com item proprio na suite e dente proprio (`placeholder_vira_indeterminado`).
- **Defeito (3), nos proprios itens do aceite:** (a) o item do MOTIVO do dead-letter usava `\(` no padrao BRE —
  em BRE o parentese e' literal **sem** barra e `\(` e' agrupamento, entao o `grep` **nunca casava** (o aceite
  r2 reprovou por causa do proprio padrao); (b) o dente do placeholder saiu `MUTACAO_SEM_DENTE` porque a ancora
  tambem existia num item vizinho que continuava OK. Corrigido na raiz: ancora virou **trecho unico**, o juiz
  julga **FALHOU antes de OK** e ganhou controle proprio para o caso (7 saidas sinteticas).
- **Ambiente (4):** durante a rodada r5 o **trio descartavel foi removido por fora** (a VPS e' compartilhada com
  outra rodada; o n8n passou a devolver `The DNS server returned an error`) e o aceite **se recusou a contar o
  dente** (`NAO_CONTA`, sem inventar veredito) — o comportamento fail-closed funcionou, mas a mensagem dizia
  "ancora quebrada". Conserto na raiz em duas frentes: o juiz passou a **nomear ambiente quebrado** (com o motivo
  real) e o aceite passou a **restabelecer o trio** (mesmo nome, esquema reaplicado, cofre do n8n preservado em
  bind mount) registrando isso como **item** — re-medido em r6.
- **Verificadores do projeto (worktree):** `verificar_estrutura.sh` **PASS (0 falhas)** — com os 14 artefatos
  novos **cobertos** (existencia + versionamento + permissao de execucao); `secret_scan.sh` PASS; `verificar_papeis.sh`
  PASS (0 falhas).
- **Ambiente medido:** dev com os **mesmos bancos** antes/depois, `homolog`/`prod` sem arquivo novo, nenhum
  segredo em claro no cofre do n8n descartavel, `sha256` dos 10 artefatos sob teste **identico ao das guardas**
  (nada mudou durante a medicao) e nada publicado em nenhuma instancia (o workflow nasce **inativo**).
- **sha256 dos artefatos sob teste (medidos no aceite):**
```
8e85ad20fafeda0be46f9ff938d467b652e7a89d86baea812517cf3146b4207b  n8n/contracts/observabilidade-sync.v1.json
01eb3059f05293c0640e6c304de1a32fb964557443d47bed36f9c23bf309aaed  n8n/codigo/observabilidade-sync.js
67dafb99bb4f90b1496155ab900f4b6432d5e00bd200adb7b32e71753b80baa7  n8n/sql/observabilidade-sync.sql
3001d22770254eab991b3cefaafb1fb313d8c08f802e43911baf385244559916  n8n/sql/observabilidade-sync-dead-letters.sql
064faff6f3e9ea990dc8401dd7ac46da9cdf5de632b1070a96b3b3ec07f9986d  n8n/workflows/TRE-observabilidade-sync.json
9c4428466d791d74da26984c3a547ee43bd5d1740fb50c88254066a87d5ba5f7  scripts/n8n/montar_workflow_observabilidade.py
0a527d5c33bbfccf449d8720c5bdff4b46b777bd83406bfdeac7c71103345d11  scripts/n8n/mutar_workflow_observabilidade.py
460df0687fa0ac07aaf56e3ad0491e4453fd70394bcc3b61958608b103ef625c  scripts/n8n/conferir_observabilidade.py
2360e84196829a2a76ba826d9b90f9f60d3ace381c9d1dd3f1d0bbde3c73fb3e  scripts/n8n/testar_observabilidade_sync.js
f7b074f594418883fb9bb4ea3903c83e9c665d58203878c869d7b574105b0b09  scripts/n8n/massa-observabilidade.sql
```
- **Evidencia guardada:** `/opt/tre/evid-t_0b77a689-r6/aceite.out`, `/opt/tre/evid-t_0b77a689-r6/dente.out`,
  `/opt/tre/evid-t_0b77a689-r6/logs-aceite/` (lente, suite, os 8 estados, sha256 antes/depois, normalizacao) e
  `/opt/tre/evid-t_0b77a689-r6/logs-dente/` (baseline + 12 mutantes + saida de cada sub-run) na VPS.
- **Nao e homologacao:** quem entrega nao homologa — o veredito deste card e' do **estagio 6** (perfil `tester`)
  e a homologacao (**estagio 7**) e' do Anderson.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W3-E06-T01 (E2E Foundation #001)

- **Base consolidada da onda W3 (agente):** `feature/TRE-W3-E06-T01` criada de `10fbb15`
  (`feature/TRE-W3-E04-T01`) + merge de `4a8cead` (`feature/TRE-W3-E05-T01`) — tres conflitos, todos em
  arquivos aditivos, resolvidos por **UNIAO** (`scripts/verificar_estrutura.sh` com as DUAS listas de
  artefatos, `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`); merge em `8a33c71`, aceite em
  `eaeb4a6`, head medido **`519d8c5`** (worktree `.worktrees/t_fcbe3d7d`).
- **Aceite completo (agente):** no clone `/opt/tre/e06t01-r6/repo` (`git clone` do bundle de `519d8c5`),
  `TRE_LOG_DIR` proprio → **`RESULTADO: E2E_FOUNDATION_001_OK (139 itens, 0 falhas, 2 passo(s) declarado(s)
  fora do escopo)`**, exit 0. No MESMO run: gates do projeto PASS e os 4 aceites de origem em
  `--apenas-codigo` — `OUTBOX_CONSUMER_OK (2 itens)`, `EVENTOS_ODOO_PG_OK (28 itens)`,
  `RECONCILIACAO_OK (37 itens)`, `OBSERVABILIDADE_SYNC_OK (3 itens)`, todos 0 falhas.
- **Prova de dente (agente):** `--prova-de-dente` no mesmo clone → **`E2E_FOUNDATION_001_DENTE_OK (3/3
  dentes cumpridos; juiz conferido; baseline nao mutado verde)`**, exit 0; as 3 mutacoes nomeadas
  (`sem_validacao_de_envelope`, `mapeamento_trocado`, `sem_consulta_de_trilha`) reprovaram cada uma o seu
  item declarado.
- **Medido no BANCO (nao na narrativa):** 1 `res.partner` com `tf_company_id` = UUID do evento
  (`COMPANY_QUALIFIED` → consumidor n8n → `POST /tf/api/v1/empresa_upsert`); trilha `postgres->odoo`
  `UPSERT` `COMPLETED` com o ID devolvido pelo Odoo no `response_payload`; ida-e-volta fechada
  (`organizations.odoo_partner_id`); os 8 eventos Odoo→PG do contrato na trilha (`SENT=8`, todos
  `COMPLETED`); reenvio do MESMO envelope sem linha nova; REPLAY do evento PG→Odoo com 0 chamada nova e a
  MESMA linha de trilha (mesmo `id`, mesmo `completed_at`); ZERO duplicatas (1 empresa, 1 contato, 1
  atividade, 1 linha por chave); `DEAD_LETTER` sem chamada para evento sem `event_version`; reconciliacao
  `OK`/0 divergencia e observabilidade `OK` no mesmo trio; dev com os MESMOS bancos antes/depois;
  `homolog`/`prod` sem arquivo novo; sha256 dos 6 artefatos sob teste identico ao das guardas.
- **Evidencia guardada:** `/opt/tre/evid-t_fcbe3d7d-r2/` (`aceite.out` sha256 `855d849c…`, `dente.out`
  sha256 `21d601de…`, `runner.out`, `logs-aceite/`, `logs-dente/` com a saida de cada sub-run mutado) e a
  rodada anterior `/opt/tre/evid-t_fcbe3d7d-r1/` (138 itens, dente 2/3 — a ancora do segundo dente estava
  so' no ramo verde; conserto na raiz em `519d8c5`).
- **Nada nasce ligado (ADR-005):** os 4 workflows nascem inativos, o trio e' descartavel e some no fim;
  nenhum arquivo em `/opt/tre/repo`, em `dev`, `homolog` ou `prod`; nenhum DDL fora do banco descartavel.
- **Achados de execucao (todos com conserto na raiz na base medida):** (1) `n8n update:workflow
  --active=true` nao ecoa nada (rc=0 e ativo de verdade) → o item passou a medir o campo `active` no
  export; (2) o `odoo shell` (uid 100) nao lia o `token.txt` modo 600 do root → `PermissionError` deixava
  a porta de ingestao `porta_nao_configurada` (0/8 entregues) → dono do arquivo ajustado; (3) o proprio
  `secret_scan` do projeto reprovava o aceite pela linha literal com o nome do campo de senha → chave
  montada por variavel; (4) a ancora de um dente existia so' no ramo verde → o ramo `falhou` passou a
  dizer o que quebrou e o passo 0 ganhou **self-check das ancoras** (3 mutacoes conferidas no proprio
  arquivo); (5) falha de gate do passo 0 nao aborta mais o cenario (snapshot de falhas antes do trio).
- **Nao e homologacao:** quem entrega nao homologa — o veredito deste card e' do **estagio 6** (perfil
  `tester`) e a homologacao (**estagio 7**) e' do Anderson.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — agente Scout (TRE-W4-E01-T01)

- **Aceite E2E do agente Scout v1 (agente):** `bash scripts/agentes/teste_scout_aceite.sh
  --prova-de-dente` sobre a branch `feature/TRE-W4-E01-T01` (commit de codigo `a93e18c`), em container
  **descartavel** `pg-scout-acc` (`postgres:16`, **sem porta publicada**, removido ao final) com a
  migration `0001` aplicada do zero → `RESULTADO: ACEITE_SCOUT_001_OK (35 itens, 0 falhas)` e
  `DENTE OK (3/3 mutacoes detectadas)`.
- **Suite offline (agente):** `python3 scripts/agentes/verificar_agente_scout.py --autoteste` →
  `RESULTADO: SCOUT_SUITE_OK (54 itens, 0 falhas)` e `AUTOTESTE OK (8/8 mutacoes detectadas)`.
- **Portao de estrutura (agente):** `bash scripts/verificar_estrutura.sh` → `RESULTADO: PASS (0 falhas)`.
- **Nenhum container do TRE foi tocado:** no fim do aceite seguiam de pe `proxy-dev`, `odoo-dev`,
  `pg-odoo-dev` e `pg-sales-dev` (nenhuma escrita em `dev`, `homolog` ou `prod`; nenhum DDL fora do
  container descartavel). O par `--desfazer` foi exercitado **no container descartavel**, nao no dev.
- **Achados de execucao (todos com conserto na raiz, ja cobertos por item da suite):** (1) o INSERT de
  `organizations` montava 23 valores para 21 colunas — a suite passou a conferir a **contagem** de
  colunas x valores, nao so' os nomes; (2) o veredito de criacao ignorava os problemas de validacao
  (nome/fonte ausentes) e tentava inserir — a recusa de forma agora vence a identidade; (3) a porta
  imprime `BEGIN`/`COMMIT` junto do resultado e a comparacao da saida inteira fazia uma criacao
  legitima parecer replay — a conferencia passou a ser **linha a linha** e o agente devolve a marca
  `SCOUT_CRIADA`; (4) o fechamento do `sync_events` estava **dentro da CTE de escrita**: como as CTEs e a
  instrucao principal rodam no **mesmo snapshot**, o `UPDATE` fechava 0 linhas e o evento ficava
  `PENDING` para sempre — o fechamento virou comando proprio (snapshot novo), ancorado na existencia da
  organizacao da rodada.
- **Evidencia bruta:** saida completa do aceite e da suite guardadas como anexo do card
  `t_fd3e41f0` (relatorios por rodada ficaram so' no diretorio temporario do aceite, removido com ele —
  nenhum dado nem segredo permanece no disco da VPS).
- **Nao e homologacao:** quem entrega nao homologa — o veredito do estagio 6 e' do perfil `tester` e a
  homologacao (estagio 7) e' do Anderson.

### Rodada 2 — conserto dos defeitos da revisao independente (mesmo card `t_fd3e41f0`)

- **Suite offline (agente, no container do Hermes):** `python3 scripts/agentes/verificar_agente_scout.py
  --autoteste` -> `RESULTADO: SCOUT_SUITE_OK (58 itens, 0 falhas)` e `AUTOTESTE OK (12/12 mutacoes
  detectadas)`, exit 0. Rodada TAMBEM com `TMPDIR=/tmp` — o ambiente em que a rodada 1 morria com
  `IndexError: 3` em `scout.py:120` — com o MESMO veredito e exit 0.
- **Sonda do mecanismo do defeito 1 (agente, local):** `bash evidencias-impl-r2/probe-stdin-laco.sh` ->
  laco antigo (`done <<< "$linhas"` com filho que consome o stdin) `TOTAL_ITERACOES=1`; laco novo
  (mutacoes numa lista lida ANTES do laco) `TOTAL_ITERACOES=3` -> `PROBE OK`. O anexo da rodada 1 tinha
  **um unico** bloco `-- mutacao:` e o `3/3` era string literal no script.
- **Aceite E2E (agente, na VPS, clone proprio do bundle):** bundle `sha256
  c67eee44adc4acc40c9a6a2e18f24e968911ad4058149c72d819163d1eb3e014`, HEAD
  `4b7a1ec70c70fa11e5db212984a5f6dd6ea9d3fa` (449 arquivos, `git status` limpo); `bash
  scripts/agentes/teste_scout_aceite.sh --prova-de-dente` em container descartavel `pg-scout-acc`
  (`postgres:16`, sem porta publicada) -> `RESULTADO: ACEITE_SCOUT_001_OK (37 itens, 0 falhas)` e `DENTE
  OK (3/3 mutacoes detectadas, cada uma pelo item esperado)`, com 3 / 14 / 1 item(ns) reprovado(s) por
  mutacao: `sem-idempotencia` (`rodada3-exit-0`, `rodada3-sem-erro`, `rodada3-ja-existe-cinco`),
  `sem-forte-tambem-cria` (`rodada1-fila-humana-pendente` e mais 13) e
  `revisao-nao-vai-para-a-fila-humana` (`rodada1-fila-humana-pendente`). Itens novos de banco verdes:
  `rodada1-criadas-com-carimbos (3)` e `rodada1-ja-existe-com-id-casado (2)`.
- **Portao de estrutura (agente):** `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0 falhas)`.
- **Nenhum container do TRE foi tocado:** ao fim seguiam de pe `proxy-dev`, `odoo-dev`, `pg-odoo-dev` e
  `pg-sales-dev` (mesmos `StartedAt`) e nao ficou residuo de container, rede ou diretorio temporario do
  aceite.
- **Consertos desta rodada (cada um com item proprio e mutacao que o reprova):** (1) o laco da prova de
  dente chamava `docker exec -i`, que consome o stdin do here-string: rodava **1 de 3** mutacoes e
  imprimia `3/3` literal -> mutacoes numa lista lida antes do laco, `</dev/null` em todo `docker exec`
  sem stdin, veredito com a contagem **medida** e exigencia do **item esperado** de cada mutacao;
  (2) `--autoteste` morria com `IndexError: 3` onde o temp e' `/tmp` (a raiz vinha de
  `Path(__file__).parents[3]`) -> raiz do repo achada por **marcador** (contrato do agente), com item que
  importa o agente de diretorio fora da arvore do repo; (3) o `rc` da escrita em `human_approvals` e em
  `agent_runs` era descartado — o agente dizia `REVISAO_IDENTIDADE`/`REVIEW_REQUIRED` com `erro=None`
  **sem nada escrito** -> fail-closed (`ERRO`/`FAILED` com `fila humana nao registrada` /
  `AUDITORIA_NAO_REGISTRADA`); (4) O4: `validar_sql` varria o conteudo dos literais e recusava como DDL
  uma candidata chamada `Drop Solucoes Ltda` -> a guarda le o **codigo SQL** (`_sem_literais`); (5) O5: o
  aceite passou a aferir os carimbos `created_at`/`updated_at` (A2) e o **id casado** do `JA_EXISTE` (A4)
  no banco.
- **Evidencia bruta:** `evidencias-impl-r2/` no diretorio de trabalho do card (`aceite-r2-vps.out`,
  `offline-tmpdir-real.out`, `estrutura-r2.out`, `probe-stdin-laco.sh` e `.out`) e anexo do card
  `t_fd3e41f0`; o log completo do aceite tambem ficou em `/opt/tre/entrega-t_fd3e41f0-r2/` na VPS.
- **Nao e homologacao:** quem entrega nao homologa — o veredito do estagio 6 e' do perfil `tester` e a
  homologacao (estagio 7) e' do Anderson.

## TRE-W4-E02-T01 — Agente Research v1 (pesquisa e enriquecimento)

- **Card:** `t_d9be7d3c` (board `transformativa-revenue-engine`) · branch `feature/TRE-W4-E02-T01`,
  base no head aprovado do card pai (`feature/TRE-W4-E01-T01` @ `cb2c53a`).
- **Momento do registro:** toda evidência abaixo foi produzida **antes** deste texto — nada é narrado
  de memória. Data do registro: 02/10/2026.

### Rodada 1 — implementação, suite offline e aceite E2E

- **Suite offline (agente, no container do Hermes):**
  `python3 scripts/agentes/verificar_agente_research.py --autoteste` ->
  `RESULTADO: RESEARCH_SUITE_OK (64 itens, 0 falhas)` e
  `AUTOTESTE OK (15/15 mutacoes detectadas)`, exit 0.
- **Aceite E2E (agente, na VPS, árvore própria em `/opt/tre/t_d9be7d3c`):** container descartável
  `pg-research-acc` (`postgres:16`, sem porta publicada) com a migration 0001 em schema limpo e 3
  organizações pré-existentes (uma com `industry_name` já preenchido) -> `RESULTADO:
  ACEITE_RESEARCH_001_OK (55 itens, 0 falhas)` e `DENTE OK (4/4 mutacoes detectadas, cada uma pelo
  item esperado)`. Itens reprovados por mutação (contagem medida no log): `sem-idempotencia` 5 itens
  (`rodada2-exit-0`, `rodada2-ja-pesquisado-seis`, `rodada3-exit-0`, …),
  `enriquecimento-sem-coalesce` 31 itens, `coluna-fora-do-tipo-liberada` 2 itens
  (`rodada1-nao-escreve-coluna-fora-do-tipo`, `rodada1-descarte-fora-do-tipo`) e
  `numero-fora-do-tipo-liberado` 2 itens. Saída integral em `evidencias-impl/aceite-e2e-vps.out`
  (anexo do card).
- **Portão de estrutura (agente):** `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0
  falhas)`, com os 7 artefatos do Research versionados e o aceite executável (bloco novo do gate).
- **Nenhum container do TRE foi tocado:** o aceite cria e remove somente `pg-research-acc`; `proxy-dev`,
  `odoo-dev`, `pg-odoo-dev` e `pg-sales-dev` seguem de pé com os mesmos `StartedAt`.
- **Evidência bruta:** `evidencias-impl/` no diretório de trabalho do card
  (`suite-offline.out`, `aceite-e2e-vps.out`, `estrutura.out`); o log completo do aceite também ficou em
  `/tmp/rs-dente2.out` na VPS.
- **O que este card NÃO mede:** homologação. Quem entrega não homologa — o veredito do estágio 6 é do
  perfil `tester` e a homologação (estágio 7) é do Anderson.

### Defeitos próprios encontrados e corrigidos ANTES de entregar (cada um com item que o reprova)

1. **Guarda recusava o SQL que o próprio agente gera** (`_EXPR_TEXTO`/`_EXPR_NUMERO` ficaram no módulo
   com o marcador `%s` sem formatar, então nunca casavam `COALESCE(NULLIF(...))`): a rodada 1 no E2E
   terminou com `research_runs=0` e todos os PESQUISADA em `ERRO`. Corrigido com padrão montado por
   coluna e com o item `sql-gerado-passa-na-propria-guarda` (agora o SQL gerado é conferido **pela
   guarda do agente**, no offline e no E2E).
2. **`employee_count` escapava da fronteira de tipo**: o número é tratado antes do laço (para a faixa
   ser derivada depois dele) e naquela passagem não havia a checagem do tipo — um `COMPANY_PROFILE`
   recebia `employee_count`. Corrigido no caminho especial, e o E2E passou a medir a fronteira nos
   **dois** caminhos (laço com `city` num `INDUSTRY`, caminho especial com `employee_count` num
   `COMPANY_PROFILE`).
3. **Item de "sem segunda cópia da regra de identidade" comparava *objetos* de função** entre duas
   cargas do mesmo módulo (sempre diferentes) e **lia o arquivo canônico em vez da cópia mutada**: a
   mutação `segunda-copia-da-regra-de-identidade` passava em silêncio. Corrigido para comparar a
   implementação (`co_code`) e o arquivo sob teste.
4. **Item da raiz não provava o marcador**: passava `raiz=` explícito, então a mutação
   `raiz-por-profundidade-do-arquivo` não era detectada. Refeito: a cópia roda a 4 níveis dentro do repo
   e de um `cwd` fora dele, exigindo a raiz **pelo marcador**, e a cópia fora da árvore tem de
   **recusar** (fail-closed) em vez de herdar o repo silenciosamente.
5. **Duas mutações mudas** (sem efeito observável) na lista de autoteste: `guarda-aceita-update-sem-modo`
   (a guarda ainda recusava pelo COALESCE) foi trocada por `guarda-aceita-enriquecimento-sem-coalesce`,
   e as expectativas de `sem-idempotencia`/`fechamento-sem-ancora-no-run` passaram a apontar os itens que
   leem o **SQL gerado** (a porta de roteiro responde por roteiro e não mede semântica de banco — quem
   mede a idempotência em banco é a rodada 3 do E2E).

### Rodada 2 — mudança pedida na revisão independente (estágio 6): guarda da própria prova

- **Defeito apontado (fail-open, no harness da prova — não no agente):** a mutação
  `raiz-por-profundidade-do-arquivo` declarava como item esperado
  `agente-importa-de-diretorio-fora-da-arvore`, nome que **não existia** na suíte (o item real é
  `raiz-vem-do-marcador-nao-da-profundidade`). `autoteste()` só consultava `resultados.get(nome)`:
  nome inexistente devolvia `None`, nunca entrava em `nao_reprovados`, e a mutação era contada como
  detectada **sem nada reprovar** — 1 das 15 provas sem exigência medida, contra o TEST do card. O
  agente não apresentou defeito no que foi medido; o buraco era da prova.
- **Correção:** (1) o item esperado passou a ser o nome real; (2) guarda da própria prova —
  `problemas_em_mutacoes()` (estática, antes de rodar) mais a checagem de nome ausente no resultado da
  suíte (dinâmica): item inexistente **ou** mutação sem item declarado **reprova**; (3) `--autoteste`
  passou a mutar o arquivo apontado por `--codigo` (antes mutava sempre o canônico, qualquer que fosse
  o alvo); (4) item novo `autoteste-recusa-mutacao-sem-item-medido` cobre a guarda — a suíte offline
  passou de 64 para **65 itens**.
- **Suíte offline (agente, no container do Hermes):** `python3
  scripts/agentes/verificar_agente_research.py --autoteste` -> `RESULTADO: RESEARCH_SUITE_OK (65 itens,
  0 falhas)` e `AUTOTESTE OK (15/15 mutacoes detectadas)`, exit 0; a mutação do defeito reprova pelo
  item certo (`OK mutacao raiz-por-profundidade-do-arquivo reprovada por 1 item(ns):
  raiz-vem-do-marcador-nao-da-profundidade`).
- **Controle do defeito (4 casos, fail-closed):** `evidencias-impl-r2/controle-autoteste-r2.out` —
  mutação **inerte** com item fantasma -> `FALHOU declaracao de mutacao -> ... item esperado inexistente
  na suite` (na rodada 1 esse caso devolvia `OK`); inerte com item real -> `FALHOU ... nao reprovou`;
  mutação **real** com o nome fantasma antigo -> FALHOU; mutação real sem item -> FALHOU.
  `CONTROLE AUTOTESTE OK (4/4 casos fail-closed)`.
- **Controle do `--codigo` (antes x depois):** arquivo sob teste com a âncora de uma mutação quebrada
  por edição inerte de formatação. Antes (harness `8e437b2`, sha256 `2a65064b…`): `AUTOTESTE OK
  (15/15)`, exit 0 — mutava o canônico enquanto a suíte media outro arquivo. Depois: `FALHOU mutacao
  cnpj-passa-a-ser-coluna-de-enriquecimento -> ancora da mutacao nao casa exatamente 1 vez (0)`,
  `AUTOTESTE FALHOU (14/15)`, exit 1.
- **Aceite E2E (agente, na VPS, árvore própria `/opt/tre/t_d9be7d3c-r2`, container descartável
  `pg-research-acc`):** `RESULTADO: ACEITE_RESEARCH_001_OK (55 itens, 0 falhas)` e `DENTE OK (4/4
  mutacoes detectadas, cada uma pelo item esperado)`, exit 0. `research.py` com o **mesmo sha256** da
  rodada 1 (`ab2e397e…`) nas duas pontas — a correção é da prova, o agente não mudou.
- **Portão de estrutura (agente):** `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0
  falhas)`.
- **Nenhum container do TRE foi tocado:** só `pg-research-acc` nasceu e foi removido; `proxy-dev`,
  `odoo-dev`, `pg-odoo-dev` e `pg-sales-dev` seguem de pé.
- **Evidência bruta:** `evidencias-impl-r2/` no diretório de trabalho do card
  (`suite-autoteste-r2.out`, `controle-autoteste-r2.out` e os casos `C1..C4`, `controle-codigo-r2.out`,
  `controle-codigo-r2-ANTES.out`, `aceite-e2e-r2.out`, `estrutura-r2.out`); log completo do aceite
  também em `/opt/tre/evid-t_d9be7d3c-r2/` na VPS.
- **O que esta rodada NÃO mede:** homologação. Quem entrega não homologa — o veredito do estágio 6 é do
  perfil `tester` e a homologação (estágio 7) é do Anderson.

## TRE-W4-E03-T01 — Agente Signal Detector v1 (deteccao de sinais)

- **Card:** `t_61a620b4` (board `transformativa-revenue-engine`) · branch `feature/TRE-W4-E03-T01`,
  base no head aprovado do card pai (`feature/TRE-W4-E02-T01` @ `82ff096`).
- **Momento do registro:** toda evidência abaixo foi produzida **antes** deste texto — nada é narrado
  de memória. Data do registro: 02/10/2026 (UTC).
- **Código sob teste (sha256, igual nas duas pontas — container do Hermes e VPS):**
  `hermes/agents/signal/signal.py` `86bb15ce2ff0468fb9f9d556bf2c8e74fa50620590ba73858a4c02efb7aa7a9b`;
  `scripts/agentes/teste_signal_aceite.sh` `175867d1f7bd2441f63f2f28a3407e70e7bde9a4abad959b1c00b041d10be402`.

### Suite offline (agente, no container do Hermes)

- `python3 scripts/agentes/verificar_agente_signal.py --autoteste` ->
  `RESULTADO: SIGNAL_SUITE_OK (75 itens, 0 falhas)` e `AUTOTESTE OK (21/21 mutacoes detectadas)`,
  exit 0. Saída integral em `evidencias-impl/suite-offline.out`.
- O autoteste muta **cópia** do arquivo sob teste e exige que o item correspondente **reprove**; a
  própria prova tem guarda (item esperado inexistente ou mutação sem item declarado reprova).

### Aceite E2E (agente, na VPS, árvore própria `/opt/tre/t_61a620b4`)

- Container **descartável** `pg-signal-acc` (`postgres:16`, sem porta publicada), schema limpo com a
  migration 0001, **3 organizações pré-existentes** (uma com `updated_at` fixo em `2000-01-01`, de
  propósito, para acusar qualquer toque em coluna de empresa) e **1 `research_run`** (o vínculo lógico
  real da detecção).
- `bash scripts/agentes/teste_signal_aceite.sh --prova-de-dente` ->
  `RESULTADO: ACEITE_SIGNAL_001_OK (64 itens, 0 falhas)`, baseline verde, `DENTE OK (4/4 mutacoes
  detectadas, cada uma pelo item esperado)`, exit 0. Saída integral em
  `evidencias-impl/aceite-e2e-vps.out` (também em `/tmp/signal-aceite-dente.log` na VPS).
- Medições do aceite (todas por SQL no container descartável): 5 sinais gravados (12 observações;
  `DETECTADO=5 JA_DETECTADO=1 REVISAO_IDENTIDADE=1 RECUSADA=5 ERRO=0`), categoria derivada do tipo
  conferida linha a linha, **0** coluna de score preenchida (`decay_factor` no default 1), **3**
  organizações intactas com `updated_at` no valor semeado, **0** linha nas tabelas não declaradas
  (`contacts`, `pain_hypotheses`, `scores`, `interactions`, `recommendations`, `outbox_events`),
  12 linhas de `agent_runs` (6 `COMPLETED`, 5 `REJECTED`, 1 `REVIEW_REQUIRED`), 5 `sync_events`
  `SIGNAL` amarrados ao sinal, 1 `SIGNAL_REVIEW` na fila humana, 1 vínculo com o `research_run` real e
  os 6 descartes com motivo (data, confiança, derivado, campo não declarado, título acima do limite,
  vínculo quebrado). Rodada 2 (mesma fonte) não duplica e não reabre pedido na fila humana; rodada 3
  (chave já reivindicada com o sinal ausente) é replay silencioso (exit 0, sem `ERRO`); `--ambiente prod`
  recusado (exit 4) sem escrita; `--planejar` sem conectar; desfazer dry-run não apaga e `--confirmo`
  apaga só o que a rodada criou, registra `ROLLBACK` e preserva auditoria, fila humana, empresa e
  pesquisa.
- **Itens reprovados por cada mutação (contagem medida no log):** `sem-idempotencia` 8 itens,
  `fechamento-sem-ancora-no-sinal` 4 itens, `categoria-chumbada` 1 item
  (`rodada1-categoria-derivada-do-tipo`) e `vinculo-quebrado-aceito` 1 item
  (`rodada1-descarte-de-vinculo-quebrado`).

### Portão de estrutura (agente)

- `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0 falhas)` (232 linhas `OK`), com os 7
  artefatos do Signal Detector versionados no git e o aceite executável. Saída integral em
  `evidencias-impl/estrutura.out`.

### Nenhum container do TRE foi tocado

- Medição depois do aceite: `proxy-dev` Up 29 horas, `odoo-dev` Up 29 horas, `pg-odoo-dev` Up 30 horas,
  `pg-sales-dev` Up 2 dias — nenhum reiniciado; `pg-signal-acc` **não existe** mais
  (`docker ps -a --filter name=pg-signal-acc` vazio). O aceite aborta se o container já existir.

### Defeitos próprios encontrados e corrigidos ANTES de entregar

1. **`sync_events.entity_type/entity_id` do sinal apontavam para a empresa** — achado pelo E2E: com
   `entity_id = organization_id` a consulta do desfazer (`e.entity_id = s.id`) não achava nada e o
   `--desfazer --confirmo` **não apagava sinal nenhum** (4 itens reprovados: claims e `ROLLBACK`).
   Corrigido para `entity_type='signal'`/`entity_id=<sinal>` (a empresa passou para o
   `request_payload`) e coberto por item próprio (`sync-event-do-sinal-aponta-o-sinal`) **mais** mutação
   `sync-event-aponta-a-empresa` que o reprova.
2. **Aritmética do meu próprio critério de auditoria** (`rodada1-agent-runs-completed` esperava 7 e o
   correto é 6: 5 `DETECTADO` + 1 `JA_DETECTADO`): critério corrigido — o agente estava certo.
3. **Expectativa errada sobre o desfazer** (`desfazer-apagou-os-claims-dos-sinais` esperava 0 claims e o
   correto é **1**): o sinal apagado por fora (rodada 3) deixa o `sync_events` dele **órfão e visível**,
   que é o comportamento declarado no doc §9. Item renomeado
   (`desfazer-deixa-o-claim-orfao-visivel`) e somado a ele o item
   `desfazer-nao-deixou-sinal-orfao-de-claim` (nenhum sinal sem claim).
4. **Duas mutações mudas e uma não detectada** no autoteste: as âncoras de `sem-idempotencia` e
   `data-invalida-aceita`/`confianca-fora-da-faixa-aceita` não casavam (a âncora do claim aparecia 2x
   depois da fila humana ganhar claim próprio; e a indentação era de 4 espaços, não 8) e a mutação da
   data não mudava nada observável (o `fromisoformat` já recusava o caso testado). Correções: âncora de
   4 linhas para o claim do sinal, indentação correta e item reforçado com a **forma básica**
   (`20260928`) — o formato declarado é ISO-8601 estendido e a variante básica é recusada com motivo,
   não adivinhada.

### O que este card NÃO mede

- **Homologação.** Quem entrega não homologa — o veredito do estágio 6 é do perfil `tester` e a
  homologação (estágio 7) é do Anderson.
- **Score.** `buying_signal_points`/`relevance_score`/`decay_factor`/`expires_at` **não** são escritos:
  o Buying Signal Score é `TRE-W5-E03-T01`. O aceite mede justamente a **ausência** de score.
- **Busca ativa.** Sem LLM, sem HTTP e sem crawler na v1: a fonte entrega a observação e a evidência.

## TRE-W4-E04-T01 — Agente Pain Hypothesis v1 (hipotese de dor com lastro)

- **Card:** `t_b4e01433` (board `transformativa-revenue-engine`) · branch `feature/TRE-W4-E04-T01`,
  base no head aprovado dos dois pais (`feature/TRE-W4-E03-T01` @ `c52167b`, que já contém o Research
  `82ff096`).
- **Momento do registro:** toda evidência abaixo foi produzida **antes** deste texto — nada é narrado
  de memória. Data do registro: 02/10/2026 (UTC).
- **Código sob teste (sha256, igual nas duas pontas — container do Hermes e VPS):**
  `hermes/agents/pain_hypothesis/pain_hypothesis.py`
  `d53754f268e5a63be6865046e592bbc2af7cd5f0a73bf286c89a742fb2abb908`;
  `scripts/agentes/teste_pain_hypothesis_aceite.sh`
  `e3976eb7831d5b2527980789c8a8d2b61d46005bff3406fc1a83c313306ee18c`.

### Suite offline (agente, no container do Hermes)

- `python3 scripts/agentes/verificar_agente_pain_hypothesis.py --autoteste` ->
  `RESULTADO: PAIN_SUITE_OK (85 itens, 0 falhas)` e `AUTOTESTE OK (27/27 mutacoes detectadas)`,
  exit 0. Saída integral em `evidencias-impl/suite-offline-verif.out` (reprodução própria, além da
  do autor: `evidencias-impl/suite-offline.out`).
- O autoteste muta **cópia** do arquivo sob teste e exige que o item correspondente **reprove**; a
  própria prova tem guarda (item esperado inexistente ou mutação sem item declarado reprova).

### Aceite E2E (agente, na VPS, árvore própria `/opt/tre/e04t01`)

- Container **descartável** `pg-pain-acc` (`postgres:16`, sem porta publicada), schema limpo com a
  migration 0001, **3 organizações pré-existentes** (`updated_at` fixo em `2000-01-01`, de propósito,
  para acusar qualquer toque em coluna de empresa), **3 `research_runs`** e **4 `signals`** semeados
  por SQL — o lastro real das hipóteses.
- `bash scripts/agentes/teste_pain_hypothesis_aceite.sh --prova-de-dente` ->
  `RESULTADO: ACEITE_PAIN_001_OK (85 itens, 0 falhas)`, baseline verde, `DENTE OK (5/5 mutacoes
  detectadas, cada uma pelo item esperado)`, exit 0. Saída integral em
  `evidencias-impl/aceite-e2e-vps.out` (log do run independente desta sessão; o run do autor ficou em
  `evidencias-impl/aceite-pain-dente.log`).
- Medições do aceite (todas por SQL no container descartável): alvo medido
  `current_database()=sales_intelligence` / `current_user=sales_ai`; 14 hipóteses processadas na
  rodada 1 (`REGISTRADA=4 JA_REGISTRADA=1 REVISAO_IDENTIDADE=1 RECUSADA=8 ERRO=0`) e **4 linhas** em
  `pain_hypotheses` (2 na ORG_A, 1 na ORG_B, 1 na ORG_C) com `pain_statement` preenchido, id UUID v4,
  `status='HYPOTHESIS'` nas 4 e categoria do vocabulário (FINANCEIRO/COMERCIAL/ATENDIMENTO/OPERACOES);
  **0** coluna proibida preenchida (`business_impact_score`, `estimated_impact_description`,
  `validated_at` todas NULL); `evidence` com `inferencia`, `marcada_como_inferencia`, `evidencias`,
  `evidencia_primaria` e `input_hash` nas 4; o lastro amarrado às origens reais com o **fato de
  origem** conservado (`signal_type`/`signal_category`/`event_date` do sinal e `research_type`/`status`
  da pesquisa) e o vínculo com o `research_run` real (1); 7 descartes com motivo medidos no SQL
  (evidência de outra empresa, evidência não encontrada, confiança fora da faixa, derivado, campo não
  declarado, resumo acima do limite, vínculo quebrado); a hipótese **sem lastro não foi gravada**
  (`SEM_EVIDENCIA_VALIDA`); **3** organizações intactas com `updated_at` no valor semeado e nenhuma
  criada; `research_runs=3` e `signals=4` intactos; **0** linha nas tabelas não declaradas (`contacts`,
  `scores`, `interactions`, `recommendations`, `outbox_events`); 14 linhas de `agent_runs` (5
  `COMPLETED`, 8 `REJECTED`, 1 `REVIEW_REQUIRED`, 0 `FAILED`), 4 delas apontando a hipótese gravada; 4
  `sync_events` `PAIN_HYPOTHESIS` `SUCCESS` amarrados à hipótese (com o formato da chave
  `pain:org:<uuid>:<hash>`) e 1 `PAIN_REVIEW` com a fila humana `PENDING`
  (`PAIN_IDENTITY_REVIEW`, duas candidatas). Rodada 2 (mesma fonte) não duplica (4 linhas), dá 5
  replays e não cria claim novo; rodada 3 (hipótese apagada por fora, chave já reivindicada) é replay
  silencioso (exit 0, sem `ERRO`, sem recriar); `--ambiente prod` recusado (exit 4) sem escrita e sem
  auditoria; `--planejar` sem conectar; desfazer dry-run não apaga e `--confirmo` apaga só o que a
  rodada criou, registra `ROLLBACK` e preserva auditoria (14), fila humana, empresa (3), pesquisa (3)
  e o lastro (4 `signals`).
- **Itens reprovados por cada mutação (contagem medida no log):** `sem-idempotencia` 9 itens,
  `fechamento-sem-ancora-na-hipotese` 4, `lastro-nao-conferido` 25,
  `lastro-de-outra-empresa-aceito` 2 (`rodada1-lastro-l4-so-o-valido`,
  `rodada1-descarte-de-evidencia-de-outra-empresa`), `status-chumbado-validado` 1
  (`rodada1-status-inicial`).

### Portão de estrutura (agente)

- `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0 falhas)` (240 linhas `OK`), com os 7
  artefatos do Pain Hypothesis versionados no git e o aceite executável. Saída integral em
  `evidencias-impl/estrutura.out`.

### Nenhum container do TRE foi tocado

- Medição depois do aceite: `proxy-dev` Up 30 horas (healthy), `odoo-dev` Up 30 horas, `pg-odoo-dev`
  Up 31 horas (healthy), `pg-sales-dev` Up 2 dias — nenhum reiniciado; `pg-pain-acc` **não existe**
  mais. O aceite aborta se o container já existir. Medição em
  `evidencias-impl/verificacao-vps.txt`.

### O que este card NÃO mede

- **Homologação.** Quem entrega não homologa — o veredito do estágio 6 é do perfil `tester` e a
  homologação (estágio 7) é do Anderson.
- **Impacto e validação.** `business_impact_score`, `estimated_impact_description` e `validated_at`
  **não** são escritos (não há fórmula de impacto homologada e validar é ato humano): o aceite mede
  justamente a **ausência** deles.
- **Busca ativa e inferência por LLM.** Sem LLM, sem HTTP e sem crawler na v1: a fonte entrega a
  hipótese e o lastro; o agente **confere** o lastro (existência e dono), não o produz.
## TRE-W4-E05-T01 — Agente Contact Research v1 (contato comercial da empresa já pesquisada)

- **Card:** `t_1a85a424` (board `transformativa-revenue-engine`) · branch `feature/TRE-W4-E05-T01`,
  base no head aprovado do card irmão (`feature/TRE-W4-E02-T01`, agente Research).
- **Momento do registro:** toda evidência abaixo foi produzida **antes** deste texto — nada é narrado
  de memória. Data do registro: 02/10/2026.

### Rodada 1 — implementação, suíte offline e aceite E2E

- **Suíte offline (agente, no container do Hermes):**
  `python3 scripts/agentes/verificar_agente_contact_research.py --autoteste` ->
  `RESULTADO: CONTACT_RESEARCH_SUITE_OK (60 itens, 0 falhas)` e
  `AUTOTESTE OK (25/25 mutacoes detectadas)`, exit 0. Saída integral em
  `evidencias-impl/suite-autoteste.out` (anexo do card).
- **Aceite E2E (agente, na VPS, árvore própria em `/opt/tre/e05t01-r1`):** container descartável
  `pg-contact-acc` (`postgres:16`, sem porta publicada) com a migration 0001 em schema limpo, **3
  organizações** pré-existentes (uma com `industry_name` curado) e **1 contato** pré-existente com
  e-mail em CAIXA ALTA, cargo curado e `do_not_contact`/`opt_out_email` ligados -> `RESULTADO:
  ACEITE_CONTACT_RESEARCH_001_OK (65 itens, 0 falhas)`, exit 0.
- **Prova de dente (mesmo aceite):** `DENTE OK (4/4 mutacoes detectadas, cada uma pelo item
  esperado)`, baseline verde antes das mutações — `sem-idempotencia` 5 itens (`rodada2-exit-0`,
  `rodada2-ja-identificado-cinco`, `rodada3-exit-0`, …), `enriquecimento-sem-coalesce` 14 itens
  (`rodada1-enriquecimento-nao-sobrescreve`, …), `identidade-sem-lower` 12 itens
  (`rodada1-identidade-casa-sem-diferenca-de-caixa`, …) e `rollback-sem-guarda-de-espelho` 4 itens
  (`desfazer-recusa-contato-espelhado`, …). Saída integral em `evidencias-impl/aceite-e2e.out` e
  `evidencias-impl/aceite-e2e-dente.out` (anexos do card); log completo também em
  `/opt/tre/evid-e05t01-r1-aceite.out` e `/opt/tre/evid-e05t01-r1-dente.out` na VPS.
- **Portão de estrutura (agente):** `bash scripts/verificar_estrutura.sh` -> `RESULTADO: PASS (0
  falhas)`, com os 7 artefatos do Contact Research versionados e o aceite executável.
- **Nenhum container do TRE foi tocado:** só `pg-contact-acc` nasceu e foi removido; `proxy-dev`,
  `odoo-dev`, `pg-odoo-dev` e `pg-sales-dev` seguem de pé.

### Correções medidas nesta rodada (defeito da PROVA, não do agente)

Cinco provas mediam a coisa errada e passavam (ou reprovavam pelo motivo errado). O agente não
apresentou defeito no que foi medido — o buraco era do verificador:

- `insert-sem-guarda-de-coluna` e `outbox-nas-tabelas-permitidas`: a âncora citava texto que a
  implementação já não tinha (a guarda do INSERT passou a ler `COLUNAS_DE_INSERT`, e a ordem de
  `TABELAS_PERMITIDAS` é `contacts, organizations, agent_runs, …`) -> a mutação **não aplicava** e o
  autoteste reprovava por buraco de verificação (comportamento correto da guarda da própria prova).
  Âncoras passaram a ser o texto real do código.
- `email-como-coluna-de-enriquecimento`: a mutação ampliava a lista declarada de enriquecimento, mas o
  item só media o SQL gerado com colunas fixas. O item passou a medir a **declaração**
  (`COLUNAS_ENRIQUECIMENTO` sem coluna de identidade) e a **guarda** (`conferir_enriquecimento` tem de
  recusar a identidade mesmo a pedido explícito).
- `raiz-por-profundidade-do-arquivo`: o item copiava o **código canônico** em vez do módulo sob teste —
  com `--codigo`/autoteste, media o agente certo enquanto a mutação passava. Agora copia
  `Path(modulo.__file__)`.
- No aceite E2E, duas expectativas do próprio aceite estavam erradas (e a mutação declarava o item
  errado): a evidência de `evento_de_espelho` existe em **5** dos 11 pedidos (só quem chegou a escrever
  contato tem rodada a espelhar); a fila humana tem **2** `PENDING` (a ambiguidade de identidade da
  empresa reaparece na rodada 2, porque é do conflito, não da rodada); e `sem-idempotencia` declarava
  `rodada2-nao-duplica` — que **passa** justamente porque a rodada 2 morre antes de escrever. O item
  que mede o defeito é `rodada2-exit-0`/`rodada2-ja-identificado-cinco`.
- **O que esta rodada NÃO mede:** homologação. Quem entrega não homologa — o veredito do estágio 6 é do
  perfil `tester` e a homologação (estágio 7) é do Anderson.

## 2026-10-02 — repositório TRE (worktree `t_a32ae24f`) + VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W4-E06-T01 (card `t_a32ae24f`): aceite E2E Sales Intelligence (a cadeia dos cinco agentes)

- **Base consolidada da onda W4 (agente):** `feature/TRE-W4-E06-T01` criada de `feature/TRE-W4-E04-T01` +
  merge de `feature/TRE-W4-E05-T01` (Contact Research) — conflitos em `CHANGELOG.md`,
  `docs/operations/registro-de-execucoes.md` e `scripts/verificar_estrutura.sh`, resolvidos por **UNIÃO**
  (as duas seções); merge em `875baaa`, aceite em `552a250`, passo 0 + dente por vínculo em `a521caa`,
  detecção do passo 0 + sha256 na evidência em `e15d4d6`. **Versão do aceite medida: `e15d4d6`** (sha256
  do arquivo `c789934e84af0b25555d53bace2fd29fda8a237bf13746b57029f4a21b5d90d7`, conferido na VPS antes
  do run); o **código sob teste** (os cinco agentes) está inalterado desde `875baaa` — sha256 dos cinco
  fontes = `65af917603a8be90cf89d09bf692abf32c6fdf673c5303c6aaad3d4a91c11323`, fixado na própria saída do
  run.
- **Aceite completo (agente):** `scripts/e2e/verificar-e2e-sales-intelligence.sh` no clone
  `/opt/tre/e06t01-si-r1`, container descartável `pg-e2e-si-acc` (postgres:16) →
  **`ACEITE_E2E_SALES_INTELLIGENCE_001_OK (76 itens, 0 falhas)`**, exit 0. **Passo 0** no mesmo run: as
  cinco suítes offline no **mesmo commit** → 5 OK / 0 FALHOU (58 + 65 + 75 + 85 + 60 = **343 itens**).
  sha256 do código sob teste fixado na evidência:
  `65af917603a8be90cf89d09bf692abf32c6fdf673c5303c6aaad3d4a91c11323`.
- **Prova de dente (agente):** `--prova-de-dente` → **`DENTE OK (5/5 mutações detectadas, cada uma pelo
  item esperado)`**: `scout-escreve-empresa-sem-identidade` (38 itens reprovados, incluindo o esperado),
  `pesquisa-run-sem-organizacao` (7, incluindo `cadeia-sinal-vincula-a-pesquisa-da-mesma-empresa` e
  `cadeia-hipotese-lastro-de-pesquisa`), `sinal-anexa-run-inexistente` (2),
  `hipotese-aceita-lastro-de-outra-empresa` (8), `contato-sem-idempotencia` (2). Baseline verde **antes**
  de cada mutação.
- **Medido no BANCO (não na narrativa):** 3 empresas `DISCOVERED` criadas pelo Scout (id de cada uma lido do
  **relatório da rodada**, não de fixture) → 4 `research_runs` das MESMAS empresas, enriquecendo coluna
  vazia (`website_url`, `unit_count`, `revenue_estimate`) e **preservando** indústria/porte/cidade do Scout,
  com o derivado declarado pela fonte descartado com motivo (`DERIVADO_NAO_ACEITO`) → 4 sinais, 2 deles
  vinculados ao `research_run_id` **produzido na rodada anterior** (o vínculo com run inexistente é
  descartado com motivo e o sinal fica sem o vínculo) → 4 hipóteses (3 registradas, 1 recusada por lastro de
  **outra empresa** (`EVIDENCIA_DE_OUTRA_ORGANIZACAO`)) → 1 contato identificado, 1 recusado e 1 ambiguidade
  que abre fila humana **sem escrever contato**. Replay das cinco rodadas com as **mesmas fontes**:
  assinatura das cinco tabelas de negócio idêntica antes/depois (zero duplicata) e a rodada ambígua
  **re-reportada** (1 → 2 `PENDING`, contato continua não escrito). Desfazer na ordem inversa (contato →
  hipótese → sinal → pesquisa → scout) devolve `0|0|0|0|0` nas tabelas de negócio, **preserva a fila
  humana** e registra os 5 `ROLLBACK`; o desfazer do Research restaura `website_url`/`unit_count`/
  `revenue_estimate` e **não** toca no derivado do Scout. Guardas: `--ambiente prod` recusado nos **cinco**
  (exit 4) sem escrever uma linha, `--planejar` sem porta de escrita, nenhuma escrita em
  `scores`/`outbox_events`/`interactions`/`recommendations` (W5 fora do escopo).
- **Evidência guardada:** `/opt/tre/evid-e06t01-si-final.out` (sha256
  `4bc83b7884e75d879a1dbd6401f2d389ed41cad5c3d96f709fb294faa2d1d027`, cópia **idêntica** em
  `attachments/t_a32ae24f/aceite-e2e.out`) e as rodadas anteriores `/opt/tre/evid-e06t01-si-dente-r1.out`
  (dente 3/5) e `…-r2.out` (4/5) — as duas que revelaram os defeitos da prova abaixo.
- **Ambiente:** só o container descartável nasceu e foi **removido** (`docker ps -a | grep e2e-si` = 0);
  `proxy-dev` (Up 31h, healthy), `odoo-dev` (Up 31h), `pg-odoo-dev` (Up 32h, healthy) e `pg-sales-dev`
  (Up 2d) intactos; nada em produção; `scripts/verificar_estrutura.sh` → **PASS** com os três artefatos do
  card versionados e o aceite executável.
- **Defeitos da PROVA corrigidos nesta rodada (medidos — o agente não apresentou defeito no que foi medido):**
  1. **falso verde no fingerprint**: `foto_das_contagens` juntava os `count(*)` com `|` **dentro do SQL**, e
     `|` é OU bit a bit — as duas pontas viravam `7` e "zero duplicata" passava sem comparar nada. Agora a
     concatenação é `||`.
  2. **passo 0 cego**: o item casava `(0 falhas)` enquanto a suíte imprime `(58 itens, 0 falhas)` → 0/5 com
     as suítes verdes. Padrão corrigido para `[0-9]+ itens, 0 falhas`.
  3. **duas mutações inertes**: tirar o `ON CONFLICT` do claim do Scout **não** duplica (a rodada seguinte
     resolve a empresa antes) e tornar a consulta de identidade inerte **também não** (o claim por
     identidade barra) — a idempotência do replay tem **duas camadas**; trocar o `COALESCE(NULLIF(...))` do
     Research por atribuição direta faz a **guarda recusar a escrita** (não há mutação de um ponto que
     produza sobrescrita). O dente passou a mirar o que **só a cadeia** mede: o **vínculo** entre o que um
     agente escreve e o que o próximo resolve.
  4. **expectativa errada do aceite**: o item do desfazer do Research exigia `employee_band IS NULL` quando
     o porte deriva do `employee_count` **do Scout** (o Research nunca escreveu ali) — passou a medir as
     colunas que o Research de fato enriquece; e o item da fila humana esperava `1 PENDING` quando a rodada
     ambígua re-reporta (`2`), que é o contrato declarado no runbook.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. Limites declarados no doc de
  arquitetura (§6): a cadeia medida são os cinco agentes W4 em `dev`; W5 (score/tier/NBA), Odoo e Titan não
  estão no caminho.

## 2026-10-02 — repositório TRE (worktree `t_e4a90eba`, branch `feature/TRE-W5-E01-T01`) + VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E01-T01 (card `t_e4a90eba`): agente ICP Score v1 (fit estrutural)

- **Acesso:** `ssh -i /opt/data/.ssh/tre_deploy tre-deploy@169.58.24.102` (BatchMode) → `hostname`
  `vmi3619453`. A árvore desta rodada é própria: `/opt/tre/w5e01` (criada por `mkdir -p` + `tar xzf -`;
  nenhum `rm -rf` na máquina). Nada foi escrito fora dela e do container descartável.
- **Suíte offline (container do Hermes, sem banco):**
  `python3 scripts/agentes/verificar_agente_icp_score.py` → `RESULTADO: VERIFICACAO_ICP_SCORE_OK (67 itens,
  0 falhas)`; `… --autoteste` → `AUTOTESTE OK (21/21 mutacoes detectadas, cada uma pelo item esperado)`,
  com o código restaurado byte a byte depois de cada mutação (`sha256` do fim igual ao do começo).
- **Aceite no banco (VPS, container descartável `pg-icp-acc`, imagem `postgres:16`, DDL aplicado do zero):**
  `bash scripts/agentes/teste_icp_score_aceite.sh` → `RESULTADO: ACEITE_ICP_SCORE_001_OK (50 itens, 0
  falhas)`. Valores medidos, não narrados: `rodada1-valor-sweet-spot (100.00)`, `…-logistica (86.00)`,
  `…-b2c-varejo (0.00)`, `…-sem-dado (0.00)`, `…-b2b2c (94.00)`, `…-porte-derivado (100.00)`; recusas
  `rodada1-recusadas (2)` (`inexistente-sem-score (0)`); replay `rodada2-replay (JA_EXISTE=6 CALCULADO=0)`;
  fonte mentindo todos os campos de score da empresa que está no banco → `rodada2b-fonte-nao-contamina
  (JA_EXISTE=1)` e `rodada2b-score-nao-se-move (100.00)`; dado alterado no banco → `rodada3-historico-
  preservado (2)` com `rodada3-valor-antigo-preservado (1)`; `rodada1-sem-llm (8)`; `rodada1-nenhuma-outra-
  tabela-escrita (0)`; `prod-recusado-exit-4 (4)` e `prod-nao-registrou-execucao (0)`; `planejar-exit-
  0-sem-conectar (0)`; `desfazer-apagou-so-a-rodada (1)` com `desfazer-preservou-auditoria (8)` e
  `desfazer-preservou-o-score-de-outra-rodada (86.00)`.
- **Prova de dente (mesmo script, `--prova-de-dente`):** `DENTE OK (9/9 mutacoes detectadas, cada uma pelo
  item esperado)` — `ausencia-de-porte-vira-fit`, `sem-faixa-derivada-do-count`, `peso-do-modelo-zerado`,
  `sem-idempotencia-no-sql`, `fingerprint-constante`, `prod-liberado`, `fonte-contamina-o-score`,
  `desfazer-apaga-a-auditoria`, `organizacao-inexistente-cria-score`.
- **Evidência guardada:** `/opt/tre/w5e01/evid-w5e01-icp-final.out` (sha256
  `e03f79d9af0f9bdae79a2d66172213bdebe2750c98eb2899d8d3eff49bf785e2`, 89 linhas, cópia **idêntica** em
  `attachments/t_e4a90eba/aceite-icp-score.out`). Código medido no aceite: `icp_score.py` sha256
  `0e6178441ff42232561e62246cfb441659a800c7ec38b0fb4909b6e715a5f1a1` e `teste_icp_score_aceite.sh` sha256
  `c922f724fbafa6b64836b12e2c9f971b6aff4ae9de3c32769461ef5cbf6f590e` (os mesmos hashes do repositório).
  As rodadas anteriores (`aceite-r1.out` com 2 falhas de item, `dente.out` e `dente2.out` com dente 8/9)
  ficaram na mesma árvore, `-r1/-r2` de propósito no nome.
- **Ambiente:** só o container descartável nasceu e foi **removido** (`docker ps -a | grep -c icp-acc` = 0);
  `proxy-dev` (Up 32h, healthy), `odoo-dev` (Up 32h), `pg-odoo-dev` (Up 33h, healthy) e `pg-sales-dev`
  (Up 2d) intactos — conferido por item do aceite (`ambiente-containers-intactos (4)`). Nada em produção.
  Observação: `pg-automation-fit-acc` (container descartável de **outro** card, W5-E02) estava de pé durante
  a rodada — não foi tocado.
- **Defeitos da PROVA corrigidos nesta rodada (medidos — o agente não apresentou defeito no que foi medido):**
  1. **mutação inerte por defesa em camadas**: liberar `prod` na lista de ambientes permitidos **não** muda
     nada — a checagem explícita de `prod` recusa antes (o dente acusou "NAO foi detectada"). A mutação
     passou a mirar a **ligação** da guarda no `main` (`if args.ambiente or not args.planejar:` → `if
     False:`), isto é, guarda que existe e não é chamada — foi detectada por `prod-recusado-exit-4` e
     `prod-nao-registrou-execucao`.
  2. **expectativa errada da mutação**: com a guarda desligada os `scores` da rodada de prod continuam 7 (o
     replay não grava linha nova), então `prod-nao-escreveu` **não** podia reprovar; o item que de fato mede
     a passagem indevida é `prod-nao-registrou-execucao` (a auditoria registra 8 linhas de `agent_runs`).
  3. **item que lia o repositório, não o código sob teste**: a checagem da raiz por marcador lia o arquivo
     do repo (`CODIGO_PADRAO`), então a mutação — que vive numa cópia — passava verde; agora o texto
     analisado é o do **módulo carregado** (`modulo.__file__`).
  4. **item medindo a coisa errada**: o item de `inputs`/`explanation` excluía a organização sem dado
     (`origem_do_porte='ausente'` → esperava 6 linhas e obtinha 5) e o item dos três motivos comparava a
     lista **ordenada alfabeticamente** em vez da ordem dos componentes (`SEGMENTO_NAO_INFORMADO,
     PORTE_NAO_INFORMADO,MODELO_DE_NEGOCIO_NAO_INFORMADO`); os dois passaram a medir o que declaram.
  5. **duas mutações com alvo mal escolhido** (`sem-faixa-derivada`, `ausencia-de-porte-vira-fit`): não
     podiam reprovar itens que usam `employee_band` do banco — as expectativas foram movidas para os itens
     que de fato dependem da regra mutada.
- **Falha de preparação da rodada** (não é defeito do código): a primeira execução do aceite na VPS partiu
  sem `docs/data/data_contract_v1.json` na árvore e o agente recusou-se a rodar (`contrato de dados ausente`)
  — o agente **exige** o contrato, como os outros da cadeia; a árvore foi completada e o aceite repetido.
- **Portão de estrutura:** `scripts/verificar_estrutura.sh` → **PASS (0 falhas)** com os artefatos do card
  versionados e o aceite executável — as sete linhas novas do portão são
  `versionado hermes/agents/icp_score/{icp_score.py,agente-icp-score-v1.json,exemplos/organizacoes-exemplo.jsonl}`,
  `versionado scripts/agentes/{verificar_agente_icp_score.py,teste_icp_score_aceite.sh}`,
  `versionado docs/{architecture/agente-icp-score-v1.md,runbooks/agente-icp-score.md}` e
  `executavel scripts/agentes/teste_icp_score_aceite.sh`.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. A fórmula `icp-v1.0.0` é
  **proposta**: o baseline (doc 03 §3) nomeia o score e não define fórmula, e o Data Contract só dá o
  contexto de negócio (`scores.icp_context`). Se homologada, o peso passa a ser parte do contrato de dados
  (v1.1), decisão do dono.
## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — Buying Signal Score v1 (TRE-W5-E03-T01)

- **Envio da árvore sob teste (agente):** `tar czf - (hermes/agents/buying_signal, hermes/agents/signal/signal.py,
  scripts/agentes/teste_buying_signal_aceite.sh, db/migrations/0001…, docs/data/data_contract_v1.json) |
  ssh root@169.58.24.102 'mkdir -p /opt/tre/buying-e03t01-r1 && tar xzf -'` → **7 arquivos** em
  `/opt/tre/buying-e03t01-r1` (`scp` com IP cru é bloqueado pelo scan; o envio é por stdin).
- **Aceite E2E (agente):** `bash scripts/agentes/teste_buying_signal_aceite.sh --raiz /opt/tre/buying-e03t01-r1`
  → container descartável **`pg-buying-acc`** (postgres:16) criado só para a medição, migration 0001 aplicada,
  massa de **3 sinais** em 1 empresa + 1 empresa sem sinal:
  **`36 OK / 0 FALHOU` → `ACEITE_BSS_001_OK`** (console `/tmp/bss-aceite-r6.console`).
  Items medidos contra o banco: `score_type=BUYING_SIGNAL`, `score_version=buying-signal-v1`,
  `valid_until = calculated_at + 30 days` = 1, `inputs.sinais_utilizados` = 3, `confianca_padrao_usada` = 1,
  `signals` = 3 linhas com `relevance_score`/`buying_signal_points`/`expires_at` **NULL**, replay
  (`bbbb…` com a mesma entrada) **não duplicou**, sinal novo ⇒ **linha nova** com valor maior e a antiga
  preservada, empresa sem sinal ⇒ `0.00` + motivo `SEM_SINAIS`, empresa fantasma ⇒ `RECUSADA` sem escrita,
  desfazer dry-run **não apagou** e `--confirmo` apagou **só a rodada** (`3 → 2`), `agent_runs` = 1 por rodada,
  `sync_events` = `PROCESSED`; `prod` recusado com **exit 4 sem escrita** e `--planejar` **sem conexão**.
- **Rodadas anteriores (agente):** `/tmp/bss-aceite-r2…r5.console` — r3 (dados do psql com fuso `+00` recusados
  pela validação ISO), r4 (confiança `NULL` lida como `''`) e r5 (hash carregando pontos ⇒ replay duplicando):
  as três revelaram **defeitos reais** do componente, corrigidos e cobertos por item de suíte.
- **Defeitos da PROVA corrigidos nesta rodada:** o primeiro aceite usava `psql -c "SELECT $1;"` com a cláusula
  completa na chamada (`SELECT SELECT …`, 32 itens falsos) e media o dry-run por `grep` no texto do relatório
  (passou a medir o **estado do banco**).
- **Ambiente:** o container `pg-buying-acc` foi **removido** pelo próprio aceite; `pg-sales-dev` (Up 2d),
  `pg-odoo-dev`, `odoo-dev` e `proxy-dev` **intactos**; o container `pg-icp-acc` (aceite do card irmão
  W5-E01-T01, rodando em paralelo) **não foi tocado**; nada em produção.
- **Prova de dente do aceite (agente):** `bash scripts/agentes/teste_buying_signal_aceite.sh --prova-de-dente`
  → **`40 OK / 0 FALHOU` → `ACEITE_BSS_001_OK`** com dente **4/4** (`sem-versao` → `A1 score_version`,
  `sem-teto` → `A1 score_value > 0`, `sem-idempotencia` → `A4 replay: continua 1 score`, `sem-validade` →
  `A1 valid_until = calculated_at + 30 dias`); console `/tmp/bss-aceite-dente2.console`, sha256
  `74871f2f5e02fafdc0031eefe9509a76bb7e46d1d2bc23a78230c2d2ea8ba8d3`, cópia em
  `attachments/t_967911e0/aceite-bss-e2e-dente.console`. O primeiro dente do aceite teve duas falhas de
  PROVA (âncora ausente e mutação inerte de duas camadas), corrigidas: a mutação passou a mirar o
  `AND status = 'REGISTERED'` do INSERT ancorado, que é o que só o E2E mede.
- **Suítes offline (agente):** `verificar_buying_signal_score.py` → **91 itens / 0 falhas**; `--prova-de-dente`
  → **12/12 OK** com controle negativo (mutação inerte **não** reprova a suíte).
## 2026-10-02 — repositório TRE (worktree `t_11815e63`) + VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E02-T01 (card `t_11815e63`): Automation Fit Score v1 (score `AUTOMATION_FIT`)

- **Base (agente):** `feature/TRE-W5-E02-T01` criada do head aprovado do card pai `TRE-W4-E06-T01` (`63d711c`),
  head do card `d877402` (6 commits: componente, contrato legível por máquina, suíte, aceite, docs, portão).
- **Passo 0 (agente, sem banco e sem rede, na própria máquina do Hermes):**
  `python3 scripts/agentes/verificar_agente_automation_fit.py --autoteste` →
  **`AUTOMATION_FIT_SUITE_OK (54 itens, 0 falhas)`** + **`AUTOTESTE OK (22/22 mutações detectadas)`** (cada
  mutação reprovando **o item esperado**, não só "o aceite falhou") — `RESULTADO FINAL: AUTOMATION_FIT_OK`.
- **Aceite E2E (agente, na VPS):** `scripts/agentes/teste_automation_fit_aceite.sh --prova-de-dente` no clone
  `/opt/tre/w5e02t01-final/repo`, container **descartável próprio** `pg-automation-fit-acc` (postgres:16),
  migration `0001` aplicada em schema limpo → **`ACEITE_AUTOMATION_FIT_001_OK (76 itens, 0 falhas)`**, exit 0,
  e **prova de dente `DENTE OK (5/5 mutações detectadas, cada uma pelo item esperado)`**: cobertura-ignorada
  (23 itens reprovados), score-constante (10), idempotencia-sem-o-estado (11), leitura-sem-filtro-de-empresa
  (37), prod-liberado (4) — **baseline verde antes de cada mutação**. `sha256` conferido na VPS antes do run e
  **fixado na própria saída**: código sob teste `d07da1562bd9b672f32c26f669c78576f2587d8c2b74e1aff15da8dccb5415fc`,
  aceite `c0fa1c1500b2b5b315b2363c216460f27b126825cef2e8b5b7d630bc942d5fb6` (idênticos ao worktree do card).
- **Medido no BANCO (não na narrativa):** rodada 1 sobre 5 empresas → vereditos
  `CALCULADO=4 JA_CALCULADO=1 REVISAO_IDENTIDADE=1 RECUSADA=4 ERRO=0` e 4 linhas em `scores` com
  `score_type=AUTOMATION_FIT` / `score_version=automation-fit-v1`, UUID v4, `valid_until` NULL, explicação com
  os cinco componentes, `inputs` com snapshot + `input_hash`. Replay do MESMO estado (rodada 2) **não duplica**
  (4 `JA_CALCULADO`, `sync_events` inalterados); **estado novo cria LINHA NOVA** (rodada 3: org A fica com 2
  linhas, 85,00 preservado e 76,00 novo, cobertura 1,00); rodada 4 replica a 3 sem duplicar.
  **Discriminação medida (AC8):** 4 faixas — 76,00 · 48,50 · 83,00 · 30,00 — margem de 53 pontos e desvio médio
  de 20 pontos da constante 50. Fronteiras: `SEM_LASTRO` recusada **sem** escrever score, empresa inexistente /
  sem identificador forte / identificador inválido recusadas, identidade ambígua vai para a fila humana
  (`AUTOMATION_FIT_IDENTITY_REVIEW`, 1 `PENDING`) **sem** escrever score. Guardas: `--ambiente prod` recusado
  nos 5 pedidos (exit 4) sem uma linha escrita e `--planejar` não abre conexão; **zero** escrita em
  `organizations` (inclusive `data_quality_score`), `signals`, `pain_hypotheses`, `research_runs` e nas demais
  tabelas. Desfazer: dry-run não apaga; `--confirmo` apaga **só** a linha da rodada (5 → 4) preservando 5
  empresas, 12 sinais, 4 hipóteses e 40 `agent_runs`, registrando `ROLLBACK` em `sync_events`.
- **Reprodutibilidade medida:** o aceite rodou **duas vezes** — a primeira em processo desanexado que sobreviveu
  ao run do harness encerrado por limite de tempo, a segunda **dentro do run `235`** — com saída **byte a byte
  idêntica** (sha256 da evidência igual), o que torna o aceite **reprodutível**, não apenas determinístico no
  valor do score.
- **Evidência guardada:** `/opt/tre/evid-e02t01-aufit-final.out` (sha256
  `d4f22259405b780c928190f5b8a45f73a7442b305e0159c61417cd1757dcac12`, cópia **idêntica** anexada ao card
  `t_11815e63`).
- **Ambiente:** só o container descartável nasceu e foi **removido** (`docker ps -a | grep automation-fit` = 0) e
  o diretório de trabalho `/tmp/automation-fit-aceite-trabalho` apagado; `proxy-dev` (Up 32h, healthy),
  `odoo-dev` (Up 32h), `pg-odoo-dev` (Up 33h, healthy) e `pg-sales-dev` (Up 2d) **intactos**; nada em produção.
  `scripts/verificar_estrutura.sh` → **PASS (0 falhas)**, com os 7 artefatos do card versionados e as duas
  suítes (`verificar_agente_automation_fit.py`, `teste_automation_fit_aceite.sh`) executáveis.
- **Limites declarados / não é homologação:** a **fórmula V1** (pesos, cobertura mínima 0,40 e faixas) é
  proposta do worker — o baseline diz **o que** o score mede, não **como**; o número é reprodutível e
  auditável, **não** validado comercialmente. Homologação é do Anderson (**estágio 7**); o veredito deste card é
  do **estágio 6** (revisão independente, perfil `tester`).
## 2026-10-02 — repositório TRE (worktree `t_701c574f`) + VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E04-T01 (card `t_701c574f`): Score Data Quality v1 (o primeiro score da W5)

- **Base e commits:** `feature/TRE-W5-E04-T01` criada de `63d711c` (baseline **V1.1.0** = a cadeia E2E
  dos cinco agentes). Entrega em `5beba62` (motor, contrato, suíte, aceite, arquitetura, runbook, gate e
  CHANGELOG); conserto do casamento de identidade + veredito do aceite em `4caf3c6`; caminho do arquivo de
  itens no escopo global em `87bdc55`; âncora do dente de confiabilidade em `7218c34`. **Versão do aceite
  medida: `7218c34`** (sha256 de `scripts/scores/teste_data_quality_aceite.sh` =
  `ccd161ad713f39664a8f87630f9e09d7088e1154a111b3ab622715ef1839e774`, conferido na VPS antes do run);
  **código sob teste** (o motor) = `bafe476c7380f21dbc7b4b9b4f0ad5f8c84dc06e66d4df100147cb3fd63fe1f9`,
  contrato do score = `b88b09f963bc866d8e2d33e3ac892a3a08ebdeac74315e3488621f16ed86954c`, suíte =
  `cd6531322605ffe3b2550fbb1dca8c8e85899ba4d85f88a36b1f3c0cbca7fa64`. O motor **não** mudou entre
  `4caf3c6` e `7218c34` (só o script de aceite mudou).
- **Bancada (offline, sem banco e sem rede):** `python3 scripts/scores/verificar_score_data_quality.py
  --autoteste` → **`DQ_SUITE_OK (25 itens, 0 falhas)`** e **15/15 dentes** reprovando o item esperado
  (cada mutação aplicada a uma cópia do código; mutação que não se aplica, que não declara item ou que
  declara item inexistente **reprova**).
- **Aceite E2E:** `scripts/scores/teste_data_quality_aceite.sh --prova-de-dente` no clone
  `/opt/tre/w5e04t01-si-r4`, container descartável `pg-dq-acc` (postgres:16), migration `0001` aplicada
  do zero → **`ACEITE_DATA_QUALITY_001_OK (35 itens, 0 falhas, 0 dentes reprovados)`**, exit 0.
  **Evidência:** `/opt/tre/evid-w5e04t01-dq.out` (53 linhas, sha256
  `aa7f2c0c829bd8905d6ca8d0f3cd9cd58d1eb9ce8cd04ceea2eebf5dd41d8866`, cópia idêntica no anexo do card).
- **Valores conferidos na mão e medidos NO BANCO:** empresa completa (tudo preenchido, válida, pesquisa
  `COMPLETED` com 3 fontes, dado fresco) = **100.00**; parcial (domínio + site + cidade + `unit_count=0`,
  1 fonte) = **65.71**; só a razão social, fonte fora do vocabulário e dado velho = **0.00**; com defeito
  de dado (CNPJ inválido + DV, faixa de porte incoerente, site de outro domínio, pesquisa sem fonte) =
  **77.00**; dado novo (indústria entra na conta) sobe a parcial para **70.21**. Números da bancada
  conferidos contra a fixture **antes** de virarem expectativa do aceite.
- **Medido no banco, não na narrativa:** replay com o banco igual → 4 `JA_EXISTE`, **0 escrita** e
  `scores` seguindo com 4 linhas (zero duplicata); identidade forte por **CNPJ gravado com pontuação** e
  por **domínio em forma de URL** resolvem a MESMA empresa (o produtor grava normalizado, mas a coluna é
  `VARCHAR` livre); CNPJ com DV inválido e organização inexistente viram `RECUSADA` **sem escrever**;
  `--planejar` mede e não escreve; `--ambiente prod` recusado com **exit 4** e sem escrita; espelho
  `organizations.data_quality_score` bate com o último score e a **foto das colunas de negócio**
  (inclusive `updated_at`) não muda em nenhuma rodada; auditoria: **1 linha por empresa processada**
  (16 no total) e 5 delas com `score_id`; desfazer **frio** não apaga nada (`dry_run: true`) e o
  `--confirmo` apaga **só** a rodada E, restaura o espelho anterior (65.71) e **preserva a auditoria**.
- **Defeitos encontrados e consertados nesta execução (3, todos achados executando e remedidos):**
  1. **Casamento de identidade** comparava a forma **BRUTA** da coluna com o valor **normalizado**: CNPJ
     gravado com pontuação nunca casava (medido: a rodada por CNPJ gravou **0** e a auditoria daquela
     empresa ficou `RECUSADA`). Conserto **na raiz**, em duas camadas — o SQL **pré-filtra** (superset:
     `regexp_replace` no CNPJ, `LIKE` de ida e volta no domínio, `LIKE` do slug no LinkedIn) e o **módulo
     de identidade decide** normalizando a forma guardada (mesma regra do Scout, para score e produtor
     não discordarem). Item novo na suíte mede as duas camadas, inclusive o host **maior** que o `LIKE`
     deixa passar e o decider rejeita.
  2. **O próprio aceite era um falso verde:** `ciclo` roda dentro de substituição de comando e os
     contadores de shell morriam no subshell — com itens reprovados o veredito imprimia
     `(0 itens, 0 falhas)` e saía com **exit 0**. A contagem passou a ser **por arquivo**, veredito vazio
     (0 itens) **reprova**, e os itens reprovados são **nomeados** no log. Na terceira rodada o caminho do
     arquivo de itens ainda voltava vazio para o veredito (atribuição feita dentro do subshell) — caminho
     fixado no **escopo global** e o ciclo apenas trunca o arquivo.
  3. **Dente que não se aplica reprova** (é buraco, não alívio): a âncora de `confiabilidade-sem-pesquisa`
     saiu de sincronia com o código (`Decimal(0)`, não `Decimal("0")`) e o aceite devolveu
     `ACEITE_DATA_QUALITY_001_FALHOU (35 itens, 0 falhas, 1 dentes reprovados)`, exit 1 — âncora corrigida
     e a bateria inteira reexecutada.
- **Dentes inertes (medido, não suposto):** mutar **uma** das duas camadas da recusa de `prod` não muda
  nada (a outra camada segura) — o dente do aceite passou a mirar a função inteira devolvendo o ambiente
  sem conferir; tirar a referência do JSON canônico não muda o hash porque a referência **também** entra
  em `inputs` (defesa em profundidade) — o dente passou a fixar o canônico. Na suíte, as mutações que
  quebram contrato de pesos/colunas **nem carregam** (o `__init__` recusa antes de medir) e viraram
  **dente de carga** (recusar é o comportamento esperado).
- **Guardas de ambiente:** o aceite usa container descartável próprio (`pg-dq-acc`) e o remove no fim
  (0 falhas); os containers do TRE (`proxy-dev`, `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`, `pg-icp-acc`)
  ficaram **intactos** e o diretório de trabalho `/tmp/dq-aceite-trabalho` foi removido. Nenhuma
  credencial, nenhuma escrita fora do banco descartável, nada tocado em produção.

## 2026-10-02 — repositório TRE (worktree `t_8ab79fb9`, branch `feature/TRE-W5-E05-T01`) + VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E05-T01 (card `t_8ab79fb9`): Priority Score v1 (agregador da W5)

- **O que este card é:** o **agregador** da onda W5 — lê o ULTIMO score de cada componente já gravado
  em `sales_intelligence.scores` (ICP, AUTOMATION_FIT, BUYING_SIGNAL, DATA_QUALITY) e grava a linha
  `score_type='PRIORITY'`, `score_version='priority-v1'` pela fórmula do **Data Contract §8**
  (`0,35*ICP + 0,30*AUTOMATION_FIT + 0,25*BUYING_SIGNAL + 0,10*DATA_QUALITY`). Os pesos são **lidos do
  contrato** (`scores.priority_weights`): a suíte reprova peso em forma executável no código.
- **Base da árvore:** `63d711c` + merge das quatro branches dos pais (`feature/TRE-W5-E01-T01`,
  `-E02-`, `-E03-`, `-E04-T01`). Conflitos só nos três hotspots conhecidos (`CHANGELOG.md`,
  `docs/operations/registro-de-execucoes.md` e `scripts/verificar_estrutura.sh`), resolvidos por
  **união**; o bloco do portão de estrutura foi reescrito para cobrir os cinco cards da W5 (antes
  eram cinco loops concorrentes — o portão reprovava quem chegasse depois).
- **Decisões de projeto tomadas neste card (declaradas, não escondidas):** (1) **ausência de componente
  RECUSA** — cobertura mínima 1,00, sem renormalização (renormalizar criaria um número que não é a
  fórmula do contrato; fica como `priority-v2`, decisão do dono); (2) **política de validade do score
  nasce aqui** (lacuna declarada nos cards irmãos): componente com `valid_until` vencido conta como
  ausente (`COMPONENTE_VENCIDO`) e o PRIORITY vence em **30 dias**; (3) leitura do componente é sempre
  o **último** por `score_type` (`DISTINCT ON ... ORDER BY score_type, calculated_at DESC, id DESC`).
- **Suíte offline (container do Hermes, sem banco):** `python3 scripts/scores/verificar_score_priority.py`
  → `RESULTADO: VERIFICACAO_PRIORITY_SCORE_OK (35 itens, 0 falhas)`, **exit 0**; `--autoteste` →
  `RESULTADO: AUTOTESTE OK (12/12 mutacoes detectadas, cada uma pelo item esperado)`, **exit 0**
  (`sha256` do código sob teste conferido antes de cada rodada: `b3f8103a1404929f85e567a63d027cda65da1a0ac259f44abedf3965ca82fe8d`).
- **Aceite E2E (VPS, container descartável `pg-priority-acc`, imagem `postgres:16`, migration 0001
  aplicada do zero; 3 empresas + 11 scores de componente):**
  `bash scripts/scores/teste_priority_aceite.sh --raiz /opt/tre/priority-e05t01 --prova-de-dente`
  → **`ACEITE_PRIORITY_001_OK` (51 itens, 0 falhas)**, **exit 0**, com **dente 5/5**
  (`cobertura-afrouxada` → `A5 lastro incompleto RECUSADA`; `sem-checagem-de-vencido` → `A6 vencido
  RECUSADA`; `sem-idempotencia` → `A4 replay: continua 1 score PRIORITY`; `sem-validade` →
  `A9 valid_until = calculated_at + 30 dias`; `sem-soma-de-um-componente` → `A1 valor do contrato`).
  Console bruto: `/tmp/priority-aceite-r3.console` (sha256
  `7a3e37e1f9b5247ea65a7290d0278eeff02bead86a4ac2de12107f1936a63199`), copiado para o card.
- **Valores conferidos NO BANCO (não narrados):** rodada 1 → `score_value = 86.45`
  (`0,35*94 + 0,30*76 + 0,25*83 + 0,10*100`), `score_type=PRIORITY`, `score_version=priority-v1`,
  `valid_until = calculated_at + 30 dias` = 1, `inputs.cobertura = 1.00`, `inputs.componentes` com os
  quatro (identidade + versão), `explanation.soma_das_parcelas = 86.45`, `llm.executado = false`,
  tokens/modelo/custo NULL em `agent_runs`; replay da MESMA entrada → `gravados=0` e continua **1**
  score; componente novo (ICP 100) → **88.55** em linha NOVA com o 86.45 preservado;
  falta `DATA_QUALITY` → **RECUSADA** (`SEM_LASTRO_COMPLETO` + `COMPONENTE_AUSENTE:DATA_QUALITY`),
  **0** escrita e auditoria `REJECTED`; ao entrar o componente que faltava → **86.45**; DATA_QUALITY
  **vencido** → RECUSADA com `COMPONENTE_VENCIDO:DATA_QUALITY` e, com a validade estendida no banco,
  volta a **86.45**; empresa fantasma → RECUSADA sem escrita; `prod` → **exit 4** com **0** score novo;
  `--planejar` → **exit 0** sem abrir conexão (prefixo de container inexistente); desfazer dry-run
  **não apagou** (17 → 17) e `--confirmo` apagou **só a rodada** (17 → 16) preservando os **13** scores
  de componente e a auditoria; rodada inexistente não apaga nada.
- **Impressão digital dos componentes (prova de que a rodada não toca a entrada):** md5 de
  `tipo:valor:versão` dos componentes medido ANTES e DEPOIS da rodada — igual. (O item antigo media
  `score_value = ROUND(score_value,2)`, que é sempre verdadeiro em `NUMERIC(5,2)`: defeito do próprio
  aceite, corrigido.)
- **Defeitos corrigidos NESTA rodada (todos do próprio instrumento, encontrados por medição):**
  1. o dente `sem-soma-de-um-componente` dizia "não reprovou" com o item reprovando: `grep` sem `-F`
     interpretava `0,35*94` como expressão regular (`5*` = zero ou mais `5`) e a linha nunca casava —
     passou a `grep -qF`;
  2. o item `A2` media a coisa errada (acima) — virou impressão digital md5;
  3. a mutação revelou um defeito REAL do componente: a linha de resumo assumia os quatro componentes
     presentes e estourava `KeyError` em qualquer contrato sem cobertura total — passou a tratar
     ausente (`detalhe[t].get("score_value", "-")`).
- **Também mudou por medição:** `RECUSADA` passou a ser **auditada** em `agent_runs` (status
  `REJECTED`) e o CLI passou a imprimir o **motivo** (`SEM_LASTRO_COMPLETO` / `COMPONENTE_VENCIDO:…`)
  na linha do veredito — antes o operador só via os motivos nominais.
- **Portão de estrutura:** `bash scripts/verificar_estrutura.sh` → **`RESULTADO: PASS (0 falhas)`**,
  **exit 0**, com os 31 artefatos da W5 versionados (portão reescrito como **um** bloco da onda).
- **Ambiente:** apenas o container descartável `pg-priority-acc` (e o `pg-priority-dente` das
  mutações), **removidos pelo próprio aceite** (`docker ps -a | grep priority` = 0 ao fim);
  `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` **intactos**; árvore de trabalho na VPS em
  `/opt/tre/priority-e05t01`; **nada em produção** (`prod` recusado com exit 4 e 0 escrita).
- **Código sob teste na VPS = o versionado:** `sha256` conferido dos dois lados —
  `priority_score.py b3f8103a…` e `teste_priority_aceite.sh 45b6c565…`; suíte `6454bd4c…`.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. As políticas de
  cobertura (1,00, sem renormalização) e de validade (30 dias) são **propostas** deste card; tiering
  (`A+/A/B/C/Nurture`) é o card W5-E06-T01 e não foi tocado aqui.
- Segredos: nenhum valor nesta entrada; a conexão do aceite é pelo container descartável, sem senha em
  argumento de linha de comando, arquivo de log ou repositório.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E06-T01: Tiering v1 (aceite E2E em PostgreSQL descartável)

- **Objeto:** o tiering (`hermes/scores/tiering/tiering.py`) lê o **último score `PRIORITY`** de
  `sales_intelligence.scores`, aplica as **faixas do Data Contract** (`scores.tiers`) e grava a
  classificação em `sync_events` (`operation='TIER'`, `source_version='tiering-v1'`) + `agent_runs`.
  **Não escreve em `scores`** (tier não é score) e não cria coluna nenhuma.
- **Comandos reais (na VPS, árvore em `/opt/tre/tiering-e06t01-r1/repo`):**
  `python3 scripts/scores/verificar_score_tiering.py --autoteste` →
  `RESULTADO: VERIFICACAO_TIERING_OK (26 itens, 0 falhas)` e
  `RESULTADO: AUTOTESTE OK (10/10 mutações detectadas, cada uma pelo item esperado)`, exit 0;
  `bash scripts/scores/teste_tiering_aceite.sh --raiz "$PWD" --prova-de-dente` →
  **`== ACEITE 53 OK / 0 FALHOU`** + `ACEITE_TIERING_001_OK`, **EXIT=0**, com os **6 dentes**
  reprovando **o item esperado**: `ausencia-vira-registro` (A5 nenhum registro para a empresa),
  `sem-checagem-de-vencido` (A6 vencido RECUSADA), `fronteira-aberta` (A7 fronteira 65,00 -> B),
  `sem-idempotencia` (A4 replay: continua 1 registro TIER), `ultimo-vira-primeiro` (A7 o PRIORITY
  lido é o mais recente), `operation-trocada` (A1 um registro TIER para a empresa).
- **Valores conferidos no banco (container descartável `pg-tier-acc`, `postgres:16`, migration 0001
  do zero):** 86,45 → **A** (faixa 80,0–89,99 gravada no `request_payload`, 5 faixas vigentes, escala
  0–100, `fonte_das_faixas` apontando `scores.tiers`); PRIORITY novo 95,00 → **A+** em **registro
  novo** com o anterior (A) preservado; replay → `gravados=0`, continua **1** registro;
  sem PRIORITY → `RECUSADA SEM_PRIORITY` com **0** linha em `sync_events` e `agent_runs` `REJECTED`;
  PRIORITY vencido → `RECUSADA PRIORITY_VENCIDO` e, com `valid_until` em +10 dias, 55,00 → **C**;
  ÚLTIMO PRIORITY (92,00 → A+), não a média (40,00 antigo); 65,00 → **B** (fronteira); fantasma →
  `ORGANIZACAO_NAO_ENCONTRADA` sem registro; desfazer dry-run **6→6** e `--confirmo` **6→5**, com a
  auditoria preservada e os `scores` intactos (**8** antes e depois).
- **Nada tocado em `scores`:** contagem e impressão digital md5 (`score_type:score_value:score_version`)
  idênticas antes/depois da rodada; **0** linhas com `score_type='TIER'`; `organizations` intactas.
- **Ambiente:** apenas o container descartável `pg-tier-acc` e o `pg-tier-dente` das mutações,
  **removidos pelo próprio aceite** (`docker ps -a | grep tier` = **0** ao fim); `pg-sales-dev`,
  `pg-odoo-dev`, `odoo-dev` e `proxy-dev` **intactos**; **nada em produção** (`prod` recusado, exit 4,
  sem escrita).
- **Código sob teste na VPS = o versionado:** `sha256` conferido dos dois lados —
  `tiering.py 380f79272858c153866aa98c78845bda5a8a684fe013c1ef644ded7936d602ae`,
  `verificar_score_tiering.py d619340cce7cfff9b44f334c9f903247d33137c1a91f8547e418384510b97e05`,
  `teste_tiering_aceite.sh 153050689189d18882cf5e71f8ec342ddf018cfc988e9026f56c4e7bb1082e62`.
- **Defeitos do próprio instrumento, encontrados por medição e corrigidos nesta rodada:**
  1. a rodada 1 do aceite reprovou um item por **formato**, não por regra: o limite da faixa era
     comparado como `80|89.99` e o contrato traz `80.0` — o item passou a esperar o literal do
     contrato (a rodada 1 fechou 46 OK / 1 FALHOU por isso; a rodada 2, 53/0 com os dentes);
  2. o item `C2` da suíte comparava o limite com `Decimal.normalize()` e acusava `9E+1`/`1E+2` —
     passou a comparar **Decimal** com Decimal;
  3. os itens `C3`/`C4` liam o arquivo **original** em vez do módulo sob teste, então a mutação
     `faixa-hardcoded-no-codigo` aparecia como **inerte** — a leitura passou a ser de `M.__file__` e
     a mutação passou a reprovar o item esperado (dente honesto);
  4. o item `S3` exigia a prova da gravação **depois do `COMMIT`**, quando ela é contada depois do
     fechamento do claim dentro da transação — o item passou a medir a ordem real
     (`UPDATE …` → `SELECT COUNT(*) … PROCESSED`);
  5. dois dentes do aceite foram trocados **antes** da rodada por ancoragem frágil (uma mutação com
     `\n` dentro de heredoc e uma âncora textual que não existe no código — `"entity_type":
     "organization"` é gerado por `lit(ENTITY_TYPE)`) — viraram mutações de uma linha com âncora real.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. Ficam **propostos**:
  a casa física do tier (contrato 1.1: `organizations.tier` ou `score_type='TIER'`) e a política de
  ausência (sem PRIORITY ⇒ sem tier).
- Segredos: nenhum valor nesta entrada; a conexão do aceite é pelo container descartável.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E07-T01: Next Best Action v1 (aceite E2E em PostgreSQL descartável)

- **Objeto:** o NBA (`hermes/scores/nba/nba.py`) lê a evidência do banco (o registro **`TIER`** do card
  irmão em `sync_events`, mais `research_runs`, `pain_hypotheses`, `signals`, `contacts`,
  `interactions`) e decide o próximo passo pela **tabela de decisão declarada**
  (`hermes/scores/nba/politica-nba-v1.json`, 12 regras — o **vocabulário das ações** é lido do Data
  Contract), gravando em `sales_intelligence.recommendations`
  (`recommendation_type='NEXT_BEST_ACTION'`, `status='OPEN'`) + `agent_runs`. **Não escreve em score,
  tier, contato, interação nem `sync_events`**, não cria coluna, não emite evento de outbox e não
  executa a ação.
- **Comandos reais (na VPS, árvore em `/opt/tre/nba-e07t01-r1/repo`):**
  `python3 scripts/scores/verificar_nba.py --raiz "$PWD"` →
  `RESULTADO: VERIFICACAO_NBA_OK (69 itens, 0 falhas)`; `--autoteste` →
  `RESULTADO: AUTOTESTE OK (7/7 mutações detectadas, cada uma pelo item esperado)`;
  `bash scripts/scores/teste_nba_aceite.sh --raiz "$PWD" --prova-de-dente` →
  **`== ACEITE 79 OK / 0 FALHOU`** + `ACEITE_NBA_001_OK`, **EXIT=0**, com os **6 dentes** reprovando
  **o item esperado**: `sem-supersessao` (A10 anterior SUPERSEDED), `sem-idempotencia` (A2 replay:
  continua 1 recomendação), `sem-checagem-de-tier` (A6 sem TIER RECUSADA), `primeira-regra-sempre`
  (A1 ação SEND_EMAIL na saída), `ordem-invertida` (A3 B → WAIT), `contato-bloqueado-ignorado`
  (A3 F → NURTURE por compliance).
- **Valores conferidos no banco (container descartável `pg-nba-acc`, `postgres:16`, migration 0001 do
  zero):** empresa com tier A+ e decisor com e-mail → **SEND_EMAIL** (`R12`), `OPEN`, prioridade 2,
  `due_at`/`expires_at` gravados, `contact_id` do decisor, `confidence` **NULL** e auditoria
  `COMPLETED` sem LLM (modelo/tokens/custo nulos); replay → `JA_RECOMENDADA` com **1** recomendação
  (nada duplicado); cada estado de evidência produziu a ação esperada — **WAIT** (abordada ontem),
  **FOLLOW_UP** (5 dias sem resposta), **CREATE_MEETING** (resposta positiva), **NURTURE** por
  `TIER_NURTURE` e por `COMPLIANCE_SEM_CANAL`, **DISQUALIFY** (dor rejeitada sem sinal),
  **RESEARCH_MORE** (sem pesquisa), **FIND_DECISION_MAKER** (contato que não é decisor, `contact_id`
  nulo), **PREPARE_LINKEDIN** (decisor sem e-mail); evidência nova → recomendação **nova** com a
  anterior **SUPERSEDED** (2 no histórico, 1 aberta, SEND_EMAIL preservada); empresa sem registro TIER
  → `RECUSADA SEM_TIER` com **0** recomendação e auditoria `REJECTED`; fantasma → recusada sem gravar;
  desfazer dry-run **11→11** e `--confirmo` **11→10**, com a auditoria preservada.
- **Nada tocado fora de `recommendations`/`agent_runs`:** contagens e impressão digital do registro
  TIER idênticas antes/depois; **0** linha em `scores`, **0** `sync_events` novo, **0** `outbox_events`;
  `contacts`, `interactions` e `organizations` intactos.
- **Ambiente:** apenas os containers descartáveis `pg-nba-acc` e `pg-nba-dente` (mutações), **removidos
  pelo próprio aceite** (`docker ps -a | grep nba` = **0** ao fim); `pg-sales-dev`, `pg-odoo-dev`,
  `odoo-dev` e `proxy-dev` **intactos**; **nada em produção** (`prod` recusado, exit 4, sem escrita).
- **Código sob teste na VPS = o versionado:** `sha256` conferido dos dois lados — `nba.py
  f06ecb5bb1814906f22581be7a6894b44373772f513229e1ec61003e745ded03`, `verificar_nba.py
  67d5856568d5b826a062e9ff53cbfb202387519b7073ff7a3bd3d17f8d7d1a72`, `teste_nba_aceite.sh
  bb9479cf78e346c3ab3f9fe677e0082a1c11f4c0515ba0f01592a9f8e93fc93c`.
- **Defeitos do próprio instrumento, encontrados por medição e corrigidos nesta rodada:**
  1. a rodada 1 do aceite fechou **73 OK / 5 FALHOU** — as cinco eram do instrumento, não do código:
     o item do replay conferia `gravados=0` quando o campo correto é `ja_existia=1` (a contagem da linha
     do próprio id é 1 no replay); dois itens contavam a string `SEM_TIER`/`ORGANIZACAO_NAO_ENCONTRADA`
     **duas vezes** (linha de saída + relatório JSON) e passaram a mirar a linha de veredito; o item de
     interações comparava com a contagem tomada **antes** da interação que a própria seção A10 cria; e
     faltava **auditar a recusa** — a recusa não gravava `agent_runs`, então o componente passou a
     auditar `RECUSADA`/`ABSTEVE` (`REJECTED`), como já fazia o tiering;
  2. o dente `sem-checagem-de-tier` reprovou **nada** na rodada 2: a âncora `if not fatos.get("tier"):`
     aparecia **duas vezes** no módulo e a mutação pegava a ocorrência de `decidir` (a guarda de
     `rodar_organizacao` seguia de pé) — o código passou a escrever as duas guardas em formas distintas
     (`fatos.get("tier") in (None, "")` na função pura) e a mutação voltou a morder o item esperado;
  3. o dente `contato-bloqueado-ignorado` falhou por **âncora ausente** (a mutação citava duas linhas
     que o SQL real tem quebradas) — a âncora passou a ser a linha única do `WHERE` e o dente mordeu.
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. Ficam **propostos**: a
  tabela de decisão (regras, ordem e prazos) e o `confidence` NULL (calibração é do W6+).
- Segredos: nenhum valor nesta entrada; a conexão do aceite é pelo container descartável.

## 2026-10-02 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W5-E08-T01: aceite E2E da cadeia W5 (Score → Tier → NBA) medido por execução real

- **O que o card é:** o "test scoring/NBA" do plano (doc 11, W5-E08-T01) — o **aceite E2E da cadeia**
  que os cards E05/E06/E07 declararam "fora do card" (`docs/architecture/score-priority-v1.md` §12,
  `score-tiering-v1.md` §13, `next-best-action-v1.md` §8). Instrumento novo:
  `scripts/e2e/verificar-e2e-scoring-nba.sh` (`76b8997006dc358c6a0eedf3f9cd1268aef8c5bc3370da4129605a13cf5858d1`,
  `100755`), com runbook `docs/runbooks/e2e-scoring-nba.md` e o contrato do card (ACCEPTANCE/TEST/
  ROLLBACK/RISK) em `docs/kanban/criterios-de-aceitacao.md`.
- **Onde rodou:** na VPS do ambiente (ADR-0008 — o container do Hermes não tem daemon Docker nem rota
  até o PostgreSQL), árvore sob teste `/opt/tre/w5e08t01-r1/repo`. O aceite sobe **um** container
  descartável próprio (`pg-w5-acc`, `postgres:16`, sem porta publicada), aplica a migration 0001 e
  roda os **sete** componentes no mesmo banco. `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev`
  não foram tocados; ao fim, `docker ps -a | grep w5` = **0** container.
- **Evidência de aceite (baseline):** `bash scripts/e2e/verificar-e2e-scoring-nba.sh` →
  `RESULTADO: ACEITE E2E SCORING/NBA 72 OK / 0 FALHOU` / `ACEITE_E2E_SCORING_NBA_001_OK`, **exit 0**.
  O que a execução mediu, item a item: as **sete suítes offline** no mesmo commit (ICP, AUTOMATION_FIT,
  BUYING_SIGNAL, DATA_QUALITY, PRIORITY, TIER, NBA — exit 0 em todas); as três empresas sintéticas
  (A: sinal de tecnologia + programa de eficiência + dor validada 85 + decisor com e-mail; B: dor 60 +
  sinal `HIRING` + abordagem de 5 dias atrás sem resposta; C: sem sinal e sem hipótese); a cadeia em
  sequência; a **composição** (PRIORITY = `0,35·ICP + 0,30·AF + 0,25·BS + 0,10·DQ` conferido em SQL
  contra os quatro scores gravados; registro `TIER` citando o `score_id` do PRIORITY lido; a
  recomendação citando o `tier` gravado pelo tiering); o **fail-closed** da empresa sem lastro
  (`SEM_LASTRO` no AUTOMATION_FIT, `SEM_LASTRO_COMPLETO` no PRIORITY, `SEM_PRIORITY` no TIER,
  `SEM_TIER` no NBA, sem gravar nada); o **escopo** (foto md5 das tabelas de negócio idêntica antes e
  depois `4e397d71eb2b31e7c961a4a0866c84f6`, `outbox` = 0, `agent_runs` com `model`/tokens/custo
  NULL); o **replay** da cadeia inteira sem duplicar (`scores` 13, `TIER` 2, `recommendations` 2, após
  o replay idênticos); as **guardas** (`prod` exit 4 nos sete componentes com a foto de contagens
  antes/depois idêntica; `--planejar`/`--regras` exit 0 com porta de banco inexistente) e o
  **desfazer** do NBA (dry-run não apaga; `--confirmo` apaga as recomendações da correlação e preserva
  a auditoria).
- **Prova de dente (4 mutações em cópia, cada uma pelo item esperado):**
  `bash scripts/e2e/verificar-e2e-scoring-nba.sh --prova-de-dente` →
  `ACEITE E2E SCORING/NBA 76 OK / 0 FALHOU` / `ACEITE_E2E_SCORING_NBA_001_OK`, **exit 0**:
  motivo do TIER `SEM_PRIORITY` → item `3.2 TIER RECUSOU a empresa SEM PRIORITY (SEM_PRIORITY)`;
  motivo do PRIORITY `SEM_LASTRO_COMPLETO` → item `3.1 PRIORITY RECUSOU a empresa sem os quatro`;
  `if not fatos.get("tier"):` → `if False:` (NBA) → item `3.3 NBA RECUSOU a empresa SEM tier
  (SEM_TIER)`; id determinístico `uuid5` → `uuid.uuid4()` (NBA) → item `5.1 replay nao duplica
  recomendacao`. Cada mutação reprovou **o item esperado** (não "o aceite falhou").
- **Defeito do INSTRUMENTO corrigido durante a rodada (rodada 1: 52 OK / 20 FALHOU):** (i) a recursão
  dos dentes passava `--raiz` e o tratamento de `--raiz` **sobrescrevia** as variáveis de caminho de
  código — a cópia mutada nunca era usada e os 4 dentes "não reprovavam" (corrigido com
  `definir_modulos()`, que respeita o override por ambiente); (ii) os itens contavam **quantas vezes**
  o motivo aparecia (o módulo imprime o motivo na linha e no relatório: `4` em vez de `1`) —
  trocado por `tem()` (aparece / não aparece), a mesma lição do E07; (iii) o NBA usa `--jsonl`, não
  `--fonte`; (iv) o CNPJ da fonte pontuada **não** casa a coluna pontuada (medido em probe dedicado:
  a resolução normaliza a fonte para dígitos e compara com a coluna — a massa passou a gravar CNPJ em
  dígitos, limite declarado no runbook §5); (v) `DATA_QUALITY --planejar` **exige** a porta do banco
  (recusa explícita, exit 1) — o item passou a medir a recusa e a ausência de escrita em vez de um
  exit 0 que o componente não dá.
- **Verificadores do repo depois da mudança:** `bash scripts/verificar_estrutura.sh` → `PASS (0
  falhas)` (o portão passou a exigir o aceite e o runbook versionados e executáveis);
  `bash scripts/secret_scan.sh` → `PASS`; `bash -n` no aceite → OK.
- **Estado do ambiente:** nenhum container do card sobrou (`pg-w5-acc` e os `pg-w5-dente-*` removidos
  pelos próprios testes); `/opt/tre/prod` e `/opt/tre/homolog` intocados; nenhuma DDL fora do
  container descartável; nada em produção (ADR-005).
- **Logs brutos** (anexos do card): `aceite-e2e-scoring-nba-72ok.out`,
  `aceite-e2e-scoring-nba-dente-76ok.out`, `sha256-artefatos.out` (hash dos sete módulos, das sete
  suítes e da migration 0001 no commit medido).
- **Não é homologação:** quem entrega não homologa — o veredito deste card é do **estágio 6** (revisão
  independente, perfil `tester`) e a homologação (**estágio 7**) é do Anderson. Ficam **propostos** os
  limites declarados no runbook §5 e a massa sintética (o aceite mede o encadeamento e o fail-closed,
  não a acurácia dos scores — isso é W8).
- Segredos: nenhum valor nesta entrada; a conexão do aceite é pelo container descartável.

---

## TRE-W6-E02-T01 — Criar GPT outreach generator (`gerador-abordagem-v1`)

**Onde foi medido:** VPS do ambiente (Contabo), árvore sob teste em `/opt/tre/outreach-e02t01-r1/repo`,
commit `ec6a427` (branch `feature/TRE-W6-E02-T01`, base `feature/TRE-W5-E08-T01`). Container
descartável `pg-outreach-acc` (imagem `postgres:16`, migration 0001 aplicada por ele mesmo), **removido
pelo próprio aceite**; `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` não foram tocados. Nada
em produção (ADR-005): `--ambiente prod` é recusado com exit 4.

**O que foi medido (execução real, não leitura):**

| Medida | Comando | Resultado |
|---|---|---|
| Suíte offline (sem banco, sem rede externa) | `python3 scripts/agentes/verificar_gerador_abordagem.py` | **PASS (100 OK / 0 falhas)**, exit 0 |
| Autoteste por mutação | `... --autoteste` | **PASS (12/12 mutações detectadas)** — cada mutação reprovou o item esperado |
| Aceite E2E em PostgreSQL descartável | `bash scripts/agentes/teste_gerador_abordagem_aceite.sh` | **ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)** |
| Prova de dente do aceite | `... --prova-de-dente` | **4/4 dentes**: guarda de contato → `A6 contato com opt_out recusa`; validação de fato → `A8 alucinação RECUSA a rodada`; id aleatório → `A4 replay não duplica pedido`; supersessão desligada → `A5 pedido antigo preservado e EXPIRED` |

**O que o aceite mostra, item a item (amostra dos números medidos):** pedido `PENDING` em `human_approvals`
com `action_type` da ação recomendada, `entity_type=CONTACT`, canal/tipo/assunto/corpo/cta/citações/hash em
`proposed_action` e `decided_by` vazio; auditoria em `agent_runs` (`COMPLETED|outreach|1.0.0|outbound-abordagem`)
com provider/modelo e `prompt_version` **estruturados** em `input`; tokens `NULL` no modo offline (nenhuma
contagem fingida) e tokens do provedor (321/654) quando a resposta vem por HTTP; nada tocado fora das duas
tabelas em **nenhuma** rodada (organizations/contacts/recommendations/scores/signals/pain_hypotheses/
research_runs/interactions/sync_events/outbox_events com as mesmas contagens, outbox em 0); replay com o
mesmo id; evidência citável nova → pedido novo + anterior `EXPIRED`; mexer só em campo **não** citável
(`relevance_score`) → replay, sem rascunho novo; contato com `opt_out_email` RECUSA e não grava; empresa sem
fato nenhum RECUSA (`SEM_EVIDENCIA`); ação `WAIT` ABSTEM; empresa inexistente RECUSA; canal `LINKEDIN`
respeitado para `PREPARE_LINKEDIN`; alucinação do provedor (número e promessa inventados) RECUSA a rodada e
**não** grava pedido; `--desfazer` dry-run não apaga e `--confirmo` apaga só o pedido da rodada, com a
auditoria preservada.

**Correções do instrumento durante a medição** (todas do aceite, não do componente): `gerar()` passava o
rótulo do caso como argumento do CLI (as rodadas saíam com `unrecognized arguments`); a subquery do contato
citava o alias `o` fora de escopo (a leitura devolvia `missing FROM-clause entry for table "o"` — corrigido no
módulo); dois itens usavam `||` antes de `->>` sem parênteses (erro de tipo no psql); itens de booleano
esperavam `t/f` em vez de `true/false`; a foto de contagens final comparava com a de antes das inserções de
evidência (falso positivo); e a mutação de evidência do caso A5 alterava só `relevance_score`, campo que
**não** entra no texto citável — o caso foi reescrito para inserir um sinal novo, e a fronteira virou item
medido (`A5(a)`).

**Não é homologação:** o veredito técnico deste registro é de quem entregou; a revisão independente é do
perfil `tester` e a homologação (estágio 7) é do Anderson. Segredos: nenhum valor aqui; a rodada de provedor
usou chave fictícia contra um stub HTTP local (`127.0.0.1`), e o modo padrão é o renderizador offline.

## 2026-10-02 — TRE-W6-E03-T01 (workflow de aprovacao humana do outbound v1)

**O que foi executado, com numero medido:** suite offline `scripts/agentes/verificar_fluxo_aprovacao.py`
-> `RESULTADO: PASS (79 OK / 0 falhas)` e `--autoteste` -> `PASS (20/20 mutacoes detectadas)`; aceite E2E
`scripts/agentes/teste_fluxo_aprovacao_aceite.sh` na VPS do ambiente em `/opt/tre/aprovacao-e03t01-r3/repo`
com PostgreSQL descartavel `pg-aprovacao-acc` + `pg-aprovacao-dente1..4` (todos removidos pelo proprio aceite;
`pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` intactos) -> `RESULTADO: ACEITE_APROVACAO_001_OK
(104 OK / 0 FALHOU)` e `--prova-de-dente` -> 4/4 (`guarda` -> A6, `validacao` -> A8, `transicao` -> A11,
`replay` -> A3). Nada em producao: `--ambiente prod` sai com exit 4 antes de falar com o banco (ADR-005).

**A cadeia do aceite e a real, nao uma simulacao:** o gerador do card irmao (`outreach_generator.py`,
W6-E02) cria os pedidos `PENDING` em `human_approvals` e este componente os notifica, decide, expira,
reverte e libera pelo portao. A massa e sintetica (6 empresas/contatos) e a rodada e descartavel.

**Defeito real encontrado pelo aceite (e corrigido):** o mapa de idempotencia da notificacao ficava vazio
porque `json_agg(output->'notificados')` produz uma **lista de listas** (uma por rodada) e o leitor so
aceitava dicts — a segunda `--fila` renotificava os 5 pedidos (`NOTIFICADO`, 5 novos) em vez de
`JA_NOTIFICADO`. Correcao: `jsonb_array_elements(...)` no `FROM`, `_achatar()` defensivo e **fail-closed**
(se a leitura falhar, a rodada RECUSA: renotificar em loop e pior do que nao notificar). Ganhou item C6 na
suite offline e a mutacao D21 no autoteste.

**Correcoes do instrumento (do aceite, nao do componente):** itens que liam colunas com `tr -d ' '`
destruiam nomes/notas de mais de uma palavra (agora `psql_limpo`); itens de booleano comparavam `t` com
`true` (agora `CASE WHEN ... THEN 'sim'`); a contagem de ocorrencias de texto em arquivo usava `grep -c`
(linhas) onde o correto era presenca; a mutacao da validacao da edicao passava `\n` literal em vez de
quebra de linha (a ancora nao era encontrada e o dente nao media nada — agora `$'...'` e a mutacao aborta o
harness quando a ancora nao existe); a linha do conflito do A13 chamava o CLI sem `--decisao`/`--por`.

**Nao e homologacao:** o veredito tecnico deste registro e de quem entregou; a revisao independente e do
perfil `tester` e a homologacao (estagio 7) e do Anderson. Segredos: nenhum. A rodada nao envia nada —
`entrega_externa: false` e `envio.executado: false` na auditoria; nenhum evento de outbox e criado.
## 2026-10-02 — container do Hermes (workspace do board TRE, worktree `feature/TRE-W6-E01-T01`) — TRE-W6-E01-T01 (Titan SMTP): entrega medida, sem rede além do loopback

- **O que este card e:** primeiro card da onda W6 (canal outbound do TRE). Entrega a camada de
  **configuracao validada** do SMTP do Titan e o **primitivo de envio de uma mensagem**. Nao toca banco
  (`sales_intelligence` intocado — o card nao abre conexao de banco), nao cria migration e nao usa
  credencial Titan: o papel `dev-harness` **nao tem** `TRE_TITAN_*` (`hermes/policies/dev-harness.yaml`
  -> `credenciais_proibidas`) e nao envia e-mail em nome da Transformativa. Onde o card roda: worktree
  proprio no container do Hermes (nao ha docker daemon aqui e nao faz falta: a prova e de rede local).
- **Artefatos sob teste (sha256, medidos antes da bateria):** `hermes/integracoes/titan/smtp_titan.py
  993cf7c8b1853e5e881819e67ec90c6ac18208f60cb89ecbadae930b15fca1f0`;
  `hermes/integracoes/titan/titan-smtp-v1.json d2b56f75d7fb50ce13e8ed1905aa87a98f795d4b33fdeb520e90ebbd88f612d3`;
  `scripts/integracoes/sink-smtp-dev.py aee5c12ce8b79dc9a954b0f0c472336c1260acac749d4c01d2f9790cbe23f875`;
  `scripts/integracoes/verificar_smtp_titan.py a733de146dd65be00f6c4515cb9874054f2f87d79d2a10b7803b54b7b6794869`;
  `scripts/integracoes/teste_smtp_titan_aceite.sh 5a6a6a8c6ed0c012290512dc2b726cd18948ab492c65d376c98ff91cda81a104`;
  `scripts/integracoes/mutar_smtp_titan.py 9ad2983a04ea64357583be004f17ea0f57ab5b4e9a80d9dc833f6b75f430a7fc`.
- **Bateria final (3 comandos, 02/10/2026 23:1xZ; saida completa + exit code):**
  - `python3 scripts/integracoes/verificar_smtp_titan.py` -> `SMTP_TITAN_SUITE_OK (49 itens, 0 falhas)`,
    **exit 0** (nenhuma conexao: a suite so roda `--planejar`/`--conferir` e caminhos de recusa);
  - `TRE_W6_TRABALHO=... bash scripts/integracoes/teste_smtp_titan_aceite.sh` ->
    `ACEITE_SMTP_TITAN_001_OK (23 itens, 0 falhas)`, **exit 0**;
  - `TRE_W6_TRABALHO=... bash scripts/integracoes/teste_smtp_titan_aceite.sh --prova-de-dente` ->
    `ACEITE_SMTP_TITAN_001_OK (29 itens, 0 falhas)`, **exit 0**, com as **5 mutacoes** cada uma
    reprovando o item esperado (`sem-guarda-de-host` -> item 5, `sem-matriz-porta-tls` -> item 6,
    `ignora-confirmo` -> item 4, `senha-sem-mascara` -> item 7, `sem-idempotencia` -> item 9) e o
    **controle** (item 11.1): a suite roda verde no modulo INTACTO depois das mutacoes.
- **O que o aceite mediu de verdade (nao e leitura de codigo):** sink SMTP proprio em `127.0.0.1`
  (TLS implicito em 2465 e STARTTLS em 2587, certificado gerado na hora por `openssl`, SAN
  `IP:127.0.0.1`); `--provar` mediu `ehlo 250`, `versao_tls TLSv1.3`, `autenticacao 235 2.7.0
  Autenticacao bem-sucedida` nos dois modos; entrega capturada pelo sink com `mail_from
  no-reply@dev.local`, `rcpt_to [caixa@dev.local]`, `assunto prova-aceite`, corpo, `X-TRE-Card:
  TRE-W6-E01-T01` e `autenticado_como sink-dev`; `--enviar` **sem** `--confirmo` nao entregou (caixa
  0 -> 0) e **com** `--confirmo` entregou uma (0 -> 1); replay da mesma chave deu `JA_ENVIADO` com a
  caixa parada em 1; chave nova entregou (1 -> 2); as guardas recusaram com a caixa intacta
  (`HOST_NAO_E_DEV` 3, `PRODUCAO_NAO_E_DESTE_CARD` 4, `DESTINO_NAO_PERMITIDO` 3, `CONFIG_INCOERENTE`
  3, `PORTA_NAO_AUTORIZADA` 3); o item 3.3 provou que o TLS nao foi decorativo (contra o sink TLS, o
  caminho `nenhuma` FALHOU, exit 1).
- **Nada saiu do loopback:** item 9.2 conferiu `execucoes.txt` — todo caso com host nao-loopback
  (`192.0.2.1`, TEST-NET, nao roteavel) saiu em RECUSA medida (exit 3/4); se o modulo tivesse tentado
  conectar, o exit seria 1. **Nada em producao:** item 9.1 comparou `git status --porcelain` antes e
  depois (hashes identicos) e o aceite escreveu so no diretorio de trabalho proprio.
- **Segredo:** o valor da senha (`sentinela-dev-...`, valor de teste do proprio aceite, nunca
  versionado) nao apareceu em **nenhum** arquivo do trabalho nem na captura do sink (item 7.1 = 0
  ocorrencias); a captura guarda o USUARIO autenticado, nunca a senha (o sink descarta a senha na
  hora). O modulo ainda tem checagem fail-closed propria: senha no que seria gravado -> gravacao
  recusada, exit 5 `SENHA_VAZADA`.
- **Defeitos do INSTRUMENTO corrigidos durante o card (rodada 1 -> rodada 2), medidos:**
  1. rodada 1 do aceite: **19 OK / 4 FALHOU** (itens 3.3, 4.1, 4.2, 9.2).
  2. `contar()` imprimia `0\n0` (`grep -c .` ja imprime `0` e **sai 1**, e o `|| echo 0` somava um
     segundo zero) -> os itens 4.1/4.2 reprovavam com a medicao certa na mao (`[: 0\n0: integer
     expression expected`). Corrigido com captura de variavel.
  3. `printf '%s' "$SAIDA" | grep -q ...` sob `set -o pipefail`: o `grep -q` fecha o pipe, o `printf`
     morre por SIGPIPE e a pipeline devolve **141** — 6 dos 8 itens reprovados na rodada da suite
     eram isso. Trocado por `contem()` (case/glob, sem pipe).
  4. **Defeito REAL do modulo, achado pelo item 3.3:** em `TRE_TITAN_SMTP_SEGURANCA=nenhuma` o
     `medidas["versao_tls"] = sessao.sock.version()` estourava `AttributeError` (socket TCP puro nao
     tem `version()`) e a rodada morria **sem relatorio** (exit 1, sem `FALHOU`). Corrigido para
     `sock.version() if isinstance(sock, ssl.SSLSocket) else None`. A rodada 2 ficou **22 OK / 1
     FALHOU** (so o item 3.3) — o item 3.3 estava certo: ele expunha o defeito.
  5. rodada 1 do dente: **28 OK / 1 FALHOU** — o dente `sem-matriz-porta-tls` media com
     `--ambiente dev`, onde a guarda `HOST_NAO_E_DEV` mascarava a mutacao (o aceite continuava
     reprovando, mas pelo motivo ERRADO, e o dente exige o item esperado). Corrigido para medir em
     `homolog` com aprovacao, o mesmo ambiente do item 6 -> rodada 2: **29 OK / 0 FALHOU**, exit 0.
- **Limites declarados:** (a) a prova contra `smtp.titan.email` NAO e deste card — exige credencial do
  Sales AI + aprovacao registrada, e por isso e de **homolog**; (b) o aceite prova TLS contra um
  certificado proprio de dev, o que mede negociacao TLS e AUTH, nao a cadeia de confianca publica
  (que so o provedor real exercita); (c) a trilha de envio e arquivo JSONL fora do banco — quando o
  banco do TRE estiver de pe, W6-E04 decide se ela passa a viver em `sync_events`.
- **Logs brutos:** saidas completas em `/opt/data/cache/scratch/t6smtp.suite.out`,
  `t6smtp.aceite-base.out`, `t6smtp.aceite-dente.out` e `t6smtp.sha256.out` (anexados ao card).
- Segredos: nenhum valor nesta entrada; a senha usada na prova e um valor de teste local gerado pelo
  proprio aceite e nunca entra em arquivo versionado, argumento de linha de comando ou log.

## 2026-10-02 — container do Hermes (workspace do board TRE, worktree `feature/TRE-W6-E01-T02`) — TRE-W6-E01-T02 (Titan IMAP): entrega medida, sem rede além do loopback

- **O que este card e:** segundo card da onda W6 (canal inbound). Entrega a camada de **configuracao
  validada** do IMAP do Titan e o **primitivo de leitura** da caixa (listar envelopes e ingerir as
  mensagens novas uma unica vez cada). Nao toca banco, nao cria tabela, nao classifica resposta (isso e
  do W6-E05) e nao escreve no servidor.
- **Invariante de leitura (o traco do card):** a caixa e aberta **somente** com `EXAMINE`
  (`readonly=True`), todo conteudo vem de `BODY.PEEK` e o proprio componente **audita a fonte antes de
  qualquer conexao** (`ESCRITA_NO_CODIGO`, exit 3, sem conectar). O aceite mede no sink que **todas** as
  selecoes foram `EXAMINE`, que `total_buscas_sem_peek = 0`, que `total_comandos_de_escrita = []` e que
  **nenhuma** mensagem ficou marcada `\Seen` (flags identicas antes e depois).
- **Ambiente e seguranca:** worktree proprio, nada em producao (ADR-005). O papel dev-harness nao tem
  `TRE_TITAN_*` (`hermes/policies/dev-harness.yaml`), nenhuma credencial real foi usada e nenhum host
  fora do loopback foi contatado: os casos "host real" usam `192.0.2.1` (TEST-NET, nao roteavel) e sao
  sempre recusa medida — se o modulo tivesse tentado conectar, o exit seria 1 (FALHOU), nunca 3/4.
  `--ambiente prod` recusa por desenho (exit 4).
- **Medido por execucao real** (verde, exit 0, tres instrumentos):
  - suite offline `scripts/integracoes/verificar_imap_titan.py`: **IMAP_TITAN_SUITE_OK (67 itens,
    0 falhas)** — config, inferencias, matriz porta x TLS (993/143; 25/465/587 SMTP e 110/995 POP3
    recusadas), guardas de ambiente, invariante de leitura (incluindo a **violacao injetada** numa copia
    do modulo, que a auditoria pega), segredo, trilha/idempotencia/desfazer e contrato;
  - aceite `scripts/integracoes/teste_imap_titan_aceite.sh` com sink descartavel em `127.0.0.1` (TLS
    proprio, modos `implicit_tls` e `starttls`): **ACEITE_IMAP_TITAN_001_OK (33 itens, 0 falhas)** —
    TLS/LOGIN/EXAMINE/NOOP, leitura de envelopes sem marcar lido, ingesta medida na captura (3 arquivos,
    3 INGERIDO), replay `JA_INGERIDO` que **nao busca o corpo de novo**, invariante medido no sink,
    guardas com a caixa intacta, senha com 0 ocorrencias em todos os artefatos, desfazer com auditoria
    preservada e `git status` identico antes/depois;
  - `--prova-de-dente` (6 mutacoes: sem-guarda-de-host, sem-matriz-porta-tls, ignora-confirmo,
    senha-sem-mascara, **busca-sem-peek**, sem-idempotencia): **ACEITE_IMAP_TITAN_001_OK (40 itens,
    0 falhas)** — cada mutacao reprovou **o item esperado** e o controle rodou a suite de novo no modulo
    intacto, verde.
- **Defeitos REAIS medidos na rodada 1 e corrigidos** (todos entraram como item de regressao):
  1. **a captura do sink guardava a senha**: o comando `LOGIN` era registrado cru (`LOGIN <usuario>
     <senha>`); agora o sink registra `LOGIN <usuario> <senha-oculta>` e o item 8.2 do aceite exige a
     marca;
  2. **a auditoria de leitura se auto-detectava**: o padrao de escrita era `"." + "append("`, que casa
     com `lista.append()` de Python — o modulo recusava a si mesmo (`ESCRITA_NO_CODIGO` com APPEND,
     BUSCA_SEM_PEEK e BUSCA_ANTIGA). A auditoria passou a mirar o **recebedor da sessao**
     (`sessao.<metodo>`) e o comando enviado por `uid(...)`, com regex;
  3. **o DRY_RUN do proprio `--desfazer` servia de alvo**: como o dry-run tambem entra na trilha
     (auditoria do que NAO aconteceu), um desfazer de mensagem nunca ingerida achava a si mesmo e
     respondia `DESFEITO`. Agora so uma INGESTA registrada e alvo (`NAO_ENCONTRADO`, exit 1) — item 7.9
     da suite.
- **Defeitos do proprio instrumento** (medidos ao rodar, corrigidos): o sink nao implementava STARTTLS
  (o item 3.2 do aceite reprovou com o modulo correto antes da correcao) e o aceite tinha um
  `[ "$A" = "$B ]` sem a aspa de fechamento, que quebrava a leitura do proprio roteiro (`unexpected EOF`
  do bash) — os dois foram corrigidos e o aceite passou a rodar `bash -n` limpo.
- **Limites declarados:** (a) a prova contra `imap.titan.email` NAO e deste card — exige credencial do
  Sales AI + aprovacao registrada, e por isso e de **homolog**; (b) o aceite prova TLS contra um
  certificado proprio de dev: mede negociacao TLS e LOGIN, nao a cadeia de confianca publica (que so o
  provedor real exercita); (c) nao ha `IDLE`/push nem parser de anexo/HTML nesta v1 — quem repete a
  rodada e o chamador, com a mesma identidade de mensagem; (d) a trilha e arquivo JSONL fora do banco
  (quando o banco do TRE estiver de pe, o card de sincronizacao decide se ela passa a viver em
  `sync_events`); (e) o sink e **estrito por desenho**: o RFC 3501 descartaria a flag em `EXAMINE`, e o
  sink a aplica mesmo assim para expor a INTENCAO do cliente (mede o pedido, nao apenas o efeito).
- **Logs brutos:** saidas completas anexadas ao card —
  `/opt/data/kanban/boards/transformativa-revenue-engine/attachments/t_9d38e360/aceite-suite-imap-titan-67ok.out`,
  `aceite-imap-titan-33ok.out` e `aceite-imap-titan-dente-40ok.out` (mais `00-cabecalho.out` e
  `sha256-artefatos.out`).
- Segredos: nenhum valor nesta entrada; a senha usada nas provas e um valor de teste local gerado pelo
  proprio aceite e nunca entra em arquivo versionado, argumento de linha de comando ou log.
## 2026-10-03 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) + container do Hermes — TRE-W6-E04-T01: envio outbound v1 medido por execucao real

- **Objetivo:** fechar o card que **integra os dois pais** da onda W6 (W6-E01-T01 SMTP + W6-E03-T01 aprovacao):
  entregar o orquestrador de envio outbound que consome um pedido aprovado por humano e o envia pelo primitivo
  de SMTP, respeitando politica, guarda de escrita e contrato de dados.
- **Entrega:** `hermes/agents/outreach/send_workflow.py`, `politica-envio-v1.json`,
  `envio-outbound-v1.json`, `scripts/agentes/verificar_envio_outbound.py`,
  `scripts/agentes/duble_psql_envio.py`, `scripts/agentes/teste_envio_outbound_aceite.sh`,
  `docs/architecture/envio-outbound-v1.md`, `docs/runbooks/envio-outbound.md`.
- **Como foi medido (execucao real, nao inspecao):**
  1. **Suite offline** (`verificar_envio_outbound.py`, no container do Hermes, sem PostgreSQL e sem SMTP): duble
     de porta de banco (`duble_psql_envio.py`) + primitivo falso que anota a mensagem => `PASS (60 itens, 0
     falhas)`; com `--autoteste` (7 mutacoes no modulo, cada uma tem de reprovar o item que nomeia) => 7/7
     dentes verdes.
  2. **Aceite E2E** (`teste_envio_outbound_aceite.sh --prova-de-dente`, na VPS, PostgreSQL descartavel
     `pg-envio-acc` com a migration 0001 e **sink SMTP local** em `127.0.0.1` com certificado proprio): o
     pedido nasce do gerador irmao (W6-E02) e e aprovado pelo workflow irmao (W6-E03) — cadeia real; o envio
     e conferido na captura do sink (TLS, `rcpt_to`, AUTH sem senha, assunto e corpo iguais ao aprovado) e no
     banco (`interactions`, `sync_events`) => `ACEITE_ENVIO_OUTBOUND_001_OK (44 itens, 0 falhas)` + 3/3 dentes.
- **Defeitos reais encontrados e corrigidos durante a medicao** (nenhum deles apareceria numa inspecao de
  codigo): (a) `contact_id` de `interactions` recebia o **nome** do contato (uuid invalido) — o portao devolve
  e-mail e nome, entao o id passou a ser lido do proprio pedido (`entity_id`, leitura); (b) o claim nao
  registrava o primitivo usado (trilha incompleta) — passou a entrar no `request_payload`;
  (c) `--desfazer` e replay ficaram medidos no banco; (d) o aceite precisou de certificado com SAN de IP
  proprio porque o `-addext` do openssl deste host falha (configuracao de sistema incompleta).
- **Decisoes:** worktree dedicado do card e branch baseada no pai da aprovacao com merge do pai do SMTP;
  `acoes-declaradas.yaml` conflitado adotado do pai (`--theirs`); o orquestrador e o **unico** ponto de escrita
  SQL (so `interactions` e `sync_events`), com `ddl`/`delete` declarados `recusado` na politica.
- **Lacunas declaradas:** producao nao e deste componente (`prod` exit 4, por desenho); o aceite com o
  `smtp.titan.email` real e de homolog (credencial do Sales AI + aprovacao do dono); a politica nasce
  `PROPOSTA_A_HOMOLOGAR`; a ingestao de resposta e o W6-E05/IMAP.
- **Logs brutos:** saidas completas em `/opt/data/cache/scratch/tre-e04t01/*.out` (suite offline, autoteste e
  aceite E2E com dentes), anexadas ao card do board.
- Segredos: nenhum valor nesta entrada. O aceite nao usa credencial Titan: a senha do sink e um valor de teste
  local gerado pelo proprio aceite, nunca versionado, nunca em argumento de linha de comando e nunca em log
  (ha item medindo isso na captura do sink).

## 2026-10-03 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W6-E07-T01: E2E Outbound #002 medido ponta a ponta

- **O que este card e':** o unico card da onda W6 que nao entrega componente novo — ele **encadeia** os
  sete anteriores (`E01-T01`, `E01-T02`, `E02`, `E03`, `E04`, `E05`, `E06`) mais o NBA do W5 no
  **cenario do doc 08 §4** (10 passos do outbound), com as pecas reais de cada card num **unico trio
  descartavel** em loopback: PostgreSQL + sink SMTP + sink IMAP + stub da API controlada do Odoo.
- **Integracao das linhas (parte do trabalho):** as duas linhas da onda estavam **separadas** no git
  (`develop -> E01-T01 -> E01-T02 -> E05 -> E06` de um lado; `W5-E08 -> E02 -> E03 -> E04` do outro).
  O branch do card integra as duas (merge com 3 conflitos, todos de **apendice de doc/portao**:
  `criterios-de-aceitacao.md`, `registro-de-execucoes.md`, `verificar_estrutura.sh` — resolvidos
  mantendo OS DOIS lados; no portao, o `fi` comum ao fim do bloco fechava um `if` diferente em cada
  lado e passou a ser dois). Sem esse merge nao existe "cadeia W6" para medir.
- **Defeito real medido na rodada 1 (e corrigido no modulo do aceite):** `correlation_id` e' coluna
  `uuid` no banco canonico. Ids de mentira (`e2e002-nba1`) derrubavam a **auditoria** de TODOS os
  componentes (`invalid input syntax for type uuid`) e o NBA nem gravava a recomendacao — o aceite
  passou a gerar UUID de verdade.
- **Defeito real medido na rodada 2 (e corrigido no modulo do aceite):** o stub da API do Odoo **nao
  tinha pidfile**, entao o `--manter` de uma rodada deixou o processo de pe e a rodada seguinte falou
  com o **stub velho** (chave velha -> `HTTP 401 credencial_invalida` em nome do componente). O aceite
  agora grava o pid do stub e **ABORTA** se uma das 3 portas locais ja' estiver ocupada — medir contra
  sobra de rodada e' falso negativo.
- **Outros defeitos do instrumento corrigidos:** o `PEDIDO` vazio (efeito do primeiro) estourava SQL
  com uuid vazio; a comparacao do operador era feita depois de `tr -d ' '` (nome virava
  `AndersonRibeiro`); a fila de aprovacao imprime o resumo **sem** o bloco `fila` no stdout (o
  `novos`/`veredito` estao no relatorio JSON); o dente reentrava no proprio bloco de dentes (precisou
  de `--sub-run`); os sub-runs do dente precisavam das 3 portas que as pontas do aceite-pai ainda
  seguravam (liberadas antes de medir).
- **Medido por execucao real** (verde, exit 0): `bash scripts/e2e/verificar-e2e-outbound-002.sh` ->
  **ACEITE_E2E_OUTBOUND_002_OK (56 itens, 0 FALHOU)**; `--prova-de-dente` -> **3/3** (`sem-cta-na-mensagem`
  reprovou o item 5.7, `sem-supersessao` o 10.3, `primeira-regra-sempre` o 2.2) com o controle verde e o
  veredito OK no fim. Itens de destaque: envio dry-run `PLANO` que **nao entrega**, entrega unica sob
  TLS ao contato do pedido com corpo == texto aprovado + CTA (hash conferido), `interactions` com
  `content_reference = envio:<pedido>:<hash>`, resposta do lead classificada `INTERESSE` com o
  invariante de leitura re-medido no sink (`EXAMINE`, `0` buscas sem `PEEK`, `0` comandos de escrita,
  nenhuma marca `\Seen`), CRM atualizado pela API controlada (`RESPOSTA_INTERESSE` + `RESPONDER_AGORA`,
  atividade no contato, toda escrita com `idempotency_key`), NBA de novo com `CREATE_MEETING`
  SUPERSEDENDO a recomendacao anterior, `prod` recusado (exit 4) nos **4** componentes e senha/chave
  ausentes de toda saida.
- **Lacuna MEDIDA, nao escondida:** nenhum componente da onda W6 grava `interactions.odoo_lead_id`. O
  aceite **mede o efeito** (item 9.1: sem o vinculo o CRM responde `SEM_VINCULO`, exit 0) e so' depois
  aplica a **ponte declarada** do harness (item 9.2), que representa o papel do E2E #001 / fundacao
  W3-W4 (o lead nasce no CRM e o id volta para a interacao). Fechar a lacuna e' do caminho de
  fundacao/sync, nao deste card.
- **Ambiente e seguranca:** nada em producao (ADR-005). Toda ponta e' local (`127.0.0.1`) e
  descartavel; nenhuma credencial Titan, nenhum destino real, nenhuma chave de API do Odoo. Os
  containers do ambiente (`pg-sales-dev`, `pg-odoo-dev`, `odoo-dev`, `proxy-dev`) **nao** foram
  tocados: o aceite aborta se o container ou as 3 portas dele ja' estiverem em uso. O container do
  trio (`pg-resp-e2e004`/`pg-resp-e2e005` nas rodadas medidas) e' removido no fim.
- **Logs brutos:** saidas completas anexadas ao card —
  `aceite-e2e-outbound-002-56ok.out` e `aceite-e2e-outbound-002-dentes-3de3.out` (mais
  `sha256-artefatos.out`).
- Segredos: nenhum valor nesta entrada. A senha do sink SMTP e a chave da API do stub sao valores de
  teste gerados na hora pelo proprio aceite; ha item medindo 0 ocorrencias delas na saida.

---

## TRE-W8-E01-T01 — Funil (dashboard derivado) — 03/10/2026

**Card:** `TRE-W8-E01-T01` (W8 / Analytics) · **Base:** `origin/feature/TRE-W6-E07-T01` @ `20e8ea6` ·
**Branch:** `feature/TRE-W8-E01-T01` · **Máquina:** VPS Contabo `vmi3619453` (169.58.24.102), ambiente **dev**
Transf: `tar` por ssh (sem `scp`) para `/opt/tre/w8e01t01-r3` (r1 e r2 foram as rodadas de conserto).

**O que foi medido (por execução real, não por leitura do código):**

- `python3 scripts/agentes/verificar_funil.py --autoteste` → **24 itens, 0 falhas + 8/8 mutações**
  detectadas (`d1` rótulo trocado → item 2; `d2` estágio sem fonte → item 4; `d3` verbo de escrita no SQL →
  item 5; `d4` guarda de `prod` desligada → item 9; `d5` guarda de container de dev desligada → item 8;
  `d6` alcance cumulativo quebrado → a suíte REPROVA (o componente recusa funil não monotônico);
  `d7` casamento por semelhança → item 11; `d8` Nurture deixando de ser lateral → item 3). Descoberto na
  primeira rodada de autoteste: mutação só vale se reprovar item que o **alvo limpo não reprova** —
  comparar contra falha pré-existente faz dente verde falso (corrigido antes do aceite).
- `bash scripts/agentes/teste_funil_aceite.sh` → **`ACEITE_FUNIL_OK` (34 itens, 0 falhas, exit 0)**, ~17 s,
  PostgreSQL descartável `pg-funil-acc` + migration 0001 + base semeada (9 organizações cobrindo todos os
  níveis, 2 terminais e o ramo lateral). Itens de destaque: alcance por estágio **conferido à mão**
  (9/5/5/5/5/5/4/3/3/2/1/1/1), conversões (55,56 / 80,0 / 75,0 / 66,67 / 50,0 e Nurture 20,0 de
  Qualificado), **contagem por organização** (3 interações de resposta no banco → 2 organizações),
  **três dentes medidos no banco** (rótulo de estágio inventado na trilha não vira estágio;
  `OPPORTUNITY_LOST` sem organização atribuível não entra em Lost; `organizations.status` não declarado
  fica em lacuna), `score_version` vazia **não** qualifica, **leitura pura** por snapshot md5 das 12
  tabelas antes/depois **e** pela transação `READ ONLY` recusando a escrita de prova (sem deixar linha),
  determinismo (mesmo `hash_do_relatorio` em duas rodadas), saída sem PII e dashboard auto-contido.
- **Defeito medido pelo próprio aceite (DETECTADO POR: aceite, antes de qualquer entrega):**
  `psql -c "SET default_transaction_read_only = on; SELECT …"` num único `-c` **não** vale — a transação
  implícita já começou antes do `SET` e a escrita de prova passou (`INSERT 0 1`). Medido de novo em
  container descartável: com **dois** `-c` o PostgreSQL responde
  `cannot execute INSERT in a read-only transaction`. O componente passou a emitir dois `-c` (+`-q`, que
  também tirou o eco do tag `SET` da saída — o eco inflava `fontes` em 1).
- `bash scripts/verificar_estrutura.sh` → **PASS (0 falhas)**, com o bloco novo do card (arquivos
  versionados, `py_compile`, `--conferir` contra o contrato de dados, marcas de guarda e validade do aceite).
- **Ambiente:** nada em produção (ADR-005). O container descartável do aceite é removido no fim; os
  containers do ambiente não foram tocados; nenhuma credencial real, nenhuma ponta externa (só `docker exec`
  no container local + `127.0.0.1`).
- **Lacunas declaradas:** as cinco do desenho (`docs/architecture/funil-v1.md` §5) viajam no relatório
  (`lacunas_declaradas`). A que mais pesa na operação: **evento sem organização atribuível não entra no
  estágio** (é o caso de `OPPORTUNITY_WON/LOST`, que carregam o UUID da oportunidade) — o conserto é o
  produtor publicar `organizacao_id` no payload (ou materializar estágio na coluna do contrato), nunca
  casar oportunidade com lead por semelhança.
- **Evidência anexada ao card:** `aceite-funil-34ok.out`, `suite-funil-24ok-8dentes.out`,
  `portao-estrutura.out` e `sha256-artefatos.out`.
Segredos: nenhum valor nesta entrada; o componente recusa a rodada (exit 5) se o valor de
`TRE_FUNIL_TOKEN` aparecer na evidência — item medido na suíte offline.

---

## 2026-10-03 — TRE-W8-E03-T01: efetividade do score (`efetividade-score-v1`) medida na VPS de dev

- **Escopo entregue:** `hermes/agentes/analytics/efetividade_score.py` + contrato
  `hermes/agentes/analytics/efetividade-score-v1.json`. O card mede (não recalibra): cobertura do score,
  taxa de avanço por faixa do Data Contract com lift, adesão do PRIORITY **armazenado** à fórmula V1,
  monotonicidade como **achado** com `base_suficiente`, e efetividade por componente (quartis de posto).
  O desfecho vem do próprio `funil.py` (`alcance_por_organizacao`, card W8-E01-T01, importado) — sem
  segunda verdade para o alcance. Faixas e pesos são **lidos** de `docs/data/data_contract_v1.json#scores`.
- **Suíte offline (`scripts/agentes/verificar_efetividade_score.py --autoteste`):** `PASS (22 itens, 0 falhas)`
  + `AUTOTESTE 8/8 mutações detectadas` (cada mutação reprova um item que o alvo limpo não reprova:
  peso do Data Contract alterado, faixa com lacuna, guarda de produção desligada, endpoint rebaixado,
  alcance por organização do pai quebrado, adesão sempre-conforme, faixa sem limite superior e quartis
  invertidos).
- **Aceite de ponta (`scripts/agentes/teste_efetividade_score_aceite.sh`, na VPS vmi3619453):**
  PostgreSQL descartável `pg-analytics-efet` (127.0.0.1, imagem `postgres:16`, removido no fim) + migration
  0001 + base semeada (15 organizações, 58 linhas de `scores`, trilha Odoo → PostgreSQL e interações
  cobrindo as 5 faixas, os 4 endpoints e as 4 lacunas) → **`ACEITE_EFETIVIDADE_SCORE_OK`, 41 itens,
  0 falhas**. Deste total, **34 itens são do aceite do card PAI**, reexecutado dentro deste aceite:
  `ACEITE_FUNIL_OK (34 itens, 0 falhas)` — é a prova de que expor o alcance por organização no `funil.py`
  **não** mudou o relatório `funil-v1` (JSON, HTML e `hash_do_relatorio`).
- **Números conferidos à mão no banco:** cobertura **11/15 = 73,33%** com lacunas nomeadas
  (sem PRIORITY=1, sem versão=1, vencida=1, fora da escala=1, histórico ignorado=1); taxa-base de avanço
  (`Reunião`) **45,45%**; taxa por faixa **A+ 100% / A 100% / B 0% / C 100% / Nurture 0%**; lift **A+ 2,20x**
  e **B 0,00x**; Won/Lost separados (1 e 1) e taxa de vitória 100% (C) / 0% (A); **achado de
  monotonicidade**: B (0%) abaixo de C (100%) → `monotonico: false` com a violação nomeada (achado medido,
  não erro); **base insuficiente declarada** (nenhuma faixa com 5 organizações → `base_suficiente_para_conclusao:
  false`, o gatilho do W9-E01-T01); adesão à fórmula **9 comparáveis / 8 conformes / 1 divergente**
  (desvio 1,00, 88,89%), com `DATA_QUALITY` ausente em 1 organização e versão divergente em 1; quartis por
  componente com **DATA_QUALITY Q1 100% vs Q4 50% (lift 2,0x)**; integração com o pai: as somas por faixa
  fecham com o resumo do funil (coorte 11, avanço 5 = `Reunião.alcancadas`, won 1, lost 1) e a exportação
  `--por-organizacao` entrega o **mesmo** alcance usado no relatório (org 1 nível 8, org 5 com rótulo `Won`).
- **Leitura pura provada por mecanismo:** snapshot das 12 tabelas igual antes/depois, transação `READ ONLY`
  recusando `INSERT` (`cannot execute INSERT in a read-only transaction`) e a escrita recusada **não
  deixando linha**; auditoria da fonte reprovando verbo de escrita antes de qualquer conexão. Determinismo
  (duas rodadas, mesmo `hash_do_relatorio`), saída **sem PII** e dashboard HTML auto-contido também medidos.
- **Achados/defeitos medidos durante o card (todos com a suíte como DETECTADO POR):**
  (1) o CSS do dashboard tinha `width:100%;` dentro de string formatada com `%` → `ValueError` que
  derrubava a rodada (exit 1) em vez de gerar o relatório; conserto: `100%%`;
  (2) a dependência tem a **própria** classe `Recusa` (`funil.py`), que escapava do `except Recusa` do
  componente e virava **exit 1 (traceback)** em vez de exit 3 — a recusa de guarda parecia quebra; conserto:
  captura por atributo `motivo`/`codigo`, com recusa de qualquer origem saindo pelo código dela;
  (3) as faixas voltavam em ordem **ascendente** (Nurture → A+), o que invertia a leitura da monotonicidade
  (a violação saía como "Nurture >= C") e elegia a pior faixa como "melhor"; conserto: **melhor faixa
  primeiro** (A+ … Nurture), ordem em que a monotonicidade é conferida;
  (4) o aceite dependia de `/tmp/aceite-w8e01t01` do card pai, que já pertencia a `root` de uma rodada
  anterior — o aceite do pai falhava por **permissão**, não por regressão; conserto: base própria
  (`TRE_ACEITE_BASE="$BASE/pai"`). Os itens 2 e 4 foram pegos pela **primeira** rodada do aceite na VPS.
- **Portão de estrutura:** `bash scripts/verificar_estrutura.sh` → **PASS (0 falhas)**, com o bloco do card
  (arquivos versionados, `py_compile`, `--conferir` contra o Data Contract e a dependência, marcas de
  guarda, presença de `alcance_por_organizacao`, extensão declarada no contrato do funil e a exigência de
  `ACEITE_FUNIL_OK` no aceite).
- **Ambiente:** nada em produção (ADR-005). O container descartável do aceite é removido no fim; os
  containers do ambiente não foram tocados; nenhuma credencial real, nenhuma ponta externa.
- **Lacunas declaradas (6, viajam no relatório):** L1 coorte acumulada (safra); L2 associação ≠ causa;
  L3 coorte pequena (`base_suficiente: false`); L4 faixa derivada e não persistida (criar coluna exige
  versão nova do contrato); L5 viés de sucessão do componente (último valor sobre desfecho passado);
  L6 `Lost` sem ponto de perda no V1 → `taxa_de_vitoria_pct` é `won/(won+lost)`.
- **Evidência anexada ao card:** `aceite-efetividade-41ok.out`, `suite-efetividade-22ok-8dentes.out`,
  `portao-estrutura.out` e `sha256-artefatos.out` (hash idêntico entre o repo e a cópia rodada na VPS para
  componente, funil e aceite).
- Segredos: nenhum valor nesta entrada; o componente recusa a rodada (exit 5) se o valor de
  `TRE_EFETIVIDADE_TOKEN`, `TRE_EFETIVIDADE_SCORE_TOKEN` ou `TRE_FUNIL_TOKEN` aparecer na evidência.

---

## 2026-10-03 — TRE-W9-E03-T01: previsão do melhor canal (`previsao-canal-v1`) medida na VPS de dev

- **Escopo entregue:** `hermes/agentes/analytics/previsao_canal.py` + contrato
  `hermes/agentes/analytics/previsao-canal-v1.json`. O card **mede** a efetividade histórica de cada canal
  declarado (abordadas, outbound, respostas INBOUND classificadas, taxa de resposta, avanço no endpoint
  `Reunião`, lift contra a taxa-base, Won/Lost, `base_suficiente`) e **prevê** o melhor canal por organização —
  **apenas** quando a pré-condição `dados multicanal` é atendida. O desfecho vem do `funil.py`
  (`alcance_por_organizacao`, card W8-E01-T01, **importado**, não reimplementado) e o vocabulário de canal é
  **lido** de `previsao-canal-v1.json` (o Data Contract V1 não congela valores de `interactions.channel`).
- **Pré-condição `dados multicanal` (não é card):** forma testável = ≥ 2 canais com base suficiente (≥ 3
  organizações abordadas) e ≥ 4 organizações com interação. Não atendida ⇒ `atendida=false`,
  `previsao_emitida=false`, `previsoes=[]` e `faltando` com exigido × obtido (fail-closed).
- **Suíte offline (`scripts/agentes/verificar_previsao_canal.py --autoteste`):** `PASS (23 itens, 0 falhas)`
  + `AUTOTESTE 8/8 mutações detectadas` (bloqueio de opt-out desligado no contrato, pré-condição afrouxada,
  guarda de produção desligada, endpoint principal fora da lista, nível ignorado no avanço, ranking invertido,
  bloqueio ignorado na escolha e vocabulário com nome duplicado). Base sintética conferida à mão em três
  cenários: efetividade (**A**), **empate de taxa com os quatro desempates declarados** (B) e pré-condição não
  atendida (C).
- **Aceite de ponta (`scripts/agentes/teste_previsao_canal_aceite.sh`, na VPS vmi3619453):** PostgreSQL
  descartável `pg-analytics-canal` (127.0.0.1, imagem `postgres:16`, removido no fim) + migration 0001 + base
  semeada (8 organizações; 3 canais; `opt_out_email` em 2, `opt_out_whatsapp` em 1, `do_not_contact` em 1;
  organização sem contato; canal `SMS` fora do vocabulário; direção `INTERNAL`; trilha Odoo → PostgreSQL com
  Won/Lost/Reunião/Proposta) → **`ACEITE_PREVISAO_CANAL_OK`, 42 itens, 0 falhas**.
- **Números conferidos à mão no banco:** pré-condição **ATENDIDA** (2 canais suficientes, 8 organizações com
  interação); taxa-base de avanço **62,50%**; **EMAIL 4 abordadas / 50,00% / lift 0,80x**, **WHATSAPP 3 / 100,00%
  / lift 1,60x**, **LINKEDIN 2 / sem base** (fora do ranking); taxa de resposta **EMAIL 3/7 = 42,86%** e
  **WHATSAPP 1/5 = 20,00%**; **7 previsões** (WHATSAPP 6, EMAIL 1) com O1/O2/O4/O6/O7/O8 → WHATSAPP e O5 → EMAIL;
  **opt-out é bloqueio** (O2/O7 com EMAIL bloqueado por `opt_out_email`, O5 com WHATSAPP bloqueado por
  `opt_out_whatsapp`) e **`do_not_contact` bloqueia os 3 canais** (O3 sem previsão); lacunas nomeadas: `SMS`
  fora do vocabulário = 1, direção estranha = 1, inbound sem `response_category` = 1, organização sem contato = 1,
  canais sem base = `[LINKEDIN]`.
- **Integração com o pai:** o avanço somado por canal fecha com o relatório do funil (**5 = `Reunião.alcancadas`**)
  e Won/Lost dos canais batem com o resumo do funil (**1 e 1**) — o alcance não é recalculado por conta própria;
  nenhuma previsão contradiz bloqueio (canal previsto nunca está em `canais_bloqueados`) e toda previsão viaja com
  `amostra_do_canal ≥ 3`.
- **Leitura pura provada por mecanismo:** snapshot das 12 tabelas igual antes/depois, transação `READ ONLY`
  recusando `INSERT` (`cannot execute insert in a read-only transaction`) e a escrita recusada **não deixando
  linha**; auditoria da fonte reprovando verbo de escrita antes de qualquer conexão. Determinismo (duas rodadas,
  mesmo `hash_do_relatorio`), saída **sem PII** (nenhum e-mail/nome/domínio da base semeada, nenhuma coluna de
  contato direto no SQL das fontes próprias) e dashboard HTML auto-contido também medidos.
- **Defeito MEDIDO e corrigido no próprio card (DETECTADO POR: aceite, antes de qualquer entrega):** o bloco de
  números conferidos à mão chamava `O(i)` sobre uma **string** de formatação e morria com
  `TypeError: 'str' object is not callable`; como o aceite contava só as linhas `OK`/`FALHOU` impressas, ele
  **fechou PASS (34 itens) sem rodar os 5 itens seguintes** (opt-out como bloqueio, `do_not_contact`, lacunas
  nomeadas, contrato/dependência). Conserto: `def O(i)` **e** um item que exige o bloco inteiro rodando até o
  fim (`bloco ... rodou ate' o fim (exit 0)`, medido pelo exit code do heredoc) — o defeito era a **prova**, não
  o componente. Remedição: **42 itens, 0 falhas**.
- **Portão de estrutura:** `bash scripts/verificar_estrutura.sh` → **PASS (0 falhas)**, com o bloco do card
  (arquivos versionados, `py_compile`, `--conferir` contra o Data Contract e a dependência, marcas de guarda
  `RECUSA por desenho (exit 4)` / `READ ONLY` / `lacunas_declaradas` / `base_suficiente` / `dados_multicanal`,
  presença de `alcance_por_organizacao` no componente e `ACEITE_PREVISAO_CANAL_OK` no aceite).
- **Ambiente:** nada em produção (ADR-005). O container descartável do aceite é removido no fim; os containers do
  ambiente não foram tocados; nenhuma credencial real, nenhuma ponta externa, nenhum envio.
- **Lacunas declaradas (7, viajam no relatório):** L1 vocabulário de canal não congelado no Data Contract;
  L2 associação ≠ causa; L3 `LINKEDIN` sem coluna de opt-out própria; L4 prior de canal da coorte, não
  personalização por contato; L5 coorte acumulada; L6 canal ≠ mensagem; L7 a previsão não é ato (virar
  `recommendations` exige versão nova do contrato + approval).
- Segredos: nenhum valor nesta entrada; o componente recusa a rodada (exit 5) se o valor de `TRE_CANAL_TOKEN`,
  `TRE_PREVISAO_CANAL_TOKEN` ou `TRE_FUNIL_TOKEN` aparecer na evidência.
## 03/10/2026 — TRE-W8-E04-T01 (Message performance / `desempenho-mensagens-v1`)

- **Comando (offline):** `python3 scripts/agentes/verificar_desempenho_mensagens.py --autoteste` →
  `PASS (desempenho de mensagens v1: 48 itens, 0 falhas)`, exit 0, com **7/7 dentes** (cada mutacao aplicada
  numa copia temporaria reprova o item que nomeia: sem normalizacao de canal, janela exclusiva no limite,
  credito a todos os envios, auto-resposta contada como resposta, amostra desligada, sem guarda de prod e
  referencia malformada aceita).
- **Comando (E2E, VPS do ambiente):** `bash scripts/agentes/teste_desempenho_mensagens_aceite.sh` em
  `/tmp/tre-w8e04c-*` na VPS `vmi3619453` (usuario `tre-deploy`) → `ACEITE_DESEMPENHO_MENSAGENS_001_OK
  (19 itens, 0 falhas)`, exit 0. O aceite subiu PostgreSQL descartavel `pg-desemp-acc` (`postgres:16`),
  aplicou `db/migrations/0001_sales_intelligence_v1.sql`, semeou 15 envios + 5 respostas **na forma declarada
  pelos contratos irmaos** (canal/direcao/tipo lidos de `politica-envio-v1.json`; categorias de
  `ingestao-respostas-v1.json`) e mediu a analise contra o banco real. Container removido no `trap`.
- **O que ficou provado no E2E:** totais (15 enviadas / 4 respondidas / 3 positivas / 1 opt-out / 1
  auto-resposta descartada), `taxa_de_interesse` da variante H1 = 0,4, tempo medio da H1 = 2,25h, taxa de
  opt-out da H8 = 0,2, melhor variante = H1 (so entre as de amostra suficiente); **contagem das 12 tabelas
  identica antes/depois** (`12|21|3|1`) — prova de que a analise e somente leitura; saida **reproduzivel**
  (duas rodadas iguais, sem carimbo); saida sem `organization_id`/`contact_id`/`approval_id`; `--ambiente
  prod` → exit 4.
- **Segredos:** nenhum valor nesta entrada. Uso exclusivo de loopback/container descartavel; a senha do
  Postgres descartavel e literal de teste do proprio aceite.
- **Lacunas declaradas (medidas, nao escondidas):** `interactions` nao tem `delivered`/`opened`/`bounced`
  (a taxa e de RESPOSTA, nao de entrega); o `channel` do envio (`EMAIL`) diverge do `channel` da ingestao
  (`email`) — normalizado na leitura e registrado como defeito de FORMA do dado gravado; `campaign_id` e
  vinculo logico sem FK/entidade; a cadeia real SMTP/IMAP ja foi medida no W6-E07 e no aceite do W6-E05 —
  aqui o que roda de ponta a ponta e a ANALISE.

### 2026-10-03 — TRE-W9-E04-T01 (Best timing / `melhor-horario-v1`) — entrega medida

- **Campos do card definidos no início da execução** (o doc 11 não os detalha): ACCEPTANCE/TEST/ROLLBACK/RISK
  registrados em `docs/kanban/criterios-de-aceitacao.md` (§ acima) e no contrato
  `hermes/analytics/melhor-horario-v1.json`; a pré-condição (*histórico de interações*) foi conferida como
  existente (`sales_intelligence.interactions`, W6-E04/E05).
- **Decisão de arquitetura (medida, não presumida):** a atribuição resposta→envio é **uma só**. O componente
  importa `desempenho_mensagens.py` (W8-E04-T01) e usa `ler_interacoes`, `separar`, `creditar`, `medir_grupo`,
  `PortaBanco` e `afirmar_somente_leitura`; o que é novo é apenas o **eixo de tempo** (dia × faixa no fuso
  declarado). Item de suíte e de aceite medem a coerência: na mesma base, os totais do melhor-horário são
  **idênticos** aos do irmão de desempenho (dente `segunda-regra-de-credito` reprova o item quando alguém
  reimplementa o crédito).
- **Offline (medido):** `python3 scripts/agentes/verificar_melhor_horario.py --autoteste` →
  `VERIFICADOR_MELHOR_HORARIO_PASS (29 itens, 0 falhas) + autoteste OK (6/6 mutações detectadas)`, exit 0.
  Mutações com o item que cada uma reprova: `offset-ignorado`→V4 (virada de dia), `amostra-desligada`→V17
  (abstenção sem amostra), `grade-aceita-buraco`→V23 (fail-closed da grade), `fuso-sem-validacao`→V24
  (`FUSO_INVALIDO`), `ranking-por-chave`→V7 (melhor janela pela taxa), `segunda-regra-de-credito`→V13
  (coerência com o irmão).
- **Comando (E2E, VPS do ambiente):** `TRE_TIMING_TRABALHO=/tmp/timing-aceite-trabalho bash
  scripts/agentes/teste_melhor_horario_aceite.sh` em `/tmp/tre-w9e04-*` na VPS `vmi3619453` →
  `ACEITE_MELHOR_HORARIO_001_OK (19 itens, 0 falhas)`, exit 0. O aceite subiu PostgreSQL descartável
  `pg-timing-acc` (`postgres:16`), aplicou `db/migrations/0001_sales_intelligence_v1.sql`, semeou 12 envios +
  4 respostas **na forma declarada pelos contratos irmãos** (canal/direção/tipo lidos de
  `politica-envio-v1.json`; categorias de `ingestao-respostas-v1.json`) e mediu a análise contra o banco real.
  Container removido no `trap`.
- **O que ficou provado no E2E:** grade completa de **49 células** com soma das células e das duas marginais
  igual ao total (**12**); célula `segunda/manha` = 5 enviadas, 2 positivas, taxa **0,4**; `terca/tarde` =
  5/1/0,2; **virada de dia medida** (sexta 02:00Z caiu em `quinta/noite`, 2 envios) e célula com 2 envios com
  `amostra_suficiente: false`; `melhor_janela` = `segunda/manha`; linha OUTBOUND sem `envio:` fora da grade;
  totais **iguais aos do irmão de desempenho** na mesma base; contagem das tabelas idêntica antes/depois
  (`12|17|3`) — prova de somente leitura; saída **reproduzível** (duas rodadas iguais, sem carimbo); saída sem
  `organization_id`/`contact_id`/`approval_id`; `--ambiente prod` → exit 4.
- **Segredos:** nenhum valor nesta entrada. Uso exclusivo de loopback/container descartável; a senha do
  Postgres descartável é literal de teste do próprio aceite.
- **Lacunas declaradas (medidas, não escondidas):** fuso é offset fixo declarado (horário de verão exige versão
  nova); a análise é histórica, não previsão por lead; a grade cobre 24 h porque o contrato de dados não
  declara expediente; `contacts` sem fuso do contato (usa-se o do remetente); só `EMAIL` é produzido pelo irmão
  de envio, então não há corte por canal nesta versão.


## 2026-10-03 — TRE-W9-E05-T01 (Automated nurture / `nutricao-automatica-v1`) — entrega medida

- **Comando (offline):** `python3 scripts/agentes/verificar_nutricao_automatica.py --autoteste` →
  `VERIFICADOR_NUTRICAO_AUTOMATICA_PASS (42 itens, 0 falhas)` + `AUTOTESTE OK (5/5 mutacoes detectadas)`,
  exit 0. As fixtures sao montadas NA FORMA dos contratos dos pais (dias, faixas e fuso LIDOS de
  `melhor-horario-v1.json`), e o dente da guarda de escrita planta um statement numa copia do componente
  para provar que ela REPROVA (nao apenas documenta).
- **Comando (E2E, VPS do ambiente):** `bash scripts/agentes/teste_nutricao_automatica_aceite.sh` em
  `/tmp/tre-e05t01b` na VPS `vmi3619453` (usuario `tre-deploy`) → `ACEITE_NUTRICAO_AUTOMATICA_001_OK`
  (36 itens, 0 falhas), exit 0. O aceite subiu PostgreSQL descartavel `pg-analytics-nurture-acc`
  (`postgres:16`), aplicou `db/migrations/0001_sales_intelligence_v1.sql`, semeou 8 organizacoes / 3 canais
  / bloqueios de opt-out / desfecho no funil e, na FORMA declarada pelos contratos, 5 envios com
  `content_reference` (`envio:...`) e 2 respostas (`EMAIL_RESPOSTA`/`INTERESSE`) na MESMA celula de janela.
- **O que ficou provado no E2E (cadeia real, nao fixture de leitura):** o pai do canal mediu a base com
  pre-condicao ATENDIDA e 7 previsoes; o pai do horario escolheu a melhor janela com amostra; o nurture
  derivou 28 toques (4 por organizacao) com canal = o previsto pelo pai, janela = a do pai, `due_at`
  alinhado ao dia x faixa e nunca no passado, fila deterministica e todo toque com aprovacao humana
  obrigatoria; abstencao medida quando a janela do pai vem sem amostra; duas rodadas com a mesma referencia
  → mesmo `hash_do_plano`; contagem das 12 tabelas identica antes/depois (12|28|8); `--ambiente prod` →
  exit 4.
- **Segredos:** nenhum valor nesta entrada. O componente nao abre banco; o aceite usa container descartavel
  em loopback e a senha do Postgres descartavel e' literal de teste do proprio aceite.
- **Lacunas declaradas (medidas, nao escondidas):** o plano nao materializa nem agenda toques; nao avalia o
  estagio do funil na derivacao (a parada por avanco/resposta e' condicao carregada em cada toque); janela
  global e canal de coorte (sem segmentacao); cadencia declarada, nao medida; sem feriados/fuso do
  destinatario; sem deduplicacao entre rodadas.
