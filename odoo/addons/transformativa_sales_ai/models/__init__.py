# -*- coding: utf-8 -*-
# Pacote de modelos do modulo `transformativa_sales_ai`.
#
# Cada card de customizacao acrescenta o SEU arquivo e a SUA linha aqui — e' o unico ponto de
# contato entre cards paralelos (ver "hotspot" registrado nos cards TRE-W2-E04-T01/T02).
#
# Card TRE-W2-E04-T01: campos de dedup e IDs canonicos em `res.partner`.
from . import res_partner

# Card TRE-W2-E04-T02: campos de rastreio em `crm.lead`.
from . import crm_lead

# Card TRE-W2-E05-T01: `tf.process.opportunity`, a oportunidade canonica do lado Odoo
# (Data Contract V1.0, secao 2: `opportunity_owner: Odoo`).
from . import tf_process_opportunity
