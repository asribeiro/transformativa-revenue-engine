# Tiering v1 (`tiering-v1`) — contrato do card TRE-W5-E06-T01

**Card:** TRE-W5-E06-T01 · **Onda:** W5 · **Épico:** E06 · **Prioridade:** P1
**Depende de:** W5-E05-T01 (Priority Score — a ENTRADA deste card)
**Artefatos:** `hermes/scores/tiering/tiering.py`, `hermes/scores/tiering/score-tiering-v1.json`,
`scripts/scores/verificar_score_tiering.py`, `scripts/scores/teste_tiering_aceite.sh`

## 1. O que o componente é

O TIERING classifica a empresa na **faixa de tier** do Data Contract: lê o **último** score `PRIORITY`
já gravado em `sales_intelligence.scores`, aplica as faixas de `scores.tiers` e grava a classificação
no **registro auditado da rodada**.

| Faixa (do contrato) | Intervalo |
|---|---|
| `A+` | 90,00 – 100,00 |
| `A` | 80,00 – 89,99 |
| `B` | 65,00 – 79,99 |
| `C` | 50,00 – 64,99 |
| `Nurture` | 0,00 – 49,99 |

As faixas **não existem em forma executável no código**: são lidas de
`docs/data/data_contract_v1.json#scores.tiers` a cada rodada, e a cobertura da escala (0 a 100, sem
lacuna e sem sobreposição) é conferida antes de classificar. Mudar faixa = versão nova do contrato.

## 2. Onde o tier é persistido (e por que não em `scores`)

- **`sync_events`** — o REGISTRO: `operation='TIER'`, `entity_type='organization'`,
  `source_version='tiering-v1'`, `idempotency_key` única e `request_payload` com o tier, a faixa
  usada, a tabela de faixas vigente, a escala, a identidade do PRIORITY lido e o modelo.
- **`agent_runs`** — a auditoria da rodada (a RECUSA também é auditada, `REJECTED`).

**Nada em `scores`.** `scores.types` do contrato lista cinco tipos e `scores` não tem coluna de tier:
criar uma sexta linha de tipo ou uma coluna é **mudança de contrato** (governança §10 + ADR-0004) e
exige aprovação humana registrada — decisão do dono, declarada como lacuna, não feita pelo worker. A
guarda de escrita **recusa** qualquer INSERT/UPDATE/DELETE em `scores`. Mesmo espírito do precedente
`entity_match_confidence` (TRE-W1-E04-T02).

## 3. ACCEPTANCE (AC)

| AC | Critério | Como foi medido |
|---|---|---|
| AC1 | Tier A+/A/B/C/Nurture derivado das **faixas do contrato**, nunca do código | suíte `C2`, `C3`, `C4` (AST: limite/nome de faixa no código reprova) + aceite `A1` |
| AC2 | Fronteiras fechadas dos dois lados (90→A+, 89,99→A, 79,99→B, 65→B, 49,99→Nurture, 0→Nurture, 100→A+) | suíte `N1` + aceite `A7` (65,00→B) |
| AC3 | Empresa **sem PRIORITY RECUSA** (`SEM_PRIORITY`) e **não registra nada** — Nurture não é default | suíte `N2`, `S5` + aceite `A5` + dente `ausencia-vira-registro` |
| AC4 | PRIORITY **vencido** RECUSA (`PRIORITY_VENCIDO`); o vencimento é **lido do banco** | suíte `N3` + aceite `A6` + dente `sem-checagem-de-vencido` |
| AC5 | O tier é do **último** PRIORITY (não da média do histórico) | suíte `S1` + aceite `A7` + dente `ultimo-vira-primeiro` |
| AC6 | Replay da mesma entrada **não duplica**; PRIORITY novo gera **registro novo** e preserva o anterior | suíte `I2` + aceite `A4` + dente `sem-idempotencia` |
| AC7 | A rodada **não toca** `scores` (nem cria `score_type='TIER'`) nem `organizations` | suíte `G2` + aceite `A2` (impressão digital md5 dos scores) + dente `guarda-libera-escrita-em-scores` |
| AC8 | `prod` recusado (exit 4) **sem escrita**; `--planejar` e `--faixas` **não abrem conexão** | aceite `A3` + suíte `S9`, `S10`, `S11` |
| AC9 | Empresa inexistente RECUSADA sem registrar, com a recusa **auditada** | suíte + aceite `A8` |
| AC10 | Guarda de escrita: DDL, `scores`, `agent_runs` (UPDATE) e escrita sem a operation `TIER` são recusados | suíte `G1`–`G5` (10/10 mutações do autoteste) |
| AC11 | Desfazer: dry-run não apaga; `--confirmo` apaga **só** o registro da rodada; auditoria preservada | aceite `A9` |
| AC12 | Determinístico: Decimal, 2 casas, sem LLM e sem rede | suíte `N6` + aceite `A1` (tokens/modelo nulos) |
| AC13 | Contrato incoerente (faixas com lacuna/sobreposição) **RECUSA** em vez de classificar | suíte `C5` + dente `fronteira-aberta` |

## 4. TEST

- **Suíte offline** (sem banco, sem rede): `python3 scripts/scores/verificar_score_tiering.py`
  → `VERIFICACAO_TIERING_OK (26 itens, 0 falhas)`; `--autoteste`
  → `AUTOTESTE OK (10/10 mutações detectadas, cada uma pelo item esperado)`.
- **Aceite E2E** (PostgreSQL descartável `pg-tier-acc`, `postgres:16`, migration 0001 do zero):
  `bash scripts/scores/teste_tiering_aceite.sh --raiz <raiz>` e `--prova-de-dente` (mutações em cópia
  do módulo exigindo o **item esperado**). O container é criado e removido pelo próprio aceite; nada é
  escrito em `dev`, `homolog` ou produção.
- **Portão de estrutura:** `bash scripts/verificar_estrutura.sh` (o gate da W5 cobre os artefatos
  deste card).

## 5. ROLLBACK

- **Desfazer de rodada:** `--desfazer <correlation_id>` (dry-run) e `--confirmo` — apaga **só** as
  linhas de `sync_events` que a rodada criou (ancoradas no `output` de `agent_runs`), preservando a
  auditoria e os scores.
- **Desfazer de versão:** `source_version='tiering-v1'` é história; voltar à versão anterior é rodar o
  código anterior (registro novo). Nenhum caminho faz `UPDATE` em `scores`.
- Nada em produção: o componente não é executável em `prod` (exit 4, sem escrita). Rollback de
  ambiente é do card de release.

## 6. RISK

| Risco | Mitigação medida |
|---|---|
| Tier inventado para empresa sem evidência (Nurture como default) | fail-closed: sem PRIORITY RECUSA com motivo nominal; Nurture só com score 0–49,99 (aceite `A5`) |
| Evidência velha classificando | `PRIORITY_VENCIDO`: `valid_until` lido do banco (dente reprova a mutação que desliga a checagem) |
| Faixa divergir do contrato | faixas lidas do Data Contract + conferência de cobertura a cada rodada; suíte reprova limite escrito no código |
| Criar casa física para o tier sem decisão registrada | nada em `scores`; persistência só no registro auditado; a lacuna está declarada para o dono (contrato 1.1) |
| Média do histórico mascarar o tier atual | leitura do último PRIORITY (`calculated_at DESC, id DESC`), provada por dente |
| Mutação inerte na hora de provar | autoteste do aceite exige o **item esperado** de cada mutação |

## 7. Limites declarados

- O Data Contract V1.0 **não define casa física** para o tier. Este card grava no registro auditado
  (`sync_events.request_payload` + `agent_runs`) e **não cria coluna/score_type**. Proposta ao dono:
  `organizations.tier` (contrato 1.1) ou `score_type='TIER'` — **decisão do Anderson (estágio 7)**.
- A política de ausência (sem PRIORITY ⇒ sem tier) é **proposta a homologar**.
- O tier reflete o último PRIORITY e **não vence por si**: quem vence é o PRIORITY (30 dias, card E05).
- **Fora do card:** Next Best Action (W5-E07-T01), evento de outbox `COMPANY_QUALIFIED` e espelhamento
  de `tf_tier` no Odoo (W3/W6), aceite E2E da cadeia (W5-E08).
- Não é homologação: quem entrega não homologa; o veredito é do estágio 6 (revisão independente, perfil
  `tester`) e a homologação (estágio 7) é do Anderson.
