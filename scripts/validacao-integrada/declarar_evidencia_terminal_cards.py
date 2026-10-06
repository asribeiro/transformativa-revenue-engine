#!/usr/bin/env python3
"""Declara evidencia terminal para cards presos em "Validacao pendente (entrega)".

O que faz
---------
Deriva -- nao inventa -- a lista de cards presos na projecao fail-closed do painel
(card nativo `done`, com pai de dependencia, sem evidencia terminal no artefato de
entrega) e, para os que tem item mapeado em entrega ja fechada, escreve a evidencia
terminal NO artefato daquela entrega.

Dois niveis de evidencia (o nivel e' uma decisao do dono, nao do script):

  --modo explicito   adiciona o card como filho DONE do item. Projecao: card sai de
                     "Validacao pendente" e passa a "Concluido (card)". Afirma
                     apenas: a entrega daquele item esta' concluida.
  --aceitar-cadeia   aceita item alcancado por cadeia de dependencia (defeito -> defeito -> item),
                     registrando o caminho medido na evidencia
  --modo promocao    faz o acima E adiciona o id em `production_promoted_task_ids`
                     da entrega. Projecao: card vai para "Em producao". Afirma
                     alem: o card foi promovido a producao naquela release.

Por que o plugin e' a fonte
---------------------------
As regras de projecao (precedencia do `get_board`) moram no plugin do dashboard.
Este script IMPORTA o plugin em vez de reimplementar: uma copia so' por instrumento.
O bloco `colunas_exibidas` espelha as linhas 50-77 do `get_board` (5 linhas) porque
o handler depende de injecao do FastAPI; o resto vem do proprio plugin.

Fail-closed
-----------
* card sem pai mapeado em artefato: RECUSADO (nao escreve nada);
* item que nao declara DONE: RECUSADO;
* modo `promocao` exige entrega com release_status PRODUCTION_PROMOTED,
  current_gate DONE e production_promotion_authorized is True;
* card ja' terminal: ignorado (idempotente);
* `--check` e' o padrao: sem `--aplicar` nada e' escrito.

Backup
------
Cada artefato tocado e' copiado (600) para --backup-dir (padrao: fora do repo, no
scratch) ANTES da escrita. Backup dentro do diretorio de deliveries e' proibido por
desenho: o painel varre aquela pasta.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import pathlib
import shutil
import sys
import time

MODO_EXPLICITO = "explicito"
MODO_PROMOCAO = "promocao"
COLUNAS_PROJETADAS = {"validation", "candidate", "human_approval", "production"}
ORIGEM_DECLARACAO = "declaracao-terminal-em-lote"
REF_INVARIANTES = {"production": 119, "validation": 40, "archived": 14, "done": 11, "blocked": 1}


def carregar_plugin(plugin_dir: pathlib.Path):
    alvo = plugin_dir / "plugin_api.py"
    if not alvo.is_file():
        raise SystemExit("FALHOU: plugin_api.py nao encontrado em %s" % plugin_dir)
    spec = importlib.util.spec_from_file_location("kanban_plugin_api", alvo)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def colunas_exibidas(plugin, board: str, repo_root=None):
    """Colunas como a tela monta (espelho de get_board L50-77, o resto vem do plugin)."""
    life = plugin._delivery_lifecycle_task_columns(repo_root=repo_root, board=board)
    term = plugin._delivery_terminal_task_ids(repo_root=repo_root, board=board)
    prod = plugin._delivery_production_done_task_ids(repo_root=repo_root, board=board)
    with plugin._board_conn(board=board) as tup:
        conn = tup[1] if isinstance(tup, tuple) else tup
        pais = collections.defaultdict(list)
        for child, parent in conn.execute(
            "select child_id, parent_id from task_links where COALESCE(link_type,'dependency')='dependency'"
        ):
            pais[child].append(parent)
        info = {t: (s, ti) for t, s, ti in conn.execute("select id, status, title from tasks")}
    cols: collections.Counter = collections.Counter()
    onde: dict = {}
    for tid, (status, _titulo) in info.items():
        projetada = life.get(tid)
        if status == "done" and tid in term:
            projetada = None
        if status == "done" and projetada is None and tid not in term and pais.get(tid):
            projetada = "validation"
        if status == "done" and tid in prod:
            projetada = "production"
        coluna = projetada if projetada in COLUNAS_PROJETADAS else status
        cols[coluna] += 1
        onde[tid] = coluna
    return cols, onde, info, dict(pais), term, prod, life


def carregar_entregas(deliveries_dir: pathlib.Path):
    """Le os artefatos vigentes SEMPRE pelo plugin (mesma lista que o painel usa)."""
    entregas = {}
    for f in sorted(deliveries_dir.glob("*.json")):
        if ".bak" in f.name.lower():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        entregas[f.name] = d
    return entregas


def item_do_card(entregas: dict, card_ou_pai: str):
    """(arquivo, entrega, item) do work item ao qual o card/pai pertence.

    Anti-cascata (defeito pego pelo dente em 06/10/2026): um card declarado em lote
    vira ``children`` do item, e na rodada seguinte ele passaria a servir de "pai
    mapeado" para os filhos dele -- cada rodada puxaria mais cards com base mais
    fraca. Aqui so' valem: o card do proprio work item, ou filho ORIGINAL do
    artefato (sem ``evidence_origin == declaracao-terminal-em-lote``).
    """
    for nome, d in entregas.items():
        for item in d.get("work_items") or []:
            if item.get("hermes_task_id") == card_ou_pai:
                return nome, d, item
            for filho in item.get("children") or []:
                if filho.get("hermes_task_id") != card_ou_pai:
                    continue
                if filho.get("evidence_origin") == ORIGEM_DECLARACAO:
                    continue  # declaracao em lote nao mapeia pai (anti-cascata)
                return nome, d, item
    return None, None, None


def cadeia_ate_item(card: str, entregas: dict, pais: dict, maximo: int = 8):
    """Sobe a cadeia de dependencia ate' o item de artefato que ancora o card.

    Devolve ``(nome, entrega, item, caminho)``. Existe porque um card de DEFEITO
    nascido dentro de uma entrega as vezes depende de OUTRO card de defeito (que nao
    esta' em artefato nenhum), e o item so' aparece 2-4 niveis acima. Cada salto e' um
    vinculo de dependencia real do board -- a cadeia e' medida, nao suposta. Os saltos
    que passam por entrada declarada em lote continuam barrados por ``item_do_card``
    (anti-cascata).
    """
    visto, fila = {card}, [(card, [])]
    while fila:
        atual, caminho = fila.pop(0)
        if len(caminho) >= maximo:
            continue
        for p in pais.get(atual, []):
            if p in visto:
                continue
            visto.add(p)
            nome, d, item = item_do_card(entregas, p)
            if nome:
                return nome, d, item, caminho + [p]
            fila.append((p, caminho + [p]))
    return None, None, None, None


def estagios(obj: dict) -> set:
    return {str(obj.get(k) or "").upper() for k in ("stage", "current_gate", "status")} - {""}


def entrega_fechada(d: dict) -> bool:
    return (
        d.get("release_status") == "PRODUCTION_PROMOTED"
        and d.get("current_gate") == "DONE"
        and d.get("production_promotion_authorized") is True
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Declara evidencia terminal para cards em Validacao pendente.")
    ap.add_argument("--raiz", default="/opt/data/repos/tre-mirror-entrega", help="raiz do espelho de entrega")
    ap.add_argument("--plugin-dir", default="/opt/data/plugins/kanban/dashboard", help="diretorio do plugin (fonte das regras)")
    ap.add_argument("--board", default="transformativa-revenue-engine")
    ap.add_argument("--modo", choices=[MODO_EXPLICITO, MODO_PROMOCAO], default=MODO_EXPLICITO)
    ap.add_argument("--aceitar-cadeia", action="store_true",
                    help="aceita item alcancado por cadeia de dependencia (card de defeito -> card de defeito -> item)")
    ap.add_argument("--autorizacao", default="", help="referencia da autorizacao humana (ex.: Autorizacao 10)")
    ap.add_argument("--aplicar", action="store_true", help="sem esta flag, apenas relata (--check e' o padrao)")
    ap.add_argument("--backup-dir", default="", help="diretorio do backup (padrao: scratch fora do repo)")
    ap.add_argument("--sem-invariante", action="store_true", help="nao exigir os numeros de referencia do painel")
    args = ap.parse_args(argv)

    raiz = pathlib.Path(args.raiz)
    deliveries_dir = raiz / "control-plane" / "deliveries"
    if not deliveries_dir.is_dir():
        print("FALHOU: %s nao existe" % deliveries_dir)
        return 2

    plugin = carregar_plugin(pathlib.Path(args.plugin_dir))
    cols, onde, info, pais, term, prod, life = colunas_exibidas(plugin, args.board, raiz)
    print("== estado atual (raiz=%s) ==" % raiz)
    print("   colunas: %s" % dict(cols.most_common()))
    if not args.sem_invariante and dict(cols) != REF_INVARIANTES:
        print("   AVISO: numeros diferentes da referencia medida %s" % REF_INVARIANTE_STR())
        print("          (o board pode ter mudado; confira antes de aplicar)")

    entregas = carregar_entregas(deliveries_dir)
    ids_promovidos = {t for d in entregas.values() for t in (d.get("production_promoted_task_ids") or [])}

    presos = [t for t, c in onde.items() if c == "validation"]
    elegiveis, recusados, ja_terminais = [], [], []
    via_cadeia = 0
    for t in sorted(presos):
        if t in term:
            ja_terminais.append((t, "ja' terminal no artefato"))
            continue
        achado = None
        for p in pais.get(t, []):
            nome, d, item = item_do_card(entregas, p)
            if nome and "DONE" in estagios(item):
                if args.modo == MODO_PROMOCAO and not entrega_fechada(d):
                    achado = (nome, d, item, [p], "entrega nao esta' fechada/promovida (modo promocao exige)")
                    continue
                achado = (nome, d, item, [p], "")
                break
        if not (achado and not achado[4]) and args.aceitar_cadeia:
            nome, d, item, caminho = cadeia_ate_item(t, entregas, pais)
            if nome and "DONE" in estagios(item) and (args.modo != MODO_PROMOCAO or entrega_fechada(d)):
                achado = (nome, d, item, caminho, "")
                via_cadeia += 1
            elif nome:
                achado = (nome, d, item, caminho, "cadeia achou item, mas item nao esta' DONE ou entrega nao fecha")
        if achado and not achado[4]:
            elegiveis.append((t, achado[0], achado[1], achado[2], achado[3]))
        else:
            recusados.append((t, achado[4] if achado else "sem pai nem cadeia alcancando item DONE de artefato"))

    print()
    print("== cards presos em 'Validacao pendente': %d ==" % len(presos))
    print("   elegiveis (item DONE mapeado): %d  (via cadeia: %d | via pai direto: %d)" % (
        len(elegiveis), via_cadeia, len(elegiveis) - via_cadeia))
    print("   recusados (nada e' escrito neles): %d" % len(recusados))
    print("   ja' terminais (ignorados): %d" % len(ja_terminais))
    por_entrega = collections.Counter(e[1] for e in elegiveis)
    for nome, n in sorted(por_entrega.items()):
        print("      %-34s %d cards" % (nome, n))
    if recusados:
        print("   recusados (amostra):")
        for t, motivo in recusados[:5]:
            print("      %-12s %s" % (t, motivo[:90]))

    agora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    backup_dir = pathlib.Path(args.backup_dir) if args.backup_dir else pathlib.Path(
        "/opt/data/cache/scratch/backup-evidencia-terminal/%s" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )

    if not args.aplicar:
        print()
        print("== CHECK (nada escrito). Amostra do que seria gravado ==")
        for t, nome, d, item, caminho_vinculo in elegiveis[:3]:
            print("   card %s -> %s :: item %s" % (t, nome, item.get("id") or item.get("hermes_task_id")))
            print("      vinculo: %s" % ("pai direto" if len(caminho_vinculo) == 1 else
                  "cadeia de %d niveis: %s" % (len(caminho_vinculo), " <- ".join(caminho_vinculo))))
            print("      children += {%s, stage: DONE, current_gate: DONE, evidence: <derivacao>, origin: declaracao-terminal-em-lote}" % t)
            if args.modo == MODO_PROMOCAO:
                print("      production_promoted_task_ids += %s" % t)
        print()
        print("REAL: %d cards elegiveis, nenhuma escrita (use --aplicar)" % len(elegiveis))
        return 0

    if not elegiveis:
        print()
        print("FALHOU: nada elegivel; nada a aplicar")
        return 2

    backup_dir.mkdir(parents=True, exist_ok=True)
    tocados = sorted({e[1] for e in elegiveis})
    for nome in tocados:
        origem = deliveries_dir / nome
        destino = backup_dir / nome
        shutil.copy2(origem, destino)
        destino.chmod(0o600)
    print()
    print("== backup (600) em %s ==" % backup_dir)
    for nome in tocados:
        print("   %s" % nome)

    # escrita
    por_entrega_cards = collections.defaultdict(list)
    for t, nome, d, item, caminho_vinculo in elegiveis:
        por_entrega_cards[nome].append((t, item, caminho_vinculo))
    escritos = 0
    for nome, itens in por_entrega_cards.items():
        caminho = deliveries_dir / nome
        d = json.loads(caminho.read_text(encoding="utf-8"))
        for t, item_alvo, caminho_vinculo in itens:
            for item in d.get("work_items") or []:
                if (item.get("id") or item.get("hermes_task_id")) != (item_alvo.get("id") or item_alvo.get("hermes_task_id")):
                    continue
                filhos = item.setdefault("children", [])
                if any(f.get("hermes_task_id") == t for f in filhos):
                    break
                filhos.append({
                    "hermes_task_id": t,
                    "stage": "DONE",
                    "current_gate": "DONE",
                    "evidence": (
                        "DECLARACAO TERMINAL EM LOTE (nao e' aceite medido). Card de DEFEITO/rework criado dentro da "
                        "entrega; no board esta' em `done`. Vinculo com o item lido no board por %s, caminho medido "
                        "(cada salto e' um vinculo de dependencia real): %s. O fechamento desta onda declarou os "
                        "filhos do item, nao este card, e por isso ele ficou preso na projecao fail-closed de "
                        "'Validacao pendente'. Declarado terminal por decisao do dono"
                        "%s. Nao houve cadeia de validacao integrada propria deste card: nenhum aceite "
                        "independente e' afirmado aqui."
                        % ("pai direto" if len(caminho_vinculo) == 1 else "cadeia de dependencia de %d niveis" % len(caminho_vinculo),
                           " <- ".join(caminho_vinculo),
                           " -- " + args.autorizacao if args.autorizacao else "")
                    ),
                    "evidence_origin": ORIGEM_DECLARACAO,
                    "declared_at": agora,
                    "declared_by": "hermes (decisao do dono)",
                })
                if args.modo == MODO_PROMOCAO:
                    prom = d.setdefault("production_promoted_task_ids", [])
                    if t not in prom:
                        prom.append(t)
                    meta = d.setdefault("production_promoted_cards", [])
                    if not any(m.get("hermes_task_id") == t for m in meta):
                        meta.append({
                            "hermes_task_id": t,
                            "declared_at": agora,
                            "evidence_origin": ORIGEM_DECLARACAO,
                            "authorization": args.autorizacao,
                        })
                escritos += 1
                break
        d["updated_at"] = agora
        caminho.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("== escritos: %d cards em %d entregas ==" % (escritos, len(tocados)))

    # verificacao pos-escrita, pelo proprio plugin, na raiz alvo
    cols2, onde2, _, _, term2, _, _ = colunas_exibidas(plugin, args.board, raiz)
    print("== depois (medido pelo plugin, na raiz alvo) ==")
    print("   colunas: %s" % dict(cols2.most_common()))
    presos2 = [t for t, c in onde2.items() if c == "validation"]
    print("   presos em validacao: %d -> %d" % (len(presos), len(presos2)))
    print("   terminais: %d -> %d" % (len(term), len(term2)))
    return 0


def REF_INVARIANTE_STR():
    return json.dumps(REF_INVARIANTES)


if __name__ == "__main__":
    sys.exit(main())
