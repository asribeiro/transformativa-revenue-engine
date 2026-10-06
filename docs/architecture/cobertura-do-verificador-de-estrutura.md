# Cobertura do verificador de estrutura — decisão (ADR)

- **Data:** 02/10/2026 · **Decisor:** Anderson Ribeiro (dono) · **Estado:** DECIDIDA
- **Origem:** card `t_c77ca273` — DEFEITO [cobertura] do `TRE-W3-E03-T01`
- **Registro:** `docs/operations/registro-de-aprovacoes.md` (02/10/2026, decisão "C")

## Problema medido

`scripts/verificar_estrutura.sh` mantém **lista fixa** de artefatos do módulo. Os **17 arquivos novos** do
E03-T01 (models, data/ir_cron, tests, n8n/contracts, n8n/workflow, scripts/n8n, scripts/odoo, runbook) ficaram
fora: o verificador imprime `PASS (0 falhas)` **mesmo se qualquer um deles sumir da árvore versionada**.
Precedente da casa: `t_a1bed5fa`.

## Decisão: **opção C — híbrida**

1. **Descoberta automática.** Os artefatos são enumerados da árvore versionada (`git ls-files` no caminho do
   módulo), não de lista fixa.
2. **Isenções declaradas.** Arquivo versionado de isenções; cada uma com justificativa, responsável e data.
3. **Fail-closed.** Artefato novo que não está **nem coberto nem isento** → reprova, nomeando o arquivo e o que
   fazer (como cobrir ou como isentar).

## Alternativas descartadas

- **A — só descoberta:** sem exceções, gera falso vermelho em arquivo legítimo e trava worker.
- **B — manifesto por card:** cobertura exata, mas depende de disciplina; manifesto vazio passaria silencioso.
- **Mínima — acrescentar os 17 na lista fixa:** remendo. É exatamente o que reabriu o furo entre o card anterior
  e o E03-T01.

## Rito da isenção (o custo aceito pelo dono)

Toda isenção exige: arquivo/padrão, **justificativa**, **responsável** e **data**; revisão na abertura de onda.
Isenção sem justificativa é inválida e o verificador reprova.

## Consequências e riscos

- **Ganho:** a classe do defeito (artefato novo fora da rede de estrutura) deixa de existir por construção; nada
  escapa em silêncio, porque o dente é a exceção explícita.
- **Risco 1 —** o verificador vira ponto de merge disputado → mitigar mantendo o script pequeno e as isenções em
  arquivo separado.
- **Risco 2 —** isenção apodrece → mitigar com revisão obrigatória por onda.
- **Risco 3 —** falso vermelho bloqueia card legítimo → mitigar com mensagem acionável (arquivo, motivo, como
  isentar).

## Aprovação

Anderson Ribeiro, **02/10/2026**, via Telegram (decisão "C"), registrada em
`docs/operations/registro-de-aprovacoes.md`.
