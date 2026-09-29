# JEV Decision Policy V1

**Versão:** `jev-policy-v1.0` · **Congelada em:** 29/09/2026 · **Card:** TRE-W0-E04-T01
**Forma legível por máquina:** [`hermes/jev/policy_v1.yaml`](../../hermes/jev/policy_v1.yaml)
**Origem no baseline:** docs 13 (Hermes Implementation Brief) e 14 (JEV Decision Layer) do baseline V1.1.0

Este documento e o YAML dizem a mesma coisa — e há um verificador que prova isso
(`scripts/verificar_jev_policy.py`). Divergência entre os dois é falha, não ambiguidade.

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

## 4. Limiares de confiança

| Confiança | O que acontece |
|---|---|
| **≥ 0,85** | aceita a classificação do JEV |
| **0,65 – 0,85** | aceita, mas pela **lane mais conservadora** (high) |
| **< 0,65** | **abstém** e escala — nunca chuta |
| empate | lane mais conservadora + registro explícito de abstenção |

O limite vale para cima e para baixo: confiança baixa **não** vira "lane barata para economizar". O erro
que custa caro é o **falso rebaixamento** — tratar como `small` o que era `critical` — e é justamente o
que a métrica acompanha.

## 5. Catálogo de modelos (fora da política)

Nomes e preços **não** ficam nesta política nem no YAML. Modelo e preço se confirmam no provedor **no
momento da configuração** — preço dentro de arquivo de política vira mentira em poucas semanas. Trocar de
modelo não muda a política: muda o catálogo, e o recibo registra qual modelo foi usado.

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

## 9. Métrica: o que é sucesso

O objetivo é **minimizar o custo total por card VERIFIED** — não minimizar tokens isoladamente.
Acompanhar: custo por card verificado, taxa de abstenção, taxa de escalação, **falso rebaixamento**,
latência por lane e regressão/retrabalho.

Critério de aceite: o custo por card VERIFIED cai **sem** aumento de regressão, retrabalho ou incidente.
Se cair o custo e subir a regressão, a política falhou.

## 10. Mudança de política

Mudar limiar, lane, precedência, guardrail, fallback, recibo ou métrica exige **nova versão**
(`v1.1`, `v2.0`) e registro do motivo — não edição silenciosa do arquivo. O verificador reprova política
cuja versão no YAML não seja a esperada nem divergência entre documento e arquivo.

O que depende desta política e vem depois: integração ao Dev Harness (T02), benchmark anotado (T03) e
validação de guardrails/fallback (T04).
