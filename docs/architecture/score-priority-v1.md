# Priority Score v1 (`priority-v1`) — contrato do card TRE-W5-E05-T01

**Card:** TRE-W5-E05-T01 · **Onda:** W5 · **Épico:** E05 · **Prioridade:** P1
**Depende de:** W5-E01-T01 (ICP), W5-E02-T01 (Automation Fit), W5-E03-T01 (Buying Signal), W5-E04-T01 (Data Quality)
**Artefatos:** `hermes/scores/priority/priority_score.py`, `hermes/scores/priority/score-priority-v1.json`,
`scripts/scores/verificar_score_priority.py`, `scripts/scores/teste_priority_aceite.sh`

## 1. O que o componente é

O PRIORITY é o **agregador** da W5: lê o **último** score de cada um dos quatro componentes já
gravados em `sales_intelligence.scores`, aplica a fórmula do **Data Contract V1.0 §8** e grava a
linha `score_type='PRIORITY'`, `score_version='priority-v1'`.

```text
PRIORITY = 0.35 * ICP + 0.30 * AUTOMATION_FIT + 0.25 * BUYING_SIGNAL + 0.10 * DATA_QUALITY
```

Os **pesos são lidos do Data Contract** (`scores.priority_weights`) — nenhum peso existe em forma
executável no código (item `C3` da suíte reprova se aparecer um). Mudar peso = versão nova do
contrato (1.1), decisão do dono.

## 2. ACCEPTANCE (AC)

| AC | Critério | Como foi medido |
|---|---|---|
| AC1 | PRIORITY gravado com `score_type=PRIORITY`, `score_version=priority-v1`, `inputs` e `explanation` | aceite E2E `A1`+`A9` (banco descartável) |
| AC2 | Valor = fórmula do contrato (0,35*94 + 0,30*76 + 0,25*83 + 0,10*100 = **86,45**) | aceite E2E `A1` (valor conferido no banco) + suíte `N1` |
| AC3 | Componente **ausente** RECUSA (`SEM_LASTRO_COMPLETO`) e **não escreve** — não há renormalização | aceite E2E `A5` + suíte `N2`, `N3` |
| AC4 | Componente **vencido** (`valid_until` no passado) conta como ausente; vencimento é lido do banco | aceite E2E `A6` + suíte `N4`, `N5` |
| AC5 | Replay da mesma entrada **não duplica**; componente novo gera **linha nova** e preserva a anterior | aceite E2E `A4` + suíte `I1`–`I3` |
| AC6 | A rodada **não toca** os scores dos componentes nem `organizations` | aceite E2E `A2` (impressão digital md5 dos componentes) |
| AC7 | `prod` recusado (exit 4) **sem escrita**; `--planejar` **não abre conexão** | aceite E2E `A3` + suíte `S9`, `S10` |
| AC8 | Empresa inexistente RECUSADA **sem escrever** score, com a recusa **auditada** (`REJECTED`) | aceite E2E `A7` + suíte `S5` |
| AC9 | `valid_until = calculated_at + 30 dias` (política de validade **deste** card) | aceite E2E `A9` + suíte `S3` |
| AC10 | Guarda de escrita: DDL, `UPDATE` em `scores`, outro `score_type` no INSERT e escrita nas tabelas de entrada são **recusados** | suíte `G1`–`G7` (12/12 mutações do autoteste) |
| AC11 | Desfazer: dry-run não apaga; `--confirmo` apaga só a rodada; auditoria preservada | aceite E2E `A8` + suíte `S7` |
| AC12 | Tudo determinístico: Decimal, `ROUND_HALF_UP`, 2 casas; sem LLM e sem rede | suíte `N7`, `N8` + aceite E2E (`tokens`/`model` nulos) |

## 3. TEST

- **Suíte offline** (sem banco, sem rede): `python3 scripts/scores/verificar_score_priority.py`
  → `VERIFICACAO_PRIORITY_SCORE_OK (35 itens, 0 falhas)`; `--autoteste`
  → `AUTOTESTE OK (12/12 mutações detectadas, cada uma pelo item esperado)`.
- **Aceite E2E** (PostgreSQL descartável `pg-priority-acc`, `postgres:16`, migration 0001 aplicada do zero):
  `bash scripts/scores/teste_priority_aceite.sh --raiz <raiz>` e `--prova-de-dente` (mutações em cópia
  do módulo exigindo o **item esperado**). O container é criado e removido pelo próprio aceite; nada é
  escrito em `dev`, `homolog` ou produção.
- **Portão de estrutura:** `bash scripts/verificar_estrutura.sh` → `PASS (0 falhas)`.

## 4. ROLLBACK

- **Desfazer de rodada:** `--desfazer <correlation_id>` (dry-run) e `--confirmo` — apaga **só** as
  linhas de `scores` que a rodada criou (ancoradas no `output` de `agent_runs`), preservando os scores
  dos componentes, a auditoria e a trava de idempotência.
- **Desfazer de versão:** `score_version` é histórico; voltar a versão anterior é recalcular com o
  código anterior (linha nova). Nenhum caminho faz `UPDATE` em `scores`.
- Nada em produção: o componente não é executável em `prod` (exit 4, sem escrita). Rollback de
  ambiente é do card de release.

## 5. RISK

| Risco | Mitigação medida |
|---|---|
| Prioridade calculada sobre evidência parcial (inflaria o ranking) | fail-closed: cobertura mínima 1,00; ausente/vencido RECUSA com motivo nominal |
| Componente obsoleto sustentando prioridade para sempre | política de validade deste card: PRIORITY vence em 30 dias; `valid_until` do componente vencido é lido do banco |
| Empate/duplicata de replay em retry | idempotência pela ENTRADA (identidade dos componentes), não pelo relógio |
| Guarda de escrita de outro agente valendo como deste | porta própria + `validar_sql` deste card (recusa `UPDATE`/DDL/outro `score_type`) |
| Mutação inerte na hora de provar | autoteste do aceite exige o **item esperado** de cada mutação |

## 6. Limites declarados

- A **política de validade do score** nasce aqui (30 dias) — o ICP deixou `valid_until` NULL
  declarando que a política era deste card. É **proposta a homologar** (estágio 7, Anderson).
- Só o BUYING_SIGNAL grava `valid_until` hoje: ICP, AUTOMATION_FIT e DATA_QUALITY ficam NULL e não
  vencem por si — consequência declarada.
- A cobertura mínima 1,00 (sem renormalização) é política proposta; `priority-v2` fica para o dono.
- **Fora do card:** tiering `A+/A/B/C/Nurture` (W5-E06-T01), Next Best Action (W5-E07-T01), evento de
  outbox `PRIORITY_SCORE_CHANGED`/`COMPANY_QUALIFIED` (W3) e o aceite E2E da cadeia (W5-E08).
- Não é homologação: quem entrega não homologa; o veredito deste card é do estágio 6 (revisão
  independente, perfil `tester`) e a homologação (estágio 7) é do Anderson.
