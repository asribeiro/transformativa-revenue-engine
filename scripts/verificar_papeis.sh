#!/usr/bin/env bash
# Aceite TRE-W0-E02-T01: verifica a separacao dos papeis Dev Harness x Sales AI.
# Le as politicas e checa invariantes. Um item por linha, OK ou FALHOU.
set -u
P=hermes/policies
FALHAS=0
chk() { if [ "$2" = "1" ]; then echo "OK    $1"; else echo "FALHOU $1"; FALHAS=$((FALHAS+1)); fi; }

[ -f "$P/dev-harness.yaml" ] && [ -f "$P/sales-ai.yaml" ] && [ -f "$P/human-approval.yaml" ]; D=$?
chk "politicas dos dois papeis existem" $([ $D -eq 0 ] && echo 1 || echo 0)

grep -q "^role: sales-ai" "$P/sales-ai.yaml" 2>/dev/null; chk "papel sales-ai declarado" $([ $? -eq 0 ] && echo 1 || echo 0)
grep -q "^role: dev-harness" "$P/dev-harness.yaml" 2>/dev/null; chk "papel dev-harness declarado" $([ $? -eq 0 ] && echo 1 || echo 0)

# Sales AI nao pode deploy/codigo/DDL
S=$(cat "$P/sales-ai.yaml")
echo "$S" | grep -q "executar deploy"            ; chk "sales-ai proibido de deploy" $([ $? -eq 0 ] && echo 1 || echo 0)
echo "$S" | grep -q "alterar codigo do repositorio"; chk "sales-ai proibido de alterar codigo" $([ $? -eq 0 ] && echo 1 || echo 0)
echo "$S" | grep -q "aplicar DDL"                ; chk "sales-ai proibido de DDL/migration" $([ $? -eq 0 ] && echo 1 || echo 0)
echo "$S" | grep -q "publicar ou alterar workflow"; chk "sales-ai proibido de publicar workflow n8n" $([ $? -eq 0 ] && echo 1 || echo 0)

# credenciais: cada papel tem GITHUB_TOKEN? so o dev
echo "$S" | grep -q "GITHUB_TOKEN"               ; chk "sales-ai NAO recebe GITHUB_TOKEN" $([ $? -ne 0 ] && echo 1 || echo 0)
D2=$(cat "$P/dev-harness.yaml")
echo "$D2" | grep -q "GITHUB_TOKEN"              ; chk "dev-harness recebe GITHUB_TOKEN (push)" $([ $? -eq 0 ] && echo 1 || echo 0)
echo "$D2" | grep -q "TRE_TITAN"                 ; chk "dev-harness NAO recebe credencial Titan" $([ $? -ne 0 ] && echo 1 || echo 0)
echo "$S" | grep -q "TRE_TITAN"                  ; chk "sales-ai recebe credencial Titan (outbound)" $([ $? -eq 0 ] && echo 1 || echo 0)

# approval
grep -q "primeiro contato outbound" "$P/human-approval.yaml" 2>/dev/null; chk "primeiro contato exige Human Approval" $([ $? -eq 0 ] && echo 1 || echo 0)
grep -q "promocao de release para producao" "$P/human-approval.yaml" 2>/dev/null; chk "promocao de release exige Human Approval" $([ $? -eq 0 ] && echo 1 || echo 0)
echo
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
