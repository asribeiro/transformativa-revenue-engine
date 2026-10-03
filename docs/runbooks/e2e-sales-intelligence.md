# Runbook — Aceite E2E Sales Intelligence (TRE-W4-E06-T01)

O que este aceite é, como se roda, o que cada item significa e **o que ele NÃO mede** (lacuna
declarada — não se inventa medição que mente). Contrato/critérios:
`docs/architecture/e2e-sales-intelligence.md`.

- Aceite: `scripts/e2e/verificar-e2e-sales-intelligence.sh`
- Gate da onda (doc 07 §7): *empresa → research/signals/hypothesis/contacts*
- Veredito: `ACEITE_E2E_SALES_INTELLIGENCE_001_OK` / `ACEITE_E2E_SALES_INTELLIGENCE_001_FALHOU`

## 1. Onde ele roda

**Na VPS do ambiente** (ADR-0008): o container do Hermes não tem daemon Docker nem rota até o
PostgreSQL, então quem orquestra entra por SSH e o aceite roda **no host**. Ele sobe **um** container
PostgreSQL descartável próprio (`pg-e2e-si-acc`, `postgres:16`, sem porta publicada) e aplica a
migration `db/migrations/0001_sales_intelligence_v1.sql` em schema limpo. Os **cinco agentes** rodam
no mesmo banco, em sequência.

Se o container `pg-e2e-si-acc` **já existir**, o aceite **aborta** (exit 2) em vez de mexer no que não
é dele. `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` nunca são tocados.

## 2. Como se roda

```bash
cd /opt/tre/<dir-com-o-repo>

# o aceite completo (container descartável; é o modo de produção do aceite)
bash scripts/e2e/verificar-e2e-sales-intelligence.sh

# o aceite tem dentes? (baseline verde + uma mutação por agente, cada uma pelo item esperado)
bash scripts/e2e/verificar-e2e-sales-intelligence.sh --prova-de-dente

# deixar container e diretório de trabalho de pé para investigar
bash scripts/e2e/verificar-e2e-sales-intelligence.sh --manter
```

**Variáveis** (todas com default seguro): `TRE_E2E_RAIZ`, `TRE_E2E_IMAGEM` (`postgres:16`),
`TRE_E2E_SI_CONTAINER` (`pg-e2e-si-acc`), `TRE_E2E_SI_TRABALHO` (diretório temporário) e um caminho
de código por agente — `TRE_E2E_SCOUT_PY`, `TRE_E2E_RESEARCH_PY`, `TRE_E2E_SIGNAL_PY`,
`TRE_E2E_PAIN_PY`, `TRE_E2E_CONTACT_PY` (default: o código versionado do repo). São essas variáveis
que a prova de dente usa para apontar o aceite para as **cópias mutadas**.

**Saída**: uma linha por item (`OK` / `FALHOU`), o resumo `<rotulo>: N OK / M FALHOU` e o veredito em
uma linha. **Exit codes**: `0` = aceite OK · `1` = FALHOU · `2` = guarda/uso (container existente,
docker ausente, artefato faltando).

## 3. O que cada passo mede

| passo | o que mede | por que não dá para fingir |
| --- | --- | --- |
| 0 · suítes | as **cinco suítes offline** dos agentes (58 + 65 + 75 + 85 + 60 itens) no **mesmo commit** do aceite | diz em que commit a cadeia foi medida: contrato de agente quebrado reprova aqui, antes de qualquer banco |
| A · Scout | 3 candidatas → 3 empresas `DISCOVERED`, auditoria por pedido, trilha `INSERT` | o id de cada empresa (usado nos passos seguintes) sai do **relatório da rodada**, não da fixture |
| B · Research | 4 pedidos nas MESMAS empresas → 4 `research_runs`; enriquece coluna vazia; **não sobrescreve** o que o Scout escreveu (indústria, porte e cidade do Scout sobrevivem a valores diferentes na fonte) | sobrescrever aqui não quebra nenhum teste unitário — só a cadeia, e só se os dois agentes rodarem no mesmo banco |
| C · Signal | 4 observações, 2 declarando o `research_run_id` **produzido no passo B**; vínculo com run inexistente é descartado com motivo (`RESEARCH_RUN_NAO_ENCONTRADO`) e o sinal fica sem o vínculo | o `research_run_id` é lido do relatório do Research: é o encadeamento que o item mede |
| D · Pain Hypothesis | 3 hipóteses com lastro em `signals.id` (passo C) e `research_runs.id` (passo B) + 1 hipótese cujo lastro é um sinal **de outra empresa** → RECUSADA com `EVIDENCIA_DE_OUTRA_ORGANIZACAO` | é o dente da integridade: id existente mas de outro cliente passa por qualquer leitura ingênua |
| E · Contact Research | 2 contatos nas empresas da cadeia, 1 conflito de identidade → fila humana **sem** contato escrito, 1 empresa inexistente → RECUSADA | o contato tem de cair na empresa que o Scout criou (FK), não numa empresa da fixture |
| F · estado final | `organizations|research_runs|signals|pain_hypotheses|contacts` = `3|4|4|3|2` e uma empresa atravessando os cinco agentes; `scores`/`outbox`/`interactions`/`recommendations` = 0; nenhuma chamada de LLM | o número é o resultado do encadeamento; o vazio das tabelas de saída é o escopo declarado (W5/W3/W6) |
| G · replay | repetir as cinco rodadas com as MESMAS fontes: `JA_EXISTE`/`JA_PESQUISADO`/`JA_DETECTADO`/`JA_REGISTRADA`/`JA_IDENTIFICADO` e **foto das 5 tabelas de negócio idêntica** | a foto é feita com `||` (concatenação); com `|` sozinho o SQL seria OR bit a bit e o item passaria comparando um número sem sentido |
| H · guardas | `--ambiente prod` recusado (exit 4) nos cinco agentes **sem escrita**; `--planejar` com prefixo de container inexistente (exit 0, sem conexão) | roda os cinco agentes de verdade, com a fonte de cada um |
| I · desfazer | contato (dry-run + `--confirmo`) → hipótese → sinal → pesquisa → scout; banco volta ao estado inicial, auditoria e fila humana preservadas, um `ROLLBACK` por rodada | a pesquisa restaura a coluna que ela enriqueceu (valor anterior vem do `sync_events` da rodada) e o Scout só apaga empresa `DISCOVERED` |

## 4. Ler o resultado

- **`OK`** — o item mediu o que declara.
- **`FALHOU`** — o item imprimiu `esperado=` e `obtido=`: é ali que está o diagnóstico. O aceite
  **não** para no primeiro FALHOU de propósito: a lista inteira mostra o efeito em cascata.
- **A fila humana não é tabela de negócio.** A ambiguidade de identidade é **re-reportada** a cada
  rodada que a encontra (`CONTACT_IDENTITY_REVIEW` novo, nada de contato escrito) — o item
  `replay-re-reporta-a-ambiguidade-sem-escrever-contato` mede as duas coisas juntas. É comportamento
  do agente, medido, não defeito do aceite.
- **`--manter`** deixa `pg-e2e-si-acc` e o diretório de trabalho (relatórios `r-*.json` e saídas
  `*.out` de cada rodada) de pé. Os relatórios são a fonte dos itens: se um id não aparecer neles, o
  passo seguinte já não é gerado.

## 5. Prova de dente

Cinco mutações, **uma por agente**, aplicadas em cópia do código sob teste:

| mutação | o que quebra | item que tem de reprovar |
| --- | --- | --- |
| `scout-escreve-empresa-sem-identidade` | grava a empresa **sem o identificador forte** que a resolveu | `pesquisa-rodada1-run-aponta-a-empresa-do-scout` |
| `pesquisa-run-sem-organizacao` | grava a `research_run` sem o vínculo com a empresa | `pesquisa-rodada1-run-aponta-a-empresa-do-scout` |
| `sinal-anexa-run-inexistente` | passa a anexar o `research_run_id` sem conferir se existe | `cadeia-sinal-run-invalido-nao-anexado` |
| `hipotese-aceita-lastro-de-outra-empresa` | remove a conferência de dono da evidência | `cadeia-hipotese-evidencia-de-outra-empresa-recusada` |
| `contato-sem-idempotencia` | remove o `ON CONFLICT` do claim da chave | `replay-contato-nao-duplica` |

Regras do dente: baseline verde **antes**; mutação que não se aplica na âncora (`MUTACAO_NAO_APLICAVEL`)
reprova por buraco de verificação; e "o aceite falhou" **não** conta — o item esperado tem de
aparecer como `FALHOU`. O dente é fail-closed: baseline vermelho fecha `DENTE_FALHOU` sem medir nada.

**Por que as mutações do E2E são por VÍNCULO e não por "duplicar"/"sobrescrever"** (medido, não
suposto, nesta rodada): as duas propriedades mais óbvias da cadeia têm **duas camadas independentes**
cada uma, e nenhuma mutação de um ponto só produz o efeito ruim —

- *replay do Scout*: a chave de idempotência é a **identidade** (claim em `sync_events`) **e** a
  rodada seguinte resolve a empresa na base. Trocar o `ON CONFLICT` do claim não muda nada (a
  resolução encontra a empresa antes) e tornar a resolução inerte também não (o claim barra a chave) —
  medido: as duas mutações ficaram **verdes**, ou seja, inertes;
- *"não sobrescreve" do Research*: a coluna preenchida nem entra na lista de enriquecimento **e** a
  guarda exige `COALESCE(NULLIF(col,''), valor)`. Trocar o `COALESCE` por atribuição direta faz a
  guarda **recusar a escrita** (a rodada inteira vira `ERRO`) — não há mutação de um ponto que produza
  sobrescrita.

Nos dois casos a propriedade é medida pelo item do aceite (que lê os **valores** e os **vereditos**
reais) e pela suíte de mutações do próprio agente; o dente do E2E mira o que **só** a cadeia mede: o
vínculo entre o que um agente escreve e o que o próximo resolve.

## 6. O que fazer quando reprova

1. **Um item de `FALHOU` só num agente** — rode o aceite daquele agente
   (`scripts/agentes/teste_<agente>_aceite.sh --prova-de-dente`): a falha isolada indica mudança de
   contrato do agente, não da cadeia.
2. **Item de `cadeia-*`** — é fronteira entre agentes. Leia o relatório do passo anterior em
   `$TRE_E2E_SI_TRABALHO` (`--manter`): se o id não está lá, o problema é o agente que deveria
   devolvê-lo; se está e não foi resolvido pelo próximo, o problema é a resolução de identidade
   (importada do Scout).
3. **Item de replay/foto** — desconfie primeiro da **medida**, depois do código: foto com `|` em vez
   de `||`, contagem em tabela que a rodada de replay legitimamente escreve (auditoria) ou fila
   humana (re-reportada).
4. **Container pendurado** — o nome é fixo (`pg-e2e-si-acc`): `docker ps -a` mostra se o `trap` não
   rodou; `docker rm -f` é seguro (é descartável) e o próximo aceite aborta se ele existir.

## 7. Verificação e portão

```bash
bash scripts/e2e/verificar-e2e-sales-intelligence.sh               # aceite (76 itens)
bash scripts/e2e/verificar-e2e-sales-intelligence.sh --prova-de-dente   # + 5 mutações
bash scripts/verificar_estrutura.sh                                # artefatos versionados
```

Nada aqui homologa e nada aqui promove: `prod` é recusado por desenho (exit 4) e a sequência
dev → homologação → produção do ADR-005 é decisão do Anderson.
