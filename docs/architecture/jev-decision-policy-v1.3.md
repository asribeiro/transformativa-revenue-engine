# JEV Decision Policy v1.3 — o código comum `execucao_de_card` (RASCUNHO NÃO HOMOLOGADO)

- **Versão:** `jev-policy-v1.3`
- **Estado:** **RASCUNHO NÃO HOMOLOGADO** — entregue pelo card `TRE-W0-E04-T10` para a decisão do dono. Quem está **em vigor** é a `jev-policy-v1.2`, e é ela que o roteador carrega por padrão (`CAMINHO_POLITICA_PADRAO` → `hermes/jev/policy_v1_2.yaml`). **A homologação é do dono.**
- **Substitui (quando homologada):** `jev-policy-v1.2` (preservada para auditoria, congelada e ainda executável)
- **Escrita em:** 30/09/2026
- **Decisão do dono:** Anderson Ribeiro, 30/09/2026 (Telegram) — nomear **um** código comum novo, `execucao_de_card`, com escopo estreito: ambiente de desenvolvimento, **sem produção e sem credencial**. Nomear mais de um código **não** foi autorizado.
- **Forma legível por máquina:** `hermes/jev/policy_v1_3.yaml` (este documento é a autoridade em prosa; os dois têm de dizer a mesma coisa, e o verificador prova isso item por item)
- **Verificador:** `scripts/verificar_jev_policy_v1_3.py`
- **Cards:** `TRE-W0-E04-T10` (nomear o código) — a medição que sustenta está no `TRE-W0-E04-T03-D01`

---

## 1. O que muda (uma coisa, e só uma)

1. **O código comum `execucao_de_card` passa a existir no catálogo do roteador** (execução genérica de card de desenvolvimento, escopo estreito: dev, sem produção e sem credencial).
2. **A lane desse código é declarada nesta política** (`lane_por_codigo_de_acao` → `high`), junto dos quatro códigos que a v1.2 já declarava.

Nada mais muda: limiares, lanes, perfis, guardrails, precedência, fallback, recibo e métricas são **exatamente** os da v1.1/v1.2, restatados na seção 8.

## 2. Por que (a lacuna medida no T03-D01)

A anotação do corpus de benchmark (`TRE-W0-E04-T03-D01`, corpus v1.4) mediu o problema com número: **21 dos 32 casos não tinham código no catálogo vigente**, e **18 deles** são `execucao_de_card` — execução genérica de card de desenvolvimento. Com a postura estrita homologada (texto livre sem código canônico não executa, D07), essa lacuna **retinha o próprio card que consertava a classificação**: o trabalho de execução de card não tinha por onde ser declarado.

O caminho certo nunca foi afrouxar o D07 nem voltar a casar prosa — é **nomear o código**, que é decisão do dono sobre o catálogo. Este rascunho entrega o nome, a lane e a prova.

## 3. Escopo estreito — o que o código **não** concede

`execucao_de_card` nomeia **execução genérica de card de desenvolvimento**. Declarar a lane dele **não** declara permissão para:

- **produção/release** — a falha fechada do D07 continua barrando domínio sensível que o código não cobre; card com sinal de produção **escala**, não executa;
- **credencial** — idem: credencial é domínio que o código não cobre;
- **dado de cliente** e **outbound a terceiro** — idem.

E o **piso por ambiente** continua mandando: card genérico de desenvolvimento que declare DDL/migration em ambiente vivo sobe para `critical`, com aprovação humana registrada. A prova disso é **por comportamento** (§7), não por prosa.

## 4. Como a lane é decidida (ordem, sem rebaixamento)

1. **Lane declarada** para o código canônico (`lane_por_codigo_de_acao`). Limiar de confiança **não** se aplica a valor declarado: limiar existe para julgar **estimativa**. O recibo registra `confidence: null` e a origem `politica: lane_por_codigo_de_acao`.
2. **Guardrails** (Security) — barreira dura, inalterada.
3. **Human Approval** — o que exige humano nunca é delegado, inalterado.
4. **Código canônico da ação** — sem código declarado, a decisão **abstém** (fail-closed do D07, inalterado).
5. **Piso por ambiente** — DDL em ambiente novo/dev = `high`; em ambiente vivo = `critical` com aprovação humana registrada. **Só eleva.**
6. **Override humano** — **só eleva.**

**Sem lane declarada para o código, a decisão abstém e escala**, registrando a lane conservadora `high`. Ausência de resposta é abstinência, nunca permissão silenciosa.

## 5. Cobertura e manutenção

O mapa tem de cobrir **todos** os códigos comuns do roteador (`CODIGOS_DE_ACAO_COMUNS`) — hoje **cinco**. O verificador confere o **espelho contra o código**, item por item, nas duas direções (todo código comum tem lane declarada; toda chave do mapa é código comum). Código comum **sem** lane declarada **abstém** — nunca executa por omissão. Mexer no mapa é mexer em política: exige versão nova e homologação.

## 6. O mapa desta versão

| código canônico | lane declarada |
|---|---|
| `ajuste_de_texto` | `high` |
| `consulta_interna` | `high` |
| `operacao_comercial` | `high` |
| `migracao_de_esquema` | `high` |
| `execucao_de_card` | `high` |

O mapa segue **conservador** (todos em `high`): é o valor que a medição do T03-D01 sustenta — o classificador empatava com a constante "sempre `high`". **O custo, sem verniz:** o código novo executável ganha lane `high`, mais cara que a lane `medium`/`small` de alguns cards do corpus; o que justifica baixar o valor de um código é **dado** (custo por card e latência por lane), e o ajuste entra como versão seguinte, nunca como edição silenciosa.

**Quem executa esta versão:** o roteador `jev-router-v1.3` — o mesmo que declara suporte a ela (`VERSOES_DE_POLITICA_SUPORTADAS`) e que carrega a v1.2 **por padrão** enquanto o portão estiver fechado. O campo `execucao.roteador_que_a_executa` do YAML declara isso, e o verificador confere contra o código, não por leitura.

## 7. Rito e evidências

O verificador `scripts/verificar_jev_policy_v1_3.py` tem cinco partes, e **nenhuma substitui a anterior**:

- **PARTE 1** — a bateria da v1.0, inteira, sobre a v1.3 (`docs/architecture/jev-decision-policy-v1.md`);
- **PARTE 2** — a bateria da v1.1, inteira, sobre a v1.1 (`scripts/verificar_jev_policy_v1_1.py`), provando que a versão anterior não foi tocada;
- **PARTE 3** — a bateria da v1.2, inteira, sobre a v1.2 (`scripts/verificar_jev_policy_v1_2.py`), mesma prova para a versão em vigor;
- **PARTE 4** — o contrato novo: identidade e estado **rascunho honesto**, `lane_por_codigo_de_acao` com **cobertura medida no código nas duas direções**, o código novo declarado, o **portão de versão FECHADO** (a v1.2 continua sendo o caminho padrão do roteador, medido no código), o executor declarado e o documento e o YAML concordando;
- **PARTE 5** — provas de **comportamento** contra o roteador: `execucao_de_card` com texto limpo **executa** na lane declarada; o **mesmo código** com domínio sensível declarado (produção ou credencial) **não executa** (escopo estreito provado); o piso por ambiente continua elevando; o classificador (`classificar_card`, a linha de base histórica do benchmark) **não é chamado** pelo caminho de decisão; o recibo mantém os 13 campos; código comum sem lane declarada **abstém**; e, sob a política **em vigor** (v1.2), o código novo **abstém** — declarar suporte não é entrar em vigor.

Com `--autoteste`, o verificador muta o YAML, o documento e o roteador em cópias temporárias e exige que cada item falhe quando deve falhar (item que não pega mutação não é item).

---

## 8. Contrato herdado (v1.0/v1.1/v1.2) — restatado nesta versão

**Precedência (nesta ordem; nada abaixo contraria o de cima):**

Security → Human Approval → prioridade/dependências → JEV → LLM

**Limiares de confiança** (valem para **estimativa**, nunca para lane declarada):

| confiança | decisão |
|---|---|
| ≥ 0,85 | aceita a classificação do JEV |
| 0,65 – 0,85 | aceita, mas pela lane mais conservadora (`high`) |
| < 0,65 | abstém e escala (`ESCALATE`) |

**Nunca decidido por máquina** — as oito matérias que exigem humano: aprovação de produção, primeiro contato outbound, envio de proposta comercial, mudança estrutural de arquitetura, rollback em produção, exclusão de dado de cliente, rotação ou revogação de credencial e publicação em nome da Transformativa.

**Guardrails:** segredo nunca entra em prompt, log, recibo ou mensagem — e falha de guardrail bloqueia, não libera: dúvida na avaliação = `BLOCK`.

**Fallback e modo degradado:** se o JEV não responder, a decisão segue pela lane conservadora configurada — `high` —, o recibo grava `degraded_mode: true`, e o fallback **nunca contorna Human Approval**. Ausência de resposta é abstenção, nunca permissão.

**Recibo — 13 campos, e não cresce por conveniência:** `decision_id`, `card_id`, `task_hash`, `lane`, `model_profile`, `selected_model`, `effort`, `confidence`, `policy_version`, `router_version`, `timestamp`, `override` e `outcome`. `latencia` e `custo` ficam **fora** do recibo e são agregados pelo instrumentador.

**Decisão tipada:** `PASS` (executa), `RETRY`, `ESCALATE` (abstém e escala) e `BLOCK` (guardrail).

**Métricas:** o objetivo é minimizar o custo por card VERIFIED **sem** aumento de regressão, retrabalho ou incidente. O instrumento é `scripts/medir_metricas_por_lane.py`, com a unidade de custo relativa declarada (nunca dinheiro).

**Decisão de próximo passo é finita:** apenas `resultado`.

**Mudar isto exige versão nova.** Mexer em limiar, lane, precedência, guardrail, fallback, recibo ou métrica é mexer em política: nova versão, documento novo e homologação do Anderson.

## 9. Limites honestos deste rascunho

- **Não está em vigor.** Enquanto o dono não homologar, o roteador continua carregando a v1.2 e o código novo **abstém** sob ela (ausência de lane declarada é abstinência). O que esta entrega prova é que a v1.3, **se** apontada, decide o código na lane declarada;
- **um código só.** A lacuna remanescente do corpus (3 casos sem código no catálogo) **não** foi fechada por conveniência: o dono autorizou um código;
- **a lane é julgamento de política, não ótimo medido** — `high` é o piso que a medição sustenta;
- **não** resolve o caminho de execução da aprovação humana (o "beco" declarado no §7 da v1.2), que segue como card próprio;
- a medição de re-anotação do corpus (`corpus-anotacao-v1.5`) e o resultado versionado com `sha256` de política, roteador e corpus estão em `docs/validation/jev-benchmark-routing.md` (§11) e em `hermes/jev/benchmarks/`.
