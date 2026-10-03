# Observabilidade de sincronizacao (outbox -> trilha)

Runbook do card **TRE-W3-E05-T01**. O que existe, como se roda, o que cada numero significa e
**o que ainda nao e' observavel** (lacuna declarada — nao se inventa metrica que mente).

## 1. O que e' esta observabilidade

Uma **fotografia somente-leitura** de duas tabelas do `sales_intelligence`:

- `outbox_events` — a fila do consumidor (TRE-W3-E02-T01/T02);
- `sync_events` — a trilha de escrita (TRE-W3-E02-T02 e TRE-W3-E03-T01).

Nada e' escrito, nada e' travado: as consultas sao `SELECT` (a medicao esta' em
`n8n/sql/observabilidade-sync.sql` e `n8n/sql/observabilidade-sync-dead-letters.sql`). A prova de
que a rodada **nao** altera o que observa esta' no aceite: digest do conteudo das duas tabelas
identico antes/depois de uma execucao completa.

O resultado e' um **relatorio** com:

1. as metricas declaradas (uma linha por metrica, na unidade do contrato);
2. a secao `DETALHES` (dead-letter **com motivo**, falhas e recusas da trilha, nomeadas);
3. o **veredito**: `OK` / `ATENCAO` / `CRITICO` / `INDETERMINADO`.

## 2. Como se roda

Tudo sai do **mesmo workflow derivado** (`n8n/workflows/TRE-observabilidade-sync.json`,
`id` estavel `TREOBSERVSYNC1`), montado dos artefatos versionados:

```bash
# 1) o workflow em disco bate com contrato + SQL + nucleo?  (nao escreve nada)
python3 scripts/n8n/montar_workflow_observabilidade.py --conferir

# 2) lente estrutural (contrato x SQL x nucleo x workflow), sem docker
python3 scripts/n8n/conferir_observabilidade.py

# 3) suite do nucleo (node puro)
node scripts/n8n/testar_observabilidade_sync.js

# 4) aceite completo NA VPS (banco descartavel + n8n descartavel + dentes)
TRE_LOG_DIR=/opt/tre/<dir>/logs-aceite bash scripts/n8n/verificar-observabilidade-sync.sh
bash scripts/n8n/verificar-observabilidade-sync.sh --prova-de-dente
```

No n8n, o workflow tem **dois gatilhos** e ambos sao de leitura: `Agenda` (cron) e
`Executar agora` (manual). Ele nasce **inativo**: ligar e' passo de operacao (ADR-005).

**Importante para quem editar o workflow:** os dois nos Postgres tem `executeOnce`. Sem isso o n8n
executa a consulta **uma vez por item de entrada** — as 20 linhas de metrica viram 20 execucoes da
consulta de detalhes e a lista de detalhes sai multiplicada (medido no primeiro aceite deste card:
20 copias do mesmo dead-letter). O no' `Detalhes` tem `alwaysOutputData`: rodada **saudavel** tem
zero detalhe e a cadeia nao pode parar — o nucleo sabe distinguir "sem detalhe" (item vazio) de
"detalhe com tipo nao declarado" (que fecha `INDETERMINADO`).

## 3. As metricas (o que olhar primeiro)

| metrica | leitura |
|---|---|
| `fila_pendentes`, `fila_retry` | tamanho da fila por estado — `PENDING`/`RETRY` |
| `fila_no_teto` | evento que gastou as tentativas do `teto_de_tentativas` do consumidor |
| `fila_idade_maxima_s` | **idade do evento mais antigo na fila**: fila que cresce sem idade nao existe; idade sem fila e' fila parada |
| `dead_letter_total` | eventos `DEAD_LETTER` |
| `dead_letter_sem_motivo` | dead-letter **sem motivo nomeado** — `CRITICO` com limiar 0 |
| `trilha_por_status` | grade declarada (direcao x status), inclusive as combinacoes **sem atividade** (0 e' informacao) |
| `trilha_direcao_nao_declarada` | alguem escreveu na trilha fora das portas declaradas — `CRITICO` com limiar 0 |
| `outbox_processado_sem_trilha` | `PROCESSED` cuja chave derivada nao tem trilha `COMPLETED`: **sucesso sem prova** |
| `outbox_processado_total` | informativa (volume de entregas bem-sucedidas) |
| `trilha_sem_conclusao` | trilha `COMPLETED` sem `completed_at` |
| `trilha_falhas`, `trilha_recusas` | `FAILED` / `REFUSED` nomeados |
| `trilha_ultima_atividade_s` | staleness: quanto tempo desde a ultima atividade |
| `trilha_total` | informativa (volume) |

Limiares, unidades e o proprio conjunto de metricas vivem **apenas** em
`n8n/contracts/observabilidade-sync.v1.json`. O nucleo nao tem numero magico: nenhum id de metrica
nem valor de limiar aparece literal no JS (ha' verificacao que reprova se aparecer) — o teto do
motivo no relatorio tambem vem do contrato (`relatorio.tamanho_maximo_do_motivo`).

## 4. Como ler o veredito (fail-closed)

- `OK` (exit 0) — tudo dentro do limiar.
- `ATENCAO` (exit 1) — pelo menos uma metrica na faixa de alerta.
- `CRITICO` (exit 2) — pelo menos uma metrica na faixa critica.
- `INDETERMINADO` (exit 3) — **a observabilidade nao consegue decidir**: metrica declarada sem
  linha na medicao, valor nao numerico, metrica que o SQL devolve e o contrato nao declara, limiar
  ausente, direcao/dimensao desconhecida ou conferencia cruzada divergente (metrica x lista de
  detalhes contando historias diferentes).

`INDETERMINADO` **nunca** vira `OK` e `INDETERMINADO` ganha de `CRITICO` no mesmo estado: metricas
cegas escondendo um critico e' o pior caso possivel. Se aparecer `INDETERMINADO`, o relatorio diz o
motivo (`indeterminados[].motivo`) — leia o motivo antes de qualquer outra coisa.

## 5. Calibracao de limiar (dono)

Os limiares atuais (`alerta`/`critico`) foram escolhidos para o volume do ambiente de dev e estao
**declarados** no contrato. Ajustar limiar e' **nova versao do contrato** (`versao`) — nunca edicao
silenciosa do JSON nem "ajuste" no workflow: quem mede tem de saber contra qual declaracao o numero
foi comparado. Apos mudar o contrato:

```bash
python3 scripts/n8n/montar_workflow_observabilidade.py      # regenera o workflow derivado
python3 scripts/n8n/montar_workflow_observabilidade.py --conferir
node scripts/n8n/testar_observabilidade_sync.js             # a suite le o contrato do workflow
```

## 6. Lacunas declaradas (por desenho, nao por esquecimento)

1. **Replay/dedup nao e' observavel na V1** sem coluna nova em `sync_events` (o E02-T02 registra a
   chave e o resultado, mas nao conta "quantas vezes a chave foi reaproveitada"). Nao ha metrica
   disso — e' lacuna declarada no contrato (`lacunas_declaradas`), nao numero inventado.
2. **Volume/negocio** (quantos parceiros, quantas oportunidades) e' do CRM, nao desta superficie.
3. **Alertas de envio** (e-mail/Telegram/Slack) nao existem aqui: o workflow entrega o relatorio no
   proprio n8n; a notificacao e' passo de operacao.
4. **Nada nasce ligado** (ADR-005): o workflow nasce inativo e nada foi publicado em homolog/prod.

## 7. Quando o aceite fica vermelho

1. Leia o **primeiro** `FALHOU` do aceite (o relatorio completo fica no `TRE_LOG_DIR` da rodada).
2. `INDETERMINADO` no relatorio: o motivo esta' nomeado; quase sempre e' contrato/SQL fora de sincro
   (rode `montar_workflow_observabilidade.py --conferir` e a lente).
3. Falha de ambiente (imagem docker, banco do dev ocupado, n8n sem subir): o aceite **nao** conta
   dente nenhum — ele fecha `..._DENTE_FALHOU` com baseline vermelho, de proposito.
4. `--prova-de-dente` roda o **baseline nao mutado** primeiro: se o baseline nao fica verde, o
   veredito de dente nao vale e o modo sai com exit 1.

## 8. Evidencia e reversao

- O aceite guarda tudo em `TRE_LOG_DIR` (lente, suite, cada estado, sha256 antes/depois, sub-runs
  dos dentes). O diretorio do dente e' persistido (`<LOG_DIR>/dente`): cada mutante aplicado e a
  saida do sub-run ficam guardados.
- Reverter o card e' `git revert` do commit: os arquivos sao **novos** (nada existente foi alterado
  fora do `CHANGELOG.md`, do `registro-de-execucoes.md` e do `verificar_estrutura.sh`, em blocos
  aditivos). Nao ha DDL nem escrita em banco: nao ha dado a restaurar.
- O workflow nasce **inativo**; despublicar e'
  `n8n update:workflow --id=TREOBSERVSYNC1 --active=false`.
