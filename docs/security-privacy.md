# Segurança e Privacidade

## Regras

- **Nunca comite `.env`** (inclua no `.gitignore`).
- **Não logue**:
  - `AZDO_PAT`
  - `OPENAI_API_KEY`
  - tokens/sessões/segredos
  - PII (CPF, e-mail, telefone, etc.)

## refine_cli: risco de vazamento

- A sanitização é **heurística** e não elimina 100% dos riscos.
- Não cole transcrições com segredos.
- Use `store=false` (padrão). Só habilite `--store` se você souber por que precisa.

## Azure DevOps

- O PAT dá poder real de escrita em work items. Trate como segredo.
- Prefira PAT com escopo mínimo necessário.

## Loading

- Loading escreve em `stderr` e pode ser desativado:
  - `AZDO_PROGRESS=0`
  - `REFINE_PROGRESS=0`

