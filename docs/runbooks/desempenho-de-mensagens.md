# Runbook — análise de desempenho de mensagens v1 (`desempenho-mensagens-v1`)

Card **TRE-W8-E04-T01** (onda W8 · épico E04 · prioridade P2). Contrato/política:
`hermes/analytics/desempenho-mensagens-v1.json`. Componente: `hermes/analytics/desempenho_mensagens.py`.

## O que é

Leitura **agregada** do fato já gravado pelos cards irmãos e resposta a três perguntas de operação:

1. **Qual texto performa melhor?** — por variante do TEXTO aprovado (`texto_hash` da referência
   `envio:<approval_id>:<texto_hash>` que o W6-E04 grava em `interactions.content_reference`).
2. **Qual canal performa melhor?** — por `interactions.channel` **normalizado**.
3. **Em quanto tempo a resposta chega?** — tempo médio e mediano até a PRIMEIRA resposta comercial.

Não entrega/abertura/bounce: o contrato V1 **não tem** essas colunas. A taxa deste card é de **resposta**,
não de entrega (lacuna declarada no contrato).

## Antes de qualquer coisa

- `prod` é **recusado** por este componente (exit 4). Permitidos: `dev` e `homolog`.
- É **somente leitura**: a única instrução enviada à porta de banco é um `SELECT`. Qualquer statement que
  não comece por `SELECT`/`WITH` ou que traga verbo de escrita é recusado (exit 3) — e o duble de teste
  recusa independentemente.
- A saída é **agregada**: não carrega `organization_id`, `contact_id`, e-mail, nome, telefone nem o
  `approval_id` (a chave da aprovação humana não sai na saída).

## Rodar (dev)

```
python3 hermes/analytics/desempenho_mensagens.py --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --desde 2026-09-01T00:00:00Z --ate 2026-09-30T23:59:59Z --janela-dias 14 --limite-amostra 5
```

- `--prefixo` é o comando da porta de banco (`psql`), no mesmo contrato dos cards irmãos.
- `--desde`/`--ate` delimitam os **envios**; respostas candidatas são lidas até `--ate` + `--janela-dias`
  (uma resposta posterior ao fim do recorte ainda pertence a um envio dentro dele).
- `--json <arq>` grava o relatório; `--csv <arq>` grava as variantes (`;` como separador).
- `--com-carimbo` inclui `gerado_em`; **sem** ele a saída é reproduzível (use para comparar rodadas).
- `--regras` imprime critério, classes, métricas, guardrails e lacunas — e sai, sem tocar o banco.

## Regras (declaradas no contrato, não no código)

| Decisão | Regra | Por quê |
|---|---|---|
| Identidade da mensagem | `direction=OUTBOUND` e `content_reference` = `envio:<approval_id>:<texto_hash>` (3 partes) | é a forma que o W6-E04 grava; enviada assim é o que entra |
| Canal | **normalizado** (trim + caixa alta) | o irmão de envio grava `EMAIL` e o de ingestão grava `email` — sem normalizar, resposta e envio nunca se encontram |
| Classes de resposta | partição do vocabulário do irmão (`INTERESSE` = positiva; `SEM_INTERESSE`/`OPT_OUT` = negativa; `INDEFINIDO`; `AUTO_RESPOSTA`/`BOUNCE`/`RUIDO` = **não é resposta de lead**) | vocabulário tem um dono (W6-E05); divergência = `CONTRATO_INCOERENTE` exit 3, fail-closed |
| Crédito | **mais próximo anterior**, em duas faixas: (1) mesmo `contact_id` da resposta; (2) envio sem contato (escopo da organização) | sem isso dois envios creditariam a MESMA resposta e a taxa mediria esforço, não efeito |
| Janela | `tempo ≤ --janela-dias` (default 14), limite **inclusivo** | borda testada: exatamente no limite CONTA, 1s depois não |
| Vencedor | só entre variantes com `enviadas ≥ --limite-amostra` (default 5) | não se coroa variante com 1 envio |

## Medição

```
python3 scripts/agentes/verificar_desempenho_mensagens.py --autoteste   # offline: 48 itens + 7 dentes
bash   scripts/agentes/teste_desempenho_mensagens_aceite.sh            # VPS: E2E 19 itens
```

O aceite sobe PostgreSQL **descartável** (`pg-desemp-acc`, `postgres:16`) + `db/migrations/0001`, semeia
envios/respostas **na forma declarada pelos irmãos** (canal/direção/tipo e categorias lidos dos contratos
`politica-envio-v1.json` e `ingestao-respostas-v1.json` — nada digitado à mão no script) e roda a análise
contra o banco real. Se o container já existir, o aceite **para e avisa**: não mexe em container que não criou.

## Lacunas declaradas (não escondidas)

1. **Sem entrega/abertura/bounce**: `interactions` não tem `delivered`/`opened`/`bounced`. `BOUNCE` entra
   como categoria de resposta do irmão e é medido como **descarte**, não como falha de entrega.
2. **Canal divergente entre os irmãos** (`EMAIL` × `email`): defeito de FORMA do dado gravado, não do
   contrato. Este card normaliza na leitura e o registra; corrigir a forma na **gravação** é do dono do
   dado (card de defeito, se ele decidir).
3. **`interactions.campaign_id`** é vínculo lógico sem FK e sem entidade de campanha no contrato V1: a
   análise **não** agrupa por campanha (agrupa por `texto_hash` e canal).
4. **Só `EMAIL` é produzido hoje** pelo irmão de envio. WhatsApp/LinkedIn assistidos do W7 não passam pelo
   mesmo `envio:` — enquanto não passarem, aparecem como excluídas (`outbound_sem_referencia_de_envio`).
5. **Sem materialização**: roda sob demanda; não há tabela de métrica nem agendamento (criar um exige
   versão nova do contrato + aprovação humana).
6. **Fixture no aceite**: as linhas de envio/resposta são semeadas por SQL na forma dos irmãos; a cadeia
   real SMTP/IMAP → `interactions` já foi medida no `TRE-W6-E07-T01` e no aceite do W6-E05. O que este
   aceite mede de ponta a ponta é a **análise** contra o PostgreSQL real.

## Reverter

Nada é escrito em nenhuma tabela e não há migration nova: reverter é remover os artefatos do card
(`hermes/analytics/`, `verificar_desempenho_mensagens.py`, `duble_psql_desempenho.py`,
`teste_desempenho_mensagens_aceite.sh`). O container de aceite é descartável e sai no `trap`.
