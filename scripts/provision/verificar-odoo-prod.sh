#!/usr/bin/env bash
# STATUS (06/10/2026): derivado de homolog com a REVISAO DE INVERSaO CONCLUIDA. As listas de
#   isolamento apontam para dev E homolog (nao para o proprio ambiente), as portas sao as de
#   producao (Odoo 8080 / n8n 5682) e o hostname da borda e' tre.transformativa.com.br.
#   AINDA NAO EXECUTADO na VPS: nenhum container, volume ou rede de producao existe.
# Verificador do Odoo no ambiente de PRODUCAO do TRE. Roda NA VPS.
#
# Uso (na VPS):  bash verificar-odoo-prod.sh
# Saida: uma linha PASS/FALHOU por item + RESULTADO: ... ao final (exit != 0 se houver FALHOU).
#
# O que ele prova (e por que cada item existe):
#   * o servico esta' no ar e healthy;
#   * a porta e' LOOPBACK: quem expoe e' a borda. Publicar 0.0.0.0 aqui furaria a decisao de
#     que o publico entra por um unico ponto (e o Postgres nao publica nada);
#   * o banco e' o DE PRODUCAO (`odoo_prod`) e esta' inicializado — banco vazio responde 500;
#   * a rede e' propria de producao: se um container de dev OU de homolog aparecesse nela, os dois ambientes
#     estariam falando pela mesma rede (fim da paridade com isolamento);
#   * a imagem tem o MESMO digest do dev: ambiente que roda imagem diferente nao homologa nada;
#   * o dev continua intocado;
#   * nenhum segredo nos artefatos versionados.
set -uo pipefail

ENVFILE="${TRE_ODOO_ENV:-/opt/tre/prod/compose/odoo.env}"
DEV_ENV="${TRE_ODOO_ENV_DEV:-/opt/tre/dev/compose/odoo.env}"
HOSTNAME_EDGE="tre.transformativa.com.br"
PORTAS_PROIBIDAS=("0.0.0.0:8080" "[::]:8080")

falhas=0; itens=0
ok()   { itens=$((itens+1)); echo "PASS   $*"; }
ruim() { itens=$((itens+1)); falhas=$((falhas+1)); echo "FALHOU $*"; }

# 1. containers
for c in pg-odoo-prod odoo-prod; do
  estado="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
  [ "$estado" = "running" ] && ok "container $c running" || ruim "container $c nao esta running (estado: $estado)"
done
saude="$(docker inspect pg-odoo-prod --format '{{.State.Health.Status}}' 2>/dev/null || echo ausente)"
[ "$saude" = "healthy" ] && ok "pg-odoo-prod healthy" || ruim "pg-odoo-prod health=$saude"

# 2. porta so' em loopback / Postgres sem porta
mapa="$(docker port odoo-prod 2>/dev/null || true)"
case "$mapa" in
  *"127.0.0.1:8080"*) ok "odoo-prod publica em 127.0.0.1:8070" ;;
  *)                   ruim "odoo-prod nao publica 127.0.0.1:8080 (medido: ${mapa:-vazio})" ;;
esac
for proibida in "${PORTAS_PROIBIDAS[@]}"; do
  case "$mapa" in *"$proibida"*) ruim "odoo-prod publicado em $proibida (deveria ser so' loopback)";; esac
done
mpg="$(docker port pg-odoo-prod 2>/dev/null || true)"
[ -z "$mpg" ] && ok "pg-odoo-prod nao publica porta nenhuma" || ruim "pg-odoo-prod publica porta: $mpg"

# 3. banco de producao, inicializado, e prova de que o cluster NAO tem banco de outro ambiente
#    (o `psql` sem `-d` conecta num banco com o nome do usuario, que nao existe — medido: devolvia
#    vazio e a checagem acusava "banco inesperado: vazio" sem nada estar errado).
DBS="$(docker exec pg-odoo-prod psql -U odoo -d odoo_prod -tAc "select string_agg(datname,' ') from pg_database" 2>/dev/null | tr -s ' ')"
INI="$(docker exec pg-odoo-prod psql -U odoo -d odoo_prod -tAc "select 1 from information_schema.tables where table_schema='public' and table_name='ir_module_module'" 2>/dev/null || true)"
case " $DBS " in
  *" odoo_prod "*) ok "cluster de producao tem o banco odoo_prod (bancos: ${DBS:-vazio})" ;;
  *)                  ruim "cluster de producao sem o banco odoo_prod (bancos: ${DBS:-vazio})" ;;
esac
case " $DBS " in
  *" odoo_dev "*|*" odoo_homolog "*) ruim "o cluster de producao CONTEM banco de OUTRO ambiente (odoo_dev/odoo_homolog) — ambientes misturados" ;;
  *)              ok "bancos de dev e homolog ausentes do cluster de producao (isolamento de dados)" ;;
esac
[ "$INI" = "1" ] && ok "banco odoo_prod inicializado pelo Odoo (ir_module_module presente)" \
                 || ruim "banco odoo_prod NAO inicializado (o Odoo responderia 500)"

# 4. HTTP local
CODIGO="$(curl -s -o /dev/null -m 10 -w '%{http_code}' 'http://127.0.0.1:8080/web/login' || true)"
[ "$CODIGO" = "200" ] && ok "http://127.0.0.1:8080/web/login -> 200" || ruim "http://127.0.0.1:8070/web/login -> $CODIGO"

# 5. rede propria (nenhum container de outro ambiente nela)
NA_REDE="$(docker network inspect tre-odoo-prod --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | tr -s ' ')"
case " $NA_REDE " in
  *" odoo-dev "*|*" pg-odoo-dev "*|*" odoo-homolog "*|*" pg-odoo-homolog "*) ruim "rede tre-odoo-prod tem container de OUTRO ambiente: $NA_REDE" ;;
  *) case "$NA_REDE" in
       *pg-odoo-prod*odoo-prod*|*odoo-prod*pg-odoo-prod*) ok "rede tre-odoo-prod contem so' os containers de producao ($NA_REDE)" ;;
       *) ruim "rede tre-odoo-prod inesperada: ${NA_REDE:-vazia}" ;;
     esac ;;
esac

# 6. paridade de imagem com o dev (mesmo digest)
D_HOM="$(docker image inspect "$(docker inspect odoo-prod --format '{{.Config.Image}}')" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
D_DEV="$(docker image inspect "$(docker inspect odoo-dev --format '{{.Config.Image}}')" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
[ -n "$D_HOM" ] && [ "$D_HOM" = "$D_DEV" ] && ok "imagem identica ao dev ($D_HOM)" \
  || ruim "imagem difere do dev (prod=${D_HOM:-?} dev=${D_DEV:-?})"
if [ -f "$ENVFILE" ] && [ -f "$DEV_ENV" ]; then
  P_HOM="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ENVFILE")"; P_DEV="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$DEV_ENV")"
  [ "$P_HOM" != "$P_DEV" ] && ok "portas distintas por ambiente (prod=$P_HOM dev=$P_DEV)" \
    || ruim "producao e dev na MESMA porta ($P_HOM) — um dos dois nao subiu"
fi

# 7. borda: nome publico respondendo (401 = basic auth no lugar; 200/303 tambem aceitavel)
CODB="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "https://$HOSTNAME_EDGE/web/login" 2>/dev/null || echo X)"
case "$CODB" in 200|303|401) ok "borda: https://$HOSTNAME_EDGE/web/login -> $CODB (TLS publico + basic auth)" ;;
               *)             ruim "borda: https://$HOSTNAME_EDGE/web/login -> $CODB" ;; esac

# 8. dev intocado
DEV_ESTADO="$(docker inspect odoo-dev --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
[ "$DEV_ESTADO" = "running" ] && ok "dev intocado (odoo-dev running desde $(docker inspect odoo-dev --format '{{.State.StartedAt}}'))" \
                             || ruim "odoo-dev nao esta running (estado: $DEV_ESTADO)"

# 9. segredo nos artefatos versionados — exige FORMA de atribuicao, nao mencao.
#    A primeira versao reprovava a PROSA do par nao-secreto (o comentario que explica que a senha
#    vive em /etc/tre/... cita `admin_passwd`): verificador que acusa o que esta' certo queima a
#    confianca no proprio verificador. Comentario e' ignorado; o que conta e' `chave = valor`.
if grep -rInE "^[^#]*(POSTGRES_PASSWORD|admin_passwd|db_password)[[:space:]]*=[[:space:]]*[^[:space:]]" \
     /opt/tre/prod/compose/*.yml /opt/tre/prod/compose/*.env 2>/dev/null | grep -q .; then
  ruim "artefato de producao em /opt/tre/prod/compose ATRIBUI chave de segredo"
  grep -rInE "^[^#]*(POSTGRES_PASSWORD|admin_passwd|db_password)[[:space:]]*=" /opt/tre/prod/compose/*.yml /opt/tre/prod/compose/*.env 2>/dev/null | sed 's/^/       /'
else
  ok "nenhum segredo ATRIBUIDO nos artefatos de /opt/tre/prod/compose (prosa de comentario nao conta)"
fi

echo
if [ "$falhas" -eq 0 ]; then
  echo "RESULTADO: PROD_ODOO_OK itens=$itens falhas=0"
  exit 0
fi
echo "RESULTADO: PROD_ODOO_FALHOU itens=$itens falhas=$falhas"
exit 1
