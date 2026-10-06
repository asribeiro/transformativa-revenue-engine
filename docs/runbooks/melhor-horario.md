# Runbook — melhor horário de contato (`melhor-horario-v1`, TRE-W9-E04-T01)

Componente: `hermes/analytics/melhor_horario.py` · Contrato: `hermes/analytics/melhor-horario-v1.json`
Verificador offline: `scripts/agentes/verificar_melhor_horario.py`
Aceite E2E: `scripts/agentes/teste_melhor_horario_aceite.sh` (container descartável na VPS)

## 1. O que ele responde (e o que não responde)

Responde, sobre o histórico de interações já gravado: **em que janela (dia da semana × faixa horária) os
contatos respondem mais**, com amostra declarada. Não prevê por lead (isso é W9-E02/Predictive scoring), não
envia nada, não agenda e não escreve em nenhuma tabela.

A janela é derivada do **instante de ENVIO** convertido para o fuso declarado no contrato
(`America/Sao_Paulo`, offset fixo `-03:00`). O desfecho de cada envio é a resposta creditada pela **mesma
regra do card irmão W8-E04-T01** (resposta vai para o envio mais próximo anterior, mesma organização, mesmo
canal normalizado, mesma faixa de contato, janela inclusiva). O componente **importa**
`desempenho_mensagens.py` justamente para não existir uma segunda regra de atribuição no projeto.

## 2. Como rodar

```bash
python3 hermes/analytics/melhor_horario.py --regras                 # fuso, grade e regras (sem banco)
python3 hermes/analytics/melhor_horario.py --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --desde 2026-09-01T00:00:00Z --ate 2026-09-30T23:59:59Z \
  --limite-amostra 5 --json /tmp/melhor-horario.json --csv /tmp/melhor-horario.csv
```

- `--prefixo` é o comando da porta de banco (o mesmo contrato dos irmãos: fala `psql`, responde em `-tA -F'|'`).
  Nada de segredo na linha de comando por este componente.
- `--desde`/`--ate` tornam a saída **reproduzível** (sem carimbo de tempo por padrão; `--com-carimbo` inclui
  `gerado_em` e quebra o determinismo de propósito).
- `--limite-amostra` (default 5) é o piso para uma célula concorrer a "melhor janela".

Exit codes: `0` ANALISADO/SEM_ENVIOS · `2` uso/porta ausente · `3` fail-closed (contrato, grade, fuso, escrita)
· `4` produção recusada.

## 3. Como ler a saída

- `veredito`: `ANALISADO` (há envios no recorte) ou `SEM_ENVIOS` (taxas `null`, nunca `0.0`).
- `grade`: os 7 dias e as 7 faixas declaradas.
- `por_janela`: **49 células sempre presentes** (7 dias × 7 faixas), inclusive as vazias — a grade não é o
  conjunto de células que por acaso tem dado. `soma(enviadas) == totais.enviadas` (e as duas marginais fecham
  igual): se não fechar, há buraco e o contrato teria recusado antes.
- `por_dia` / `por_faixa`: marginais com as mesmas métricas.
- `melhor_janela` (+ `melhor_dia`, `melhor_faixa`): só entre células com `amostra_suficiente: true`; ordem
  `taxa_de_interesse` desc, `taxa_de_resposta` desc, `enviadas` desc, chave asc.
- `melhor_janela_motivo`: `AMOSTRA_INSUFICIENTE` (existe envio, mas nenhuma célula tem piso) ou `SEM_ENVIOS`.

**Leitura honesta:** com a base atual do projeto (poucos envios por célula) o resultado esperado é
`AMOSTRA_INSUFICIENTE`. Isso é a resposta correta — não é defeito. O card responde "não sei" em vez de coroar
um horário com 2 envios.

## 4. Guardas (todas medidas por item de suíte/aceite)

| Guarda | Comportamento |
| --- | --- |
| `prod` | RECUSA por desenho, exit 4 (ADR-005) |
| Somente leitura | todo SQL começa por `SELECT`/`WITH`; verbo de escrita = `ESCRITA_RECUSADA` exit 3 (guarda herdada do irmão) |
| Contrato | grade com buraco/sobreposição, fuso ilegível ou vocabulário do dono divergente = exit 3 (fail-closed) |
| PII | saída agregada por célula: sem `organization_id`, `contact_id`, e-mail, telefone, nome ou `approval_id` |
| Fuso | offset **declarado** no contrato (não vem do sistema nem do banco): mesma base ⇒ mesma janela |

## 5. Verificação

```bash
python3 scripts/agentes/verificar_melhor_horario.py --autoteste   # 29 itens + 6 mutações (dentes)
bash scripts/agentes/teste_melhor_horario_aceite.sh               # E2E na VPS (PostgreSQL descartável)
```

O aceite usa o container descartável `pg-timing-acc` (`postgres:16`), aplica `db/migrations/0001` e semeia a
fixture **na forma declarada pelos contratos irmãos** (canal/direção/tipo lidos de
`politica-envio-v1.json`; categorias de `ingestao-respostas-v1.json`). Veredito: `ACEITE_MELHOR_HORARIO_001_OK`.

## 6. Rollback

Remover os artefatos do card (`hermes/analytics/melhor-horario-v1.json`, `hermes/analytics/melhor_horario.py`,
`scripts/agentes/verificar_melhor_horario.py`, `scripts/agentes/teste_melhor_horario_aceite.sh`,
`docs/runbooks/melhor-horario.md`). Não há migration nova, nenhuma escrita em tabela e nenhum serviço de pé: o
componente é leitura sob demanda e o container de aceite sai no `trap`.

## 7. Lacunas declaradas (ver contrato §`lacunas`)

Fuso é offset fixo (horário de verão exige versão nova do contrato) · base histórica, não previsão · a grade
cobre 24 h porque o Data Contract V1 não declara expediente · não separa canal (só `EMAIL` é produzido hoje) ·
não há fuso do contato em `contacts` (usa-se o do remetente) · sem materialização/agendamento.
