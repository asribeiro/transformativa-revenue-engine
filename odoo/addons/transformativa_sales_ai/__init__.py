# -*- coding: utf-8 -*-
# Pacote do modulo `transformativa_sales_ai`.
#
# A base (manifesto, versao, dependencias) e' do card TRE-W2-E03-T01. A base nasceu sem modelo de
# proposito: cada card de customizacao acrescenta o SEU arquivo dentro de `models/` e importa a
# SUA linha em `models/__init__.py`, mantendo este arquivo estavel.
from . import models

# Card TRE-W3-E01-T01 (`t_e0489efc`): a API controlada (controlador HTTP `POST /tf/api/v1/*`).
# O pacote `api` NAO entra aqui de proposito — ele e' importado pelo controlador, e a politica
# (`api/politica_api.json`) so' e' lida em tempo de chamada: politica ilegivel recusa a chamada
# (fail-closed) em vez de derrubar a instalacao do modulo.
from . import controllers
