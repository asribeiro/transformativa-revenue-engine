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

# Card TRE-W3-E01-T05 (`t_cb615018`): a ANCORA da atividade (`res_model` por nome de modelo, que o
# Odoo 19 nao escreve direto — campo related-readonly) e os dois campos de rastreio da atividade.
from . import mail_activity

# Card TRE-W3-E03-T01 (`t_85cb2838`): os eventos Odoo->PostgreSQL. A FILA (outbox do lado Odoo, com
# chave de idempotencia UNIQUE e remetente pela porta unica) e os tres detectores de fato — um por
# modelo de origem (`crm.lead`, `mail.activity`, `calendar.event`). Arquivos ADITIVOS: o card nao
# edita os arquivos de modelo dos cards irmaos (o unico ponto de contato e' esta lista).
from . import tf_evento_outbox
from . import eventos_crm_lead
from . import eventos_mail_activity
from . import eventos_calendar_event
