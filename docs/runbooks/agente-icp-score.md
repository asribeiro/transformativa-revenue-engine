# Runbook — Agente ICP Score v1 (TRE-W5-E01-T01)

Contrato do agente: `docs/architecture/agente-icp-score-v1.md` (ACCEPTANCE/TEST/ROLLBACK/RISK).
Modelo: `icp-v1.1.0` — **homologado pelo dono** em 07/10/2026
(`docs/business/icp-transformativa-v1.md`): corte de porte ≥ 50, estado de São Paulo e três
sinais de intenção com fonte e data. Quem homologa não é quem entrega.

## 1. Onde roda

O banco do ambiente vive na **VPS do TRE** (ADR-0008); o container do Hermes só orquestra por
SSH. O agente fala com o banco pelo **prefixo psql** (`docker exec -i <container> psql ...`).

```bash
# na VPS (ou por SSH, do container do Hermes)
cd /opt/tre/<staging-do-repo>   # cópia do repo no commit sob teste
```

## 2. Rodada de pontuação

```bash
# 1) planejar (NÃO abre conexão; mede o modelo com os campos da fonte)
python3 hermes/agents/icp_score/icp_score.py --planejar \
  --fonte hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl

# 2) pontuar no dev
cat > /tmp/icp-fonte.jsonl <<'JSONL'
{"organization_id":"<uuid-da-organizacao>"}
JSONL
python3 hermes/agents/icp_score/icp_score.py --ambiente dev --fonte /tmp/icp-fonte.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/icp-score-rodada.json
```

A fonte carrega **apenas** `organization_id`: o dado do score é lido no banco
(`sales_intelligence.organizations` e, para a intenção, `sales_intelligence.signals` —
**somente leitura**: o agente não escreve sinal, não preenche fonte nem data). Campo de score
na fonte é ignorado no modo real — se ele mudasse o resultado, a rodada não estaria medindo o
banco.

Os sinais precisam de **fonte** (`source_type`/`source_url`) **e data** (`event_date`): sinal
sem fonte declarada não dá crédito de intenção (motivo `INTENCAO_SEM_FONTE` na explicação).
Sinal sem `state`/fora de SP ou com porte abaixo de 50 **não entra na campanha** — o score é
zerado pelo corte, mesmo que a conta ponderada fosse alta.

## 3. Depois da rodada — o que conferir

```bash
PSQL='docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence -tA -F|'

# scores gravados na rodada (tipo, versão e valor)
$PSQL -c "SELECT s.score_type, s.score_version, s.score_value, s.organization_id
            FROM sales_intelligence.scores s
            JOIN sales_intelligence.agent_runs a
              ON (a.output->>'score_id')::uuid = s.id
           WHERE a.correlation_id = '<correlation_id>' ORDER BY s.score_value DESC;" </dev/null

# como o número saiu (componente a componente)
$PSQL -c "SELECT s.score_value, s.explanation FROM sales_intelligence.scores s
            JOIN sales_intelligence.agent_runs a ON (a.output->>'score_id')::uuid = s.id
           WHERE a.correlation_id = '<correlation_id>';" </dev/null

# auditoria da rodada (um por organização; model/tokens/custo NULL)
$PSQL -c "SELECT status, count(*) FROM sales_intelligence.agent_runs
           WHERE agent_name = 'icp_score' AND correlation_id = '<correlation_id>'
           GROUP BY status;" </dev/null
```

Leitura rápida do resultado:

- `score_value = 100` ⇒ sweet spot + segmento ICP + B2B + em SP + os três sinais com fonte e
  data; `0` ⇒ **corte** (porte < 50 ou fora de SP), fora do ICP **ou** dado ausente — os casos
  se distinguem em `explanation.cortes` (valor efetivo e motivo de cada corte),
  `explanation.criterios` (os cinco critérios, um a um) e `explanation.motivos`;
- `explanation.score_bruto` guarda a conta ponderada antes do corte: é ele que mostra que o
  corte derrubou um score alto;
- `JA_EXISTE` ⇒ mesmos dados da rodada anterior (replay, nada foi escrito);
- `RECUSADA` ⇒ organização inexistente/apagada ou `organization_id` ilegível;
- `ERRO` ⇒ porta de banco ou auditoria falhou: **nada** foi gravado como concluído.

## 4. Desfazer uma rodada

```bash
# dry-run (padrão): mostra o que seria apagado, sem apagar
python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id> --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# aplica (apaga SOMENTE os scores da rodada; auditoria é preservada)
python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id> --ambiente dev --confirmo \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

## 5. Homologação e produção

`--ambiente prod` é **recusado** (exit 4) e nada é escrito nem auditado: promover o agente é
card próprio com aprovação humana registrada (ADR-005) — a ida do motor para produção é
decisão do ADR-0009, em item próprio. O modelo já está homologado (1.1); o que não se homologa
por rodar em homolog é o comportamento em produção.

## 6. Verificação (antes de dizer que está certo)

```bash
# suíte offline + autoteste por mutação (sem banco; roda de qualquer diretório — a raiz do
# repo é achada por marcador, não pela profundidade do arquivo)
python3 scripts/agentes/verificar_agente_icp_score.py --autoteste

# aceite em container PostgreSQL descartável (na VPS; exige docker)
bash scripts/agentes/teste_icp_score_aceite.sh --prova-de-dente

# portão de estrutura do repositório
bash scripts/verificar_estrutura.sh
```

## 7. Problemas conhecidos

- **`pg_isready` mente no início**: a imagem oficial do PostgreSQL sobe um servidor temporário
  e o derruba depois. O aceite espera `SELECT 1` funcionar **duas vezes** antes de aplicar a
  migration (lição medida na W1).
- **`docker exec -i` consome stdin**: todo comando que não lê stdin no aceite leva `</dev/null`,
  senão o laço de mutações morre na segunda iteração (defeito medido na W4).
- **Container descartável com nome fixo** (`pg-icp-acc`): se ele já existir, o aceite **aborta**
  em vez de mexer no que não é dele.
- **Rodada depois de mudar o dado**: o score **novo** é gravado e o anterior fica; a consulta
  do relatório mostra os dois. Não existe "atualizar score" — existe versão nova.
