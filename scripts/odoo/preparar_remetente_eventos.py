# -*- coding: utf-8 -*-
"""Configura o REMETENTE dos eventos Odoo->PostgreSQL no banco em uso — card TRE-W3-E03-T01.

Rodado por `odoo shell` (nao e' modulo: e' passo de preparo do ambiente/harness):

    TRE_INGEST_URL=http://n8n:5678 TRE_INGEST_TOKEN_FILE=/preparo/token.txt \\
        odoo shell -d <banco> --no-http < scripts/odoo/preparar_remetente_eventos.py

O que ele faz: grava em `ir.config_parameter` a base da PORTA UNICA
(`transformativa_sales_ai.ingest_url`) e o token (`transformativa_sales_ai.ingest_token`), LENDO O
TOKEN DE ARQUIVO — o segredo nunca entra em argumento de linha de comando nem no log (mesma regra da
chave da API controlada). O que ele NAO faz: nao liga a agenda (o cron nasce inativo) e nao cria
evento nenhum.

Saida (marcadores lidos pelo aceite): `TF_REMETENTE url=<base> token=<presente|ausente>`.
"""
import os

url = (os.environ.get('TRE_INGEST_URL') or '').strip()
caminho_do_token = (os.environ.get('TRE_INGEST_TOKEN_FILE') or '').strip()

if not url or not caminho_do_token:
    print('FALHOU preparo do remetente: TRE_INGEST_URL / TRE_INGEST_TOKEN_FILE ausentes')
    raise SystemExit(2)

with open(caminho_do_token, 'r', encoding='utf-8') as fh:
    token = fh.read().strip()
if not token:
    print('FALHOU preparo do remetente: arquivo do token vazio')
    raise SystemExit(2)

parametros = env['ir.config_parameter'].sudo()
parametros.set_param('transformativa_sales_ai.ingest_url', url)
parametros.set_param('transformativa_sales_ai.ingest_token', token)
env.cr.commit()

print('OK    remetente configurado (token lido de arquivo, nunca de argumento)')
print('TF_REMETENTE url=%s token=%s' % (url, 'presente' if token else 'ausente'))
