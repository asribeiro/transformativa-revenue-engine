# -*- coding: utf-8 -*-
# DESFAZ a aplicacao do funil comercial (odoo/crm/funil-transformativa.yaml) no banco do Odoo
# DEV: remove as etapas declaradas e o ramo lateral criados pelo configurador. NAO roda
# sozinho: quem o executa e' scripts/provision/reverter-crm-dev.sh, DENTRO do container
# `odoo-dev`, por `odoo shell`.
#
# Card: TRE-W2-E02-T01 (`t_adea8e6b`). Runbook: docs/runbooks/odoo-crm-dev.md §5 (rollback).
#
# Guarda fail-closed: etapa ou pipeline que tenha oportunidade (crm.lead.stage_id/team_id) NAO
# e' removido — o script reprova em vez de mexer em dado de negocio.
#
# A reposicao das etapas do PROPRIO modulo (New, Qualified, Proposition, Won no estado de
# crm_stage_data.xml) e' feita pelo wrapper com `-u crm` DEPOIS deste script: e' o modulo que
# sabe qual e' o padrao anterior, nao este codigo.
#
# Ultima linha de sucesso: RESULTADO: CRM_DESFEITO ...
import yaml
from typing import NoReturn

CAMINHO_YAML = "/tmp/funil-transformativa.yaml"


def falhar(motivo: str) -> NoReturn:
    print("FALHOU %s" % motivo)
    raise RuntimeError(motivo)


_escopo = globals()
if "env" not in _escopo:
    falhar("este script roda por `odoo shell` (variavel `env` ausente)")
env = _escopo["env"]

with open(CAMINHO_YAML, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)

Etapa = env["crm.stage"]
Time = env["crm.team"]
Lead = env["crm.lead"]

removidas = []


def remover_etapa(etapa):
    nome = etapa.name
    leads = Lead.search_count([("stage_id", "in", etapa.ids)])
    if leads:
        falhar("etapa '%s' tem %d oportunidade(s) — recuso remover dado de negocio" % (nome, leads))
    print("ETAPA removida: %s (id=%s)" % (nome, etapa.id))
    etapa.unlink()
    removidas.append(nome)


for item in cfg["pipeline_padrao"]["etapas"]:
    for etapa in Etapa.search([("name", "=", item["nome"])]):
        remover_etapa(etapa)

lateral = cfg.get("ramo_lateral") or {}
if lateral.get("representacao") == "pipeline_proprio":
    for etapa in Etapa.search([("name", "=", lateral["nome"])]):
        remover_etapa(etapa)
    time_lateral = Time.search([("name", "=", lateral["time"])])
    for t in time_lateral:
        leads = Lead.search_count([("team_id", "=", t.id)])
        if leads:
            falhar("time '%s' tem %d oportunidade(s) — recuso remover dado de negocio" % (t.name, leads))
        print("TIME removido: %s (id=%s)" % (t.name, t.id))
        t.unlink()

env.cr.commit()
print("RESULTADO: CRM_DESFEITO etapas_removidas=%d (%s)" % (len(removidas), ", ".join(removidas)))
