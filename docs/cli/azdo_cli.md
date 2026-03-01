# azdo_cli.py

CLI para criar PBIs e Tasks no Azure DevOps Boards via REST API.

## Comandos

### `help`

Mostra ajuda geral ou de um comando específico.

```bash
python3 azdo_cli.py help
python3 azdo_cli.py help pbis
python3 azdo_cli.py help tasks
````

### `pbis`

Cria Product Backlog Items (PBIs) a partir de um JSON.

```bash
python3 azdo_cli.py --org 4le --project Lab pbis --file ./data/pbi.json
```

#### Idempotência opcional por `key`

Se o PBI tiver `key`, a CLI adiciona tag `ext:<key>`.
Quando `--allow-duplicates` **não** é usado, ela tenta achar um PBI existente com essa tag via WIQL e pula a criação.

```bash
python3 azdo_cli.py --org 4le --project Lab pbis --file ./data/pbi.json
# força duplicar:
python3 azdo_cli.py --org 4le --project Lab pbis --file ./data/pbi.json --allow-duplicates
```

### `tasks`

Cria Tasks a partir de `task.json` e vincula ao PBI pai.

```bash
python3 azdo_cli.py --org 4le --project Lab tasks --file ./data/task.json
```

#### Resolução de PBI pai

Uma task pode apontar para o PBI pai de 3 formas:

1. `parent_id` (preferido)
2. `parent_url` (extrai o ID)
3. `parent_key` (lookup via WIQL usando tag `ext:<key>`) — opcional

## Variáveis de ambiente

* `AZDO_PAT` (obrigatório): PAT do Azure DevOps
* `AZDO_PROGRESS` (opcional): `0` desliga loading

## Saídas geradas

* `created_pbis_output.json` (quando cria PBIs)
* `created_tasks_output.json` (quando cria tasks)

## Observações

* `System.Description` é enviado como HTML simples.
* Quebras de linha e listas em `task.description` são convertidas para HTML por `text_to_html()`.
* Logs/prints devem evitar exibir credenciais (PAT).

