# Registro de execuções — TRE-W6-E05-T01 (ingestão e classificação de respostas v1)

Data: 02/10/2026 · Branch `feature/TRE-W6-E05-T01` · Componente `ingestao-respostas-v1`.

Toda linha abaixo tem saída completa anexada ao card (`aceite-*.out`) com o exit code no fim.

## 1. Suite offline — `scripts/agentes/verificar_ingestao_respostas.py`

```
PASS (50 itens, 0 falhas)     exit 0
```

Sem rede, sem banco, sem credencial: importa o componente, usa banco `stub` que **recusa** comando fora
do contrato (INSERT em `interactions`/`sync_events` e `SELECT` de vínculo) e um corpus de respostas
declarado no próprio verificador. Cobre: vocabulário fechado, precedência `OPT_OUT > INTERESSE`,
exclusões (citação/assinatura), `BOUNCE` por cabeçalho, `AUTO_RESPOSTA`, `RUIDO`, `NAO_RESPOSTA`,
idempotência, `SEM_VINCULO`, `--desfazer`, tabelas/colunas que o componente escreve, ausência de DDL e
de `\Seen`/flag na fonte, e o contrato recusando regra sem detecção.

## 2. Prova de dente — `... --prova-de-dente`

```
PASS (56 itens, 0 falhas)     exit 0
```

6 mutações no componente, cada uma **reprovando** o item que a nomeia (dente que não sangra não é dente):

| dente | mutação | item reprovado |
|---|---|---|
| `opt-out-sem-prioridade` | ordenação por `ordem` → `-ordem` | precedência `OPT_OUT > INTERESSE` |
| `limpeza-de-citacao-desligada` | não remove citação do histórico | `citacao_so_no_historico` |
| `sem-auditoria-da-fonte` | remove a auditoria do caminho de ingesta | caminho de ingesta EXECUTA a auditoria |
| `idempotencia-desligada` | chave por `UID` sem `UIDVALIDITY` | idempotência |
| `sem-vinculo-vira-palpite` | cria organização/contato quando não acha | `SEM_VINCULO` |
| `ddl-no-componente` | injeta `CREATE TABLE` | ausência de DDL |

Defeitos reais encontrados pelo próprio instrumento e corrigidos antes do aceite: cabeçalho de presença
declarado como `""` casando qualquer mensagem (agora `"*"`, padrão vazio reprova o contrato) e guard que
existia mas não era **chamado** (item novo mede a chamada, não a existência).

## 3. Aceite E2E em pontas reais — `scripts/agentes/teste_ingestao_respostas_aceite.sh`

Host com Docker (`root@169.58.24.102`), Postgres descartável `pg-resp-acc` com a migration
`db/migrations/0001_sales_intelligence_v1.sql` aplicada, sink IMAP local do E01-T02 com TLS próprio e
corpus de 10 respostas (interesse, sem interesse, opt-out, bounce, auto-resposta, ruído, sem vínculo ×2,
link, HTML puro, mensagem nossa).

```
ACEITE_INGESTAO_RESPOSTAS_001_OK (43 itens, 0 falhas)     exit 0
```

Medido (não inferido):

- `interactions` 0 → 7 linhas; `sync_events` 6 → 23; as outras 10 tabelas **inalteradas**;
- categorias gravadas batem com o corpus item a item (`OPT_OUT` 1, `SEM_INTERESSE` 1, `INTERESSE` 3,
  `BOUNCE` 1, `AUTO_RESPOSTA` 1, `RUIDO` 0 — o ruído de remetente desconhecido entra como `SEM_VINCULO`);
- replay: `JA_INGERIDO` nas 10 mensagens, contagem de `interactions` **igual**;
- remetente fora de `contacts` → `SEM_VINCULO` na trilha, sem organização inventada;
- invariante de leitura no sink: 10 corpos lidos, **todas** as seleções `EXAMINE`, zero
  `total_buscas_sem_peek`, zero `total_comandos_de_escrita`, zero `mensagens_marcadas_lidas`;
- guardas: prod recusa (exit 4), banco remoto recusa em dev, `--desfazer` marca `DESFEITO` e é idempotente;
- `versao_tls`: TLSv1.3; segredo nunca aparece na saída.

## 4. Regressão do pai (card TRE-W6-E01-T02)

```
python3 scripts/integracoes/verificar_imap_titan.py → 67 OK / 0 falhas
```

As duas mudanças no primitivo (`cabecalhos_completos`, `corpo_html`) são **aditivas**: o envelope do
card E01-T02 não mudou e a suite dele segue verde.

## 5. Defeitos medidos durante o aceite (e corrigidos)

| # | sintoma | causa raiz | correção |
|---|---|---|---|
| 1 | `'tuple' object has no attribute 'login'` | `imap.conectar()` devolve `(sessao, medidas)` | desempacotar e registrar `versao_tls` no evento de caixa |
| 2 | `syntax error at or near "INTO"` | `INSERT` envolvido por `SELECT ... FROM (...)` | envelope passa a ser CTE (`WITH afetados AS (...)`) |
| 3 | `WITH query "afetados" does not have a RETURNING clause` | `INSERT` sem `RETURNING` no mesmo envelope | envelope com CTE **só** quando há `RETURNING` |
| 4 | `role "sales_ai" does not exist` | corrida: `psql select 1` responde durante o boot do cluster | espera 2× `ready to accept connections` + retry do `CREATE ROLE` |
| 5 | uid HTML classificado `INDEFINIDO` | em mensagem só-HTML o primitivo entrega as **tags** em `corpo_texto` | rota por `Content-Type: text/html` → `html_para_texto` antes da regra (novo item de suite) |
| 6 | fixture HTML com `text/plain` de enchimento | `add_alternative` criava alternativa de texto | HTML puro (single-part) — senão o caso não mede nada |
| 7 | `TypeError: set_content not valid on multipart` | bounce precisa de `multipart/report` | relatório de entrega montado à mão |
