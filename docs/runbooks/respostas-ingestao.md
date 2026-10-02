# Runbook — ingestão de respostas (TRE-W6-E05-T01)

## 1. Antes de rodar

```bash
export TRE_AMBIENTE=dev
export TRE_TITAN_IMAP_HOST=127.0.0.1        # dev exige loopback
export TRE_TITAN_IMAP_PORT=2993
export TRE_TITAN_IMAP_SEGURANCA=implicit_tls
export TRE_TITAN_USER=respostas-dev@dev.local
export TRE_TITAN_PASSWORD='<senha do sink>'
export TRE_TITAN_DOMINIO_DEV=dev.local
export TRE_TITAN_PORTA_BANCO="docker exec -i pg-resp psql -U sales_ai -d sales_intelligence"
```

Verificar a montagem antes de qualquer leitura de caixa (não abre conexão):

```bash
python3 hermes/agentes/respostas/ingestao_respostas.py --conferir
```

## 2. Dry-run (mede, não ingere)

```bash
python3 hermes/agentes/respostas/ingestao_respostas.py --ingerir --saida /tmp/medicao.jsonl
```

Sem `--confirmo` nada é gravado: a saída lista as categorias e o que **seria** inserido.

## 3. Rodada real

```bash
python3 hermes/agentes/respostas/ingestao_respostas.py --ingerir --confirmo \
  --chave-idempotencia resposta --relatorio /tmp/relatorio.json --registro /tmp/trilha.jsonl
```

- veredito esperado: `INGESTAO_RESPOSTAS_001_OK` (também no stdout: `# veredito: ...`);
- exit codes: `0` ok · `1` falha medida · `2` configuração inválida · `4` ambiente que recusa (prod).

## 4. Desfazer uma ingestão

```bash
python3 hermes/agentes/respostas/ingestao_respostas.py --desfazer 999:42 --confirmo
```

Marca `DESFEITO` na trilha do `sync_events`; a linha em `interactions` é **preservada** (auditoria não se
apaga). Idempotente: repetir devolve `JA_DESFEITO`.

## 5. Aceite E2E (host com Docker)

```bash
bash scripts/agentes/teste_ingestao_respostas_aceite.sh
```

Sobe Postgres descartável (`pg-resp-acc`) + sink IMAP local com TLS próprio e um corpus de 10 respostas,
mede: 12 tabelas antes/depois, categorias gravadas, replay sem duplicata, `SEM_VINCULO` na trilha,
invariante de leitura no sink (só `EXAMINE`, sem escrita), guardas de ambiente, `--desfazer`, escopo com
banco remoto. O container é removido ao final; nenhum dado de produção é tocado.

## 6. Se falhar

| sintoma | causa provável | ação |
|---|---|---|
| `CONFIG_DO_PRIMITIVO` | host/login/segurança fora do modo `dev` | conferir envs do passo 1 |
| `BANCO_RECUSOU: porta de banco falhou` | DSN/porta errada ou banco fora | testar o comando de `TRE_TITAN_PORTA_BANCO` na mão |
| `ESCRITA_NO_CODIGO` | a fonte IMAP ganhou escrita | **parar**: a auditoria recusou de propósito; revisar o primitivo |
| categoria `INDEFINIDO` em massa | corpus fora do vocabulário | ler `motivo` no relatório e ajustar o contrato (nova versão, `ordem` explícita) |
| replay duplicando | `UIDVALIDITY` mudou de verdade | esperado: a identidade é `UIDVALIDITY:UID`; conferir a trilha antes de assumir bug |

## 7. Registros de execução

Ver `docs/validation/registro-de-execucoes-e05-t01.md` (saídas completas anexadas ao card).
