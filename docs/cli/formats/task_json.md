
## `docs/formats/task_json.md`

# Formato `task.json`

## Estrutura mínima (consumida por azdo_cli)

```json
{
  "tasks": [
    {
      "parent_id": 123,
      "state": "To Do",
      "title": "Minha task",
      "description": "Texto",
      "priority": 2,
      "remaining_work": 4,
      "assigned_to": null,
      "iteration": "Lab\\Sprint 2",
      "activity": null,
      "area_path": "Lab",
      "tags": ["tag1", "tag2"]
    }
  ]
}
```

## Extras produzidos pelo refine_cli

O `refine_cli.py` gera um payload mais rico, mas o `azdo_cli.py` ignora o que não precisa:

* `meta`
* `assumptions`
* `open_questions`
* `sanitization`

## Regras de vínculo com PBI pai

Cada task precisa resolver um pai via:

* `parent_id` (preferido)
* `parent_url` (extração de ID)
* `parent_key` (lookup por tag `ext:<key>`)
