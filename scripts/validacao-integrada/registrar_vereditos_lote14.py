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
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D03",
        "title": "DEFEITO [retroativo]: configurador ligava o destino externo mesmo com a prova reprovada",
        "task": "t_79fc6cab",
        "evidence": (
            "Conserto presente em `scripts/backup/configurar-destino-externo.sh`: o `RESULTADO: "
            "DESTINO_EXTERNO_OK` e a gravacao do `backup.env` estao AMBOS sob `[ \"$FALHAS\" -eq 0 ]`, e "
            "o arquivo de prova e' removido (`rm -f \"$PROBE\"`) — ou seja, o destino so' e' ligado depois "
            "da prova de ida e volta (sobe, confere, remove), nao por ter conseguido escrever o arquivo. "
            "MEDIDO POR MIM (caminho de RECUSA, o que o card exige), em copia isolada com os caminhos "
            "desviados para /tmp para nao encostar na configuracao real da VPS: com chaves FALSAS -> "
            "'FALHOU falha ao enviar o arquivo de teste (chaves ou endpoint incorretos?)', "
            "'PULADO o destino so e ligado no backup depois de uma prova de ida e volta bem-sucedida', "
            "'backup.env NAO foi alterado' e `RESULTADO: DESTINO_EXTERNO_FALHOU (2 falha(s))`; nenhum "
            "backup.env nasceu no diretorio. DENTE NA CLASSE EXATA DO DEFEITO: mutando os dois portoes "
            "para `if true`, as MESMAS chaves falsas passaram a produzir 'OK backup apontado para "
            "contabo:tre-backup' e `RESULTADO: DESTINO_EXTERNO_OK` — isto e', reproduz o defeito "
            "original; o portao e' o que separa chave errada de destino 'ligado'. O caminho POSITIVO "
            "(chaves verdadeiras -> DESTINO_EXTERNO_OK com bucket `tre-backup` lido de volta do Object "
            "Storage) foi rodado pelo proprio dono no prompt da VPS, como o card declara. OBSERVACAO "
            "DECLARADA (fora do escopo deste card): uma rodada com chave errada deixa o `rclone.conf` "
            "escrito (o arquivo de credencial e' gravado ANTES da prova); o `backup.env` — que e' o "
            "interruptor do destino — permanece intocado."
        ),
        "verification": {
            "verificador": "scripts/backup/configurar-destino-externo.sh (execucao do caminho de recusa) + inspecao do portao",
            "conserto": "portoes `[ \"$FALHAS\" -eq 0 ]` na gravacao do env e no RESULTADO; `rm -f $PROBE`",
            "medicao": "chaves falsas -> DESTINO_EXTERNO_FALHOU (2 falha(s)), backup.env NAO alterado",
            "dente": "MORDE: portoes mutados para `if true` -> mesmas chaves falsas dao DESTINO_EXTERNO_OK (reproduz o defeito)",
            "isolamento": "copia com ENVFILE e RCLONECONF desviados para /tmp — a configuracao real da VPS nao foi tocada",
            "caminho_positivo": "rodado pelo dono no prompt da VPS (declarado no card): bucket tre-backup + artefato lido de volta",
            "log": "/tmp/tre_lote14/d132/ (recusa) e d132m/ (dente)",
        },
    },
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D04",
        "title": "DEFEITO [retroativo]: manifesto do backup registrava 'externo: pendente' apos envio bem-sucedido",
        "task": "t_586daace",
        "evidence": (
            "Conserto presente em `scripts/backup/backup-tre.sh`, na forma que o card exige: o "
            "`rclone copy` sobe o artefato (linha 398), o resultado REAL do envio e' entao gravado no "
            "manifesto (`externo: enviado ($EXTERNO)` na linha 400 / `externo: falhou` na 408) e o "
            "manifesto e' REENVIADO (linha 401, com o comentario que registra a causa raiz: 'o manifesto "
            "sobe ANTES de saber o resultado do envio; reenvia para que a copia...'). `externo: pendente` "
            "ficou restrito ao caso legitimo 'sem destino configurado' (linha 412). EVIDENCIA MEDIDA NA "
            "FONTE (leitura de volta DO BUCKET, read-only, sem escrever nada): manifesto do artefato real "
            "de 05/10/2026 05:32Z (o do timer diario) em `contabo:tre-backup/tre_dev_20261005T053217Z/"
            "manifest.txt` registra `externo: enviado (contabo:tre-backup)`, com `banco: "
            "sales_intelligence` e `tabelas: 12`. LIMITACAO DECLARADA (decisao do dono, 05/10/2026): o "
            "DENTE por mutacao NAO foi provado nesta rodada — ele exigiria subir um artefato de prova ao "
            "bucket real (prefixo `prova-t03/`, como o card declara) para mostrar que a mutacao que pula o "
            "reenvio deixa a copia do bucket sem a linha `externo:`; o dono optou por nao escrever no "
            "Object Storage nesta rodada. Observacao de metodo: a checagem desse criterio no verificador "
            "existe SOMENTE no modo `--ambiente` (`if [ \"$MODO\" = \"ambiente\" ]` em "
            "teste-backup-restore.sh) — por isso os runs do modo descartavel (cards 130/131) nao a "
            "exercitam; quem fecha essa regua e' o card TRE-W1-E06-T01 (t_72672e48), o proximo da fila."
        ),
        "verification": {
            "verificador": "leitura de volta do bucket (rclone cat) + inspecao de backup-tre.sh (reenvio do manifesto)",
            "conserto": "backup-tre.sh linhas 398-401: rclone copy -> grava resultado real -> reenvia o manifesto",
            "medicao": "manifesto do artefato 2026-10-05T05:32Z no bucket: 'externo: enviado (contabo:tre-backup)'",
            "dente": "NAO PROVADO nesta rodada: exigiria escrita no bucket real (prefixo prova-t03/); decisao do dono = nao escrever",
            "efeitos_externos": "nenhum — medida 100% leitura (rclone cat/lsf)",
            "acoplamento": "a regua deste criterio so' roda no modo --ambiente: card TRE-W1-E06-T01 (t_72672e48)",
        },
    },
    {
        "onda": "W1",
        "id": "TRE-W1-E06-T01",
        "title": "Testar backup/restore (contra o banco real do ambiente dev)",
        "task": "t_72672e48",
        "evidence": (
            "Ciclo completo de backup/restore rodado contra o banco REAL do ambiente dev, na VPS, em "
            "container descartavel apenas para o RESTORE (producao recusada pelo proprio script, ADR-005). "
            "DUAS passadas identicas no modo `--ambiente dev`: `RESULTADO: TESTE_OK (14 itens, 0 falhas)` "
            "(o modo descartavel tem 12; os 2 a mais sao exatamente os do ambiente). Os itens medidos, "
            "todos OK: 'origem e o container do ambiente dev: pg-sales-dev (nenhum container descartavel de "
            "origem)'; 'postgres do ambiente dev responde (16.15)'; 'origem tem conteudo real: 12 tabelas "
            "em sales_intelligence (amostra: 4 linhas)'; 'backup do ambiente dev concluido (BACKUP_OK)'; "
            "'sha256 do dump confere com o manifesto'; 'manifesto registra a origem real do ambiente "
            "(pg-sales-dev)'; 'manifesto registra o envio externo (externo: enviado "
            "(contabo:tre-backup/prova-l14))'; 'teste de restore APROVADO (RESTORE_OK)'; 'contagens "
            "conferidas linha a linha na restauracao'; 'reverificacao do dump bom segue APROVADA'; e "
            "'container do ambiente intacto (mesmo Id e StartedAt antes/depois do ciclo)'. DENTE 1 (embutido "
            "no verificador, morde): 'teste negativo: dump truncado foi REPROVADO, como devia (8 falha(s) "
            "apontada(s))'. DENTE 2 (meu, o que o header do script promete — 'nunca cai para container "
            "descartavel em silencio'): apontando TRE_PG_SERVICO para um container inexistente, o teste "
            "REPROVOU -> `RESULTADO: TESTE_FALHOU`, 'container de origem pg-que-nao-existe NAO existe no "
            "ambiente dev — o modo ambiente nao cai para container descartavel', e ZERO containers "
            "descartaveis foram criados. Essa rodada FECHA a regua do card 133 (o criterio 'manifesto "
            "registra externo: enviado' so roda neste modo). EFEITOS EXTERNOS: um artefato de prova no "
            "prefixo `prova-l14/` do bucket (autorizado pelo dono) — lido de volta ('servico: pg-sales-dev', "
            "'externo: enviado') e REMOVIDO em seguida; os artefatos da rotina diaria nao foram tocados. "
            "ANOMALIA DE MODELAGEM DECLARADA: o artefato `W1-dados-e-dedup.json` tem 10 work_items e "
            "NENHUM item E06, enquanto W2..W7 todos tem um `E06-T01` — este card existe no board mas nao "
            "tinha casa no artefato da propria onda; foi registrado aqui como work_item novo (mesmo "
            "tratamento dado aos 4 cards de defeito do W0)."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh --ambiente dev",
            "passadas": 2,
            "medicao": "TESTE_OK (14 itens, 0 falhas) x2 — origem = container real pg-sales-dev, sem container descartavel de origem",
            "dente": "MORDE x2: (a) dump truncado reprovado com 8 falhas (embutido); (b) container do ambiente inexistente -> TESTE_FALHOU sem fallback",
            "adr005": "container do ambiente intacto (mesmo Id e StartedAt antes/depois); producao recusada pelo script",
            "efeitos_externos": "artefato de prova no prefixo prova-l14/ (autorizado) — removido com rclone purge; rotina diaria intocada",
            "baseline_vps": "5 containers / 7 volumes / 0 dangling / 4 redes",
            "fecha": "TRE-W0-E01-T03-D04 (t_586daace) — criterio 'externo: enviado' medido de verdade",
            "log": "/tmp/tre_lote14/card134_{pass1,pass2}.out (VPS)",
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
