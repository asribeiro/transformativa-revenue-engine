# Registro de aprovações humanas

Obrigatório para toda ação sensível (`hermes/policies/human-approval.yaml`): quem aprovou, quando, o que foi
aprovado e qual é a evidência. Sem valor de credencial, nunca.

| Data | Aprovado por | O que | Evidência |
|---|---|---|---|
| 29/09/2026 | Anderson Ribeiro | Matriz de permissões Dev Harness × Sales AI e matriz de segregação de credenciais + gatilhos de rotação (política de secrets V1) | comentários nos cards `TRE-W0-E01-T02` e `TRE-W0-E02-T01` (board `transformativa-revenue-engine`) |
| 29/09/2026 (19:36 BRT, Telegram) | Anderson Ribeiro | Postura do roteador JEV para ação não classificada (defeitos `TRE-W0-E04-T02-D07`/`-D08`): **manter o critério estrito** — texto livre sem código canônico conhecido não executa, escala sempre — e **revisitar só depois do T05** (dispatch passar `acao_codigo`); fica valendo a regra de que, se afrouxar for preciso, a única forma aceita é nomear mais um código comum na política, nunca voltar a casar prosa | `docs/validation/jev-guardrails-e-fallback.md` §8.9 (veredito + palavra do dono), impacto medido em `scripts/analisar_impacto_de_afrouxar.py` (afrouxar compraria 4 de 32 casos do corpus; nenhum ganho nos 9 bloqueios), comentários nos cards `t_83242193` (D07) e `t_4200e054` (D08) |
| 29/09/2026 (Telegram) | Anderson Ribeiro | Seguir com o `TRE-W0-E04-T05` (ligar o roteador JEV ao dispatch do board), com o contrato do encaixe registrado no corpo do card **antes** de liberá-lo | comentário de unblock no card `t_6d326367`; contrato no corpo do card; execução provada em `docs/validation/jev-gate-no-dispatch.md` (suite `scripts/verificar_gate_jev.py`, 28 itens). **Pendente de ato do operador (root):** `bash deploy/hermes/aplicar_gate_jev.sh` aplica o encaixe em `/opt/hermes` + reiniciar o despachante — a autorização para ligar o roteador à execução automática é dele |
