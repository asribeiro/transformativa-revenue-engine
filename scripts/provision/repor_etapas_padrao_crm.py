# -*- coding: utf-8 -*-
# Repoe as etapas PADRAO do modulo `crm` na configuracao de CRM — usado pelo rollback
# (scripts/provision/reverter-crm-dev.sh), depois de desfazer o funil da Transformativa.
#
# Card: TRE-W2-E02-T01 (`t_adea8e6b`). Runbook: docs/runbooks/odoo-crm-dev.md §5.
#
# POR QUE ESTE SCRIPT EXISTE (medido, nao suposto): `-u crm` NAO repoe os dados com
# `noupdate="1"` que foram apagados — medido em 01/10/2026 nesta instancia: com a tabela
# crm_stage vazia, `odoo -u crm --stop-after-init` carregou `crm/data/crm_stage_data.xml`
# (linha "loading crm/data/crm_stage_data.xml" no log) e o pipeline continuou VAZIO. O padrao
# anterior so' volta se for escrito de volta — e e' isso que este script faz, com os valores
# LITERAIS de `crm/data/crm_stage_data.xml` do Odoo 19.0 (nome, sequencia, cor, is_won) e com o
# xmlid do modulo, para o modulo voltar a ser dono dos proprios dados.
#
# Idempotente: o que ja' existe nao e' recriado.
# Ultima linha de sucesso: RESULTADO: CRM_PADRAO_REPOSTO ...
from typing import NoReturn

# Valores literais de /usr/lib/python3/dist-packages/odoo/addons/crm/data/crm_stage_data.xml
# (Odoo 19.0-20260926): xmlid, nome, sequencia, cor, is_won.
PADRAO = [
    ("crm.stage_lead1", "New", 1, 11, False),
    ("crm.stage_lead2", "Qualified", 2, 5, False),
    ("crm.stage_lead3", "Proposition", 3, 8, False),
    ("crm.stage_lead4", "Won", 70, 10, True),
]


def falhar(motivo: str) -> NoReturn:
    print("FALHOU %s" % motivo)
    raise RuntimeError(motivo)


_escopo = globals()
if "env" not in _escopo:
    falhar("este script roda por `odoo shell` (variavel `env` ausente)")
env = _escopo["env"]

Etapa = env["crm.stage"]
Xmlid = env["ir.model.data"]

for xmlid, nome, sequencia, cor, is_won in PADRAO:
    modulo, _, nome_xmlid = xmlid.partition(".")
    ja_existe = Xmlid.search([("module", "=", modulo), ("name", "=", nome_xmlid)], limit=1)
    if ja_existe and Etapa.browse(ja_existe.res_id).exists():
        print("PADRAO intacto: %s (%s seq=%s)" % (nome, xmlid, sequencia))
        continue
    achadas = Etapa.search([("name", "=", nome)], limit=1)
    if achadas:
        etapa = achadas[0]
        etapa.write({"sequence": sequencia, "color": cor, "is_won": is_won,
                     "team_ids": [(5, 0, 0)]})
        acao = "adotada"
    else:
        etapa = Etapa.create({"name": nome, "sequence": sequencia, "color": cor,
                              "is_won": is_won})
        acao = "criada"
    if not ja_existe:
        Xmlid.create({"module": modulo, "name": nome_xmlid, "model": "crm.stage",
                      "res_id": etapa.id})
    print("PADRAO %s: %s (id=%s seq=%s is_won=%s)" % (acao, nome, etapa.id, etapa.sequence, etapa.is_won))

# Conferencia: o padrao do modulo tem de estar completo e sem etapa sobrando.
nomes = [p[1] for p in PADRAO]
presentes = Etapa.search([("name", "in", nomes)])
medidos = sorted((e.name, e.sequence, bool(e.is_won)) for e in presentes)
esperados = sorted((nome, sequencia, is_won) for _, nome, sequencia, _, is_won in PADRAO)
if medidos != esperados:
    falhar("padrao do modulo incompleto/divergente: medido %s, esperado %s" % (medidos, esperados))
total = Etapa.search_count([])
if total != len(PADRAO):
    falhar("esperava %d etapas apos repor o padrao do modulo, medidas %d" % (len(PADRAO), total))

env.cr.commit()
print("RESULTADO: CRM_PADRAO_REPOSTO etapas=%d (%s)" % (total, ", ".join(nomes)))
