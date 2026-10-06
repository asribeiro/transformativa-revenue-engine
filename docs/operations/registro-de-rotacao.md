# Registro de rotação de credenciais

Sem valores. Só data, credencial, motivo e responsável.

| Data | Credencial | Motivo | Responsável | Verificação |
|---|---|---|---|---|
| 29/09/2026 | `GITHUB_TOKEN` | PAT antigo inválido (`Bad credentials`) | Anderson Ribeiro | `GET /user` → login asribeiro, token gravado e revalidado |
| 06/10/2026 | basic auth da borda (`TRE_PROXY_USUARIO`/`TRE_PROXY_SENHA`/`TRE_PROXY_HASH` em `/etc/tre/proxy-edge/` e `/etc/tre/proxy-dev/`) | senha apareceu na saída de erro de um comando durante a verificação do proxy de borda (o `wget` do BusyBox não aceita `--user` e ecoou o valor) — vazamento no log da sessão do agente, não em lugar público | Hermes Agent, com o dono aprovando a rotação no Telegram em 06/10/2026 ("Rotacionar") | senha nova (32 hex) + hash bcrypt gerados na VPS (hash pelo próprio Caddy, senha por stdin); os dois arquivos regravados 600 root; borda recriada em 7 s; medido: sem credencial `401`, com a credencial nova `200` na página do Odoo |
