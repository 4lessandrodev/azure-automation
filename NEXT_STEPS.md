# Próximos passos — Evoluir o Refinement Agent para um Agente Interativo (UI + Multimodal + Integração Azure)

Este documento descreve a evolução planejada do processo atual (TXT → OpenAI → task.json → Azure) para um **agente interativo** com **interface amigável**, suporte a **múltiplas fontes de entrada** (wireframe, imagens, links) e execução **end-to-end** com confirmação do usuário antes de criar tasks no Azure DevOps.

Objetivo final: **o refinamento continua manual**, mas **captura, estruturação, validação e criação das tasks** passam a ser totalmente automatizadas e guiadas por UI.

---

## 1) Estado atual (baseline)
- `refine_cli.py`:
  - recebe `refinement.txt` + `parent_id`
  - sanitiza (básico)
  - chama OpenAI com Structured Outputs (JSON Schema strict)
  - injeta tasks padrão (QA/GMUD/Deploy)
  - valida e gera `task.generated.json`
- `azdo_cli.py`:
  - consome `task.generated.json`
  - cria tasks e linka ao PBI no Azure DevOps

---

## 2) Problema a resolver (o que está faltando)
Hoje o fluxo é eficiente, mas:
- exige **CLI** (não é amigável para pessoas não técnicas)
- aceita basicamente **texto**
- não tem **confirmação interativa** (review guiado)
- sanitização é **heurística** e limitada
- não tem “policy layer” robusta para **dados sensíveis** em mensagens

---

## 3) Visão do “Agente Interativo” (o que será)
Um aplicativo/agente que:
1) coleta refinamento de múltiplas fontes
2) extrai contexto e requisitos
3) gera tasks (variáveis + padrão)
4) apresenta uma tela de revisão (diff/preview)
5) só após confirmação executa integração Azure e cria tasks
6) registra auditoria (sem PII) para rastreabilidade

---

## 4) Entradas suportadas (multifonte)
### 4.1 Texto
- transcrição de call
- notas do refinamento
- documento de requisitos

### 4.2 Imagens / wireframes
- screenshots de protótipos
- wireframes (Figma export, PNG/JPG)
- fluxos desenhados

### 4.3 Links
- link para documento (o agente deve pedir autorização/import ou solicitar colagem do conteúdo relevante)
- link para Figma (ideal: exportar frames ou colar descrição + imagens)

> Regra: o agente deve sempre transformar tudo em “texto canônico” interno antes de gerar tasks.

---

## 5) Experiência do usuário (UX proposta)
### Fluxo principal
1) **Escolher fonte** (texto / upload imagem / colar link)
2) **Selecionar PBI pai** (informar `parent_id`)
3) **Resumo do agente** (o que entendeu do refinamento)
4) **Geração de tasks** (variáveis + padrão)
5) **Revisão guiada**
   - agrupar por fase (dev, QA, deploy)
   - exibir “lacunas” (open_questions)
   - permitir editar título/descrição/estimativa rapidamente
6) **Confirmação final**
   - preview do que será criado no Azure
7) **Executar criação no Azure**
8) **Resultado + auditoria**
   - IDs criados
   - log de execução
   - output JSON final versionado (opcional)

---

## 6) Arquitetura recomendada (camadas)
### 6.1 Core (biblioteca interna)
- `ingest/` (text, images, links)
- `sanitize/` (PII/segredos/policy)
- `extract/` (decisões, requisitos, dependências)
- `plan/` (decomposição em tasks)
- `generate/` (descrição rica + schema)
- `standard_tasks/` (injector sem tokens)
- `validate/` (schema + regras de qualidade)
- `export/` (task.json, run_record.json)

### 6.2 Interface
Opções de UI (em ordem de pragmatismo):
1) **Web UI simples (Next.js) + API local** (recomendado para “amigável”)
2) **TUI/CLI interativo** (curses, prompts) — rápido, mas menos amigável
3) **Desktop app** (Electron) — mais pesado

### 6.3 Integração Azure
Reusar a lógica atual do `azdo_cli.py` como módulo:
- `azure_client.py` (criar tasks + link)
- `dry-run` e “preview mode” para segurança

---

## 7) Política de dados sensíveis (obrigatória)
### 7.1 Sanitização forte (antes de qualquer chamada ao modelo)
- PII: e-mails, telefones, CPFs, nomes completos (quando possível), IDs sensíveis
- segredos: tokens, keys, secrets, cookies, headers Authorization
- links internos/privados: mascarar parâmetros e domínios internos

### 7.2 “Modo seguro” por padrão
- não registrar input bruto em logs
- armazenar somente hash do input sanitizado (`sha256`)
- outputs auditáveis sem PII

### 7.3 Segurança de prompts
- instruir o modelo a **não repetir** dados sensíveis mesmo que apareçam
- “red team list” de padrões proibidos (ex.: `Authorization: Bearer`)

### 7.4 Controles de privacidade
- toggle: `store=false` por padrão na API
- opção de rodar “offline” (sem OpenAI) usando templates manuais, se necessário

---

## 8) Qualidade e controle de risco
### 8.1 DoD do agente (quality gate)
Uma execução só pode “criar no Azure” se:
- JSON válido (schema OK)
- `open_questions` vazio OU confirmado manualmente pelo usuário (“aceitar riscos”)
- tasks padrão presentes (dedup OK)
- descrição contém seções obrigatórias (Contexto, Objetivo, Escopo, DoD, Testes, etc.)
- sem padrões de PII detectados após sanitização (verificação final)

### 8.2 Proteção contra duplicação
- tasks padrão deduplicadas por `std:*`
- tasks variáveis opcionalmente deduplicadas por hash do título + parent_id + iteração

---

## 9) Auditoria e rastreabilidade
Gerar sempre:
- `run_record.json`:
  - timestamp
  - agent_version
  - schema_version
  - input_hash (sanitizado)
  - parent_id / iteration / area
  - modelo usado
  - contagem de tasks
  - warnings (sanitização, lacunas)
- `task.generated.json` (final, pós-edits e dedup)

---

## 10) Roadmap sugerido (incremental)
### Fase 1 — “CLI interativa” (baixo custo)
- prompts interativos no terminal para:
  - confirmar parent_id
  - revisar open_questions
  - editar rapidamente título/estimativa
- manter output JSON + criação no Azure

### Fase 2 — Web UI (Next.js) com preview
- upload de arquivos (txt, imagens)
- preview de tasks antes de criar no Azure
- botão “Criar tasks” após confirmação

### Fase 3 — Multimodal real (imagens/wireframes)
- pipeline:
  - OCR/vision → texto canônico
  - extração de fluxos/telas
  - geração de tasks por componente/feature
- validação: o agente mostra o que extraiu da imagem (para evitar interpretação errada)

### Fase 4 — “Agente de projeto”
- templates por tipo de sistema (web, mobile, backend)
- padrões de deploy/GMUD por organização
- módulos plugáveis para diferentes governanças

---

## 11) Entregas técnicas (checklist)
- [ ] `docs/NEXT_STEPS_AGENT.md` (este documento no repo)
- [ ] `refinement-agent-core/` (módulo core reutilizável)
- [ ] `ui/` (web app simples com preview)
- [ ] `security/`:
  - policy de PII/segredos
  - detector pós-sanitização
  - logs sem input bruto
- [ ] `azure/`:
  - cliente API reutilizável (extraído do azdo_cli)
  - modo preview/dry-run
- [ ] `examples/`:
  - inputs sintéticos (texto + imagens)
  - outputs esperados
- [ ] CI básico:
  - valida schema em PR
  - lint/format

---

## 12) Resultado esperado
Ao final, o usuário:
- cola texto ou faz upload de wireframe/imagem
- informa `parent_id`
- revisa as tasks num preview amigável
- confirma
- o agente cria automaticamente no Azure DevOps e retorna IDs + auditoria

Manual só no refinamento. Todo o resto automatizado.
