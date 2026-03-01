
## `docs/troubleshooting.md`

# Troubleshooting

## 401 / 403 no Azure DevOps

Causas comuns:
- PAT inválido/expirado
- escopo insuficiente (precisa Work Items Read & write)
- org/projeto incorretos

Ações:
- gere novo PAT
- valide `--org` e `--project`
- rode `AZDO_PROGRESS=0` se estiver atrapalhando logs de erro

## "Work item type not found" (Task/PBI)

Causa:
- processo do projeto não usa o tipo esperado (ex.: nome diferente)

Ações:
- confirme tipos existentes no projeto (Process/Boards)
- ajuste `create_work_item("Task")` / `create_work_item("Product Backlog Item")` se necessário

## IterationPath / AreaPath inválidos

Sintoma:
- erro 400 ao criar item

Ações:
- copie exatamente do Azure DevOps (Project settings -> Boards -> Project configuration)

## Descrição sem quebra de linha no Azure DevOps

- `task.description` passa por `text_to_html()` (converte `- ` em `<ul>` e parágrafos).
- `pbi.description` é enviado como `<p>...</p>` simples.

Se quiser consistência total:
- mude PBI para usar `text_to_html()` também (opcional).

## refine_cli retorna "open_questions"

Isso significa que o input não tinha dados suficientes para tasks executáveis.
Ações:
- refine manualmente e rode `generate` de novo
- ou converta `open_questions` em tasks de investigação explícitas

