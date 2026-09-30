# JEV Decision Policy V1.1 — rascunho

**Versão:** `jev-policy-v1.1` · **Estado:** **RASCUNHO, NÃO HOMOLOGADO** · **Card:** TRE-W0-E04-T06
**Substitui:** [`jev-policy-v1.0`](jev-decision-policy-v1.md) (em vigor até a homologação)
**Forma legível por máquina:** [`hermes/jev/policy_v1_1.yaml`](../../hermes/jev/policy_v1_1.yaml)
**Verificador:** [`scripts/verificar_jev_policy_v1_1.py`](../../scripts/verificar_jev_policy_v1_1.py)
**Origem no baseline:** docs 13 (Hermes Implementation Brief) e 14 (JEV Decision Layer) do baseline V1.1.0

> **Este documento não muda nada sozinho.** Enquanto o `homologacao.registrada_em` do YAML for `null`,
> a versão em vigor é a v1.0, o roteador em vigor (`jev-router-v1.0`) **recusa** esta versão, e o
> verificador prova essa recusa. Homologação é do Anderson, com registro em
> [`docs/operations/registro-de-aprovacoes.md`](../operations/registro-de-aprovacoes.md) — não de máquina.

## 0. O que muda da v1.0 para a v1.1

| # | Mudança | Por que |
|---|---|---|
| 1 | **`limiares.lane_conservadora: high`** — chave explícita | na v1.0 a lane conservadora era lida de **prosa** (`fallback.acao` e o parêntese de `limiares.empate`). Funcionava e falhava fechado se as duas fontes divergissem, mas prosa não é contrato: quem ler o recibo não vê de onde saiu a lane |
| 2 | **`regra_de_lane_por_ambiente`** — regra de lane por ambiente | convenção do Anderson (29/09/2026): DDL em ambiente **novo/dev** = `high`; DDL em ambiente **vivo** = `critical` com aprovação humana registrada. Sem ela, "DDL" na lista de exemplos de `critical` tornaria todo DDL crítico e a régua perderia poder de separação |
| 3 | **`metricas.instrumentacao`** — instrumentação declarada | a v1.0 declarava custo e latência por lane e **não media**: métrica declarada e silenciosamente não medida é pior que métrica ausente |
| 4 | **`custo_por_lane`** entra em `metricas.acompanhar` | o objetivo da política é custo por card VERIFIED, que ainda **não** é medível; custo por lane é o que se pode medir hoje, então passa a ser acompanhado nominalmente |

Nada mais muda: limiares numéricos, as quatro lanes, os perfis de modelo, a precedência, as oito ações
`nunca_decidido_por_maquina`, os guardrails, o fallback e os 13 campos do recibo são **idênticos** aos da
v1.0 — e o verificador da v1.1 roda, item por item, a mesma bateria da v1.0 sobre este documento e este
YAML antes de acrescentar os itens novos.

---

## 1. O que o JEV é (e o que não é)

O JEV é o **System-1 Decision Layer**: decide escolhas **tipadas e finitas** — barato, rápido e
determinístico. Ele **não** raciocina, não escreve código, não substitui LLM e **nunca** substitui
aprovação humana.

> JEV para escolhas tipadas e finitas. LLM para reasoning, geração, síntese, debugging e design.

O que ele decide, antes da LLM: complexidade, lane, perfil de modelo, esforço, risco, exige revisão,
exige escalação. Depois de um ciclo: `PASS | RETRY | ESCALATE | BLOCK`.

**Não é para:** código, patch, arquitetura, incidente ambíguo, migração complexa, aprovação de produção
ou qualquer decisão sensível.

## 2. Precedência — quem decide primeiro

```
Security → Human Approval → prioridade/dependências → JEV → LLM
```

Nenhuma camada abaixo contraria a de cima. Na prática:

1. **Security** — guardrails determinísticos rodam **antes** de qualquer classificador. São barreira dura,
   não sugestão: se um guardrail falha, o fluxo para ali (`fail-closed`).
2. **Human Approval** — o que exige humano nunca é delegado a classificador, em nenhum limiar. Nem o JEV
   com confiança 1,00 decide: aprovação de produção, primeiro contato outbound, envio de proposta
   comercial, mudança estrutural de arquitetura, **rollback em produção**, exclusão de dado de cliente,
   rotação ou revogação de credencial, nem **publicação em nome da Transformativa**.
3. **Prioridade e dependências** — a ordem do board manda: card bloqueado não inicia; filho com pai
   pendente espera. O JEV não "otimiza" a fila por conta própria.
4. **JEV** — só então classifica e roteia.
5. **LLM** — recebe a tarefa já classificada e faz o que só ela faz: reasoning e geração.

Essa ordem não é burocracia: é o que impede um classificador rápido de decidir algo que exige julgamento
de negócio ou que expõe dado/segredo.

## 3. Lanes

| Lane | Escopo | Risco | Perfil de modelo | Revisão |
|---|---|---|---|---|
| **small** | mecânico e isolado, sem decisão de arquitetura | baixo | `worker-barato` | testes automatizados |
| **medium** | engenharia comum, um módulo por vez | médio | `reasoning-padrao` | testes + revisão de diff |
| **high** | reasoning complexo ou multi-sistema | alto | `reasoning-forte` | revisão dedicada + regressão |
| **critical** | produção, segurança, dados, arquitetura, regressão persistente | crítico | `critical-frontier-reviewer` | revisão dedicada + **aprovação humana registrada** |

### 3.1 Lane por ambiente (novo na v1.1)

`DDL` aparece como exemplo da lane `critical`, e lido ao pé da letra isso tornaria **todo** DDL crítico —
a régua perderia poder de separação e o W1 inteiro dependeria de aprovação card a card. O que separa DDL
comum de DDL crítico é o **ambiente** que ela encosta, nunca a palavra DDL:

| Ambiente declarado | Lane mínima | Aprovação humana registrada |
|---|---|---|
| `desenvolvimento`, `dev` (ambiente novo, sem dado real) | **high** | não |
| `vivo`, `producao` (ambiente em uso, com dado real) | **critical** | **sim** |
| não declarado | `critical` — ausência de declaração é abstinência, nunca permissão | **sim** |

A regra é **piso de lane**: só **eleva**, nunca rebaixa lane já decidida (mesma postura do `override`
humano, que só aumenta a conservação). Ela **não** substitui a camada Security: o guardrail "DDL não nasce
em produção" continua rodando antes e continua bloqueando.

**Estado de execução:** a regra está **declarada** e **ainda não executada**. O roteador em vigor
(`jev-router-v1.0`) não implementa o piso, e é isso que torna esta versão inerte: o roteador **recusa**
política de versão que ele não implementa. Entrar em vigor exige a homologação do Anderson **e** o card de
implementação do piso no roteador, no mesmo movimento.

## 4. Limiares de confiança

| Confiança | O que acontece |
|---|---|
| **≥ 0,85** | aceita a classificação do JEV |
| **0,65 – 0,85** | aceita, mas pela **lane mais conservadora** (high) |
| **< 0,65** | **abstém** e escala — nunca chuta |
| empate | lane mais conservadora + registro explícito de abstenção |

A lane conservadora é **declarada** em `limiares.lane_conservadora` (`high`). As duas formas em prosa que a
v1.0 usava — `fallback.acao` ("seguir pela lane conservadora configurada: high") e o parêntese de
`limiares.empate` ("lane mais conservadora (high) + …") — continuam no arquivo, mas como **conferência**:
divergência entre a chave e qualquer uma das prosas é política inconsistente e o roteador recusa executar.
A chave sozinha basta — é o que o verificador prova, tirando a prosa e mostrando que a lane continua sendo
resolvida.

O limite vale para cima e para baixo: confiança baixa **não** vira "lane barata para economizar". O erro
que custa caro é o **falso rebaixamento** — tratar como `small` o que era `critical` — e é justamente o
que a métrica acompanha.

## 5. Catálogo de modelos (fora da política)

Nomes e preços **não** ficam nesta política nem no YAML. Modelo e preço se confirmam no provedor **no
momento da configuração** — preço dentro de arquivo de política vira mentira em poucas semanas. Trocar de
modelo não muda a política: muda o catálogo, e o recibo registra qual modelo foi usado.

A **classe de custo** (`baixo`/`medio`/`alto`) do perfil é o que a métrica de custo por lane lê. Trocar a
classe muda a métrica; trocar o modelo não.

## 6. Guardrails determinísticos (camada Security)

- segredo nunca entra em prompt, log, recibo ou mensagem;
- empresa com `do_not_contact` ou `opt_out` não é contatada;
- DDL não nasce em produção (ADR-005);
- Sales AI não tem credencial de deploy nem altera código (matriz Dev × Sales);
- **falha de guardrail bloqueia, não libera**: dúvida na avaliação = `BLOCK`.

## 7. Fallback e modo degradado

Gatilho: JEV indisponível, timeout, saída fora do contrato ou versão de política desconhecida.

Ação, nesta ordem: aplica os guardrails → segue pela lane conservadora configurada (**high**) → registra
`degraded_mode: true` no recibo → **nunca contorna Human Approval**.

É proibido inferir confiança quando o JEV não respondeu: **ausência de resposta é abstinência**, não
permissão. Sem política carregada, o sistema não "segue com o que dá".

## 8. Recibo de decisão

Cada decisão grava: `decision_id`, `card_id`, `task_hash`, `lane`, `model_profile`, `selected_model`,
`effort`, `confidence`, `policy_version`, `router_version`, `timestamp`, `override`, `outcome`.

Segredo nunca entra no recibo. O recibo é o que permite reconstruir **por que** uma tarefa foi tratada
daquele jeito semanas depois — sem ele, "o orquestrador decidiu" não é auditável.

O contrato tem **13 campos** e não cresce por conveniência: `latencia` e `custo` ficam **fora** do recibo
de propósito. A latência é medida na camada de decisão pelo instrumento; o custo é derivado de
`lane → perfil → classe` da própria política. Campo a mais no recibo é recusado pelo roteador.

## 9. Métrica: o que é sucesso

O objetivo é **minimizar o custo total por card VERIFIED** — não minimizar tokens isoladamente.
Acompanhar: custo por card verificado, **custo por lane**, taxa de abstenção, taxa de escalação,
**falso rebaixamento**, latência por lane e regressão/retrabalho.

Critério de aceite: o custo por card VERIFIED cai **sem** aumento de regressão, retrabalho ou incidente.
Se cair o custo e subir a regressão, a política falhou.

### 9.1 Instrumentação (novo na v1.1)

O instrumento é `scripts/medir_metricas_por_lane.py`. Ele lê a classe de custo do perfil **do YAML**
(nunca do código), mede a latência da **camada de decisão** executando o roteador sobre o corpus anotado,
e agrega as decisões reais dos recibos. Métrica que ele não consegue medir sai no resultado como
**não medida, com o motivo** — nunca como zero.

| Métrica | Medível hoje | Fonte |
|---|---|---|
| `custo_por_lane` | sim | classe `custo` do perfil da lane, no YAML |
| `latencia_por_lane` | sim (camada de decisão) | execução do roteador sobre o corpus |
| `taxa_de_abstencao`, `taxa_de_escalacao`, `falso_rebaixamento` | sim | execução do roteador sobre o corpus |
| `custo_por_card_verified` | **não** | nenhum card do corpus foi executado até VERIFIED; o recibo não carrega custo |
| `regressao_ou_retrabalho` | **não** | exige histórico de execução de card, que o encanamento ainda não registra |
| `latencia_de_execucao_por_lane` | **não** | depende do modelo do catálogo, que a política proíbe fixar |

**Limite que não pode ser esquecido:** `high` e `critical` têm a **mesma** classe de custo (`alto`). O
custo por lane **não** enxerga rebaixamento `critical → high`; quem enxerga é `falso_rebaixamento`. Instrumentar
custo sem essa nota produz "sem problema" num cenário de rebaixamento crítico. Custo é unidade relativa
declarada, **nunca** dinheiro. E lane sem decisão no período reporta `null`, nunca zero: "não medi" e
"medi zero" são coisas diferentes.

## 10. Mudança de política

Mudar limiar, lane, precedência, guardrail, fallback, recibo ou métrica exige **nova versão**
(`v1.1`, `v2.0`) e registro do motivo — não edição silenciosa do arquivo. O verificador reprova política
cuja versão no YAML não seja a esperada nem divergência entre documento e arquivo.

O rito é o mesmo da v1.0: **documento + verificador + homologação do Anderson**. Homologar esta v1.1
significa (1) a palavra dele registrada em `docs/operations/registro-de-aprovacoes.md`, (2) o campo
`homologacao.registrada_em` preenchido no YAML, e (3) o card de implementação do piso por ambiente no
roteador — porque contrato declarado sem executor é pior que contrato ausente: dá sensação de guarda.

O que depende desta política e vem depois: a implementação do piso por ambiente no roteador e a medição
comparada (v1.0 × v1.1) sobre o mesmo corpus anotado — a diferença entre as duas rodadas é o efeito medido.
