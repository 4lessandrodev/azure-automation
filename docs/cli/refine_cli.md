# refine_cli.py

CLI para transformar um refinamento (TXT) em JSON de Tasks compatível com `azdo_cli.py`.

## Comandos

### `help`

```bash
python3 refine_cli.py help
python3 refine_cli.py help generate
python3 refine_cli.py help validate
````

### `generate`

Gera `task.json` chamando OpenAI com Structured Outputs (json_schema + strict).

```bash
python3 refine_cli.py generate \
  --input ./data/refinement.txt \
  --parent-id 123 \
  --iteration "Lab\Sprint 1" \
  --area-path "Lab" \
  --out ./data/task.json \
  --model gpt-4.1-nano
```

Flags relevantes:

* `--api-key`: alternativa a `OPENAI_API_KEY`
* `--store`: se habilitado, permite armazenamento do output (privacidade)

### `validate`

Valida o arquivo gerado localmente (sem chamar OpenAI):

```bash
python3 refine_cli.py validate --file ./data/task.json --parent-id 123
```

## Variáveis de ambiente

* `OPENAI_API_KEY` (obrigatório para `generate`)
* `REFINE_PROGRESS` (opcional): `0` desliga loading

## Segurança/privacidade

* O input passa por sanitização **heurística**. Não confie nisso como “garantia”.
* Não inclua tokens, senhas, dados pessoais ou URLs privadas no refinamento.
* Preferir `store=false` (default) para minimizar risco.

## Output

O `task.json` gerado contém:

* `meta`: auditoria (agent, versão, hash do input, etc.)
* `tasks`: lista de tasks compatíveis com `azdo_cli.py`
* `assumptions`: premissas quando faltou detalhe
* `open_questions`: pontos que impedem execução sem esclarecimento
* `sanitization.notes`: o que foi mascarado

Além disso, o script injeta tasks padrão (`std:*`) para consistência de processo.
