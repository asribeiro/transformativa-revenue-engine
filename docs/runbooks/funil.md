# Runbook — funil (`funil-v1`) — card TRE-W8-E01-T01

**Componente:** `hermes/agentes/analytics/funil.py` · **Contrato:** `hermes/agentes/analytics/funil-v1.json`
**Documento de desenho:** `docs/architecture/funil-v1.md` · **Aceite:** `scripts/agentes/teste_funil_aceite.sh`
**Suíte offline:** `scripts/agentes/verificar_funil.py --autoteste`

---

## 1. Campos do card (doc 11 §2)

| Campo | Definição registrada |
|---|---|
| **ACCEPTANCE** | `ACEITE_FUNIL_OK` — **34 itens, 0 falhas**, exit 0: guardas de ambiente, suite offline verde, funil conferido à mão sobre base semeada (alcance cumulativo, terminal Won/Lost separado, Nurture lateral, conversões), contagem por organização, três dentes de ponta medidos no banco, leitura pura provada por snapshot das 12 tabelas **e** pelo mecanismo `READ ONLY`, determinismo, ausência de PII e dashboard auto-contido |
| **TEST** | `python3 scripts/agentes/verificar_funil.py --autoteste` (**24 itens + 8 mutações**, cada mutação reprovando um item que o alvo limpo não reprova) e `bash scripts/agentes/teste_funil_aceite.sh` (PostgreSQL descartável `pg-funil-acc` na VPS de dev, ~17 s) |
| **ROLLBACK** | reverter o commit (5 arquivos novos, **sem DDL** e sem migration) e remover o container descartável do aceite. Nada em homolog/produção; nenhum serviço, nenhum cron, nenhuma credencial |
| **RISK** | **baixo** — leitura pura sobre base de dev, saída com contagem (sem PII), nenhum ato externo. Riscos declarados: leitura de dado de cliente em dev exige a mesma disciplina de ambiente das outras ondas (porta de banco local, `prod` recusado) e o valor literal do vocabulário de estágio do Odoo ainda **não** está congelado (lacuna L3) — rótulo fora da lista declarada cai em lacuna, nunca em estágio |

## 2. Uso

```bash
# conferência sem banco (contrato x estágios congelados x fontes)
python3 hermes/agentes/analytics/funil.py --ambiente dev --conferir
python3 hermes/agentes/analytics/funil.py --ambiente dev --planejar

# rodada no ambiente de dev (base canonica em container local)
python3 hermes/agentes/analytics/funil.py \
  --ambiente dev \
  --porta-banco "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/funil

# recorte de período (ISO; normalizado para UTC)
python3 hermes/agentes/analytics/funil.py --ambiente dev \
  --porta-banco "docker exec -i pg-funil-acc psql -U sales_ai -d sales_intelligence" \
  --desde 2026-09-01 --ate 2026-10-01 --saida /tmp/funil-setembro
```

Saída: `funil.json` (relatório) e `funil.html` (dashboard auto-contido — abre no navegador sem servidor).

Códigos de saída: `0` ok/reconferência · `2` uso errado (janela inválida, ambiente desconhecido) ·
`3` recusa (contrato/fonte/guarda/banco) · `4` produção recusada · `5` segredo vazado.

## 3. Leitura do relatório

- `estagios[].alcancadas` — organizações que **alcançaram** o estágio (cumulativo); `evidencia_propria`
  — organizações com fato **daquele** estágio. `Diagnóstico` costuma ter própria = 0 e alcance > 0:
  é o esperado (quem está em Proposta passou por Diagnóstico, mas o fato não é registrado por estágio).
- `conversao_da_anterior_pct` — conversão contra o estágio anterior da sequência linear; o primeiro
  estágio não tem (null). `Nurture` converte de `Qualificado` (`conversao_de`).
- Denominador zero devolve `null` — base vazia é base vazia, não conversão zero.
- `lacunas.estagios_desconhecidos` / `lacunas.status_nao_declarado` — rótulos fora do vocabulário
  declarado, com a forma normalizada (não viram estágio).
- `lacunas.eventos_sem_atribuicao` — eventos da trilha sem organização atribuível, por operação
  (é o caso de `OPPORTUNITY_WON/LOST`, que carregam o UUID da **oportunidade**).

## 4. Ambiente (ADR-005)

| Ambiente | Estado neste card |
|---|---|
| `dev` | **onde roda**: base canônica em container local, pós-migration 0001 |
| `homolog` | não provisionado; exige `--confirmo` e é passo de operador |
| `prod` | **recusado por desenho** (exit 4), antes de qualquer leitura |

Nenhuma credencial real, nenhum destino externo: o aceite sobe um `postgres:16` descartável
(`pg-funil-acc`), aplica a migration 0001, semeia a base, mede e **remove o container**, deixando os
containers do ambiente intactos.

## 5. Lacunas declaradas

As cinco lacunas do desenho (`docs/architecture/funil-v1.md` §5) viajam no próprio relatório
(`lacunas_declaradas`). A que mais importa na operação: **evento sem organização atribuível não entra
no estágio** — se `lacunas.eventos_sem_atribuicao` crescer, o caminho é fazer o produtor publicar
`organizacao_id` no payload do evento (ou materializar estágio na coluna do contrato), nunca "casar"
oportunidade com lead por semelhança.

## 6. Registro de execuções

`docs/operations/registro-de-execucoes.md` (entrada `TRE-W8-E01-T01`) — com o nome dos arquivos de
evidência anexados ao card e as lacunas medidas.
