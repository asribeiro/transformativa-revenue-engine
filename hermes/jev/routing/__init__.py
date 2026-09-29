# -*- coding: utf-8 -*-
"""Pacote do roteador do JEV (card TRE-W0-E04-T02).

O roteador integra o JEV ao Hermes Dev Harness: classifica a tarefa ANTES da LLM
(lane, perfil de modelo, esforco, revisao) segundo a politica
`hermes/jev/policy_v1.yaml`, aplica a precedencia
`Security -> Human Approval -> prioridade/dependencias -> JEV -> LLM` e grava o
recibo de decisao com os 13 campos declarados na politica.

Uso programatico:

    from hermes.jev.routing import carregar_politica, decidir
    politica = carregar_politica()
    resultado = decidir({"card_id": "...", "acao": "...", "lane_proposta": "small",
                         "confianca": 0.9}, politica=politica)
    resultado["recibo"]   # 13 campos, sem segredo
    resultado["decisao"]  # decidido, guardrails, degraded_mode, papel executor

Uso por linha de comando: `python3 hermes/jev/routing/router.py --card <id>`
(le o card do board) ou `--json`/`--json-file`/`--stdin` para entrada sintetica.

Este modulo nao decide nada por conta propria: limiares, lanes, perfis, acoes
proibidas por maquina e a lane de fallback vem SEMPRE do YAML.
"""
from .router import (  # noqa: F401
    LANE_DEGRADADA_PADRAO,
    ROUTER_VERSION,
    PoliticaInvalida,
    ReciboInvalido,
    acao_de_decisao_humana,
    acoes_de_decisao_humana,
    acoes_nunca_decididas_por_maquina,
    aplicar_limiares,
    campos_do_recibo,
    carregar_card_do_board,
    carregar_politica,
    carregar_politicas_de_papel,
    classificar_card,
    classificar_tarefa,
    config_da_lane,
    decidir,
    esforco_do_perfil,
    escrever_recibo,
    limiares,
    main,
    montar_recibo,
    ordem_lanes,
    perfil_da_lane,
    rotear,
)

__all__ = [
    "LANE_DEGRADADA_PADRAO",
    "ROUTER_VERSION",
    "PoliticaInvalida",
    "ReciboInvalido",
    "acao_de_decisao_humana",
    "acoes_de_decisao_humana",
    "acoes_nunca_decididas_por_maquina",
    "aplicar_limiares",
    "campos_do_recibo",
    "carregar_card_do_board",
    "carregar_politica",
    "carregar_politicas_de_papel",
    "classificar_card",
    "classificar_tarefa",
    "config_da_lane",
    "decidir",
    "esforco_do_perfil",
    "escrever_recibo",
    "limiares",
    "main",
    "montar_recibo",
    "ordem_lanes",
    "perfil_da_lane",
    "rotear",
]
