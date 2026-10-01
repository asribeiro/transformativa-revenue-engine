# -*- coding: utf-8 -*-
# Pacote do modulo `transformativa_sales_ai` (base: card TRE-W2-E03-T01).
#
# A base nasceu sem modelo de proposito. A primeira card de customizacao (TRE-W2-E04-T01,
# campos de dedup e IDs canonicos em `res.partner`) acrescentou o pacote `models/` — cada card
# seguinte importa o SEU modulo dentro de `models/__init__.py`, mantendo este arquivo estavel.
from . import models
