# -*- coding: utf-8 -*-
# Aplica a declaracao do funil comercial (odoo/crm/funil-transformativa.yaml) no banco do Odoo
# DEV, pelo ORM. NAO roda sozinho: quem o executa e' scripts/provision/configurar-crm-dev.sh,
# DENTRO do container `odoo-dev`, por `odoo shell`:
#
#   docker cp funil-transformativa.yaml odoo-dev:/tmp/funil-transformativa.yaml
#   docker exec -i odoo-dev odoo shell -d odoo_dev --no-http < aplicar_funil_crm.py
#
# Card: TRE-W2-E02-T01 (`t_adea8e6b`). Runbook: docs/runbooks/odoo-crm-dev.md.
#
# Idempotente: rodar duas vezes nao cria duplicata (o casamento e' por NOME da etapa: a etapa
# existente e' adotada/atualizada) e na segunda vez nao muda nada.
#
# Etapa fora da declaracao: em `crm.stage` do Odoo 19 NAO existe campo `active` (medido:
# campos = color, fold, is_won, name, requirements, rotting_threshold_days, sequence, team_ids,
# ...), ou seja, etapa nao se ARQUIVA — a reconciliacao declarada e' REMOVER. Guarda
# fail-closed: etapa que tenha oportunidade (crm.lead.stage_id) NAO e' removida; o script
# reprova em vez de mexer em dado de negocio.
#
# Evidencia: tudo vai para stdout (o wrapper guarda). A ultima linha e'
#   RESULTADO: CRM_CONFIGURADO ...   (sucesso)
# e qualquer reprovacao sai como "FALHOU <motivo>" seguida de excecao.
import yaml
from typing import NoReturn

CAMINHO_YAML = "/tmp/funil-transformativa.yaml"


def falhar(motivo: str) -> NoReturn:
    print("FALHOU %s" % motivo)
    raise RuntimeError(motivo)


# O `odoo shell` injeta `env` no escopo do script executado — ele nao vem de `import`.
_escopo = globals()
if "env" not in _escopo:
    falhar("este script roda por `odoo shell` (variavel `env` ausente)")
env = _escopo["env"]

with open(CAMINHO_YAML, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)

modulo = env["ir.module.module"].search([("name", "=", "crm")])
if modulo.state != "installed":
    falhar("modulo `crm` nao esta instalado no banco (estado=%s)" % modulo.state)

Time = env["crm.team"]
Etapa = env["crm.stage"]
Lead = env["crm.lead"]


def estado(s):
    times = ",".join(s.team_ids.mapped("name")) or "-"
    return "%s|seq=%s|won=%s|times=%s" % (s.name, s.sequence, s.is_won, times)


def remover_etapa(etapa, motivo):
    nome = etapa.name  # guardado ANTES do unlink: depois o recordset nao le mais campo
    leads = Lead.search_count([("stage_id", "in", etapa.ids)])
    if leads:
        falhar("etapa '%s' tem %d oportunidade(s) — recuso remover dado de negocio "
               "(motivo da remocao: %s)" % (nome, leads, motivo))
    print("ETAPA removida: %s (%s)" % (estado(etapa), motivo))
    etapa.unlink()
    return nome


print("MODULO crm=%s banco=%s" % (modulo.state, env.cr.dbname))

xmlid = cfg["pipeline_padrao"]["xmlid"]
try:
    time = env.ref(xmlid)
except ValueError:
    falhar("pipeline padrao nao encontrado pelo xmlid %s (o modulo que o cria esta instalado?)" % xmlid)
print("PIPELINE xmlid=%s id=%s nome=%s" % (xmlid, time.id, time.name))

print("ANTES:")
for s in Etapa.search([]):
    print("  " + estado(s))

declaradas = []
for item in cfg["pipeline_padrao"]["etapas"]:
    nome = item["nome"]
    seq = item["sequencia"]
    is_won = bool(item.get("is_won", False))
    achadas = Etapa.search([("name", "=", nome)])
    if achadas:
        etapa = achadas[0]
        for sobra in achadas[1:]:  # duplicata pre-existente por nome
            remover_etapa(sobra, "duplicata do nome '%s'" % nome)
        etapa.write({"sequence": seq, "is_won": is_won, "team_ids": [(6, 0, [time.id])]})
        acao = "atualizada"
    else:
        etapa = Etapa.create({"name": nome, "sequence": seq, "is_won": is_won,
                              "team_ids": [(6, 0, [time.id])]})
        acao = "criada"
    print("ETAPA %s: %s" % (acao, estado(etapa)))
    declaradas.append(nome)

# Etapa do pipeline (ou global, sem time) que nao esta' na declaracao e' removida.
removidas = []
if cfg.get("reconciliacao", {}).get("etapas_extras") == "remover":
    for etapa in Etapa.search([]):
        if etapa.name in declaradas:
            continue
        if not etapa.team_ids or time in etapa.team_ids:
            removidas.append(remover_etapa(etapa, "nao esta na declaracao do funil"))

lateral = cfg.get("ramo_lateral") or {}
representacao = lateral.get("representacao")
if representacao == "pipeline_proprio":
    time_lateral = Time.search([("name", "=", lateral["time"])])
    if time_lateral:
        print("RAMO time existente: %s (id=%s)" % (time_lateral.name, time_lateral.id))
    else:
        time_lateral = Time.create({"name": lateral["time"]})
        print("RAMO time criado: %s (id=%s)" % (time_lateral.name, time_lateral.id))
    achadas = Etapa.search([("name", "=", lateral["nome"])])
    if achadas:
        etapa_lateral = achadas[0]
        etapa_lateral.write({"sequence": lateral["sequencia"], "is_won": False,
                             "team_ids": [(6, 0, [time_lateral.id])]})
        print("RAMO etapa atualizada: %s" % estado(etapa_lateral))
    else:
        etapa_lateral = Etapa.create({"name": lateral["nome"], "sequence": lateral["sequencia"],
                                      "is_won": False,
                                      "team_ids": [(6, 0, [time_lateral.id])]})
        print("RAMO etapa criada: %s" % estado(etapa_lateral))
elif representacao == "etapa_no_funil":
    achadas = Etapa.search([("name", "=", lateral["nome"])])
    if not achadas:
        etapa_lateral = Etapa.create({"name": lateral["nome"], "sequence": lateral["sequencia"],
                                      "is_won": False, "team_ids": [(6, 0, [time.id])]})
    else:
        etapa_lateral = achadas[0]
        etapa_lateral.write({"sequence": lateral["sequencia"], "is_won": False,
                             "team_ids": [(6, 0, [time.id])]})
    print("RAMO etapa (no funil principal): %s" % estado(etapa_lateral))
else:
    print("RAMO nao configurado nesta declaracao (representacao=%s)" % representacao)

campos_minimos = cfg.get("campos_minimos") or {}
modelo = campos_minimos.get("modelo")
faltando = [c for c in campos_minimos.get("campos", [])
            if not env["ir.model.fields"].search_count([("model", "=", modelo), ("name", "=", c)])]
if faltando:
    falhar("campos minimos ausentes em %s: %s" % (modelo, ",".join(faltando)))
print("CAMPOS_MINIMOS ok (%d campos em %s)" % (len(campos_minimos.get("campos", [])), modelo))

print("DEPOIS:")
for s in Etapa.search([]):
    print("  " + estado(s))

etapas_ganho = Etapa.search([("is_won", "=", True)])
if len(etapas_ganho) != 1 or etapas_ganho.name != "Won":
    falhar("esperava exatamente uma etapa de ganho, chamada Won; medido: %s"
           % (", ".join(etapas_ganho.mapped("name")) or "nenhuma"))
if Lead.search_count([("team_id", "!=", time.id), ("team_id", "!=", False)]):
    falhar("existe oportunidade em outro time — o funil configurado nao cobre esse time")

env.cr.commit()

print("RESULTADO: CRM_CONFIGURADO etapas=%d removidas=%d etapa_ganho=%s pipeline=%s banco=%s"
      % (len(declaradas), len(removidas), etapas_ganho.name, time.name, env.cr.dbname))
