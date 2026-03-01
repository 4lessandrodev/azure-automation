## `docs/formats/pbi_json.md`

# Formato `pbi.json`

## Estrutura

```json
{
  "pbis": [
    {
      "key": "STRING_OPCIONAL",
      "name": "TÍTULO",
      "state": "New",
      "description": "Texto simples",
      "acceptance_criteria": ["item 1", "item 2"],
      "priority": 1,
      "effort": 5,
      "iteration": "Lab\\Sprint 2",
      "area_path": "Lab",
      "value_area": "Business",
      "tags": ["tag1", "tag2"]
    }
  ]
}
```

## Regras relevantes

* `name` (ou `title`) é obrigatório.
* Se `key` existir, a CLI pode usar idempotência via tag `ext:<key>`.
* `acceptance_criteria` se vier como lista, será convertido em HTML `<ul>`.

## Campos comuns (Azure DevOps)

* `System.Title` <- `name/title`
* `System.Description` <- `description` (HTML simples)
* `System.IterationPath` <- `iteration`
* `System.AreaPath` <- `area_path`
* `Microsoft.VSTS.Common.Priority` <- `priority`
* `Microsoft.VSTS.Scheduling.Effort` <- `effort`
* `Microsoft.VSTS.Common.ValueArea` <- `value_area`
* `System.Tags` <- `tags` + `ext:<key>` (quando aplicável)
