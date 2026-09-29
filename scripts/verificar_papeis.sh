#!/usr/bin/env bash
# Aceite TRE-W0-E02-T01: separacao dos papeis Dev Harness x Sales AI.
# Checa as CONCESSOES (secoes credenciais_permitidas / pode), nao o arquivo inteiro:
# citar uma credencial em "credenciais_proibidas" nao e conceder.
set -u
P=hermes/policies
FALHAS=0
chk() { if [ "$2" = "1" ]; then echo "OK    $1"; else echo "FALHOU $1"; FALHAS=$((FALHAS+1)); fi; }
# extrai o bloco de uma chave de topo do YAML (da linha '<sec>:' ate a proxima chave de topo)
secao() { awk -v s="$1:" 'index($0, s)==1{f=1;next} /^[a-z_]+:/{f=0} f' "$2"; }
tem()   { secao "$1" "$2" | grep -q "$3"; }
naotem(){ ! secao "$1" "$2" | grep -q "$3"; }

chk "politicas dos papeis existem" \
  $([ -f "$P/dev-harness.yaml" ] && [ -f "$P/sales-ai.yaml" ] && [ -f "$P/human-approval.yaml" ] && echo 1 || echo 0)
chk "papel sales-ai declarado"  $(grep -q "^role: sales-ai"   "$P/sales-ai.yaml"    && echo 1 || echo 0)
chk "papel dev-harness declarado" $(grep -q "^role: dev-harness" "$P/dev-harness.yaml" && echo 1 || echo 0)

# 1) proibicoes explicitas do Sales AI
tem nao_pode "$P/sales-ai.yaml" "executar deploy"             ; chk "sales-ai proibido de deploy" $([ $? -eq 0 ] && echo 1 || echo 0)
tem nao_pode "$P/sales-ai.yaml" "alterar codigo do repositorio"; chk "sales-ai proibido de alterar codigo" $([ $? -eq 0 ] && echo 1 || echo 0)
tem nao_pode "$P/sales-ai.yaml" "aplicar DDL"                 ; chk "sales-ai proibido de DDL/migration" $([ $? -eq 0 ] && echo 1 || echo 0)
tem nao_pode "$P/sales-ai.yaml" "publicar ou alterar workflow" ; chk "sales-ai proibido de publicar workflow n8n" $([ $? -eq 0 ] && echo 1 || echo 0)

# 2) credenciais CONCEDIDAS (nao pode receber credencial de deploy)
naotem credenciais_permitidas "$P/sales-ai.yaml" "GITHUB_TOKEN"; chk "sales-ai NAO recebe GITHUB_TOKEN" $([ $? -eq 0 ] && echo 1 || echo 0)
naotem credenciais_permitidas "$P/sales-ai.yaml" "TRE_N8N_API_KEY"; chk "sales-ai NAO recebe chave de publicacao do n8n" $([ $? -eq 0 ] && echo 1 || echo 0)
tem    credenciais_permitidas "$P/sales-ai.yaml" "TRE_TITAN"   ; chk "sales-ai recebe credencial Titan (outbound)" $([ $? -eq 0 ] && echo 1 || echo 0)
tem    credenciais_permitidas "$P/dev-harness.yaml" "GITHUB_TOKEN"; chk "dev-harness recebe GITHUB_TOKEN (push)" $([ $? -eq 0 ] && echo 1 || echo 0)
naotem credenciais_permitidas "$P/dev-harness.yaml" "TRE_TITAN"; chk "dev-harness NAO recebe credencial Titan" $([ $? -eq 0 ] && echo 1 || echo 0)
tem    credenciais_proibidas "$P/sales-ai.yaml" "GITHUB_TOKEN"; chk "GITHUB_TOKEN declarado como proibido ao sales-ai" $([ $? -eq 0 ] && echo 1 || echo 0)

# 3) Direto: quem tem permissao de deploy e so o Dev
naotem pode "$P/sales-ai.yaml" "deploy"; chk "sales-ai nao lista deploy entre as permissoes" $([ $? -eq 0 ] && echo 1 || echo 0)
tem    pode "$P/dev-harness.yaml" "promocao de release"; chk "dev-harness lista promocao de release" $([ $? -eq 0 ] && echo 1 || echo 0)

# 4) Human Approval
tem exige_aprovacao "$P/human-approval.yaml" "primeiro contato outbound"; chk "primeiro contato exige Human Approval" $([ $? -eq 0 ] && echo 1 || echo 0)
tem exige_aprovacao "$P/human-approval.yaml" "promocao de release para producao"; chk "promocao de release exige Human Approval" $([ $? -eq 0 ] && echo 1 || echo 0)
tem nunca_automatico "$P/human-approval.yaml" "publicar conteudo"; chk "publicar em nome da Transformativa nunca e automatico" $([ $? -eq 0 ] && echo 1 || echo 0)
echo
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
