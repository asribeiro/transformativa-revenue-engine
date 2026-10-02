# E2E Sales Intelligence — a cadeia da onda W4 num único banco

Card **TRE-W4-E06-T01** (W4 · EPIC E06 · P1). Aceite:
`scripts/e2e/verificar-e2e-sales-intelligence.sh` · Runbook: `docs/runbooks/e2e-sales-intelligence.md`.
Gate da onda (doc 07 §7): **"empresa → research/signals/hypothesis/contacts"**.

Este documento declara os quatro campos que a seção 2 do doc 11 exige e que o plano **não**
detalhava para este card: **ACCEPTANCE**, **TEST**, **ROLLBACK** e **RISK**. Eles foram definidos no
início da execução e registrados na thread do card antes de rodar o aceite.

## 1. Escopo: o que este aceite mede (e o que ele NÃO mede)

**Mede** o encadeamento dos cinco agentes da onda W4 — `Scout` (`E01-T01`), `Research` (`E02-T01`),
`Signal` (`E03-T01`), `Pain Hypothesis` (`E04-T01`) e `Contact Research` (`E05-T01`) — num **único
container PostgreSQL descartável**, com o que um agente escreve entrando como **entrada do
próximo**: o id da empresa criada pelo Scout é o que o Research resolve; o `research_run_id` que o
Research devolve é o vínculo que o Signal grava; o `signal_id` que o Signal devolve é o **lastro**
que a hipótese declara; a empresa resolvida é a que recebe o contato.

Cada agente já tem o seu aceite E2E próprio (num banco próprio, com fixture própria). Uma junta de
aceites verdes **não** prova encadeamento: prova que cada peça funciona sozinha. As perguntas que só
este aceite responde são as de fronteira: *o id que um agente devolve é o id que o outro resolve?*,
*o vínculo declarado entre duas tabelas de donos diferentes sobrevive à rodada seguinte?*, *repetir o
lote inteiro escreve de novo?*, *desfazer a cadeia devolve o banco ao estado inicial — inclusive as
colunas que um agente enriqueceu e o outro não escreveu?*

**NÃO mede** (declarado, nunca apresentado como OK):

- **W5 (scoring/NBA)**: `scores`, tier, `recommendations` e `outbox_events` ficam **vazios** — o
  aceite mede o vazio deles como item próprio (`cadeia-zero-em-saida-e-score`), porque a cadeia W4
  não calcula score por desenho;
- **Odoo → n8n → Titan (W3/W6)**: nada de CRM, de envio ou de resposta. O caminho ponta a ponta *da
  fundação* é o aceite do card `TRE-W3-E06-T01` (`scripts/e2e/verificar-e2e-foundation-001.sh`);
- **LLM**: nenhum dos cinco agentes tem caminho de LLM na v1 — o aceite mede
  `cadeia-nenhuma-chamada-de-llm` = 0 (`model`, `tokens_*` e `estimated_cost` nulos em toda a
  auditoria);
- **homologação e produção**: `--ambiente prod` é recusado nos cinco agentes (exit 4) e o aceite
  prova isso; promover é card próprio com aprovação humana registrada (ADR-005).

## 2. ACCEPTANCE

| # | Critério | Itens que o medem |
| --- | --- | --- |
| AC1 | os cinco agentes rodam **em sequência no mesmo banco**, cada um resolvendo o id que o anterior produziu (sem fixture de resultado pronta) | `cadeia-empresa-da-descoberta-e-a-do-relatorio`, `cadeia-pesquisa-devolve-o-id-da-rodada`, `cadeia-sinal-vincula-a-pesquisa-da-mesma-empresa`, `cadeia-hipotese-lastro-de-sinal-verdadeiro`, `cadeia-contato-na-empresa-do-scout` |
| AC2 | **Scout**: 3 candidatas → 3 empresas, status inicial, auditoria por pedido e trilha `INSERT` por empresa | `scout-rodada1-*` |
| AC3 | **Research**: enriquece coluna **vazia** e **não sobrescreve** o que o Scout escreveu; derivado declarado pela fonte é descartado com motivo | `pesquisa-rodada1-nao-sobrescreve-o-scout`, `pesquisa-rodada1-enriquece-coluna-vazia`, `pesquisa-rodada1-derivado-descartado-com-motivo` |
| AC4 | **Signal**: sinal da empresa ancora no `research_run` **daquela** empresa; vínculo com `research_run` inexistente é descartado com motivo e o fato **não** se perde | `cadeia-sinal-vincula-a-pesquisa-da-mesma-empresa`, `cadeia-sinal-run-inexistente-descartado`, `cadeia-sinal-run-invalido-nao-anexado` |
| AC5 | **Pain Hypothesis**: lastro tem de existir **e** ser da MESMA empresa — evidência de outra empresa é RECUSADA (o dente da integridade entre agentes) | `cadeia-hipotese-lastro-de-sinal-verdadeiro`, `cadeia-hipotese-lastro-de-pesquisa`, `cadeia-hipotese-evidencia-de-outra-empresa-recusada` |
| AC6 | **Contact Research**: contato na empresa da cadeia; identidade ambígua → fila humana **sem** escrever contato; empresa inexistente → RECUSADA | `contato-rodada1-*`, `cadeia-contato-casa-empresa-e-pesquisa` |
| AC7 | **zero duplicata**: repetir as cinco rodadas com as MESMAS fontes não cria linha nova e devolve o veredito de replay de cada agente | `replay-*-nao-duplica`, `replay-zero-duplicata-em-todas-as-tabelas`, `replay-re-reporta-a-ambiguidade-sem-escrever-contato` |
| AC8 | **fail-closed**: `--ambiente prod` recusado (exit 4) nos cinco, sem escrita; `--planejar` não abre conexão | `prod-recusado-nos-cinco-agentes`, `prod-nao-escreveu`, `planejar-nao-conecta-nos-cinco-agentes` |
| AC9 | **rollback da cadeia**: desfazer na ordem inversa devolve o banco ao estado inicial, preserva auditoria e fila humana e registra um `ROLLBACK` por rodada | `desfazer-*` |
| AC10 | **ambiente**: container descartável próprio, quatro containers persistentes do TRE intactos, nada em produção, código sob teste identificado (sha256) | `--manter`/inspeção + `docker ps` na evidência |

Veredito de sucesso: `ACEITE_E2E_SALES_INTELLIGENCE_001_OK` (uma linha `OK`/`FALHOU` por item).

## 3. TEST

O plano está no cabeçalho do aceite e detalhado no runbook. Em resumo: **passo 0** roda as cinco
suítes offline dos agentes no mesmo commit (diz em que commit a cadeia foi medida) → guardas (docker,
python3, migration, os cinco agentes em disco, nome do container livre) → sobe o container descartável
e aplica a migration 0001 em schema limpo → **A** Scout → **B** Research → **C** Signal → **D** Pain
→ **E** Contact → **F** estado final da cadeia → **G** replay das cinco rodadas → **H** guardas de
ambiente → **I** desfazer na ordem inversa. As fontes de C e D são **geradas a partir dos relatórios
de B e C** (é isso que torna o encadeamento medido, e não narrado).

Prova de dente (`--prova-de-dente`): baseline verde primeiro e, depois, **uma mutação por agente**
(5 no total), aplicada em cópia do código sob teste, cada uma exigindo que o aceite reprove **o item
esperado** — não basta "o aceite falhou". Mutação que não se aplica na âncora reprova por buraco de
verificação.

## 4. ROLLBACK

O rollback da cadeia é exercitado **no próprio aceite**, na ordem inversa da escrita (contato →
hipótese → sinal → pesquisa → scout), com `--desfazer <correlation_id> --confirmo` de cada agente:

- **contato**: dry-run por padrão (não apaga); com `--confirmo`, restaura as colunas enriquecidas,
  apaga os contatos **criados** pela rodada e os `sync_events` deles, e **recusa** a rodada cujo
  contato já foi espelhado no CRM (fail-closed);
- **hipótese / sinal**: apaga o que a rodada criou e os `sync_events` dela, registra `ROLLBACK`, sem
  tocar `agent_runs`, `human_approvals`, `research_runs`, `signals` nem `organizations`;
- **pesquisa**: restaura as colunas que a rodada enriqueceu (valor anterior vem do `sync_events` da
  própria rodada) e apaga os `research_runs` da rodada;
- **scout**: apaga as organizações criadas pela correlação que continuam `DISCOVERED` e os
  `sync_events` de identidade.

O aceite mede o estado final: `0|0|0|0|0|<PENDING>` nas cinco tabelas de negócio, `agent_runs`
preservada (auditoria não se apaga), fila humana preservada e **um `sync_events` de `ROLLBACK` por
rodada**. A ordem importa: contato e pesquisa referenciam a empresa por FK, então a empresa só pode
sair no fim.

## 5. RISK

| Risco | Como o aceite se protege |
| --- | --- |
| **Falso verde por fixture pronta**: semear o resultado de um agente e "medir" o próximo | as fontes de C e D são **geradas** dos relatórios de B e C; o id de cada empresa vem do relatório do Scout; os itens conferem que o id do relatório existe no banco |
| **Medir o próprio alvo**: agente rodando num banco com estado de rodada anterior | cada rodada do aceite começa com `DROP SCHEMA … CASCADE` + migration; o container é novo e tem nome próprio (`pg-e2e-si-acc`); se o nome já existir, o aceite **aborta** em vez de mexer no que não é dele |
| **Tocar ambiente de outra pessoa** | só o container descartável é criado/removido; `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` são verificados de fora, antes e depois |
| **Foto de contagens que mente** | a foto usa `||` (concatenação) — com `|` sozinho o SQL viraria OR bit a bit e o item compararia um número sem sentido consigo mesmo (defeito medido nesta rodada) |
| **Fila humana contada como "duplicata"** | a ambiguidade de identidade é **re-reportada** a cada rodada que a encontra (comportamento do agente, medido) e **nada** de contato é escrito: o item mede as duas coisas juntas, em vez de fingir zero duplicata na fila |
| **Id fixo no teste** | nenhum `organization_id`/`research_run_id`/`signal_id` é chumbado: todos vêm do relatório da rodada anterior |
| **Container pendurado / porta** | espera por `SELECT 1` funcionando **duas vezes** (o `pg_isready` mente no início), `trap` de limpeza e `--manter` para inspeção |
| **Mutação que não morde** | o dente exige o **item esperado** em `FALHOU` (5 mutações, uma por agente) e reprova mutação não aplicada |

## 6. Lacunas declaradas

- **W5 não existe ainda**: nenhum score/tier/NBA é calculado — os itens de zero são a medida do
  escopo, não uma promessa de que a cadeia "fecha o funil".
- **Sem LLM, HTTP ou crawler**: os cinco agentes leem uma fonte local; o aceite mede a ausência de
  modelo/tokens/custo na auditoria.
- **Sem Odoo/Titan**: o espelhamento no CRM e o envio são W3/W6.
- **Homologação não é feita aqui**: quem entrega não homologa (estágio 6 é o perfil `tester`, estágio
  7 é o Anderson).

## 7. Como citar este veredito

> *A cadeia W4 (Scout → Research → Signal → Pain Hypothesis → Contact Research) fecha num único
> banco, encadeando id produzido por um agente como entrada do próximo, sem duplicar quando o lote é
> repetido, fail-closed em produção e com rollback que devolve o banco ao estado inicial. Ela **não**
> calcula score (W5), **não** fala com Odoo/Titan (W3/W6) e **não** é homologada.*
