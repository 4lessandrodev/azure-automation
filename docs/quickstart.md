# Quickstart

## 1) Pré-requisitos

- Python 3.10+
- Dependências:
  - `requests` (azdo_cli)
  - `openai` (refine_cli)
- Azure DevOps:
  - Personal Access Token (PAT) com permissão **Work Items (Read & write)**

## 2) Setup local

Crie um `.env` (não comite):

```bash
AZDO_PAT="SEU_PAT"
OPENAI_API_KEY="SUA_OPENAI_KEY"
````

Instale dependências:

```bash
make install
```

## 3) Ver ajuda

```bash
make run-help
# ou
make run-help-azdo
make run-help-refine
```

## 4) Criar PBIs

Exemplo:

```bash
make run-pbis ORG=4le PROJECT=Lab FILE=./docs/examples/pbi.sample.json
```

Saída:

* `created_pbis_output.json` com IDs criados.

## 5) Gerar tasks via refinamento (OpenAI)

```bash
make run-refine INPUT=./docs/examples/refinement.sample.txt PARENT_ID=123 ITERATION='Lab\Sprint 1' AREA=Lab OUT=./data/task.json
```

Depois:

```bash
make run-refine-validate FILE=./data/task.json PARENT_ID=123
```

## 6) Criar tasks no Azure DevOps

```bash
make run-tasks ORG=4le PROJECT=Lab FILE=./data/task.json
```

## 7) Pipeline completo (recomendado)

```bash
make run-full ORG=4le PROJECT=Lab INPUT=./docs/examples/refinement.sample.txt PARENT_ID=123 ITERATION='Lab\Sprint 1' AREA=Lab
```

## 8) Loading no terminal (opcional)

Desligar loading:

```bash
AZDO_PROGRESS=0 REFINE_PROGRESS=0 make run-full ...
```
