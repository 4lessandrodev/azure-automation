#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
refine_cli.py

CLI para transformar refinamento técnico (texto/transcrição/protótipo textual)
em JSON de Tasks compatível com azdo_cli.py (Azure DevOps Boards).

Fluxo:
1) Você debate o refinamento manualmente (o único passo humano).
2) Salva a conversa/rascunho em um .txt.
3) Executa: refine_cli.py generate ... -> task.json
4) Executa: azdo_cli.py tasks --file task.json -> cria no board.

Observações:
- Sanitização básica (heurística) para evitar vazamento de PII/segredos.
- A chamada ao modelo usa Structured Outputs (json_schema + strict).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Dependências externas:
missing = []
try:
    from openai import OpenAI
except ModuleNotFoundError:
    missing.append("openai")

if missing:
    print(
        "\nFaltam dependências Python:\n"
        + "\n".join([f" - {m}" for m in missing])
        + "\n\nInstale com:\n"
        + "  python3 -m pip install --user " + " ".join(missing) + "\n\n"
        + "Recomendado (virtualenv):\n"
        + "  python3 -m venv .venv && source .venv/bin/activate && python -m pip install " + " ".join(missing) + "\n",
        file=sys.stderr,
    )
    sys.exit(1)

__version__ = "0.1.0"


# -----------------------------
# Configuração e .env
# -----------------------------
def load_dotenv(path: str = ".env", override: bool = False) -> None:
    """Carrega variáveis de ambiente a partir de um arquivo `.env`.

    Objetivo:
        Popular `os.environ` com variáveis definidas em um `.env` para facilitar
        execução local, sem depender de ferramentas externas.

    Args:
        path: Caminho do arquivo `.env`.
        override: Se True, sobrescreve variáveis já presentes em `os.environ`.
                  Se False, preserva variáveis já definidas.

    Returns:
        None.

    Side Effects:
        - Lê arquivo do disco.
        - Atualiza `os.environ`.

    Notes:
        - Ignora linhas vazias e comentários iniciados por `#`.
        - Aceita linhas no formato `export KEY=VALUE`.
        - Remove aspas simples/duplas externas do valor.
        - Se o arquivo não existir, retorna silenciosamente.
    """
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw in f.readlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

            if not override and key in os.environ:
                continue

            os.environ[key] = value


load_dotenv()  # carrega .env na inicialização


# -----------------------------
# Util
# -----------------------------
def die(msg: str, code: int = 1) -> None:
    """Encerra a execução com erro e mensagem clara.

    Objetivo:
        Padronizar falhas "controladas" sem stacktrace para cenários previsíveis
        (arquivo ausente, JSON inválido, variáveis não informadas, etc).

    Args:
        msg: Mensagem de erro a exibir.
        code: Código de saída do processo (default: 1).

    Returns:
        None (não retorna: finaliza o processo).

    Side Effects:
        - Escreve mensagem em `stderr`.
        - Encerra o processo com `sys.exit(code)`.
    """
    print(f"Erro: {msg}", file=sys.stderr)
    sys.exit(code)


# -----------------------------
# Loading visual (terminal)
# -----------------------------
_PROGRESS_ENABLED = os.getenv("REFINE_PROGRESS", "1") != "0"


@contextmanager
def loading(label: str, enabled: bool = True, interval: float = 0.25):
    """Mostra um indicador simples de execução no terminal.

    Objetivo:
        Dar feedback visual enquanto etapas estão em execução. Escreve em `stderr`
        para não poluir `stdout` (que fica para logs/saídas "reais").

    Args:
        label: Texto base exibido (ex.: "call_openai_structured(gpt-4.1-nano)").
        enabled: Liga/desliga o loading por chamada.
        interval: Intervalo em segundos entre atualizações (default: 0.25s).

    Yields:
        Um contexto (`with loading(...):`) durante o qual o loading fica ativo.

    Side Effects:
        - Escreve em `stderr` com carriage return (`\\r`).
        - Cria e gerencia uma thread daemon temporária.

    Conditions:
        - Só renderiza se `sys.stderr.isatty()` for True (terminal interativo).
        - Pode ser desativado via env `REFINE_PROGRESS=0`.
    """
    if not (enabled and _PROGRESS_ENABLED and sys.stderr.isatty()):
        yield
        return

    stop = threading.Event()
    state = {"last_len": 0, "i": 0}
    dots = ["", ".", "..", "..."]

    def run() -> None:
        while not stop.is_set():
            msg = f"{label}{dots[state['i'] % 4]}"
            state["i"] += 1

            pad = max(0, state["last_len"] - len(msg))
            state["last_len"] = len(msg)

            sys.stderr.write("\r" + msg + (" " * pad))
            sys.stderr.flush()
            stop.wait(interval)

        msg = f"{label}"
        pad = max(0, state["last_len"] - len(msg))
        sys.stderr.write("\r" + msg + (" " * pad) + "\n")
        sys.stderr.flush()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=1.0)


def read_text(path: str) -> str:
    """Lê um arquivo texto (UTF-8) do disco.

    Objetivo:
        Centralizar leitura do input de refinamento.

    Args:
        path: Caminho do arquivo de texto.

    Returns:
        Conteúdo do arquivo como string.

    Exit:
        Finaliza com `die()` se o arquivo não existir.

    Side Effects:
        - Lê arquivo do disco.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        die(f"Arquivo não encontrado: {path}")


def sha256_text(text: str) -> str:
    """Gera hash SHA-256 de um texto.

    Objetivo:
        Permitir auditoria/referência do input sem persistir conteúdo bruto.

    Args:
        text: Texto de entrada.

    Returns:
        String no formato `sha256:<hex>`.
    """
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def utc_now_iso() -> str:
    """Gera timestamp ISO-8601 em UTC.

    Objetivo:
        Padronizar timestamps para metadados do payload.

    Returns:
        String ISO-8601 em UTC (ex.: `2026-03-01T12:34:56.789+00:00`).
    """
    return datetime.now(timezone.utc).isoformat()


def sanitize_input(text: str) -> Tuple[str, List[str]]:
    """Sanitiza PII/segredos básicos antes de enviar ao modelo (heurístico).

    Objetivo:
        Reduzir risco de vazamento de informações sensíveis no input enviado ao modelo.

    Args:
        text: Texto bruto (refinamento/transcrição).

    Returns:
        Tuple contendo:
            - texto sanitizado (str)
            - notas do que foi mascarado (list[str])

    Notes:
        - É heurístico (regex simples). Não garante remoção total de PII/segredos.
        - Mascara e-mails, CPF, telefones BR e padrões de "token/secret/password".
    """
    notes: List[str] = []
    original = text

    email_re = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    if email_re.search(text):
        text = email_re.sub("[EMAIL_REDACTED]", text)
        notes.append("E-mails mascarados.")

    cpf_re = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
    if cpf_re.search(text):
        text = cpf_re.sub("[CPF_REDACTED]", text)
        notes.append("CPFs mascarados.")

    phone_re = re.compile(r"(\+?55\s?)?(\(?\d{2}\)?\s?)?\d{4,5}[-\s]?\d{4}")
    if phone_re.search(text):
        text = phone_re.sub("[PHONE_REDACTED]", text)
        notes.append("Telefones mascarados.")

    secret_re = re.compile(r"(?i)\b(api[_-]?key|token|secret|senha|password)\b\s*[:=]\s*\S+")
    if secret_re.search(text):
        text = secret_re.sub("[SECRET_REDACTED]", text)
        notes.append("Tokens/segredos mascarados (heurístico).")

    if text == original:
        notes.append("Nenhuma sanitização aplicada (nenhum padrão detectado).")

    return text, notes


# -----------------------------
# Schema (Structured Outputs)
# -----------------------------
def tasks_json_schema() -> Dict[str, Any]:
    """Retorna o JSON Schema do payload (Structured Outputs, strict=true).

    Objetivo:
        Definir o contrato do JSON gerado pelo modelo:
        - `meta`, `tasks`, `assumptions`, `open_questions`, `sanitization`
        - Com `strict=true`, todos os campos em `required`.
        - Campos "opcionais" são modelados como união com `null`.

    Returns:
        Dict com o JSON Schema no formato esperado pelo Structured Outputs.
    """
    task_item_properties = {
        "parent_id": {"type": "integer"},
        "state": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "priority": {"type": "integer", "minimum": 1, "maximum": 3},
        "remaining_work": {"type": "integer", "minimum": 1, "maximum": 80},
        "assigned_to": {"type": ["string", "null"]},
        "iteration": {"type": "string"},
        "activity": {"type": ["string", "null"]},
        "area_path": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    }

    meta_properties = {
        "agent": {"type": "string"},
        "agent_version": {"type": "string"},
        "created_at": {"type": "string"},
        "input_hash": {"type": "string"},
        "parent_id": {"type": "integer"},
        "iteration": {"type": "string"},
        "area_path": {"type": "string"},
    }

    root_properties = {
        "meta": {
            "type": "object",
            "additionalProperties": False,
            "properties": meta_properties,
            "required": list(meta_properties.keys()),
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": task_item_properties,
                "required": list(task_item_properties.keys()),
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "sanitization": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"notes": {"type": "array", "items": {"type": "string"}}},
            "required": ["notes"],
        },
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": root_properties,
        "required": list(root_properties.keys()),
    }


# -----------------------------
# OpenAI call
# -----------------------------
def build_messages(
    sanitized_text: str,
    parent_id: int,
    iteration: str,
    area_path: str,
    min_tasks: int,
    max_tasks: int,
) -> List[Dict[str, str]]:
    """Monta mensagens (system/user) para a chamada ao modelo.

    Objetivo:
        Produzir um prompt fechado (anti-alucinação) e com contrato claro,
        garantindo que o output respeite schema e qualidade mínima por task.

    Args:
        sanitized_text: Input sanitizado do refinamento.
        parent_id: ID do PBI pai (aplicado em todas as tasks).
        iteration: IterationPath (aplicado em todas as tasks).
        area_path: AreaPath (aplicado em todas as tasks).
        min_tasks: Mínimo desejado de tasks.
        max_tasks: Máximo desejado de tasks.

    Returns:
        Lista de mensagens no formato aceito pela Responses API.
    """
    system = (
        "Você é um agente de refinamento técnico especialista em programação, arquitetura de software e engenharia de plataforma.\n"
        "Sua função é transformar um texto de refinamento (transcrição/notas/protótipo) em tasks executáveis para um(a) desenvolvedor(a) que NÃO participou do refinamento.\n\n"
        "REGRAS OBRIGATÓRIAS (anti-alucinação):\n"
        "- Não invente fatos/requisitos. Só afirme o que estiver no input.\n"
        "- Se faltar informação para uma task ficar executável, não chute: registre em open_questions.\n"
        "- Quando precisar preencher lacunas sem bloquear a task, registre premissas em assumptions.\n"
        "- Não inclua dados pessoais/segredos (e-mails, telefones, CPFs, tokens, chaves).\n"
        "- Não cite URLs privadas nem credenciais.\n\n"
        "FORMATO DE RESPOSTA:\n"
        "- Retorne APENAS um objeto JSON que respeite o schema fornecido pela aplicação.\n"
        "- Não inclua explicações, cabeçalhos, markdown, ou qualquer texto fora do JSON.\n\n"
        "CONTRATO (campos e defaults):\n"
        f"- Use parent_id={parent_id} em TODAS as tasks.\n"
        f"- Use iteration='{iteration}' e area_path='{area_path}' em TODAS as tasks.\n"
        "- Use state padrão 'To Do' quando não houver indicação.\n"
        "- priority: 1 (alta), 2 (média), 3 (baixa).\n"
        "- remaining_work: estimativa em horas (inteiro), conservadora.\n\n"
        "QUALIDADE MÍNIMA POR TASK (descrição autoexplicativa):\n"
        "Cada task DEVE ser clara o suficiente para implementação sem contexto extra.\n"
        "A descrição de CADA task DEVE conter EXATAMENTE estas seções, nesta ordem:\n\n"
        "Contexto\n"
        "- Explique por que essa task existe e qual parte do sistema ela afeta.\n"
        "- Se o contexto vier do input, deixe isso explícito.\n\n"
        "Objetivo\n"
        "- Resultado esperado em 1–2 frases.\n\n"
        "Escopo\n"
        "- O que fazer (bullets).\n\n"
        "Fora do escopo\n"
        "- O que NÃO fazer (bullets) para evitar expansão.\n\n"
        "Passos sugeridos\n"
        "- Roteiro técnico provável (bullets).\n\n"
        "Exemplo(s)\n"
        "- Se houver exemplo no input, reproduza.\n"
        "- Se NÃO houver, forneça um 'Exemplo ilustrativo (genérico)' e deixe claro que é ilustrativo.\n"
        "  (Ex.: payload request/response, configuração, pseudo-código curto, formato de log)\n\n"
        "Critérios de pronto (DoD)\n"
        "- 3–7 bullets verificáveis.\n\n"
        "Testes\n"
        "- O que testar (unit/integração/e2e) em bullets.\n\n"
        "Dependências/Riscos\n"
        "- Dependências técnicas e riscos (bullets).\n\n"
        "Referências do refinamento\n"
        "- 1–3 bullets citando decisões/trechos do input (curtos, sem PII).\n\n"
        "REGRAS DE SAÍDA:\n"
        "- Não criar tasks duplicadas.\n"
        "- Evite tarefas vagas do tipo 'refatorar tudo'. Quebre em entregas pequenas.\n"
        "- Se um ponto impedir a execução, registre em open_questions.\n"
        "- Mantenha as tasks pequenas e executáveis dentro de 1 dia.\n"
        "- Se necessário quebre uma task em partes menores.\n"
    )

    user = (
        "Gere tasks a partir do refinamento abaixo.\n"
        f"Quantidade alvo: entre {min_tasks} e {max_tasks} tasks.\n\n"
        "INPUT (sanitizado):\n"
        "```text\n"
        f"{sanitized_text}\n"
        "```"
    )

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_openai_structured(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    schema: Dict[str, Any],
    store: bool,
) -> Dict[str, Any]:
    """Chama a OpenAI Responses API com Structured Outputs (json_schema, strict=true).

    Objetivo:
        Obter um JSON válido (parseável) e aderente ao schema definido pela aplicação.

    Args:
        api_key: Chave da OpenAI.
        model: Nome do modelo.
        messages: Lista de mensagens (system/user).
        schema: JSON Schema do output.
        store: Se True, permite armazenamento do output (config de privacidade).

    Returns:
        Dict (payload) parseado do JSON retornado pelo modelo.

    Exit:
        Finaliza com `die()` se:
        - não houver `output_text` utilizável,
        - houver recusa,
        - o retorno não for JSON parseável.

    Side Effects:
        - Faz chamada de rede à API.
    """
    client = OpenAI(api_key=api_key)

    response = client.responses.create(
        model=model,
        input=messages,
        store=store,
        text={
            "format": {
                "type": "json_schema",
                "name": "refinement_tasks",
                "strict": True,
                "schema": schema,
            }
        },
    )

    out_text = getattr(response, "output_text", None)
    if not out_text:
        try:
            for item in response.output:
                for c in item.content:
                    if getattr(c, "type", "") == "refusal":
                        die(f"Modelo recusou a solicitação: {getattr(c, 'refusal', '')}")
        except Exception:
            pass
        die("Resposta não contém output_text utilizável.")

    try:
        return json.loads(out_text)
    except json.JSONDecodeError:
        die("O modelo retornou texto que não é JSON parseável (inesperado com Structured Outputs).")


# -----------------------------
# Validation
# -----------------------------
def validate_tasks_payload(payload: Dict[str, Any], expected_parent_id: Optional[int] = None) -> None:
    """Validação local (leve) do payload gerado.

    Objetivo:
        Reforçar invariantes do seu fluxo, mesmo com Structured Outputs:
        - existe tasks[]
        - tasks tem campos mínimos
        - description tem tamanho mínimo
        - parent_id uniforme quando requerido

    Args:
        payload: Dict com o JSON completo.
        expected_parent_id: Se informado, valida que todas tasks possuem este parent_id.

    Returns:
        None.

    Exit:
        Finaliza com `die()` se alguma validação falhar.
    """
    if not isinstance(payload, dict):
        die("JSON gerado não é um objeto.")

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) == 0:
        die("JSON precisa conter 'tasks' como lista com pelo menos 1 item.")

    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            die(f"Task[{i}] não é um objeto.")

        for req in ["parent_id", "title", "description", "iteration", "area_path"]:
            if req not in t:
                die(f"Task[{i}] sem campo obrigatório: {req}")

        if expected_parent_id is not None and int(t["parent_id"]) != int(expected_parent_id):
            die(f"Task[{i}] parent_id={t['parent_id']} difere do esperado={expected_parent_id}")

        if not str(t["title"]).strip():
            die(f"Task[{i}] title vazio.")
        if len(str(t["description"])) < 30:
            die(f"Task[{i}] description muito curta (mín 30 chars).")


def build_standard_tasks(parent_id: int, iteration: str, area_path: str) -> List[Dict[str, Any]]:
    """Gera tasks padrão de processo (sem gastar tokens).

    Objetivo:
        Garantir consistência mínima do processo (validação local, QA, GMUD, deploy)
        sem depender do modelo para lembrar dessas tasks.

    Args:
        parent_id: ID do PBI pai.
        iteration: IterationPath.
        area_path: AreaPath.

    Returns:
        Lista de tasks no mesmo formato do schema (com todas as chaves obrigatórias).

    Notes:
        - `assigned_to` e `activity` podem ser `None`.
        - `tags` sempre existe (array).
        - Inclui tag `std:<id>` para deduplicação posterior.
    """
    base = {
        "parent_id": parent_id,
        "state": "To Do",
        "assigned_to": None,
        "iteration": iteration,
        "area_path": area_path,
        "tags": [],
        "priority": 2,
        "remaining_work": 2,
        "activity": None,
        "title": "",
        "description": "",
    }

    def mk(
        std_tag: str,
        title: str,
        description: str,
        priority: int,
        hours: int,
        activity: Optional[str],
        tags: List[str],
    ) -> Dict[str, Any]:
        """Cria uma task padrão com âncora `std:<id>`."""
        t = dict(base)
        t["title"] = title
        t["description"] = description
        t["priority"] = priority
        t["remaining_work"] = hours
        t["activity"] = activity
        t["tags"] = [f"std:{std_tag}"] + tags
        return t

    return [
        mk(
            "dev-local-validate",
            "[Padrão] Validação do Dev em ambiente local",
            "Contexto\n"
            "Após concluir o desenvolvimento, é obrigatório validar localmente para reduzir retrabalho e evitar repassar falhas óbvias para QA.\n\n"
            "Objetivo\n"
            "Confirmar que o fluxo implementado funciona localmente com dados/configuração equivalentes ao esperado.\n\n"
            "Escopo\n"
            "- Subir ambiente local (API/UI, se aplicável)\n"
            "- Executar o fluxo ponta-a-ponta relacionado ao PBI\n"
            "- Validar logs básicos (sem PII/segredos)\n\n"
            "Fora do escopo\n"
            "- Testes exploratórios profundos (isso é QA)\n"
            "- Ajustes de ambiente que não sejam necessários ao fluxo\n\n"
            "Passos sugeridos\n"
            "- Rodar setup do projeto\n"
            "- Executar fluxo principal e casos de erro relevantes\n"
            "- Capturar evidências mínimas (prints/logs não sensíveis)\n\n"
            "Exemplo(s)\n"
            "Exemplo ilustrativo (genérico):\n"
            "- Caso feliz: login -> ação principal -> resposta esperada\n"
            "- Caso erro: credencial inválida -> mensagem neutra -> status correto\n\n"
            "Critérios de pronto (DoD)\n"
            "- Fluxo principal funciona localmente\n"
            "- Casos de erro principais retornam comportamento esperado\n"
            "- Nenhum log expõe tokens/segredos\n\n"
            "Testes\n"
            "- Rodar unit tests (se existirem)\n"
            "- Rodar smoke manual do fluxo\n\n"
            "Dependências/Riscos\n"
            "- Dependência: variáveis de ambiente local corretas\n"
            "- Risco: ambiente local divergente de QAS\n\n"
            "Referências do refinamento\n"
            "- Task padrão de processo (sempre aplicável)\n",
            priority=2,
            hours=2,
            activity="Development",
            tags=["dev", "local", "validation", "smoke"],
        ),
        mk(
            "qa-qas",
            "[Padrão] Testes do QA em ambiente QAS",
            "Contexto\n"
            "Depois da validação do dev, o QA executa testes de regressão e exploração em QAS para reduzir risco antes de subir para UAT/produção.\n\n"
            "Objetivo\n"
            "Validar funcionalmente em QAS que o escopo do PBI está correto e não quebrou fluxos correlatos.\n\n"
            "Escopo\n"
            "- Executar testes funcionais do PBI em QAS\n"
            "- Rodar smoke + regressão mínima (rotas/fluxos relacionados)\n"
            "- Reportar evidências e bugs encontrados\n\n"
            "Fora do escopo\n"
            "- Performance/Load (se não tiver sido solicitado)\n"
            "- Pentest (se não tiver sido solicitado)\n\n"
            "Passos sugeridos\n"
            "- Confirmar build/version em QAS\n"
            "- Executar casos de teste definidos no refinamento\n"
            "- Registrar evidências (sem dados sensíveis)\n\n"
            "Exemplo(s)\n"
            "Exemplo ilustrativo (genérico):\n"
            "- Caso feliz + 2 casos de erro\n"
            "- Verificar status codes e mensagens neutras\n\n"
            "Critérios de pronto (DoD)\n"
            "- Casos de teste do escopo passam\n"
            "- Sem regressões críticas detectadas\n"
            "- Bugs abertos e triados quando aplicável\n\n"
            "Testes\n"
            "- Execução manual + checklist\n"
            "- Se existir: suíte automatizada em QAS\n\n"
            "Dependências/Riscos\n"
            "- Dependência: deploy em QAS concluído\n"
            "- Risco: dados/config divergente\n\n"
            "Referências do refinamento\n"
            "- Task padrão de processo (sempre aplicável)\n",
            priority=2,
            hours=4,
            activity="Testing",
            tags=["qa", "qas", "testing", "regression"],
        ),
        mk(
            "gmud",
            "[Padrão] Abertura de GMUD para aprovação do deploy",
            "Contexto\n"
            "Para promover mudanças entre ambientes e produção, é necessário abrir GMUD conforme governança.\n\n"
            "Objetivo\n"
            "Garantir aprovação formal para deploy (rastreabilidade e compliance).\n\n"
            "Escopo\n"
            "- Criar GMUD com resumo da mudança\n"
            "- Incluir riscos, rollback e evidências de teste\n"
            "- Submeter para aprovação\n\n"
            "Fora do escopo\n"
            "- Mudanças emergenciais fora do fluxo padrão (a menos que explicitado)\n\n"
            "Passos sugeridos\n"
            "- Preencher descrição objetiva do que mudou\n"
            "- Anexar evidências (QA/dev) e plano de rollback\n"
            "- Solicitar aprovação\n\n"
            "Exemplo(s)\n"
            "Exemplo ilustrativo (genérico):\n"
            "- Mudança: ajuste em autenticação\n"
            "- Risco: login indisponível\n"
            "- Rollback: reverter release X\n\n"
            "Critérios de pronto (DoD)\n"
            "- GMUD criada e completa\n"
            "- Aprovada pelo responsável\n\n"
            "Testes\n"
            "- Referenciar validação local + QAS\n\n"
            "Dependências/Riscos\n"
            "- Dependência: evidências de QA\n"
            "- Risco: janela de deploy indisponível\n\n"
            "Referências do refinamento\n"
            "- Task padrão de processo (sempre aplicável)\n",
            priority=3,
            hours=2,
            activity=None,
            tags=["change", "gmud", "approval", "governance"],
        ),
        mk(
            "deploy-nonprod",
            "[Padrão] Deploy para homologação (DEV / QAS / UAT)",
            "Contexto\n"
            "Para validar em cadeia, a mudança deve ser promovida para ambientes não produtivos.\n\n"
            "Objetivo\n"
            "Disponibilizar a versão para validação em DEV/QAS/UAT conforme pipeline.\n\n"
            "Escopo\n"
            "- Executar pipeline de deploy para DEV\n"
            "- Promover para QAS\n"
            "- Promover para UAT (se aplicável)\n"
            "- Validar healthcheck e versão\n\n"
            "Fora do escopo\n"
            "- Deploy produção (tem task específica)\n\n"
            "Passos sugeridos\n"
            "- Rodar pipeline CI/CD\n"
            "- Verificar logs/healthcheck\n"
            "- Informar QA para iniciar validação\n\n"
            "Exemplo(s)\n"
            "Exemplo ilustrativo (genérico):\n"
            "- Validar endpoint /healthcheck retorna 200\n"
            "- Conferir versão/tag do build\n\n"
            "Critérios de pronto (DoD)\n"
            "- Deploy concluído sem erros\n"
            "- Healthcheck ok\n"
            "- Versão correta em DEV/QAS/UAT\n\n"
            "Testes\n"
            "- Smoke rápido pós-deploy\n\n"
            "Dependências/Riscos\n"
            "- Dependência: pipeline configurado\n"
            "- Risco: config por ambiente\n\n"
            "Referências do refinamento\n"
            "- Task padrão de processo (sempre aplicável)\n",
            priority=2,
            hours=3,
            activity=None,
            tags=["deploy", "dev", "qas", "uat", "release"],
        ),
        mk(
            "deploy-prod",
            "[Padrão] Deploy para produção",
            "Contexto\n"
            "Após validações e aprovação (GMUD), a mudança é promovida para produção.\n\n"
            "Objetivo\n"
            "Publicar a versão em produção com risco controlado e monitoramento.\n\n"
            "Escopo\n"
            "- Executar deploy de produção\n"
            "- Validar healthcheck e métricas\n"
            "- Confirmar comportamento do fluxo principal\n\n"
            "Fora do escopo\n"
            "- Mudanças fora do pacote aprovado\n\n"
            "Passos sugeridos\n"
            "- Confirmar GMUD aprovada\n"
            "- Executar pipeline de produção\n"
            "- Monitorar logs/métricas\n"
            "- Executar smoke pós-deploy\n\n"
            "Exemplo(s)\n"
            "Exemplo ilustrativo (genérico):\n"
            "- Smoke: login -> ação principal -> sucesso\n"
            "- Monitorar aumento de 401/500\n\n"
            "Critérios de pronto (DoD)\n"
            "- Deploy concluído\n"
            "- Healthcheck ok\n"
            "- Smoke pós-deploy executado\n"
            "- Sem alertas críticos\n\n"
            "Testes\n"
            "- Smoke pós-deploy (manual/automatizado)\n\n"
            "Dependências/Riscos\n"
            "- Dependência: janela de deploy + GMUD\n"
            "- Risco: regressão em produção\n\n"
            "Referências do refinamento\n"
            "- Task padrão de processo (sempre aplicável)\n",
            priority=1,
            hours=2,
            activity=None,
            tags=["deploy", "prod", "release", "monitoring"],
        ),
    ]


def ensure_standard_tasks(payload: Dict[str, Any], parent_id: int, iteration: str, area_path: str) -> None:
    """Injeta tasks padrão no payload sem gastar tokens.

    Objetivo:
        Garantir que o payload final sempre inclua tasks de processo, evitando
        que o modelo “esqueça”.

    Args:
        payload: Payload final (dict) que contém `tasks`.
        parent_id: PBI pai das tasks padrão.
        iteration: IterationPath das tasks padrão.
        area_path: AreaPath das tasks padrão.

    Returns:
        None.

    Notes:
        Deduplica por tag âncora `std:<id>`. Se já existir, não adiciona.
    """
    tasks = payload.setdefault("tasks", [])
    existing_std = set()

    for t in tasks:
        for tag in (t.get("tags") or []):
            if isinstance(tag, str) and tag.startswith("std:"):
                existing_std.add(tag)

    for std_task in build_standard_tasks(parent_id, iteration, area_path):
        std_tag = std_task["tags"][0]
        if std_tag not in existing_std:
            tasks.append(std_task)


# -----------------------------
# Commands
# -----------------------------
def cmd_generate(args: argparse.Namespace) -> int:
    """Comando `generate`: gera task.json a partir de um input.txt.

    Objetivo:
        Orquestrar:
        - leitura do input
        - sanitização (PII/segredos)
        - montagem do prompt
        - chamada OpenAI (Structured Outputs)
        - injeção de tasks padrão
        - validação local
        - escrita do JSON final

    Args:
        args: Namespace do argparse (parâmetros do CLI).

    Returns:
        Código de saída (0 em sucesso).

    Exit:
        Finaliza com `die()` se faltar `OPENAI_API_KEY` ou se validações falharem.

    Side Effects:
        - Lê arquivo do disco.
        - Faz chamada de rede à OpenAI.
        - Escreve arquivo JSON no disco.
        - Escreve mensagens em stdout/stderr.
    """
    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        die("Falta OPENAI_API_KEY no ambiente (ou use --api-key).")

    with loading("read_text"):
        raw = read_text(args.input)

    with loading("sanitize_input"):
        sanitized, notes = sanitize_input(raw)

    with loading("sha256_text"):
        input_hash = sha256_text(sanitized)

    with loading("build_messages"):
        messages = build_messages(
            sanitized_text=sanitized,
            parent_id=args.parent_id,
            iteration=args.iteration,
            area_path=args.area_path,
            min_tasks=args.min_tasks,
            max_tasks=args.max_tasks,
        )

    with loading("tasks_json_schema"):
        schema = tasks_json_schema()

    with loading(f"call_openai_structured({args.model})"):
        payload = call_openai_structured(
            api_key=api_key,
            model=args.model,
            messages=messages,
            schema=schema,
            store=args.store,
        )

    payload.setdefault("meta", {})
    payload["meta"].setdefault("agent", "refine_cli")
    payload["meta"].setdefault("agent_version", __version__)
    payload["meta"].setdefault("created_at", utc_now_iso())
    payload["meta"].setdefault("input_hash", input_hash)
    payload["meta"].setdefault("parent_id", args.parent_id)
    payload["meta"].setdefault("iteration", args.iteration)
    payload["meta"].setdefault("area_path", args.area_path)

    payload.setdefault("sanitization", {"notes": notes})
    payload["sanitization"].setdefault("notes", notes)

    with loading("ensure_standard_tasks"):
        ensure_standard_tasks(payload, args.parent_id, args.iteration, args.area_path)

    with loading("validate_tasks_payload"):
        validate_tasks_payload(payload, expected_parent_id=args.parent_id)

    with loading("write_json"):
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"✅ JSON gerado: {args.out}")
    if payload.get("open_questions"):
        print(f"⚠️  open_questions: {len(payload['open_questions'])} (revise antes de executar a CLI de criação)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Comando `validate`: valida um task.json localmente (sem chamar OpenAI).

    Objetivo:
        Verificar rapidamente se o arquivo gerado está coerente para consumo no azdo_cli.py.

    Args:
        args: Namespace do argparse.

    Returns:
        Código de saída (0 em sucesso).

    Exit:
        Finaliza com `die()` se o arquivo não existir, JSON for inválido, ou validação falhar.

    Side Effects:
        - Lê arquivo do disco.
        - Escreve mensagens em stdout/stderr.
    """
    with loading("read_json"):
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError:
            die(f"Arquivo não encontrado: {args.file}")
        except json.JSONDecodeError as e:
            die(f"JSON inválido: {e}")

    with loading("validate_tasks_payload"):
        validate_tasks_payload(payload, expected_parent_id=args.parent_id)

    print("✅ JSON válido para uso no azdo_cli.py")
    return 0


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Formatter do argparse para help mais legível (defaults + descrição crua)."""
    pass


# -----------------------------
# CLI
# -----------------------------
def build_parser() -> argparse.ArgumentParser:
    """Monta o parser (argparse) da CLI.

    Objetivo:
        Definir subcomandos (`generate`, `validate`, `help`) e flags associadas,
        além de `--version`.

    Returns:
        ArgumentParser configurado.
    """
    epilog = (
        "ENV:\n"
        "  OPENAI_API_KEY  - obrigatório para o comando generate (pode vir do .env)\n\n"
        "Ajuda por tópico:\n"
        "  refine_cli help generate\n"
        "  refine_cli help validate\n\n"
        "Exemplos:\n"
        "  python3 refine_cli.py help\n"
        "  python3 refine_cli.py help generate\n"
        "  python3 refine_cli.py generate --input ./refinement.txt --parent-id 123 --iteration \"Lab\\\\Sprint 1\" --area-path \"Lab\"\n"
        "  python3 refine_cli.py validate --file ./task.json --parent-id 123\n"
    )

    p = argparse.ArgumentParser(
        prog="refine_cli",
        description=(
            "Transforma um refinamento técnico (TXT) em JSON de Tasks compatível com azdo_cli.py.\n"
            "Foco: tarefas executáveis, pequenas, com DoD, testes e riscos; sem PII/segredos."
        ),
        epilog=epilog,
        add_help=True,
        formatter_class=_HelpFormatter,
    )

    p.add_argument("--version", action="version", version="%(prog)s " + __version__)

    sub = p.add_subparsers(dest="command")

    g = sub.add_parser("generate", help="Gera task.json a partir de input.txt (chama OpenAI)")
    g.add_argument("--input", required=True, help="Caminho do .txt com o refinamento")
    g.add_argument("--parent-id", required=True, type=int, help="ID do PBI pai (obrigatório)")
    g.add_argument("--iteration", required=True, help='IterationPath, ex: "Lab\\Sprint 1"')
    g.add_argument("--area-path", required=True, help='AreaPath, ex: "Lab"')
    g.add_argument("--out", default="task.json", help="Arquivo de saída (default: task.json)")
    g.add_argument("--model", default="gpt-4.1-nano", help='Modelo (default: "gpt-4.1-nano")')
    g.add_argument("--min-tasks", type=int, default=5, help="Mínimo de tasks a gerar (default: 5)")
    g.add_argument("--max-tasks", type=int, default=12, help="Máximo de tasks a gerar (default: 12)")
    g.add_argument("--api-key", default=None, help="Opcional: OpenAI API Key (ou use OPENAI_API_KEY)")
    g.add_argument("--store", action="store_true", help="Armazenar resposta (default: false)")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="Valida um task.json (sem chamar API)")
    v.add_argument("--file", required=True, help="Caminho do task.json")
    v.add_argument("--parent-id", type=int, default=None, help="Opcional: valida se todas tasks têm este parent_id")
    v.set_defaults(func=cmd_validate)

    h = sub.add_parser("help", help="Mostra ajuda geral ou de um subcomando")
    h.add_argument("topic", nargs="?", choices=["generate", "validate"], help="generate | validate")

    return p


def main() -> None:
    """Entry point da CLI.

    Objetivo:
        - Parsear argumentos
        - Tratar `help` (geral ou por tópico)
        - Executar subcomando selecionado
        - Normalizar erros com `die()`

    Returns:
        None (encerra o processo via `sys.exit`).

    Side Effects:
        - Pode chamar rede (generate) e ler/escrever arquivos.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help" or not args.command:
        if getattr(args, "topic", None):
            topic = args.topic
            subparsers_action = next(
                (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
                None,
            )
            if not subparsers_action:
                die("Falha interna: subparsers não encontrados.")

            choices = subparsers_action.choices
            if topic not in choices:
                die(f"Tópico inválido: {topic}")

            choices[topic].print_help()
        else:
            parser.print_help()
        sys.exit(0)

    try:
        sys.exit(args.func(args))
    except Exception as e:
        die(str(e))


if __name__ == "__main__":
    main()
