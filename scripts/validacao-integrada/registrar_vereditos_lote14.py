#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da validacao integrada do LOTE 14 (ordens 130+ da fila).

O lote 14 atravessa ondas: cada card vai para o artefato da SUA onda. O child e' gravado
na forma completa (validation_result + evidence + current_gate + validated_at +
validated_by + verification), preservando as chaves que o artefato ja' tinha — o W0, por
exemplo, tinha children com apenas {hermes_task_id, stage, evidence}.

Uso:  python3 scripts/validacao-integrada/registrar_vereditos_lote14.py [--dry-run]
"""
import json
import pathlib
import sys

BASE = pathlib.Path("control-plane/deliveries")
COMMIT = "2ce808bcfd1b62c2a399b98f0a78240de24db82b"
AMBIENTE = (
    "VPS Contabo vmi3619453: clone isolado /tmp/tre_lote13/repo do develop publicado "
    "(9e638f7/2ce808b, 765 arquivos); containers e bancos descartaveis por rodada; "
    "dev/homolog/prod conferidos intactos pelo proprio verificador (ADR-005)"
)
BY = "Hermes — validacao integrada do lote 14 (cards da fila, ordem de criacao no board)"
DATA = "2026-10-05"

# onda -> artefato
ARTEFATO = {
    "W0": "W0-governanca-e-baseline.json",
    "W1": "W1-dados-e-dedup.json",
    "W2": "W2-odoo-e-seguranca.json",
    "W3": "W3-integracao-odoo-pg.json",
    "W4": "W4-agentes-e-e2e-sales-intelligence.json",
    "W5": "W5-scores-e-nba.json",
    "W6": "W6-outbound-e-canais.json",
    "W7": "W7-inbound-e-multicanal.json",
}

CARDS = [
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D01",
        "title": "DEFEITO [retroativo]: pg_isready respondia OK no servidor temporario do init",
        "task": "t_4e8ade57",
        "evidence": (
            "O conserto esta' nos DOIS arquivos que o card exige, e a espera e' a robusta: faz "
            "conexao real (`docker exec ... psql -tAc 'SELECT 1'`) e so' aceita o servidor se ele "
            "SOBREVIVER a uma segunda checagem 3s depois (`sleep 3` + mesma consulta) — que e' a "
            "definicao de 'servidor definitivo, nao o temporario da inicializacao'. O teste declara o "
            "item 'origem pronta (servidor definitivo, nao o temporario da inicializacao)'. MEDIDO NO "
            "VPS, em banco descartavel proprio: DUAS passadas identicas -> `RESULTADO: TESTE_OK (12 "
            "itens, 0 falhas)` (o card declarou 9 itens; a prova cresceu), diferindo apenas o nome do "
            "dump com carimbo de tempo e o sha256 dele, que nascem novos por rodada. LIMITACAO "
            "DECLARADA (decisao do dono, 05/10/2026): o DENTE por mutacao NAO morde neste item — mutando "
            "a espera para 'pronto sem checar' em copia isolada, o teste passou igual (`TESTE_OK 12/0`), "
            "porque no ambiente descartavel a corrida original (a janela em que o Postgres derruba o "
            "servidor temporario) nao se reproduz. Logo: conserto implementado e caminho feliz medido, "
            "mas a protecao da espera nao tem prova negativa. Construir o cenario que reproduz a corrida "
            "foi reconhecido como trabalho de card proprio, nao de validacao."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh (modo descartavel, sem --ambiente)",
            "conserto": "scripts/backup/verificar-backup.sh + scripts/backup/teste-backup-restore.sh",
            "passadas": 2,
            "medicao": "TESTE_OK (12 itens, 0 falhas) x2",
            "dente": "NAO MORDE: mutacao 'pronto sem checar' nao reprova no ambiente descartavel",
            "volateis_declarados": "nome do dump (carimbo de tempo) e sha256 do dump",
            "log": "/tmp/tre_lote14/card130_{pass1,pass2,dente}.out",
        },
    },
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D02",
        "title": "DEFEITO [retroativo]: paste -d rotaciona caracteres e o SQL montado por psql -c ficou invalido",
        "task": "t_9f106731",
        "evidence": (
            "Conserto presente nos TRES scripts que o card exige, na forma declarada: o `UNION ALL` e' "
            "montado DENTRO do SQL com `string_agg` sobre `information_schema.tables` — "
            "`backup-tre.sh` (3 ocorrencias de string_agg, 0 uso ATIVO de `paste -d`; a unica mencao e' o "
            "comentario que documenta a armadilha), `verificar-backup.sh` (2 string_agg, 0 paste) e "
            "`restore-tre.sh` (2 string_agg, 0 paste). MEDIDO NO VPS, em banco descartavel proprio: duas "
            "passadas -> `RESULTADO: TESTE_OK (12 itens, 0 falhas)`, com a migracao aplicando as 12 "
            "tabelas e as contagens por tabela batendo (os mesmos runs do card 130 — mesmo verificador, "
            "mesma arvore; diferem apenas o nome do dump com carimbo de tempo e o sha256 dele). DENTE "
            "QUE MORDE, na classe exata do defeito: mutando o separador do SQL montado de volta para a "
            "forma literal (`' UNION ALL '` -> `' | '`) em copia isolada, o teste REPROVOU -> "
            "`RESULTADO: TESTE_FALHOU (11 itens, 4 falhas)`, com 'FALHOU nao consegui extrair as "
            "contagens por tabela (schema existe, com 12 tabelas)' e 'FALHOU artefato incompleto "
            "(contagens.txt ...)' — ou seja, a regua pega quem voltar a montar o SQL fora do banco."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh (modo descartavel) + inspecao dos 3 scripts",
            "conserto": "scripts/backup/{backup-tre.sh,verificar-backup.sh,restore-tre.sh}",
            "passadas": 2,
            "medicao": "TESTE_OK (12 itens, 0 falhas) x2 (runs compartilhados com o card 130)",
            "dente": "MORDE: separador mutado -> TESTE_FALHOU (11 itens, 4 falhas), 'nao consegui extrair as contagens por tabela'",
            "volateis_declarados": "nome do dump (carimbo de tempo) e sha256 do dump",
            "log": "/tmp/tre_lote14/card131_dente.out (dente) + card130_{pass1,pass2}.out (passadas)",
        },
    },
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    por_artefato = {}
    for card in CARDS:
        por_artefato.setdefault(card["onda"], []).append(card)

    for onda, cards in por_artefato.items():
        nome = ARTEFATO.get(onda)
        if not nome:
            print(f"!! onda {onda} sem artefato mapeado — cards {[c['task'] for c in cards]} NAO gravados")
            continue
        arq = BASE / nome
        with open(arq, encoding="utf-8") as fh:
            j = json.load(fh)
        existentes = {
            c["hermes_task_id"]: (w, c)
            for w in j["work_items"]
            for c in (w.get("children") or [])
        }
        novos = []
        for card in cards:
            if card["task"] in existentes:
                _, c = existentes[card["task"]]
                c.update(
                    validation_result="PASS",
                    evidence=card["evidence"],
                    current_gate="VALIDATION",
                    validated_at=DATA,
                    validated_by=BY,
                    verification=dict(card["verification"], commit=COMMIT, ambiente=AMBIENTE),
                )
                print(f"  {card['task']}: child existente atualizado (forma completa)")
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
            j.setdefault("events", []).append(
                {
                    "event": "INTEGRATED_VALIDATION_RECORDED",
                    "by": BY,
                    "scope": f"lote 14 — onda {onda}: vereditos gravados com 2 passadas + prova de dente",
                    "production_promotion_authorized": False,
                    "at": "2026-10-05T14:00:00+00:00",
                    "cards": novos,
                }
            )
        print(f"{onda} ({nome}): novos={novos} | work_items={len(j['work_items'])}")
        if not dry:
            with open(arq, "w", encoding="utf-8") as fh:
                json.dump(j, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
    print("(dry-run: nada gravado)" if dry else "gravado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
