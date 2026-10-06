# Runbook — CRM básico no ambiente **dev** do TRE (módulo `crm` + funil comercial)

**Card:** `TRE-W2-E02-T01` (`t_adea8e6b`, perfil `desenvolvedor`) · **Status:** executado e medido em dev
(01/10/2026) — pendente de **verificação independente** e da **validação do Anderson** (critério 1 do card)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev — desde 06/10/2026 os **três
ambientes convivem de pé** no mesmo VPS (`odoo-dev`, `odoo-homolog`, `odoo-prod` e os respectivos
`pg-odoo-*`); este par opera **só** o dev (guarda de ambiente alvo, §11)
**Base:** módulo `crm` do **Odoo Community 19.0** (`19.0-20260926`), instalado no dev pelo card
`TRE-W2-E01-T01` (`docs/runbooks/odoo-dev.md`)
**Artefatos versionados:** `odoo/crm/funil-transformativa.yaml` · `scripts/provision/configurar-crm-dev.sh` ·
`scripts/provision/verificar-crm-dev.sh` · `scripts/provision/reverter-crm-dev.sh` ·
`scripts/provision/{aplicar_funil_crm,desfazer_funil_crm,repor_etapas_padrao_crm}.py`

Este runbook é o registro da **origem das etapas** (nada inventado), da **representação** de cada item do
contrato no Odoo, do **procedimento**, do **aceite medido**, do **rollback executado** e dos **defeitos
encontrados executando** — comando, saída e exit code.

---

## 1. Campos obrigatórios do card (doc 11 §2) — definidos e executados

### ACCEPTANCE CRITERIA — homologados (Anderson, 29/09/2026)

| # | Critério | Como foi provado (medido) |
|---|---|---|
| 1 | Pipeline e etapas configurados **conforme o processo comercial da Transformativa**, validado pelo Anderson | `odoo/crm/funil-transformativa.yaml` derivado **literalmente** da §7.1 do Data Contract V1.0 (congelado) e aplicado no pipeline `Sales`; `verificar-crm-dev.sh` compara banco × declaração item a item → `CRM_DEV_OK (29 itens, 0 falhas)`, exit 0. **A validação do Anderson é etapa humana e este card vai para review por causa dela** (a representação escolhida está na §3, com a alternativa de cada decisão) |
| 2 | **Nenhuma etapa ou regra inventada** sem aprovação dele | todas as 11 etapas, a ordem, o terminal `Won` e o ramo `Nurture` vêm da §7.1; o que este card **decidiu** foi só a *representação* no Odoo (§3). O que o contrato não define (time comercial, motivo de perda, campo customizado) **não** foi criado — e há item de verificador provando isso (`nenhuma etapa sem pipeline`, `Lost` sem etapa, campos customizados fora de escopo) |

### TEST PLAN (executado)

Consulta das etapas configuradas (psql no `pg-odoo-dev`) + confronto **banco × declaração versionada** por
verificador independente (29 itens) + **4 provas negativas** com exit code + reexecução idempotente. O
`Print`/tela do Odoo é a leitura operacional (`web/login` responde HTTP 200); o que o card entrega como
prova é o **estado do banco** e o confronto com a declaração.

### ROLLBACK PLAN (executado — §7)

Dois níveis, ambos medidos na VPS: (a) **padrão** — remove as etapas do funil e do ramo lateral e repõe as
etapas do próprio módulo (`New`, `Qualified`, `Proposition`, `Won`), com o módulo ainda instalado;
(b) **total** — desinstala o módulo `crm`, voltando ao estado medido antes deste card
(`crm uninstalled`, sem tabelas `crm_*`).

### AFFECTED COMPONENTS

| Componente | Efeito |
|---|---|
| Banco `odoo_dev` (em `pg-odoo-dev`, volume `pgdata-odoo-dev`) | 12 registros em `crm.stage` (11 do funil + 1 do ramo), 1 registro novo em `crm.team` (`Nurture`); 3 etapas do módulo (`New`, `Qualified`, `Proposition`) removidas e a etapa `Won` do módulo **adotada** (reciclada, não recriada) |
| Container `odoo-dev` | módulo `crm` instalado (`ir_module_module.crm = installed`); nenhuma configuração de servidor alterada |
| Artefatos do repo | `odoo/crm/funil-transformativa.yaml` + 6 scripts em `scripts/provision/` + este runbook + `CHANGELOG.md` + `docs/operations/registro-de-execucoes.md` |
| **Fora de escopo / não tocado** | `sales_intelligence`/`pg-sales-dev` (medido: intacto), `homolog`/`prod` (0 arquivo, 0 container), UFW (nenhuma regra nova), `crm.lead` de negócio (nenhum existia — 0 oportunidades antes e depois), campos customizados (card `TRE-W2-E04-*`), exposição pública/TLS (card `TRE-W2-E01-T02`) |

### RISK LEVEL: **Médio** (como proposto no card)

Dado de **configuração** de CRM em ambiente dev, reversível por script, sem credencial nova, sem porta nova
e sem dado de negócio. O risco real está em "etapa inventada" — mitigado por derivar do contrato congelado,
por o verificador confrontar banco × declaração e por a representação ficar declarada e alternativa na §3.

### Autorização

Declaração de ação do dono para este card sob a onda dev homologada em 30/09/2026 (validade 07/10/2026);
recibo JEV **`dec-ec60a87c718d671f` = PASS** (`execucao_de_card`, `lane=high`). Nada em produção.

## 2. De onde vêm as etapas (não inventadas)

`docs/data/DATA_CONTRACT_V1.md` **§7.1 "Funil (estágios, dono Odoo)"** — documento **congelado** em
29/09/2026 (card `TRE-W0-E03-T01`):

> `Descoberto → Pesquisado → Qualificado → Contato identificado → Abordagem iniciada → Engajamento →
> Reunião → Diagnóstico → Proposta → Negociação → Won | Lost`, com `Nurture` como ramo lateral a partir de
> `Qualificado`.

A declaração versionada (`odoo/crm/funil-transformativa.yaml`) carrega a origem (documento, seção, data de
congelamento) e só acrescenta o que o contrato **não** fixa: a **representação** de cada item no Odoo (§3)
e as sequências (ordem explícita no banco, 10..110 em passos de 10).

## 3. Contrato × representação no Odoo (a decisão que o Anderson valida)

O contrato é o **quê**; o Odoo tem mais de uma forma de representar cada item. A decisão está na chave
`representacao` da declaração, e **trocar de representação é editar o YAML e reexecutar o configurador —
não há código a mexer**.

| Item do contrato §7.1 | Representação escolhida | Alternativa (se o Anderson preferir) |
|---|---|---|
| 10 etapas `Descoberto … Negociação` | **etapas** (`crm.stage`) do pipeline do time padrão `Sales` (`sales_team.team_sales_department`), `sequence` 10..100 | um time (`crm.team`) próprio da Transformativa — troca do `xmlid` na declaração; **não foi feito porque o contrato não define time comercial** (criar um seria inventar) |
| `Won` | etapa com `is_won = true` (`sequence` 110) — **a própria etapa `Won` do módulo foi adotada** | — (é o mecanismo nativo do Odoo) |
| `Lost` | **nativo do Odoo**: `crm.lead.lost_reason_id` + `active = false`. **Nenhuma etapa criada** | criar uma etapa "Lost" — não recomendado (o Odoo não trata perda como etapa e isso inventaria regra) |
| `Nurture` (ramo lateral a partir de `Qualificado`) | **pipeline próprio** (time `Nurture`, etapa `Nurture` `sequence` 10): o ramo não aparece misturado no funil principal | (a) etapa `Nurture` dentro do funil principal (deixa de ser lateral); (b) `representacao: nao_configurado` (não configurar o ramo) |
| "campos mínimos" | **os 10 campos que o módulo `crm` entrega** e que o funil precisa (`crm.lead`): `name`, `partner_id`, `stage_id`, `expected_revenue`, `probability`, `user_id`, `team_id`, `date_deadline`, `description`, `active` — medidos presentes 10/10 | campo **customizado** é escopo de `TRE-W2-E04-T01/T02`, não deste card |

## 4. O que existe no dev depois desta execução (medido)

| Objeto | Valor medido (01/10/2026) |
|---|---|
| Módulo | `crm` **installed** em `odoo_dev` (`ir_module_module.state`) |
| Etapas do funil (pipeline `Sales`) | `Descoberto` 10 · `Pesquisado` 20 · `Qualificado` 30 · `Contato identificado` 40 · `Abordagem iniciada` 50 · `Engajamento` 60 · `Reunião` 70 · `Diagnóstico` 80 · `Proposta` 90 · `Negociação` 100 · `Won` 110 (`is_won=true`) |
| Ramo lateral | time `Nurture` → etapa `Nurture` (`sequence` 10), fora do pipeline `Sales` |
| Etapas do módulo | `New`, `Qualified`, `Proposition` **removidas**; `Won` **adotada** (mesmo registro do módulo, agora no funil) |
| Oportunidades | `crm.lead` com etapa: **0** antes e depois (nada de negócio foi tocado; a guarda fail-closed de remoção não foi acionada porque não havia o que guardar) |
| Serviço | `odoo-dev` no ar, `HTTP 200` em `127.0.0.1:8069/web/login`; **nenhuma porta pública** (só loopback; 80/443 são do card `TRE-W2-E01-T02`) |
| UFW | intocada (`22/tcp 80/tcp 443/tcp` — as duas últimas são do card de TLS; nenhuma regra para 8069) |

## 5. Procedimento (na VPS, a partir do repo)

```bash
# 1. transferir os artefatos versionados
#    (no card, a transferencia foi feita por helper fora do repo, com sha256 conferido nos dois lados)
#    destino: /opt/tre/dev/odoo/crm/funil-transformativa.yaml
#              /opt/tre/dev/scripts/{configurar-crm-dev.sh,verificar-crm-dev.sh,reverter-crm-dev.sh,
#                                   aplicar_funil_crm.py,desfazer_funil_crm.py,repor_etapas_padrao_crm.py}
# 2. configurar (instala o modulo se preciso e aplica a declaracao)
bash /opt/tre/dev/scripts/configurar-crm-dev.sh      # -> RESULTADO: CRM_DEV_CONFIGURADO ... exit 0
# 3. aceite
bash /opt/tre/dev/scripts/verificar-crm-dev.sh       # -> RESULTADO: CRM_DEV_OK (29 itens, 0 falhas) exit 0
```

Variáveis (todas opcionais, todas com default de dev): `TRE_ODOO_COMPOSE` (`/opt/tre/dev/compose/odoo.yml`),
`TRE_ODOO_ENV` (`/opt/tre/dev/compose/odoo.env`), `TRE_CRM_YAML`
(`/opt/tre/dev/odoo/crm/funil-transformativa.yaml`), `TRE_ODOO_BANCO` (`odoo_dev`).

Guardas do configurador e do verificador (**ambiente alvo** — §11, conserto do card `t_fd769443`): o caminho
do compose tem de estar dentro de `/opt/tre/dev` (canonicalizado por `realpath -m`: `..` e symlink não
escapam), os containers do dev têm de estar de pé e o banco `odoo_dev` responder, e o par renderizado pelo
`compose config` não pode nomear container de outro ambiente. Os três scripts recusam sem os arquivos de
entrada e **só** mexem em `odoo-dev`/`pg-odoo-dev`. O verificador mede o invariante **oposto** ao da ausência:
os vizinhos `odoo-homolog`, `pg-odoo-homolog`, `odoo-prod` e `pg-odoo-prod` seguem **de pé** depois da rodada
(é o aceite provando que o procedimento do dev não derrubou ambiente alheio).
O configurador **não escreve nada** na cópia operacional `/opt/tre/repo` (a declaração entra no container por
`docker cp` para `/tmp`).

## 6. Aceite medido (comando, saída, exit code)

```
$ bash /opt/tre/dev/scripts/verificar-crm-dev.sh
...
OK     modulo crm instalado no banco odoo_dev
OK     conjunto de etapas == declaracao (faltando: nenhuma; a mais: nenhuma)
OK     etapa 'Descoberto          ' seq=10 won=False times=Sales (declarado: seq=10 won=False)
...                                   (uma linha por etapa)
OK     as 11 etapas do funil estao em um unico pipeline 'Sales' (medido: Sales)
OK     exatamente uma etapa de ganho, 'Won', e ela e' a ultima do funil
OK     ramo lateral 'Nurture' em pipeline proprio (times: Nurture; funil: Sales)
OK     nenhuma etapa sem pipeline (etapa global apareceria em todo pipeline): nenhuma
OK     campos minimos presentes em crm.lead (10/10; ausentes: nenhum)
OK     nenhuma oportunidade sem etapa no funil
OK     banco do Odoo separado do sales_intelligence (nao existe la')
OK     pg-sales-dev (Sales Intelligence) segue de pe, intocado por este card
OK     declaracao do funil sem valor de credencial
RESULTADO: CRM_DEV_OK (29 itens, 0 falhas)          # exit 0
```

### Dentes do aceite — provas negativas medidas (não é aceite que passa sozinho)

| Prova | O que faz | Resultado medido |
|---|---|---|
| **A** — declaração mutada | cópia da declaração com uma etapa **a mais** ("Etapa inventada") entregue ao verificador (a declaração real não é tocada) | `RESULTADO: CRM_DEV_FALHOU (30 itens, 3 falhas)` — **exit 1** |
| **B** — etapa intrusa no banco | etapa plantada direto no banco (`crm.stage`), fora da declaração, **no ambiente vivo** | `RESULTADO: CRM_DEV_FALHOU (29 itens, 1 falha)` — **exit 1**; em seguida o configurador **removeu** a intrusa (`removidas=2`) e o aceite voltou a `CRM_DEV_OK`, exit 0 |
| **C** — rollback padrão | `reverter-crm-dev.sh` (nível padrão) e aceite depois dele | pipeline volta às 4 etapas do módulo; `RESULTADO: CRM_DEV_FALHOU (29 itens, 15 falhas)` — **exit 1**; reconfigurado → `CRM_DEV_OK (29 itens, 0 falhas)`, exit 0 |
| **D** — rollback total | `TRE_CRM_DESINSTALAR=1 reverter-crm-dev.sh` (desinstala o módulo) e aceite depois | `crm uninstalled`; `RESULTADO: CRM_DEV_FALHOU (13 itens, 3 falhas)` — **exit 1**; reinstalado do zero (`93 linhas` de log) + funil aplicado → `CRM_DEV_OK (29 itens, 0 falhas)`, exit 0 |

Idempotência: o configurador foi reexecutado com o funil já aplicado e **não** duplicou etapa
(`RESULTADO: CRM_CONFIGURADO etapas=11 removidas=3 etapa_ganho=Won pipeline=Sales`).

## 7. Rollback (executado de verdade — §6 provas C e D)

```bash
bash /opt/tre/dev/scripts/reverter-crm-dev.sh                    # nivel PADRAO (modulo continua instalado)
TRE_CRM_DESINSTALAR=1 bash /opt/tre/dev/scripts/reverter-crm-dev.sh   # nivel TOTAL (desinstala o modulo)
```

Nível **padrão**: remove as 12 etapas do funil + o time/etapa `Nurture` pelo ORM (`desfazer_funil_crm.py`) e
**repõe as etapas do próprio módulo** com os valores literais de `crm_stage_data.xml`
(`repor_etapas_padrao_crm.py`): `New` (seq 1, cor 11), `Qualified` (2, 5), `Proposition` (3, 8), `Won`
(70, 10, `is_won`), recriando também os **xmlid** do módulo (`crm.stage_lead1..4`) para o módulo voltar a ser
dono dos próprios dados → medido: `RESULTADO: CRM_PADRAO_REPOSTO etapas=4`, exit 0.

> **Medido, não suposto:** `odoo -u crm --stop-after-init` **não** repõe os dados de `crm_stage_data.xml`
> depois de apagados — com `crm_stage` vazia, o log mostra `loading crm/data/crm_stage_data.xml` e o pipeline
> **continua vazio** (`noupdate="1"` não é recriado em update). Por isso a reposição é explícita e conferida
> pelo próprio script. Sem isso, "voltar ao padrão anterior" deixaria um CRM **sem nenhuma etapa**.

A guarda que protege dado de negócio: etapa com oportunidade (`crm.lead.stage_id`) **não** é removida — o
configurador **reprova** em vez de mexer. O desfazer também recusa apagar etapa que tenha lead.

## 8. Operação do dia a dia

- **Mudar etapa/ordem/ramo:** editar `odoo/crm/funil-transformativa.yaml` → transferir → `configurar-crm-dev.sh`
  (idempotente: cria o que falta, atualiza `sequence`/`is_won`, adota a etapa `Won` do módulo e remove etapa
  não declarada) → `verificar-crm-dev.sh`. **Nenhum código muda** para trocar a representação.
- **Acompanhar:** `crm.lead` é a oportunidade; a etapa é `stage_id`; o pipeline é `team_id`. `Lost` continua
  sendo `lost_reason_id` + `active=false` (não existe etapa de perda).
- **Etapa órfã (sem pipeline)** aparece em todo pipeline — o verificador reprova isso de propósito.
- **Não rode** o configurador com oportunidades abertas em etapas que a declaração remove: o script reprova
  (fail-closed) e não mexe em dado de negócio.

## 9. Defeitos encontrados **executando** (todos consertados nesta execução)

| # | Defeito medido | Conserto |
|---|---|---|
| 1 | `docker exec` em container **parado** (o configurador parava o serviço antes de chamar o `odoo shell`) → `container is not running` | o `odoo shell` roda com o serviço de pé (o `exec` exige container rodando); o serviço só para para instalar módulo (`compose run`) |
| 2 | desenho previa **arquivar** etapa extra, mas no Odoo 19 `crm.stage` **não tem** `active` (campos medidos: `color, fold, is_won, name, requirements, rotting_threshold_days, sequence, team_ids`) | reconciliação passou a **remover** etapa não declarada, com guarda fail-closed de oportunidade |
| 3 | ler `etapa.name` **depois** do `unlink()` → erro de recordset inexistente (abortava a transação) | o nome é guardado **antes** do `unlink` e devolvido pela função |
| 4 | **`NULL \|\| '…'` colapsa a linha inteira**: etapa criada fora do módulo fica com `is_won` **NULL** (não `false`), a linha do `psql` saía **vazia** e o comparador **descartava a etapa em silêncio** — uma etapa intrusa no pipeline passava como aceite | todo campo do dump entrou em `coalesce` (`is_won` NULL lido como `false`, que é o que o ORM faz) e o comparador passou a **reprovar** linha vazia/malformada. Foi a **prova negativa B** que expôs isto — o defeito estava no próprio verificador |
| 5 | `rpad(jsonb, integer) does not exist`: `crm_stage.name` e `crm_team.name` são **JSONB** (traduzíveis) | leitura por `->>`/`jsonb_each_text` |
| 6 | item de exposição olhava a **linha inteira** do `ss` (a 5ª coluna é o *peer*, que sempre traz `0.0.0.0:*`) → falso "porta pública"; e exigia "UFW só com 22/tcp", fato que o card de TLS (`TRE-W2-E01-T02`) legitimamente muda | mede o **bind** da porta do Odoo (loopback) e a **inexistência de regra** de UFW para essa porta |
| 7 | rollback repunha o padrão via `-u crm`, que **não repõe** dado `noupdate` apagado → pipeline **vazio** depois do rollback | reposição explícita e conferida (`repor_etapas_padrao_crm.py`) — ver §7 |
| 8 | `odoo-dev` é **compartilhado**: `docker exec` morto no meio (exit **137**) quando outro card do dev reinicia o container — o rollback morria no meio | cada passo de ORM sobe o serviço, confere `HTTP 200` e tem até 3 tentativas (os três scripts de ORM são idempotentes); o verificador espera o container voltar antes de reprovar |

Defeitos 4 e 7 são exatamente o tipo de coisa que só aparece **executando**: um aceite que "passava" com uma
etapa intrusa no pipeline e um rollback que devolvia um CRM sem etapas.

## 10. Pendências declaradas (não são deste card)

1. **Validação do Anderson** (critério 1): a representação da §3 está aberta — em especial (a) time comercial
   próprio vs. time padrão `Sales`, (b) ramo `Nurture` como pipeline próprio, (c) `Lost` sem etapa.
2. **Verificação independente** (estágio 6) do artefato e da execução — quem entrega não homologa.
3. **Campos customizados** de `crm.lead`/`res.partner`: `TRE-W2-E04-T01/T02`.
4. **Exposição pública / TLS / proxy**: `TRE-W2-E01-T02` (o 80/443 já aparece nas regras de UFW, e é dele).
5. **Ratificação da versão do Odoo** (19.0): pendência herdada do card `TRE-W2-E01-T01`.
6. **Aviso `invalid addons directory '/mnt/extra-addons'`** no log do `odoo shell`: o compose monta
   `/opt/tre/repo/odoo/addons` ali e o diretório ainda não existe no container. Hoje é esperado (o módulo
   `transformativa_sales_ai` nasce no card `TRE-W2-E03-T01`), mas quem criar o módulo deve saber que o
   caminho montado é `/mnt/extra-addons`.

## 11. Guarda de ambiente alvo — o funil do dev não fica mais travado (card `t_fd769443`, 06/10/2026)

**O defeito medido.** O wrapper do dev recusava rodar se existisse container de homologação/produção
(`for nome in odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod … falhar "container de outro ambiente
existe (...) — este script so opera o dev"`). Escrito quando o dev era o único ambiente provisionado, isso
virou trava **por construção** quando os três ambientes passaram a conviver de pé: o caminho de manutenção do
funil no dev reprovava antes de tocar em qualquer coisa — medido, com os três de pé:
`FALHOU container de outro ambiente existe (odoo-homolog) — este script so opera o dev`, **exit 1**. O
verificador tinha a mesma guarda invertida e reprovava o aceite do dev por um fato que **não** é defeito do
dev: `RESULTADO: CRM_DEV_FALHOU (29 itens, 1 falha)` no item *"existe container de homologacao/producao — o
card so opera o dev (ADR-005)"*.

**A guarda nova** (desenho do espelho de homolog, `scripts/provision/configurar-crm-homolog.sh`):

| # | Guarda | Como reprova (medido) |
|---|---|---|
| 1 | caminho do compose **dentro de `/opt/tre/dev`**, canonicalizado por `realpath -m` | `FALHOU compose fora do ambiente dev (/opt/tre/homolog/compose/odoo.yml) — este script so opera /opt/tre/dev` — idem com `..` no caminho e com symlink criado dentro do dev apontando para o par de homolog |
| 2 | containers do dev presentes e **respondendo** (`odoo-dev`, `pg-odoo-dev`, `select 1` em `odoo_dev`) | `FALHOU banco banco_que_nao_existe nao responde em pg-odoo-dev` |
| 3 | nenhum `container_name` de outro ambiente no par **renderizado** (`compose config` — leitura pura, nenhuma ação no docker) | `FALHOU o par renderizado nomeia container de outro ambiente (pg-odoo-homolog) — este script so opera odoo-dev/pg-odoo-dev` |

A guarda de alvo vem **antes** das conferências de arquivo, de propósito: alvo errado reprova dizendo que é
alvo errado, e não "arquivo não encontrado" (o par de homolog existe neste mesmo VPS).
No verificador, o item de separação de ambientes passou a medir o invariante oposto — os quatro vizinhos de pé
— e continua **um** item, para o aceite seguir com **29 itens** (o contrato da §6); basta um vizinho fora do ar
para o item reprovar, e a mensagem nomeia qual.

**Aceite medido** (VPS `vmi3619453`, 06/10/2026, os três ambientes de pé):

| # | Comando | Resultado medido |
|---|---|---|
| 1 | `bash /opt/tre/dev/scripts/configurar-crm-dev.sh` (1ª rodada) | `RESULTADO: CRM_DEV_CONFIGURADO banco=odoo_dev modulo=crm etapas_declaradas=11 evidencia=/opt/tre/dev/evidencias/t_adea8e6b`, **exit 0** |
| 1 (idempotência) | 2ª rodada | mesmo `RESULTADO`, com `etapas=11 removidas=0`; `sha256(crm_stage+crm_team)` do dev **idêntico** nas quatro medições (`f77c2106…`, 12 etapas, 4 times, 2 oportunidades) |
| 2 | `TRE_ODOO_COMPOSE=/opt/tre/homolog/compose/odoo.yml bash /opt/tre/dev/scripts/configurar-crm-dev.sh` | `FALHOU compose fora do ambiente dev (…)`, **exit 1** |
| 3 | `bash /opt/tre/dev/scripts/verificar-crm-dev.sh` | `RESULTADO: CRM_DEV_OK (29 itens, 0 falhas)`, **exit 0** — item `OK vizinhos de pe e intocados por este aceite (medidos: odoo-homolog pg-odoo-homolog odoo-prod pg-odoo-prod)` |
| 3 (dente) | verificador rodado com um `docker` de mentira que esconde **um** vizinho da listagem | `FALHOU vizinho(s) fora do ar: odoo-prod` → `CRM_DEV_FALHOU (29 itens, 1 falha)`, **exit 1**; com o mesmo stub escondendo `pg-odoo-homolog`, idem; com o stub **neutro** (nada escondido) volta a `CRM_DEV_OK (29 itens, 0 falhas)` — a reprovação é da mutação, não do stub |
| 4 | identidade dos três ambientes antes/depois (`docker inspect` + SHA-256 de `crm_stage`+`crm_team`) | `odoo-homolog`, `pg-odoo-homolog`, `odoo-prod`, `pg-odoo-prod` **idênticos** (mesmo container id, mesma data de start, mesmo fingerprint `ce45836d…` / `093cb40f…`); nenhuma escrita deste card fora do dev |

**Higiene da cópia operacional.** Os dois arquivos foram publicados pelo caminho versionado
(`deploy/publicar.sh --commit 1e4f9342` → destino isolado `/opt/tre/.publicacao-t_fd769443`) e instalados a
partir do artefato em `/opt/tre/dev/scripts/` com `install -m 755`, com registro ao lado
(`/opt/tre/dev/scripts/.publicado-crm-dev`: commit, blobs `3d005c56…`/`42ac7eb9…`, modo, card, data). A versão
anterior foi guardada em `/opt/tre/dev/evidencias/t_fd769443/backup-antes/` (`a9d4da80…`/`111b5671…`).

**Evidência bruta:** `/opt/tre/dev/evidencias/t_fd769443/` — logs `T1`/`T2` (recusa antiga),
`T3`/`T4` (rodadas novas), `G1`–`G6` (provas negativas da guarda), `T8`, `T9`/`T9b`/`T9c` (dente e controle do
verificador), `estado-*.txt` com os fingerprints dos três ambientes e `sha256-instalacao.txt`.
**Verificação independente:** pendente (perfil `tester`) — quem entrega não homologa.
