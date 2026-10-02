# Score Data Quality v1 — TRE-W5-E04-T01

Contrato de arquitetura do **Data Quality Score V1** (onda W5, épico E04). O documento existe para
quem **operar**, **revisar** ou **citar** o veredito deste card ter onde conferir o escopo: o que o
score mede, o que ele deliberadamente não faz, como se prova e como se desfaz.

- **Artefatos:** `hermes/scores/data_quality/data_quality.py` (código),
  `hermes/scores/data_quality/score-data-quality-v1.json` (contrato do score legível por máquina),
  `scripts/scores/verificar_score_data_quality.py` (suíte offline + autoteste por mutação),
  `scripts/scores/teste_data_quality_aceite.sh` (aceite em PostgreSQL descartável).
- **Dependência:** `TRE-W4-E06-T01` (cadeia E2E Sales Intelligence) — o score mede o dado que a W4
  produz (`organizations` + `research_runs`).
- **Fonte da verdade do card:** baseline V1.1.0 docs 03 §3, 04 §3/§6/§8, 06 §7, 07 §8, 12 §1/§8 e
  `docs/data/DATA_CONTRACT_V1.md` §8.

## 1. O que é medido (e por que assim)

O doc 03 §3 define o Data Quality Score como *"confiabilidade e completude dos dados"* — e **não**
fixa fórmula nem parâmetros. Este card operacionaliza a definição em um modelo **versionado**
(`score_version = v1.0`), gravado junto de cada medição. Mudar peso, campo ou janela exige versão
nova (`v1.1`) com histórico preservado — nunca edição silenciosa, que é a regra do doc 12 §8 (score
sem versão não é reprodutível e é recusado).

| Componente | Peso | O que mede |
| --- | --- | --- |
| `completude` | 0,45 | proporção ponderada dos campos de identificação/qualificação preenchidos |
| `validade` | 0,20 | formato/coerência do que está preenchido (CNPJ, domínio, URL, banda × contagem) |
| `confiabilidade` | 0,25 | **lastro no banco**: fonte declarada, pesquisa concluída, cobertura de fontes |
| `atualidade` | 0,10 | idade do **dado** (não da linha) com decaimento linear |

`valor = 100 × Σ(peso × componente)`, arredondado `HALF_UP` em 2 casas, faixa `[0,00; 100,00]`.
**Tiering não é deste card** (é `TRE-W5-E06-T01`).

Pesos e listas ficam em **um só lugar** no código e no JSON do score, e a suíte reprova se os dois
divergirem — inclusive se o peso de um campo parar de somar 1,00.

### 1.1 Três decisões de desenho que a revisão deve cobrar

1. **Sem dupla punição.** Campo ausente já é punido na `completude`; a `validade` julga **somente o
   que existe** (média dos checks *aplicáveis`). Sem isso, esquecer um campo derrubaria a nota duas
   vezes e o score mediria a mesma falha duas vezes — medido por item de suíte
   (`validade-nao-pune-duas-vezes-o-campo-ausente`).
2. **`updated_at` é proibido como referência de atualidade.** O espelho em `organizations` é um
   `UPDATE`; usar `updated_at` faria o score **rejuvenescer a si mesmo** a cada rodada (a nota subiria
   só por ter rodado). A referência é `GREATEST(created_at, max(research_runs.completed_at))`, e o
   write do v1 não toca `updated_at` (item + mutação dedicada).
3. **Idempotência no SQL, não na prosa.** A inserção só acontece quando o **último**
   `DATA_QUALITY/v1.0` da empresa não tem o mesmo `inputs_sha256`; repetir o lote com o banco igual
   devolve `JA_EXISTE` e escreve **zero** linha. O histórico é append-only: nunca há `UPDATE` em
   `scores`.

### 1.2 Reprodutibilidade

`valor = f(estado do banco, data de referência da rodada)`. Os inputs medidos mais a referência
(`AAAA-MM-DD`, padrão hoje UTC, sobrescrevível por `--referencia`) viram `inputs_sha256`. Mesmo
estado + mesma referência ⇒ mesmo valor e mesmo hash. Referência de outro dia é uma **medição nova**
(o dado envelheceu de fato) — linha nova no histórico, nunca sobrescrita.

## 2. ACCEPTANCE

| # | Critério | Como é medido |
| --- | --- | --- |
| AC1 | modelo declarado == contrato declarado == código (pesos somam 1,00) | suíte, itens `modelo-declara-pesos-que-somam-1`, `codigo-igual-ao-contrato-do-score` |
| AC2 | completude perde **exatamente** o peso do campo ausente; vazio/espacos = ausente; zero numérico = dado | suíte, `completude-perde-exatamente-o-peso-do-campo-ausente`, `campo-vazio-e-ausente-mas-zero-e-dado` |
| AC3 | validade julga só o presente, marca o motivo e **não corrige** o dado | suíte, `validade-nao-pune-duas-vezes-o-campo-ausente`, `validade-marca-o-motivo-sem-corrigir-o-dado` |
| AC4 | confiabilidade é lastro do banco (`research_runs` COMPLETED + `source_count`), não prosa | suíte, `confiabilidade-e-lastro-no-banco-nao-prosa` |
| AC5 | atualidade usa a data do dado e nunca `updated_at` | suíte, `atualidade-usa-a-data-do-dado-e-nunca-updated-at` |
| AC6 | grava `scores` (DATA_QUALITY/v1.0) e espelha `organizations.data_quality_score`, sem tocar outra coluna | suíte, `gravacao-insere-medicao-e-espelha-sem-tocar-updated-at`; aceite, itens `linha-em-scores-*`, `espelho-*`, `nenhuma-outra-coluna-escrita` |
| AC7 | replay não duplica; mudar o dado gera medição nova | suíte, `fluxo-...-idempotente-no-replay`, `medicao-e-reproduzivel-...`; aceite, `replay-nao-duplica-*`, `segunda-rodada-com-dado-novo-*` |
| AC8 | uma linha de auditoria por organização (`agent_runs`), com veredito/valor/`score_id` | suíte, `auditoria-uma-linha-por-organizacao-processada`; aceite, `auditoria-*` |
| AC9 | valor na faixa e monotônico no dado | suíte, `valor-na-faixa-e-monotono-no-dado` |
| AC10 | guarda de escrita recusa DDL, tabela fora da lista, coluna proibida em `organizations`, `UPDATE` sem `WHERE`, `UPDATE`/`DELETE` em `scores`, `INSERT` sem versão e `INSERT` de outro score | suíte, `guarda-recusa-escrita-fora-do-contrato`; aceite, `prod-recusado-*` |
| AC11 | `prod` recusado (exit 4) e ambiente exigido para escrever (fail-closed) | suíte, `ambiente-recusado-em-prod-e-exigido-para-escrever`; aceite, `prod-recusado-sem-escrita` |
| AC12 | desfazer é dry-run por padrão e, com `--confirmo`, apaga **só** a rodada e devolve o valor anterior | suíte, `desfazer-e-dry-run-por-padrao-e-restaura-o-valor-anterior`; aceite, `desfazer-*` |
| AC13 | nada em produção, nada de rede/LLM, nenhuma escrita fora de `scores`/`organizations`/`agent_runs` | suíte, `sem-llm-sem-rede-e-gate-do-jev-fail-closed`; aceite, guardas de escopo |

## 3. TEST

```bash
# suíte offline (sem banco, sem rede) + autoteste por mutação
python3 scripts/scores/verificar_score_data_quality.py
python3 scripts/scores/verificar_score_data_quality.py --autoteste

# aceite de verdade: PostgreSQL descartável na VPS (o container do Hermes não tem daemon Docker)
bash scripts/scores/teste_data_quality_aceite.sh
bash scripts/scores/teste_data_quality_aceite.sh --prova-de-dente

# portão de estrutura do repositório (artefatos do card versionados)
bash scripts/verificar_estrutura.sh
```

O aceite sobe um container **próprio** (`pg-dq-acc`), aplica a migration `0001` num schema limpo,
cria a massa do próprio card (nunca usa `pg-sales-dev`), mede por SQL e derruba o container no fim.
A prova de dente muta **cópias** do código e exige que o aceite reprove **o item esperado** de cada
mutação — não basta "o aceite falhou".

## 4. ROLLBACK

1. **Desfazer a rodada (operacional):**
   `python3 hermes/scores/data_quality/data_quality.py --ambiente <amb> --desfazer <correlation_id> [--confirmo]`
   — roda **frio** por padrão (lista o que apagaria). Com `--confirmo`, apaga as linhas de `scores`
   criadas pela rodada e devolve `organizations.data_quality_score` ao valor anterior registrado na
   própria linha (`NULL` quando não havia score). A restauração só acontece quando o valor atual do
   espelho é exatamente o valor escrito pela rodada: **nota mais nova não é sobrescrita por rollback
   de rodada velha**. `agent_runs` (auditoria) não é apagada.
2. **Reverter o código:** o card vive na branch `feature/TRE-W5-E04-T01`; nada foi promovido
   (`prod` é recusado por desenho). Reverter é `git revert` do commit do card.
3. **Sem DDL, sem migração:** o schema V1 já tem `scores` e `organizations.data_quality_score`. Não
   há mudança de contrato de dados neste card — logo, não há rollback de migration.

## 5. RISK

| # | Risco | Mitigação (medida, não prometida) |
| --- | --- | --- |
| R1 | o baseline **não** define a fórmula do Data Quality Score — risco de "número inventado" | o modelo é declarado, versionado (`v1.0`) e conferido item a item contra o JSON do score; mudar peso = versão nova, com histórico preservado |
| R2 | dupla punição (mesmo campo derrubando `completude` e `validade`) | desenho explícito "validade julga só o presente" + item de suíte que reprova se um campo ausente derrubar a validade |
| R3 | o espelho em `organizations` divergir do último `scores` | escrita atômica na mesma instrução (INSERT + UPDATE com `EXISTS`), item de suíte e item de aceite comparando espelho × último score |
| R4 | replay inflando o histórico (uma linha por retry) | guarda de idempotência dentro do SQL (`NOT EXISTS` sobre o último score da mesma versão + `inputs_sha256`); replay medido no aceite com contagem antes/depois |
| R5 | o score "rejuvenescer a si mesmo" via `updated_at` | coluna proibida na guarda de escrita e na leitura; mutação dedicada no autoteste |
| R6 | W5 é onda **paralela**: os cards irmãos (ICP/Automation Fit/Buying Signal) tocam arquivos comuns (gate de estrutura, `CHANGELOG`, registro de execuções) | blocos **aditivos** e namespace próprio (`hermes/scores/<score>/`, `scripts/scores/`); o conflito de merge esperado está registrado como hotspot no card |
| R7 | ambiente recém-criado (sem pesquisa) produz nota baixa | é o comportamento correto e declarado: sem lastro, a `confiabilidade` é 0 e o score **não** inventa confiança |

## 6. Limites declarados (o que este card NÃO prova)

- **A `completude` mede presença, não veracidade.** Dado preenchido e falso pontua como preenchido;
  conferir contra a fonte é do enriquecimento (W4), não do score.
- **Não há detecção de conflito entre fontes** (dois valores para a mesma coluna): o schema V1 guarda
  um valor por coluna e não tem trilha de versão por campo.
- **W5 não é homologada aqui:** a cadeia medida é o score em `dev`; ICP/Automation Fit/Buying
  Signal/Priority/Tier/NBA, Odoo, n8n e Titan ficam fora do caminho.
- **Sem outbox:** `COMPANY_QUALIFIED` nasce na qualificação (`TRE-W5-E05-T01`/`TRE-W5-E06-T01`), não
  no Data Quality.
- **Homologação é do Anderson.** O veredito deste card é o do estágio de revisão independente
  (perfil `tester`); promover a produção exige card próprio com aprovação humana registrada (ADR-005).
