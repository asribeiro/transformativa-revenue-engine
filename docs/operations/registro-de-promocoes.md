# Registro de promoções entre ambientes

**Promover ≠ aprovar.** A promoção `develop` → `homolog` é ato do Hermes **mediante evidência** (desenho em
`docs/operations/ambientes-e-promocao.md`); a passagem `homolog` → `main` exige **aprovação humana** e vive em
`registro-de-aprovacoes.md`. Nenhum valor de credencial entra aqui, nunca.

Regra de evidência (a mesma do registro de aprovações, 30/09/2026): a linha cita **identidade imutável** — o
commit, o sha256 dos artefatos **naquele commit** e a leitura do verificador **datada**. Contagem solta
envelhece no commit seguinte e vira afirmação falsa.

| Data | De → Para | Commit | O que muda | Evidência | Quem |
|---|---|---|---|---|---|
| 06/10/2026 | `develop` → `homolog` (**primeira promoção**) | `fbf5bb152403bf62645ad4911b5127578573cc23` (fast-forward de `3252bba`; `homolog` remoto e cópia publicada no mesmo commit) | **13 arquivos, +921/-1 — nenhum arquivo de APLICAÇÃO** (`odoo/`, `db/`, `control-plane/`, `n8n/` intocados; conferido por `git diff --name-only homolog develop` filtrando esses prefixos = vazio). Conteúdo: proxy de borda (Caddyfile + compose + par + runbook), stack de Homolog (compose + par + instalador + verificador + runbook), gancho `pre-push`, registro do desenho de ambientes | Verificador `scripts/provision/verificar-odoo-homolog.sh` **no commit `fbf5bb152403bf62645ad4911b5127578573cc23`** (sha256 `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012`): **15 itens, 0 falhas**, rodado **depois** da troca da cópia publicada. Cópia de `/opt/tre/homolog/repo`: `.publicado` registra `commit: fbf5bb152403bf62645ad4911b5127578573cc23`, digest `2208a0288baaa5b55b09bd3d776120aef19ef9c7103cd9774530d31d20ff31b0`, 788 arquivos, trava `chattr +i` armada. Nada reiniciado no serviço; o addons montado (`/mnt/extra-addons`) seguiu visível ao container depois do `rsync --delete`. **Divergência declarada:** o campo `ref:` do `.publicado` saiu `develop` (o `git name-rev` escolhe o nome mais curto e os dois branches apontavam para o mesmo commit) — o commit é a identidade; corrigir o rótulo no `publicar.sh` fica como melhoria pendente | Hermes Agent — ato automático **com evidência**, autorizado pelo dono no Telegram em 06/10/2026 ("(a)"), dentro do desenho aprovado (D5 + plano de execução) |

## Identidades no commit desta promoção

| Artefato | sha256 |
|---|---|
| `scripts/provision/verificar-odoo-homolog.sh` | `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012` |
| `scripts/provision/instalar-odoo-homolog.sh` | `14e3c4caebf0bcce0868ede2774ddf85e4488c0233b92bee54148e9134f26325` |
| `deploy/compose/edge/Caddyfile` | `0a5f6e22e5e4d38a5a3cea31a08b4f8ce4eef6fc090ab10394f3170f219e620a` |
| `deploy/compose/homolog/odoo.yml` | `452d68a50012f8da3c5c4ef73f09fc5ec11da11e025f2ae01b6b37494a5065ac` |
| `deploy/environments/homolog.env` | `6d73bb3c96052ce84156165625f183ecca23f785488b3356a21ba6de55aadd59` |
