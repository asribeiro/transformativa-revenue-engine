# JEV Decision Policy v1.2 — lane declarada por código canônico

- **Versão:** `jev-policy-v1.2`
- **Estado:** **EM VIGOR** — homologada por Anderson Ribeiro em 30/09/2026 (Telegram: *"homologado, de acordo"*; registro em `docs/operations/registro-de-aprovacoes.md`), executada pelo roteador `jev-router-v1.2`
- **Substitui:** `jev-policy-v1.1` (preservada para auditoria, congelada e ainda executável)
- **Escrita em:** 30/09/2026
- **Decisão do dono:** Anderson Ribeiro, 30/09/2026 (Telegram: *"sim, autorizo"*) — aposentar o classificador de card e declarar a lane por código
- **Forma legível por máquina:** `hermes/jev/policy_v1_2.yaml` (este documento é a autoridade em prosa; os dois têm de dizer a mesma coisa, e o verificador prova isso item por item)
- **Verificador:** `scripts/verificar_jev_policy_v1_2.py`
- **Cards:** `TRE-W0-E04-T09` (aposentar o classificador) e `TRE-W0-E04-T10` (nomear `execucao_de_card`)

---

## 1. O que muda

1. **O classificador de card é aposentado.** A função `classificar_card` deixa de ser consultada
   pelo caminho de decisão (`tarefa_a_partir_do_card` → `decidir`). Ela permanece no código como
   **linha de base histórica**, usada exclusivamente pelo benchmark — a medição que justificou a
   aposentadoria continua reproduzível, mas **ninguém decide por ela**.
2. **A lane passa a ser DECLARADA nesta política, por código canônico de ação**
   (`lane_por_codigo_de_acao`). O roteador lê o mapa; não estima lane e não guarda lane literal.

## 2. Por que aposentar (o que a medição mostrou)

Medição do card `TRE-W0-E04-T03-D01`, sobre o corpus v1.4 (32 casos, `acao_codigo` 32/32):

- classificador isolado: **0,1875** (6/32);
- **constante estrutural "sempre `high`": 0,375** (12/32) — o classificador mede **um quinto** dela;
- na comparação direta que interessa (mesmos casos elegíveis), o classificador **empata** com a
  constante.

Estimador que empata com a constante não é fonte de lane: é ruído calibrado. E, por casar **texto**,
ele produzia erro qualitativamente pior que o erro de custo: mandava para `critical` um card que
apenas **dizia** *"sem credencial, nada de produção"* — a ressalva virava acusação. O corpo do
próprio card `T09` é o caso vivo: ele citava "produção" e "credencial" **para isentar**, e era
barrado por essas duas palavras.

## 3. Como a lane passa a ser decidida (ordem, sem rebaixamento)

1. **Lane declarada** para o código canônico (`lane_por_codigo_de_acao`). Limiar de confiança
   **não** se aplica a valor declarado: limiar existe para julgar **estimativa**. O recibo registra
   `confidence: null` e a origem `politica: lane_por_codigo_de_acao` — nada de chamar de "proposta
   do classificador" o que não vem dele.
2. **Guardrails** (Security) — barreira dura, inalterada.
3. **Human Approval** — o que exige humano nunca é delegado, inalterado.
4. **Código canônico da ação** — sem código declarado, a decisão **abstém** (fail-closed do D07,
   inalterado).
5. **Piso por ambiente** — DDL em ambiente novo/dev = `high`; em ambiente vivo = `critical` com
   aprovação humana registrada. **Só eleva.**
6. **Override humano** — **só eleva.**

**Sem lane declarada para o código, a decisão abstém e escala**, registrando a lane conservadora
`high`. Ausência de resposta é abstinência, nunca permissão silenciosa — a regra do `fallback` da
v1.1 continua valendo.

## 4. Cobertura e manutenção

O mapa tem de cobrir **todos** os códigos comuns do roteador (`CODIGOS_DE_ACAO_COMUNS`); o
verificador confere o **espelho contra o código**, item por item, nas duas direções (todo código
comum tem lane declarada; toda chave do mapa é código comum). Código novo sem lane declarada
**abstém** — nunca ganha lane por omissão. Mexer no mapa é mexer em política: exige versão nova e
homologação.

## 5. O mapa desta versão — e o custo honesto dele

| código canônico | lane declarada |
|---|---|
| `ajuste_de_texto` | `high` |
| `consulta_interna` | `high` |
| `operacao_comercial` | `high` |
| `migracao_de_esquema` | `high` |

O mapa nasce **conservador** (todos em `high`) porque é isso que a medição sustenta: o valor
declarado é o mesmo que a constante vencedora. **O custo, sem verniz:** card que hoje acerta em
lane menor passa a custar mais, até existir ajuste por código. O que justifica mudar o valor de um
código é **dado** — custo por card e latência por lane (`metricas.acompanhar`) —, e não impressão.
O ajuste entra como versão seguinte, não como edição silenciosa.

## 6. Portão de versão

O portão de versão abriu em **30/09/2026**, com as duas condições satisfeitas, na mesma ordem da v1.1:

1. **homologação do Anderson**, registrada em `docs/operations/registro-de-aprovacoes.md`;
2. **executor implementado**: o roteador declara suporte a `jev-policy-v1.2` e implementa
   `lane_por_codigo_de_acao` — regra declarada tem de ser regra executada (defeito **D08**).

O roteador que executa esta versão é o **`jev-router-v1.2`**.

## 7. Limites honestos desta versão

- `lanes.*.exemplos` **continua versionado**, agora como **linha de base do benchmark**. Não decide
  nada; existe para a medição ser reproduzível.
- O modo `classificador` do benchmark mede a **linha de base histórica** (o roteador não o consulta);
  o modo que reflete a decisão real é o que usa a política em vigor.
- O mapa é **julgamento de política**, não ótimo medido: `high` para todos é o piso que a medição
  sustenta, não a melhor alocação possível.
- A lane declarada **não** substitui a aprovação humana: o piso por ambiente continua elevando para
  `critical` com aprovação registrada.
- **Não coberto por esta versão:** o **caminho de execução da aprovação humana** (hoje a política
  exige a aprovação registrada, e nenhum código executa o card depois dela — o "beco"). Fica como
  card próprio; esta versão não o resolve nem o finge resolvido.

## 8. Rito e evidências

O verificador `scripts/verificar_jev_policy_v1_2.py` tem quatro partes, e nenhuma substitui a
anterior:

- **PARTE 1** — a bateria da v1.0, inteira, sobre a v1.2 (`docs/architecture/jev-decision-policy-v1.md`);
- **PARTE 2** — a bateria da v1.1, inteira, sobre a v1.1 (`scripts/verificar_jev_policy_v1_1.py`), provando
  que a versão anterior não foi tocada;
- **PARTE 3** — o contrato novo declarado (mapa, cobertura, regra da lane declarada, portão fechado,
  contrato da v1.1 preservado, documento e YAML concordando);
- **PARTE 4** — provas de **comportamento** contra o roteador: o texto não decide lane; o mapa decide;
  código sem lane declarada abstém; o classificador **não é chamado** pelo caminho de decisão; o piso
  por ambiente continua elevando; o recibo mantém os 13 campos.

Com `--autoteste`, o verificador muta o YAML, o documento e o roteador em cópias temporárias e exige
que cada item falhe quando deve falhar (item que não pega mutação não é item).

---

## 9. Contrato herdado (v1.0/v1.1) — restatado nesta versão

Esta versão muda **duas coisas** (a lane declarada por código e a aposentadoria do classificador) e
**não** toca no resto do contrato. O restante fica restatado aqui para que este documento continue
sendo a autoridade em prosa de quem lê apenas a versão em vigor:

**Precedência (nesta ordem; nada abaixo contraria o de cima):**

Security → Human Approval → prioridade/dependências → JEV → LLM

**Limiares de confiança** (valem para **estimativa**, nunca para lane declarada):

| confiança | decisão |
|---|---|
| ≥ 0,85 | aceita a classificação do JEV |
| 0,65 – 0,85 | aceita, mas pela lane mais conservadora (`high`) |
| < 0,65 | abstém e escala (`ESCALATE`) |

**Nunca decidido por máquina** — as oito matérias que exigem humano: aprovação de produção,
primeiro contato outbound, envio de proposta comercial, mudança estrutural de arquitetura, rollback
em produção, exclusão de dado de cliente, rotação ou revogação de credencial e publicação em nome da
Transformativa.

**Guardrails:** segredo nunca entra em prompt, log, recibo ou mensagem — e falha de guardrail bloqueia, não libera: dúvida na avaliação = `BLOCK`.

**Fallback e modo degradado:** se o JEV não responder, a decisão segue pela lane conservadora
configurada — `high` —, o recibo grava `degraded_mode: true`, e o fallback **nunca contorna Human
Approval**. Ausência de resposta é abstenção, nunca permissão.

**Recibo — 13 campos, e não cresce por conveniência:** `decision_id`, `card_id`, `task_hash`, `lane`,
`model_profile`, `selected_model`, `effort`, `confidence`, `policy_version`, `router_version`,
`timestamp`, `override` e `outcome`. `latencia` e `custo` ficam **fora** do recibo e são agregados
pelo instrumentador.

**Decisão tipada:** `PASS` (executa), `RETRY`, `ESCALATE` (abstém e escala) e `BLOCK` (guardrail).
Depois de um ciclo, a decisão de próximo passo é apenas `resultado`.

**Mudar isto exige versão nova.** Mexer em limiar, lane, precedência, guardrail, fallback, recibo ou
métrica é mexer em política: nova versão, documento novo e homologação do Anderson.
