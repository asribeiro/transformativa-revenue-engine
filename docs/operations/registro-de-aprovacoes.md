# Registro de aprovações humanas

Obrigatório para toda ação sensível (`hermes/policies/human-approval.yaml`): quem aprovou, quando, o que foi
aprovado e qual é a evidência. Sem valor de credencial, nunca.

## Como citar evidência (regra, 30/09/2026)

Uma linha deste registro cita **identidade imutável**: o verificador, o **commit** e o **sha256** do arquivo
naquele commit. A contagem de itens é **leitura datada daquele commit**, nunca medida viva — contagem solta
envelhece no commit seguinte e vira afirmação falsa (foi o que aconteceu nas linhas da v1.1 e da v1.2, ambas
corrigidas abaixo).

- linha que cita verificador **sem commit e sem sha256** = registro inválido: o verificador
  o verificador de registro (script `verificar_registro_de_aprovacoes.py`, em `scripts/`) reprova;
- **editar verificador já homologado é evento registrável**: quem, quando, o quê, por quê e com verificação
  independente (perfil `tester`). Sem isso, a edição não tem contrapartida na trilha;
- número vivo pertence à saída do verificador e aos documentos de `docs/validation/` — não a este registro.

| Data | Aprovado por | O que | Evidência |
|---|---|---|---|
| 29/09/2026 | Anderson Ribeiro | Matriz de permissões Dev Harness × Sales AI e matriz de segregação de credenciais + gatilhos de rotação (política de secrets V1) | comentários nos cards `TRE-W0-E01-T02` e `TRE-W0-E02-T01` (board `transformativa-revenue-engine`) |
| 29/09/2026 (19:36 BRT, Telegram) | Anderson Ribeiro | Postura do roteador JEV para ação não classificada (defeitos `TRE-W0-E04-T02-D07`/`-D08`): **manter o critério estrito** — texto livre sem código canônico conhecido não executa, escala sempre — e **revisitar só depois do T05** (dispatch passar `acao_codigo`); fica valendo a regra de que, se afrouxar for preciso, a única forma aceita é nomear mais um código comum na política, nunca voltar a casar prosa | `docs/validation/jev-guardrails-e-fallback.md` §8.9 (veredito + palavra do dono), impacto medido em `scripts/analisar_impacto_de_afrouxar.py`, **no commit `bbb4fd9`** (sha256 `308cf574e5fadc299aabbc9f8525fd96e3c70b8b88ba1053f8b7ebad81a5545e`): afrouxar compraria 4 de 32 casos do corpus, nenhum ganho nos 9 bloqueios — números são leitura daquela data, comentários nos cards `t_83242193` (D07) e `t_4200e054` (D08) |
| 29/09/2026 (Telegram) | Anderson Ribeiro | Seguir com o `TRE-W0-E04-T05` (ligar o roteador JEV ao dispatch do board), com o contrato do encaixe registrado no corpo do card **antes** de liberá-lo | comentário de unblock no card `t_6d326367`; contrato no corpo do card; execução provada em `docs/validation/jev-gate-no-dispatch.md` (suite `scripts/verificar_gate_jev.py`; a contagem de 28 itens daquela data **não se reproduz do container** — no commit `477bc33` a suíte aborta com `PermissionError` no overlay, que é o defeito D02 corrigido em `59832b2`; a medida reproduzível é, no commit `9a2b64c`, **30 itens PASS**, sha256 `da42fae48c23b52b8617f3bb6723264b54a13215eb8a98ec095094dcc52901b0`). **Pendente de ato do operador (root):** `bash deploy/hermes/aplicar_gate_jev.sh` aplica o encaixe em `/opt/hermes` + reiniciar o despachante — a autorização para ligar o roteador à execução automática é dele |
| 29/09/2026 (22:18 BRT; 30/09/2026 01:18 UTC, Telegram: "de acordo, homologado") | Anderson Ribeiro | **Política JEV v1.1 homologada** (rascunho do `TRE-W0-E04-T06`): chave explícita `limiares.lane_conservadora: high`; `regra_de_lane_por_ambiente` — DDL/migration em ambiente novo/dev = piso `high`, em ambiente vivo/produção = piso `critical` com aprovação humana registrada, ambiente não declarado cai no ramo conservador, e o piso só eleva lane; instrumentação das métricas declaradas (`custo_por_lane`, `latencia_por_lane`) | `hermes/jev/policy_v1_1.yaml` (`versao: jev-policy-v1.1`, ainda `estado: rascunho-nao-homologado` por desenho), `docs/architecture/jev-decision-policy-v1.1.md` §0, `docs/validation/jev-politica-v1.1-e-metricas.md`; verificador `scripts/verificar_jev_policy_v1_1.py` **no commit `b7f2aa7`** (o commit desta linha): 122 itens, 0 falhas, sha256 `ef16f4354f58e11be9a12d0efa0db7b192ee7be41882af02d54c6e3e7ff570c8` — e o autoteste **daquele commit reportava buraco**: 32/33 mutações detectadas (a mutação *"rascunho se declara homologado sem registro"* passava em silêncio → veredito FALHOU). O buraco foi encontrado e fechado no ciclo do T09: no commit `1227310` o mesmo verificador mede 155 itens PASS e 64/64 mutações (sha256 `35457cb193e6ec4e5ec9bbc4eae5392b252d2f181c6069bdbf323d93167e42ef` do verificador naquele commit). **Correção de registro (30/09/2026):** esta linha afirmava "33/33 mutações detectadas" — número que não se reproduz no commit citado; a evidência acima é a medida; implementação do piso no roteador e abertura do portão de versão = card `t_d36c7d0f` (renumerado para TRE-W0-E04-T08) |
| 30/09/2026 (Telegram: "homologado, de acordo") | Anderson Ribeiro | **Política JEV v1.2 homologada e EM VIGOR** (card `TRE-W0-E04-T09`): (1) **aposentadoria do classificador de card** — `classificar_card` sai do caminho de decisão e fica versionado como linha de base histórica do benchmark; (2) **lane declarada por código canônico** (`lane_por_codigo_de_acao`), mapa inicial conservador — `ajuste_de_texto`, `consulta_interna`, `operacao_comercial` e `migracao_de_esquema` em `high`; limiar de confiança não se aplica a lane declarada (limiar julga estimativa); código sem lane declarada abstém; piso por ambiente e override humano seguem só elevando | `hermes/jev/policy_v1_2.yaml` (`versao: jev-policy-v1.2`, `estado: em-vigor`), `docs/architecture/jev-decision-policy-v1.2.md`, roteador `jev-router-v1.2` (`CAMINHO_POLITICA_PADRAO` → v1.2); verificador `scripts/verificar_jev_policy_v1_2.py --autoteste` = 233 itens PASS e 8/8 mutações detectadas **no commit `1227310`** (sha256 `cbd00dd455c540c6f69af8f93e478c67c13941b0c3d2dd2afdbabb39b9b08e8a` do verificador naquele commit); baterias herdadas: v1.1 155 PASS (sobre a v1.1, arquivo intocado), v1.0 42 PASS; roteador 63 PASS, guardrails 74 PASS, gate 30 PASS, benchmark autoteste 23/23 (todos medidos em 30/09/2026, no commit `1227310`); medição no corpus v1.4 (`hermes/jev/benchmarks/`): lane 0,375 (= constante estrutural, o classificador empatava), execução 3,1% → 12,5% dos casos e escalação 78,1% → 68,8%; medição do empate: 0,1875 isolado do estimador |
| 30/09/2026 (Telegram: "aprovo") | Anderson Ribeiro | **Dominio sensivel: casamento EXATO (card `TRE-W0-E04-T11`) — opcao (A) aprovada**: refinar o casamento do texto em vez de reescrever o card. A inferencia de dominio deixa de herdar `CONCEITOS_DE_ACAO` (que tolera PREFIXO de 4 caracteres e fazia "implementador" ~ "implantacao", "versionado" ~ "versao", "entrar" ~ "entrega", "entrega" de projeto ~ release e "registro" de artefatos ~ dado de cliente) e passa a usar vocabulario proprio e EXATO (`TERMOS_DE_DOMINIO_SENSIVEL`). `CONCEITOS_DE_ACAO` e `REGRAS_DE_ACAO_HUMANA` ficam INTOCADAS: acao exclusiva continua exigindo humano, e o fail-closed do D07 nao foi afrouxado. Custo aceito: mexer em guardrail, com esta linha datada e verificacao independente pelo perfil `tester` | verificador `scripts/verificar_dominios_sensiveis.py` no commit `e085ffc` (sha256 `a40b9871e105957a1e06d90919e61b92e2fbeace52900943f468456ed03ca62b`) = 28 itens PASS + autoteste 3/3; roteador `hermes/jev/routing/router.py` no commit `e085ffc`; baterias medidas no mesmo ponto: roteador 63, guardrails 74, gate 30, v1.2 234, v1.1 155, v1.0 42, benchmark autoteste 23/23 — todas PASS; efeito no card: gate do T11 passou de ESCALATE para PASS e o card voltou a `ready` |
| 30/09/2026 (fechamento do card `TRE-W0-E04-T10`) | Anderson Ribeiro (regra "editar verificador homologado e evento registravel", aprovada em 30/09/2026) | **Edicao dos verificadores homologados pelo commit `83672fd` (T10)** — registro do EVENTO, como a regra manda. A entrega do T10 editou os DOIS verificadores de politica ja homologados, sem mudar a contagem de itens: verificador `scripts/verificar_jev_policy_v1_2.py` = 233 itens no commit `d6e510c` (sha256 `cbd00dd455c540c6f69af8f93e478c67c13941b0c3d2dd2afdbabb39b9b08e8a`) e 234 itens no commit `83672fd` (sha256 `ad4f9acb63da33932ede2f088c5b25ce5ef1bd02d0d2f84a096d0b89da086362`); verificador `scripts/verificar_jev_policy_v1_1.py` = 155 itens nos dois pontos: no commit `d6e510c` (sha256 `35457cb1e6ec4e5ec9bbc4eae5392b252d2f181c6069bdbf323d93167e42ef`) e no commit `83672fd` (sha256 `ddf9fa3d921efa4519da5f8f5072f9920a53bf8cc04019c1a8222b23457a7c06`). Conteudo novo com contagem igual: o que mudou foram os itens DATADOS, para a contagem deixar de ser numero vivo. Nada de credencial, nada tocado em producao. | verificacao independente: perfil `tester`, card `t_e09a95bf`, rodada 1 — **APROVADO**, base `develop` HEAD `9a2b64c` (contem `83672fd`), itens conferidos um a um com execucao propria (log bruto em `/opt/data/profiles/tester/cache/scratch/revisao_t10_bateria.log`); a linha da v1.2 neste registro foi corrigida no commit `1b27549` (233 itens no commit que a homologou) |
| 30/09/2026 (continuacao aprovada por Anderson Ribeiro: "pode continuar a implementacao") | Anderson Ribeiro (regra "editar verificador homologado e evento registravel") | **Divergencia do T12 fechada: singular E plural no vocabulario exato de dominio** (commit `363e499`). A revisao independente do card `TRE-W0-E04-T12` reproduziu a mudanca do vocabulario exato mas achou divergencia: casamento exato nao infere plural, e o prefixo antigo aceitava ("credenciais" ~ "credencial") — "rotacionar credenciais" e "dados de clientes" deixariam de declarar dominio. Agora o vocabulario traz as duas formas (producao/producoes, release/releases, credencial/credenciais, senha/senhas, cliente/clientes, lead/leads, proposta/propostas etc.). Nada de credencial, nada tocado em ambiente vivo. | verificador `scripts/verificar_dominios_sensiveis.py` no commit `363e499` (sha256 `a68411f87a632d1dc5aa8f8eef4756b9b79d6c167586625d91dfbc93506540a5`) = 32 itens PASS + autoteste 4/4 (inclui mutacao que tira os plurais); roteador `hermes/jev/routing/router.py` no mesmo commit `363e499`; baterias no mesmo ponto: roteador 63, guardrails 74, gate 30 — PASS; achado original registrado no card `t_0a236da5` (revisao T12), que reproduziu os itens 1, 2 e 5 e marcou o item 3 como reproduzido em parte |

## 30/09/2026 — APROVAÇÃO REGISTRADA (canal: telegram)

- **Card:** `TRE-W1-E01-T01` (`t_969affa7`) — criar database/schema Sales Intelligence (dev).
- **Aprovador:** Anderson Ribeiro — palavra dada no Telegram em 30/09/2026: "aprovo".
- **Escopo:** nenhum domínio sensível (`dominios_sensiveis` vazio); a escalada é de lane (DDL no texto). Ambiente alvo: desenvolvimento.
- **Vínculo:** hash do texto aprovado `8054672773fdd4d62115213b935813d110e9a250639d8bc7fe1da0413f0d73fc`.
- **Validade:** 07/10/2026 (7 dias, decisão 3).
- **Limite:** aprova a escalada deste card e só ela — não abre credencial, não autoriza ato em produção, não dispensa passo de operador.
- **Contrapartida:** revisão independente do `tester` e verificação do gate.

## 30/09/2026 — APROVAÇÃO DE ONDA `W1-dev` (canal: telegram)

- **Onda:** `W1-dev` — cobertura por regra para cards de **desenvolvimento**.
- **Aprovador:** Anderson Ribeiro — palavra no Telegram em 30/09/2026: "aprovo".
- **Regra:** card com `ambiente_alvo` de desenvolvimento, `sinais: {producao: false, credencial: false}` e sem domínio de credencial ou dado de cliente. O hash do texto é fixado no momento da execução.
- **Validade:** 07/10/2026 (7 dias).
- **Fora da onda:** ambiente vivo, credencial e dado de cliente — item a item, com commit do aprovador.

## 30/09/2026 — DECISÃO DO DONO: trio canônico de banco/container/usuário (card `t_27d0a51d`)

- **Decisão (opção 3), por Anderson Ribeiro no Telegram:** congelar o arranjo atual no contrato e
  padronizar homolog/produção com o mesmo trio.
- **Trio canônico:** container `pg-<amb>` (dev: `pg-sales-dev`), banco `sales_intelligence`,
  usuário dedicado por ambiente (dev: `sales_ai`), schema `sales_intelligence`.
- **Efeito:** o contrato deixa de citar `transformativa_ai` (corrigido por esta mudança registrada
  em `docs/data/` + nota datada no cabeçalho da migration 0001). Nenhuma DDL muda, nenhum dado é
  migrado, nenhum ambiente é reprovisionado.
- **Vale para:** W1/W2 seguintes, `TRE-W1-E05-T01` (suíte de banco), `TRE-W1-E06-T01`
  (backup/restore) e o provisionamento de homolog/produção.

## 30/09/2026 — DECISAO DO DONO: isolamento entre clientes (card `t_e340c29b`, AC2 do E05)

- **Aprovador:** Anderson Ribeiro, palavra no Telegram em 30/09/2026 ("A").
- **Decisao:** opcao A — isolamento fisico, um banco por cliente. O AC2 deixa de ser
  "consulta sem filtro de tenant nao devolve dado de outro cliente" e passa a ser
  "nao existem dois clientes no mesmo banco", que e testavel com o ambiente atual.
- **Canal:** telegram (card de documentacao de contrato em dev; nao toca ambiente vivo, credencial
  nem dado de cliente).
- **Efeito:** contrato atualizado (`docs/data/DATA_CONTRACT_V1.md`), card de decisao fechado e
  `TRE-W1-E05-T01` (t_c7281fce) liberado para fechar o AC2 na forma nova.

## 30/09/2026 — DECISAO DO DONO: realinhar o registro da migration 0001 em dev (card `t_39838c5b`)

- **Aprovador:** Anderson Ribeiro, palavra no Telegram em 30/09/2026 ("1").
- **Contexto:** o sha256 do arquivo `db/migrations/0001_sales_intelligence_v1.sql` mudou de
  `bc766a818943…` para `0484a3701b8c…` porque o **proprio agente** acrescentou a nota datada do trio
  canonico no cabecalho do arquivo (commit `c795677`). O runner passou a falhar em dev.
- **Prova de que o schema nao mudou:** `pg_dump --schema-only` em dois containers descartaveis
  (arquivo atual x arquivo sem o comentario) — diferenca apenas nos tokens internos do dump.
- **Ato executado em dev (com evidencia):** `UPDATE ... WHERE versao='0001' AND sha256=<sha antigo>`
  -> `UPDATE 1`; registro DEPOIS = `0484a3701b8c…`; e o runner na copia operacional respondeu
  `MIGRACAO_OK (--somente-checar; 0 aplicada(s), 1 pulada(s), 4 itens, 0 falhas)`, exit 0.
- **Regra que passa a valer:** secao "Realinhamento de registro de migration" em
  `docs/runbooks/aplicar-migracoes.md`.
