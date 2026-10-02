# -*- coding: utf-8 -*-
from . import test_modulo_base
from . import test_res_partner_dedup
from . import test_crm_lead_rastreio
from . import test_oportunidade_canonica
from . import test_acl_seguranca
from . import test_views_sales_ai

# Card TRE-W3-E01-T01 (`t_e0489efc`): aceite da API controlada (rota HTTP `POST /tf/api/v1/*`).
from . import test_api_controlada

# Card TRE-W3-E01-T04 (`t_8b2ed1b7`): aceite da operacao de escrita de negocio `oportunidade_upsert`.
from . import test_oportunidade_upsert
