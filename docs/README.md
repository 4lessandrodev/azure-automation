# Docs

Documentação das CLIs:

- `refine_cli.py`: transforma refinamento (TXT) em `task.json` usando OpenAI (Structured Outputs).
- `azdo_cli.py`: cria PBIs e Tasks no Azure DevOps Boards via REST API.

## Conteúdo

- [Quickstart](quickstart.md)
- [CLIs](cli/azdo_cli.md) | [refine_cli](cli/refine_cli.md)
- [Formatos JSON](formats/pbi_json.md) | [task_json](formats/task_json.md)
- [Segurança e Privacidade](security-privacy.md)
- [Troubleshooting](troubleshooting.md)
- [Negócio](business.md)
- [FAQ](faq.md)

## Princípios

- **Rastreabilidade**: manter JSON de entrada/saída versionado quando possível.
- **Segurança**: não comitar `.env`, não logar PAT/tokens, não enviar PII/segredos ao modelo.
- **Reprodutibilidade**: exemplos e contratos estáveis, outputs determinísticos dentro do possível.
