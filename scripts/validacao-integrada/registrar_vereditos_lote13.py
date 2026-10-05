#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra no artefato TRE-W2 os vereditos da validacao integrada do LOTE 13.

Lote 13 = 10 cards W2/Odoo da fila (ordens 120..129). Cada card validado com:
  2 passadas identicas (mesmo veredito) + prova de dente (mutacao que TEM de reprovar),
  sempre em clone isolado do commit publicado, com bancos/rede descartaveis e o
  `odoo_dev` conferido intacto antes e depois (ADR-005).

Uso:  python3 scripts/validacao-integrada/registrar_vereditos_lote13.py [--dry-run]
"""
import json
import sys

ARQ = "control-plane/deliveries/W2-odoo-e-seguranca.json"
COMMIT = "9e638f7986fccc293d2acfb7f1e9bc0922839b82"
AMBIENTE = (
    "VPS Contabo vmi3619453: clone isolado /tmp/tre_lote13/repo do commit 9e638f7 "
    "(bundle do develop, 765 arquivos versionados); por card, banco e rede descartaveis "
    "proprios; `odoo_dev` com os mesmos bancos antes e depois; homolog/prod sem nenhum "
    "arquivo (ADR-005), conferido pelo proprio verifier"
)
BY = "Hermes — validacao integrada do lote 13 (cards W2 da fila, ordens 120..129)"
DATA = "2026-10-04"
CARDS = [
    {
        "id": "TRE-W2-E02-T01",
        "title": "Configurar CRM básico",
        "task": "t_adea8e6b",
        "evidence": (
            "2 passadas identicas: `RESULTADO: CRM_DEV_OK (29 itens, 0 falhas)` (exit 0) em ambas; "
            "unica diferenca entre os logs e' o carimbo de tempo da medicao. Dente: alvo mutado "
            "(TRE_ODOO_BANCO apontando para banco inexistente) -> `CRM_DEV_FALHOU (13 itens, 3 falhas)`, "
            "exit 1. Verifier read-only: le o estado vivo (psql dentro de pg-odoo-dev, containers, HTTP, "
            "UFW) e nao escreve nada no ambiente."
        ),
        "verification": {
            "verificador": "scripts/provision/verificar-crm-dev.sh",
            "natureza": "mede o ESTADO VIVO do odoo_dev (nao escreve)",
            "passadas": 2,
            "dente": "mutacao do alvo -> CRM_DEV_FALHOU (13 itens, 3 falhas), exit 1",
            "log": "/tmp/tre_lote13/card123_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E04-T01",
        "title": "Customizar res.partner",
        "task": "t_adee6ad7",
        "evidence": (
            "2 passadas identicas: `RESULTADO: RES_PARTNER_OK (64 itens, 0 falhas)` (exit 0) em ambas; "
            "diferem so' nomes efemeros de container e diretorio temporario. Prova de dente do proprio "
            "verifier (`--prova-de-dente`): baseline NAO mutado mede verde + 3 mutacoes, cada uma em copia "
            "propria, todas reprovando -> `RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas)`. Aceite de "
            "5 passos: banco limpo `tre_e04_t01_res_partner`, testes do Odoo, catalogo do PostgreSQL (AC1), "
            "criacao/consulta de parceiro sintetico pelo ORM (AC3), desinstalacao (rollback declarado). "
            "Banco e rede descartaveis removidos ao fim."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-res-partner.sh",
            "natureza": "instalacao em banco limpo + testes + leitura no banco + ORM + rollback",
            "passadas": 2,
            "dente": "modo proprio do verifier -> RES_PARTNER_DENTE_OK (3 provas, 0 falhas)",
            "banco_descartavel": "tre_e04_t01_res_partner",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card124_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E06-T01",
        "title": "Criar views Sales AI",
        "task": "t_cf7519c9",
        "evidence": (
            "2 passadas identicas: `RESULTADO: VIEWS_OK (83 itens, 0 falhas)` (exit 0) em ambas — o mesmo "
            "numero de itens do aceite original documentado no registro de execucoes. Prova de dente "
            "(`--prova-de-dente`): baseline NAO mutado mede verde (`VIEWS_OK (83 itens, 0 falhas)` em "
            "`tre_e06t01_views_baseline`) + 2 mutacoes em copia propria, cada uma reprovando (uma delas "
            "derrubou 27 itens) -> `RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas)`. Aceite de 6 passos: "
            "banco limpo `tre_e06t01_views`, testes do modulo, leitura no banco (views/menus/acao/arch), "
            "prova independente (`scripts/odoo/provar_views_sales_ai.py`, 21 itens), rollback por "
            "desinstalacao pelo ORM, limpeza com dev/homolog/prod intactos."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-views-sales-ai.sh",
            "prova_independente": "scripts/odoo/provar_views_sales_ai.py (21 itens, sem traceback)",
            "passadas": 2,
            "dente": "modo proprio -> VIEWS_DENTE_OK (2 provas, 0 falhas)",
            "banco_descartavel": "tre_e06t01_views",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card126_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E04-T02",
        "title": "Customizar crm.lead",
        "task": "t_d3bd6660",
        "evidence": (
            "2 passadas identicas: `RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas)` (exit 0) em ambas. Prova "
            "de dente (`--prova-de-dente`): baseline NAO mutado mede verde (`CRM_LEAD_OK (43 itens, 0 "
            "falhas)` em `tre_e04_t02_crm_lead_baseline`) + 2 mutacoes em copia propria, ambas reprovando "
            "(16 falhas e 1 falha) -> conjunto `CRM_LEAD_DENTE_OK`. Aceite de 6 passos, incluindo o "
            "confronto modulo x contrato congelado (`docs/data/data_contract_v1.json`) como passo 0 e "
            "rollback por desinstalacao. Banco descartavel `tre_e04_t02_crm_lead`."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-crm-lead-odoo.sh",
            "contrato": "docs/data/data_contract_v1.json (confronto como passo 0/6)",
            "passadas": 2,
            "dente": "modo proprio -> baseline verde + 2 mutacoes reprovando (16 e 1 falhas)",
            "banco_descartavel": "tre_e04_t02_crm_lead",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card127_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E03-T01-D05",
        "title": "Publicar UMA vez o verificador consolidado (D01+D03+D04)",
        "task": "t_de461d14",
        "evidence": (
            "2 passadas identicas: `PASS (41 itens, 0 falhas, 1 pulados)` (exit 0) em ambas; unica "
            "diferenca entre os logs e' o nome do diretorio temporario da suite. Suite do lado do Hermes "
            "(nao do VPS), rodada com o venv do /opt/hermes contra destino temporario. Cobre o encaixe do "
            "gate de hotspot: A1/A2 formas de declaracao, B3/C4 balde `skipped_hotspot`, F1/F2 `--check` "
            "sem alterar o runtime, F5/F6 `--aplicar` (6 ancoras + adaptador) com o kernel compilando, "
            "F7 idempotencia e **F8 rollback byte a byte** (`--reverter` devolve os 3 modulos ao estado "
            "anterior e remove o adaptador). LIMITACAO DECLARADA: F3 (adaptador instalado == versionado no "
            "repo) ficou PULADO por exigir root em /opt/hermes — `deploy/hermes/aplicar_hotspot.sh` e' o "
            "caminho do operador para esse passo."
        ),
        "verification": {
            "verificador": "scripts/verificar_hotspot_gate.py (suite declarada em docs/validation/hotspot-de-arquivo-suite.md)",
            "onde": "host do Hermes (nao VPS): /opt/hermes/.venv/bin/python, contra destino temporario",
            "passadas": 2,
            "itens": "41 itens, 0 falhas, 1 pulado (F3, exige root)",
            "rollback": "F8 provado byte a byte",
            "log": "/tmp/l13_hotspot{,2}.out",
        },
    },
    {
        "id": "TRE-W2-E03-T01",
        "title": "Criar módulo transformativa_sales_ai",
        "task": "t_c536ce86",
        "evidence": (
            "2 passadas identicas: `RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas)` (exit 0) em ambas, "
            "banco descartavel `tre_e03_t01_modulo` nacendo do zero. Prova de dente (`--prova-de-dente`, "
            "tres provas: versao, teste, resquicio): as mutacoes reprovaram com 3 falhas cada "
            "(`MODULO_ODOO_FALHOU (19 itens, 3 falhas)` e `(36 itens, 3 falhas)`) -> "
            "`RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`. O aceite mede o modulo em banco "
            "limpo e le o estado no banco, nao a copia operacional."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-modulo-odoo.sh (aceite 51 itens + --prova-de-dente)",
            "passadas": 2,
            "dente": "3 provas (versao, teste, resquicio) -> MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)",
            "banco_descartavel": "tre_e03_t01_modulo",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card125_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E03-T01-D03",
        "title": "DEFEITO: itens de resquício do aceite medem pelo nome do módulo e não cobrem entidade Odoo",
        "task": "t_9e402411",
        "evidence": (
            "Verificacao do conserto no artefato entregue: o verificador passou a capturar a superficie "
            "COM o modulo instalado — `capturar_superficie_do_modulo()` recebe o banco nesse estado, o "
            "INFO imprime a regua derivada ('item sem superficie nao prova nada') e o aceite confere "
            "`ir_module_module.state = installed` no banco. PROVA DE QUE A REGUA ANTIGA NAO TINHA DENTE: "
            "a regua antiga media pelo nome do PACOTE e acusava 0 resquicio COM o modulo instalado; a nova "
            "reprova com resquicio plantado (dente 3: desinstalacao real + plantio de modelo, tabela, "
            "campo e view), e a sonda direta le `MODELOS_PROPRIOS=tf.process.opportunity`. Medicao "
            "PROPRIA nesta rodada (independente da do card): `--prova-de-dente` do mesmo verificador -> "
            "`MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`, com o aceite remedido em `MODULO_ODOO_OK (51 "
            "itens, 0 falhas)` nas duas passadas do card 125."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-modulo-odoo.sh (passo 3 com superficie + dente 3 de resquicio plantado)",
            "regua": "superficie derivada do modulo em state='installed' (nao o nome do pacote)",
            "dente": "resquicio plantado reprova; regua antiga era cega no mesmo cenario",
            "medicao_propria": "MODULO_ODOO_DENTE_OK (2 provas, 0 falhas) + aceite 51 itens/0 falhas x2",
            "registro": "docs/operations/registro-de-execucoes.md, entrada de 2026-10-01 (D03)",
        },
    },
    {
        "id": "TRE-W2-E03-T01-D03-D01",
        "title": "DEFEITO [retroativo]: runbook §10 e registro citam \"8 compartilhadas\" no crm",
        "task": "t_d705ea32",
        "evidence": (
            "Defeito de NÚMERO em texto entregue. Checagem por string exata nos documentos do repo: "
            "`das quais **8 compartilhadas**` -> **0 ocorrencias** (o numero errado nao existe mais em "
            "docs/); `das quais **11 compartilhadas**` -> 1 ocorrencia, no §10 do runbook do modulo "
            "(linha 328), ja com a lista completa de 11 (`calendar.event`, `crm.lead`, `crm.team`, "
            "`crm.team.member`, `digest.digest`, `ir.config_parameter`, `mail.activity`, "
            "`res.config.settings`, `res.partner`, `res.users`, `utm.campaign`); `crm.team.member` -> 2 "
            "ocorrencias; `utm.campaign` (forma correta do modelo Odoo) -> 2 ocorrencias. O unico "
            "`utm_campaign` que permanece e' legitimo: campo JSON de payload em "
            "`docs/runbooks/captura-de-lead-do-site.md`, nao nome de modelo. No registro de execucoes a "
            "frase com a contagem nao existe (nem com 8 nem com 11), logo nao ha' afirmacao errada "
            "remanescente. Sem efeito em codigo ou na conclusao do filtro, como o proprio card declara."
        ),
        "verification": {
            "checagem": "string exata em docs/ (grep -rF): 0 ocorrencias do numero errado, 1 do corrigido",
            "arquivo_corrigido": "docs/runbooks/odoo-modulo-sales-ai.md §10 (linha 328)",
            "nota": "defeito documental retroativo: numero errado em justificativa de filtro, sem efeito no codigo",
        },
    },
    {
        "id": "TRE-W2-E05-T01",
        "title": "Criar tf.process.opportunity",
        "task": "t_9c91ecce",
        "evidence": (
            "Aceite homologado pelo dono (29/09/2026): modelo criado com os campos do contrato, "
            "oportunidade canonica no Odoo com vinculo a `res.partner`, e teste de criacao, consulta e "
            "relacao. MEDIDO NO ARTEFATO ENTREGUE: (1) o modelo `tf_process_opportunity.py` declara os "
            "campos do contrato — `tf_uuid`, `name` (required, indexado), `active`, `company_id`, "
            "`currency_id`, `stage_id` (crm.stage), `lost_reason_id` (crm.lost.reason) — e "
            "`partner_id = fields.Many2one('res.partner', ondelete='restrict')`, com o comentario de que "
            "toda oportunidade canonica pertence a um parceiro; (2) cada criterio tem teste NOMEADO em "
            "`tests/test_oportunidade_canonica.py`: `test_01_modelo_criado_com_os_campos_do_contrato`, "
            "`test_04_campos_do_contrato_gravam_e_leem`, `test_05_consulta_por_uuid_e_por_parceiro`, "
            "`test_06_relacao_com_parceiro_e_restricao_de_exclusao` (mais uuid unico/imutavel/invalido); "
            "(3) a execucao: `tests/__init__.py` importa `test_oportunidade_canonica` (linha 5), e o "
            "Odoo roda todo teste importado — o aceite do modulo mediu **0 failed, 0 error(s) of 192 "
            "tests** no banco descartavel, nas duas passadas do card 126, com os dois arquivos de teste "
            "inventariados por sha256 pelo proprio harness. Os 6 testes do aceite dedicado do modulo "
            "(`verificar-modulo-odoo.sh`, card 125) NAO cobrem este card: e' o aceite de 192 que cobre."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-views-sales-ai.sh (passo 2 roda a suite do modulo; aceite de 192 testes)",
            "medicao": "0 failed, 0 error(s) of 192 tests, em dupla descartavel propria, 2 passadas",
            "modelo": "odoo/addons/transformativa_sales_ai/models/tf_process_opportunity.py",
            "testes": "tests/test_oportunidade_canonica.py (9 testes) importado por tests/__init__.py",
            "limite_declarado": "o runner nao nomeia cada teste no log; a cobertura dos 9 decorre do import no __init__ + suite completa verde",
        },
    },
    {
        "id": "TRE-W2-E07-T01-D01",
        "title": "DEFEITO [retroativo]: verificador novo das ACLs reincidiu D01+D02 do E03",
        "task": "t_aaaf1558",
        "evidence": (
            "Reincidencia de DUAS classes de defeito do E03 no harness novo das ACLs "
            "(`scripts/odoo/verificar-acl-modulo.sh`), corrigida no mesmo card. MEDIDO NO ARTEFATO: "
            "(1) ACEITE: `RESULTADO: ACL_OK (51 itens, 0 falhas)` em DUAS passadas identicas (4 passos: "
            "instalacao em banco limpo, testes do Odoo, regras lidas no banco, prova negativa "
            "independente). (2) DENTE: `ACL_DENTE_OK (2 provas, 0 falhas)` — 'dente 1: a regra de "
            "carteira aberta reprova o aceite' e 'dente 2: a ACL plantada (superficie/escalacao) reprova "
            "o aceite', com o item de linha de teste reprovado DISPARANDO (item com dente proprio). "
            "(3) PROVA DA REINCIDENCIA D02 (dente sobrescrevia a evidencia do aceite): rodei o dente com "
            "o MESMO `TRE_LOG_DIR` do aceite — o sha256 dos `[1-4]-*.log` ficou IDENTICO antes e depois "
            "(`0c0649358f21cda5`, `9db0cf344015a464`, `393ee73899b2507a`); o modo dente escreve so' em "
            "`$TRE_LOG_DIR/dente/prova-N` e tem guarda que fotografa o sha256 do aceite e reprova se "
            "algum arquivo mudar (na rodada com diretorio proprio a guarda avisou 'sem logs de passo do "
            "aceite ... sem o que proteger', comportamento correto). (4) PROVA DA REINCIDENCIA D01 (grep "
            "ancorado no inicio da linha): o padrao agora e' `grep -E '(^| )(FAIL|ERROR): [A-Za-z_]'` "
            "(o log do Odoo 19 vem prefixado por `data pid NIVEL banco logger:`) e, na mutacao, a linha "
            "'FALHOU 4 linha(s) de teste reprovado(a) no log: ... FAIL: "
            "TestAclSeguranca.test_03.../test_05...' disparou — exatamente o cenario (4 testes reprovados) "
            "que a regua antiga imprimia como OK."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-acl-modulo.sh (aceite de 4 passos + --prova-de-dente de 2 provas)",
            "passadas": 2,
            "dente": "ACL_DENTE_OK (2 provas, 0 falhas) — carteira aberta e ACL plantada reprovam o aceite",
            "guarda_d02": "sha256 dos logs do aceite identico antes/depois do dente no mesmo TRE_LOG_DIR",
            "regua_d01": "grep (^| )(FAIL|ERROR): [A-Za-z_] — nao ancorado; acusou as 4 linhas FAIL do dente",
            "banco_descartavel": "tre_e07t01_acl (mutacoes em tre_e07t01_acl_d1/d2)",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card122_{pass1,pass2,dente,dente2}.out",
        },
    },
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    with open(ARQ, encoding="utf-8") as fh:
        j = json.load(fh)

    existentes = {
        c["hermes_task_id"]: w
        for w in j["work_items"]
        for c in (w.get("children") or [])
    }
    novos, ja = [], []
    for card in CARDS:
        if card["task"] in existentes:
            ja.append(card["task"])
            continue
        j["work_items"].append(
            {
                "id": card["id"],
                "title": card["title"],
                "stage": "DONE",
                "current_gate": "VALIDATION",
                "children": [
                    {
                        "hermes_task_id": card["task"],
                        "stage": "DONE",
                        "current_gate": "VALIDATION",
                        "validation_result": "PASS",
                        "evidence": card["evidence"],
                        "validated_at": DATA,
                        "validated_by": BY,
                        "verification": dict(
                            card["verification"], commit=COMMIT, ambiente=AMBIENTE
                        ),
                    }
                ],
            }
        )
        novos.append(card["task"])

    if novos:
        j["updated_at"] = DATA
        j["events"].append(
            {
                "event": "INTEGRATED_VALIDATION_RECORDED",
                "by": BY,
                "scope": "lote 13 — cards W2 da fila (ordens 120..129): 4 primeiros fechados com 2 passadas + dente",
                "production_promotion_authorized": False,
                "at": "2026-10-04T17:40:00+00:00",
                "cards": novos,
            }
        )

    print(f"novos: {len(novos)} {novos}")
    print(f"ja existiam: {ja}")
    print(f"work_items agora: {len(j['work_items'])} | prod_authorized: {j['production_promotion_authorized']}")
    if dry:
        print("(dry-run: nada gravado)")
        return 0
    with open(ARQ, "w", encoding="utf-8") as fh:
        json.dump(j, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("gravado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
