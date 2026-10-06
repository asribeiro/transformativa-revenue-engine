#!/usr/bin/env bash
# Verificador do Odoo no ambiente HOMOLOG do TRE. Roda NA VPS.
#
# Uso (na VPS):  bash verificar-odoo-homolog.sh
# Saida: uma linha PASS/FALHOU por item + RESULTADO: ... ao final (exit != 0 se houver FALHOU).
#
# O que ele prova (e por que cada item existe):
#   * o servico esta' no ar e healthy;
#   * a porta e' LOOPBACK: quem expoe e' a borda. Publicar 0.0.0.0 aqui furaria a decisao de
#     que o publico entra por um unico ponto (e o Postgres nao publica nada);
#   * o banco e' o DE HOMOLOG (`odoo_homolog`) e esta' inicializado — banco vazio responde 500;
#   * a rede e' propria de homolog: se um container do dev aparecesse nela, os dois ambientes
#     estariam falando pela mesma rede (fim da paridade com isolamento);
#   * a imagem tem o MESMO digest do dev: ambiente que roda imagem diferente nao homologa nada;
#   * o dev continua intocado;
#   * nenhum segredo nos artefatos versionados.
set -uo pipefail

ENVFILE="${TRE_ODOO_ENV:-/opt/tre/homolog/compose/odoo.env}"
DEV_ENV="${TRE_ODOO_ENV_DEV:-/opt/tre/dev/compose/odoo.env}"
HOSTNAME_EDGE="homolog.tre.transformativa.com.br"
PORTAS_PROIBIDAS=("0.0.0.0:8070" "[::]:8070")

falhas=0; itens=0
ok()   { itens=$((itens+1)); echo "PASS   $*"; }
ruim() { itens=$((itens+1)); falhas=$((falhas+1)); echo "FALHOU $*"; }

# 1. containers
for c in pg-odoo-homolog odoo-homolog; do
  estado="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
  [ "$estado" = "running" ] && ok "container $c running" || ruim "container $c nao esta running (estado: $estado)"
done
saude="$(docker inspect pg-odoo-homolog --format '{{.State.Health.Status}}' 2>/dev/null || echo ausente)"
[ "$saude" = "healthy" ] && ok "pg-odoo-homolog healthy" || ruim "pg-odoo-homolog health=$saude"

# 2. porta so' em loopback / Postgres sem porta
mapa="$(docker port odoo-homolog 2>/dev/null || true)"
case "$mapa" in
  *"127.0.0.1:8070"*) ok "odoo-homolog publica em 127.0.0.1:8070" ;;
  *)                   ruim "odoo-homolog nao publica 127.0.0.1:8070 (medido: ${mapa:-vazio})" ;;
esac
for proibida in "${PORTAS_PROIBIDAS[@]}"; do
  case "$mapa" in *"$proibida"*) ruim "odoo-homolog publicado em $proibida (deveria ser so' loopback)";; esac
done
mpg="$(docker port pg-odoo-homolog 2>/dev/null || true)"
[ -z "$mpg" ] && ok "pg-odoo-homolog nao publica porta nenhuma" || ruim "pg-odoo-homolog publica porta: $mpg"

# 3. banco de homolog, inicializado
BANCO="$(docker exec pg-odoo-homolog psql -U odoo -tAc 'select current_database()' 2>/dev/null || true)"
INI="$(docker exec pg-odoo-homolog psql -U odoo -d odoo_homolog -tAc "select 1 from information_schema.tables where table_schema='public' and table_name='ir_module_module'" 2>/dev/null || true)"
[ "$BANCO" = "odoo_homolog" ] && ok "banco do servico: odoo_homolog" || ruim "banco do servico inesperado: ${BANCO:-vazio}"
[ "$INI" = "1" ] && ok "banco odoo_homolog inicializado pelo Odoo (ir_module_module presente)" \
                 || ruim "banco odoo_homolog NAO inicializado (o Odoo responderia 500)"

# 4. HTTP local
CODIGO="$(curl -s -o /dev/null -m 10 -w '%{http_code}' 'http://127.0.0.1:8070/web/login' || true)"
[ "$CODIGO" = "200" ] && ok "http://127.0.0.1:8070/web/login -> 200" || ruim "http://127.0.0.1:8070/web/login -> $CODIGO"

# 5. rede propria (nenhum container de outro ambiente nela)
NA_REDE="$(docker network inspect tre-odoo-homolog --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | tr -s ' ')"
case " $NA_REDE " in
  *" odoo-dev "*|*" pg-odoo-dev "*) ruim "rede tre-odoo-homolog tem container do DEV: $NA_REDE" ;;
  *) case "$NA_REDE" in
       *pg-odoo-homolog*odoo-homolog*|*odoo-homolog*pg-odoo-homolog*) ok "rede tre-odoo-homolog contem so' os containers de homolog ($NA_REDE)" ;;
       *) ruim "rede tre-odoo-homolog inesperada: ${NA_REDE:-vazia}" ;;
     esac ;;
esac

# 6. paridade de imagem com o dev (mesmo digest)
D_HOM="$(docker image inspect "$(docker inspect odoo-homolog --format '{{.Config.Image}}')" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
D_DEV="$(docker image inspect "$(docker inspect odoo-dev --format '{{.Config.Image}}')" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
[ -n "$D_HOM" ] && [ "$D_HOM" = "$D_DEV" ] && ok "imagem identica ao dev ($D_HOM)" \
  || ruim "imagem difere do dev (homolog=${D_HOM:-?} dev=${D_DEV:-?})"
if [ -f "$ENVFILE" ] && [ -f "$DEV_ENV" ]; then
  P_HOM="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ENVFILE")"; P_DEV="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$DEV_ENV")"
  [ "$P_HOM" != "$P_DEV" ] && ok "portas distintas por ambiente (homolog=$P_HOM dev=$P_DEV)" \
    || ruim "homolog e dev na MESMA porta ($P_HOM) — um dos dois nao subiu"
fi

# 7. borda: nome publico respondendo (401 = basic auth no lugar; 200/303 tambem aceitavel)
CODB="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "https://$HOSTNAME_EDGE/web/login" 2>/dev/null || echo X)"
case "$CODB" in 200|303|401) ok "borda: https://$HOSTNAME_EDGE/web/login -> $CODB (TLS publico + basic auth)" ;;
               *)             ruim "borda: https://$HOSTNAME_EDGE/web/login -> $CODB" ;; esac

# 8. dev intocado
DEV_ESTADO="$(docker inspect odoo-dev --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
[ "$DEV_ESTADO" = "running" ] && ok "dev intocado (odoo-dev running desde $(docker inspect odoo-dev --format '{{.State.StartedAt}}'))" \
                             || ruim "odoo-dev nao esta running (estado: $DEV_ESTADO)"

# 9. segredo nos artefatos versionados
if grep -rIlE "password[[:space:]]*=|admin_passwd|POSTGRES_PASSWORD" /opt/tre/homolog/compose/*.yml /opt/tre/homolog/compose/*.env 2>/dev/null | grep -q .; then
  ruim "artefato de homolog em /opt/tre/homolog/compose cita chave de segredo"
else
  ok "nenhum segredo nos artefatos de /opt/tre/homolog/compose"
fi

echo
if [ "$falhas" -eq 0 ]; then
  echo "RESULTADO: HOMOLOG_ODOO_OK itens=$itens falhas=0"
  exit 0
fi
echo "RESULTADO: HOMOLOG_ODOO_FALHOU itens=$itens falhas=$falhas"
exit 1
