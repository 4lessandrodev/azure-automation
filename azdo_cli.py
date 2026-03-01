#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
azdo_cli.py

CLI para criar PBIs e Tasks no Azure DevOps (Boards) via REST API.

Comandos:
- pbis  -> cria Product Backlog Items a partir de um JSON
- tasks -> cria Tasks a partir de um JSON e linka ao PBI (parent) via parent_id/parent_url
- help  -> exibe ajuda geral ou ajuda de um comando específico

Requisitos:
- Python 3.10+ (recomendado)
- requests (pip install requests)
- PAT com permissão para Work Items (Read & write)

Exemplos:
  export AZDO_PAT="SEU_PAT"

  python3 azdo_cli.py help
  python3 azdo_cli.py help pbis
  python3 azdo_cli.py help tasks

  python3 azdo_cli.py --org Name --project Sample pbis  --file ./data/pbi.json
  python3 azdo_cli.py --org Name --project Sample tasks --file ./data/task.json

Design (atual):
- PBIs podem ser criados antes (sem id no JSON).
- Tasks são criadas depois e devem referenciar:
    - parent_id (preferido), OU
    - parent_url (o script extrai o ID)
- Opcional: se você quiser idempotência por chave, pode informar "key" no PBI e
  o script adiciona tag ext:<key> e consegue evitar duplicatas.
"""

import argparse
import base64
import json
import os
import sys
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

API_VERSION = "7.1"

# Dependência externa (requests)
# Se não estiver instalada, falha com instrução clara de instalação.
missing = []
try:
    import requests
except ModuleNotFoundError:
    missing.append("requests")

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


# -----------------------------
# Configuração e .env
# -----------------------------
def load_dotenv(path: str = ".env", override: bool = False) -> None:
    """Carrega variáveis de ambiente a partir de um arquivo `.env`.

    Objetivo:
        Popular `os.environ` com variáveis definidas em um `.env`, evitando
        dependência de ferramentas externas. Útil para executar a CLI localmente.

    Entradas (Args):
        path: Caminho do arquivo `.env`.
        override: Se True, sobrescreve variáveis já presentes em `os.environ`.
                  Se False, preserva variáveis já definidas.

    Saídas (Returns):
        None.

    Efeitos colaterais:
        - Atualiza `os.environ`.
        - Lê arquivo do disco.

    Observações:
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

            # remove aspas externas
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

            if not override and key in os.environ:
                continue

            os.environ[key] = value


load_dotenv()  # carrega .env na inicialização


# -----------------------------
# Utilitários
# -----------------------------
def die(msg: str, code: int = 1) -> None:
    """Encerra a execução do programa com erro e mensagem clara.

    Objetivo:
        Padronizar falhas "controladas" (sem stacktrace) para cenários previsíveis,
        como parâmetros ausentes, JSON inválido, ausência de PAT, etc.

    Entradas (Args):
        msg: Mensagem de erro a ser exibida ao usuário.
        code: Código de saída do processo (default: 1).

    Saídas (Returns):
        None (não retorna: finaliza o processo).

    Efeitos colaterais:
        - Escreve mensagem em `stderr`.
        - Encerra o processo com `sys.exit(code)`.
    """
    print(f"Erro: {msg}", file=sys.stderr)
    sys.exit(code)


# -----------------------------
# Loading visual (terminal)
# -----------------------------
_PROGRESS_ENABLED = os.getenv("AZDO_PROGRESS", "1") != "0"


@contextmanager
def loading(label: str, enabled: bool = True, interval: float = 0.25):
    """Mostra um indicador simples de execução no terminal.

    Objetivo:
        Dar feedback visual enquanto operações potencialmente lentas executam
        (ex.: chamadas HTTP). O indicador é escrito em `stderr` para manter
        `stdout` limpo para logs/prints de resultado.

    Entradas (Args):
        label: Texto base exibido (ex.: "wiql", "create_work_item(Task)").
        enabled: Liga/desliga o loading por chamada.
        interval: Intervalo em segundos entre atualizações (default: 0.25s).

    Saídas (Yields):
        Um contexto (`with loading(...):`) durante o qual o loading fica ativo.

    Efeitos colaterais:
        - Escreve em `stderr` com carriage return (`\\r`).
        - Cria e gerencia uma thread daemon temporária.

    Condições de ativação:
        - Só renderiza se `sys.stderr.isatty()` for True (terminal interativo).
        - Pode ser desativado via env `AZDO_PROGRESS=0`.
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

        # finaliza imprimindo só o label + newline
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


def load_json(path: str) -> Dict[str, Any]:
    """Lê um arquivo JSON do disco e retorna o objeto Python (dict).

    Objetivo:
        Centralizar leitura e validação básica de JSON de entrada.

    Entradas (Args):
        path: Caminho do arquivo JSON.

    Saídas (Returns):
        Um dicionário Python (`dict`) com o conteúdo do JSON.

    Erros (Raises/Exit):
        - Finaliza com `die()` se o arquivo não existir.
        - Finaliza com `die()` se o conteúdo não for JSON válido.

    Efeitos colaterais:
        - Lê arquivo do disco.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die(f"Arquivo não encontrado: {path}")
    except json.JSONDecodeError as e:
        die(f"JSON inválido em {path}: {e}")


def safe_html(text: str) -> str:
    """Escapa HTML mínimo para evitar quebra/injeção em campos ricos do Azure DevOps.

    Objetivo:
        Evitar que caracteres `<` e `>` quebram o HTML enviado ao Azure DevOps,
        reduzindo risco de injeção de HTML.

    Entradas (Args):
        text: Texto arbitrário.

    Saídas (Returns):
        String com substituições mínimas: `<` -> `&lt;` e `>` -> `&gt;`.
    """
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def text_to_html(text: str) -> str:
    """Converte texto "markdown-like" simples em HTML para renderização no Azure DevOps.

    Objetivo:
        Transformar descrições em texto com quebras de linha e bullets em HTML
        básico aceito por campos como `System.Description`.

    Entradas (Args):
        text: Texto de entrada contendo parágrafos e listas com prefixo `- `.

    Saídas (Returns):
        HTML como string, composto de `<p>...</p>` e `<ul><li>...</li></ul>`.

    Regras:
        - Linhas em branco quebram parágrafo.
        - Blocos de linhas começando com `- ` viram listas (`<ul><li>...</li></ul>`).
        - O conteúdo é escapado via `safe_html()` para reduzir risco de injeção.

    Observação:
        Não é um parser Markdown completo; é propositalmente simples.
    """
    lines = (text or "").splitlines()

    blocks: List[str] = []
    buf: List[str] = []

    def flush_paragraph() -> None:
        """Finaliza o parágrafo atual (buffer) e escreve um `<p>` em blocks."""
        nonlocal buf
        if not buf:
            return
        p = "<br/>".join([safe_html(x) for x in buf])
        blocks.append(f"<p>{p}</p>")
        buf = []

    def flush_list(items: List[str]) -> None:
        """Escreve um `<ul>` com `<li>` escapados em blocks."""
        lis = "".join([f"<li>{safe_html(i)}</li>" for i in items])
        blocks.append(f"<ul>{lis}</ul>")

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # linha vazia: quebra de bloco
        if line.strip() == "":
            flush_paragraph()
            i += 1
            continue

        # bloco de lista "- "
        if line.lstrip().startswith("- "):
            flush_paragraph()
            items: List[str] = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(lines[i].lstrip()[2:].strip())
                i += 1
            flush_list(items)
            continue

        # texto normal
        buf.append(line)
        i += 1

    flush_paragraph()

    return "\n".join(blocks) if blocks else "<p></p>"


def html_ul(items: List[str]) -> str:
    """Converte uma lista de strings em HTML `<ul><li>...</li></ul>`.

    Objetivo:
        Gerar HTML de lista para campos como Acceptance Criteria, de forma
        consistente e escapada.

    Entradas (Args):
        items: Lista de itens (strings).

    Saídas (Returns):
        Uma string HTML no formato `<ul><li>...</li>...</ul>`.

    Observação:
        Cada item é escapado com `safe_html()`.
    """
    safe_items = [safe_html(x) for x in items]
    return "<ul>" + "".join([f"<li>{x}</li>" for x in safe_items]) + "</ul>"


def make_basic_auth(pat: str) -> str:
    """Cria header Authorization Basic Auth esperado pelo Azure DevOps para uso de PAT.

    Objetivo:
        Construir o valor do header `Authorization` no formato Basic Auth, onde:
        - username é vazio
        - senha é o PAT
        - token final = base64(":<PAT>")

    Entradas (Args):
        pat: Personal Access Token do Azure DevOps.

    Saídas (Returns):
        String no formato `Basic <base64(:PAT)>`.

    Segurança:
        Não faça print desse retorno (contém credencial).
    """
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"


def build_headers(pat: str, content_type: str) -> Dict[str, str]:
    """Monta headers padrão para requisições à API do Azure DevOps.

    Objetivo:
        Centralizar criação de headers HTTP, variando o `Content-Type` conforme endpoint.

    Entradas (Args):
        pat: Personal Access Token.
        content_type: Content-Type do request (ex.: `application/json`,
                      `application/json-patch+json`).

    Saídas (Returns):
        Dicionário com headers: Authorization, Accept, Content-Type.
    """
    return {
        "Authorization": make_basic_auth(pat),
        "Accept": "application/json",
        "Content-Type": content_type,
    }


def normalize_org_url(org: str) -> str:
    """Normaliza o argumento de organização para a URL base do Azure DevOps.

    Objetivo:
        Aceitar entradas curtas (nome da org) ou URL completa e retornar
        sempre uma URL sem barra final.

    Entradas (Args):
        org: Pode ser:
             - "Name" -> vira "https://dev.azure.com/Name"
             - "https://dev.azure.com/Name" -> permanece

    Saídas (Returns):
        URL da organização sem barra final.
    """
    if org.startswith("http://") or org.startswith("https://"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}".rstrip("/")


def extract_work_item_id_from_url(url: str) -> Optional[int]:
    """Extrai um work item ID a partir de uma URL.

    Objetivo:
        Suportar uso de `parent_url` (url do board ou API) como referência
        ao PBI pai, extraindo o ID de forma robusta.

    Entradas (Args):
        url: URL contendo o ID do Work Item.

    Saídas (Returns):
        - int com o ID extraído, se encontrado
        - None se não conseguir extrair

    Estratégias suportadas:
        - query param: `?workitem=123`
        - padrão API: `.../_apis/wit/workItems/123`
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        if "workitem" in qs and qs["workitem"]:
            return int(qs["workitem"][0])

        parts = [p for p in parsed.path.split("/") if p]
        if "workItems" in parts:
            idx = parts.index("workItems")
            if idx + 1 < len(parts):
                return int(parts[idx + 1])

    except Exception:
        return None

    return None


def normalize_title_from_task(task: Dict[str, Any]) -> str:
    """Resolve o título de uma Task com fallback seguro.

    Objetivo:
        Garantir que toda Task criada tenha um `System.Title` não-vazio.

    Entradas (Args):
        task: Dicionário com possíveis chaves:
              - `title` (preferido)
              - `name`
              - `description` (fallback parcial)

    Saídas (Returns):
        Título final (string) não-vazia.

    Erros (Exit):
        Finaliza com `die()` se nenhum campo permitir compor um título.
    """
    title = task.get("title") or task.get("name")
    if title:
        return str(title).strip()

    desc = (task.get("description") or "").strip()
    if not desc:
        die("Task precisa ter 'title' (ou 'name') ou ao menos 'description' para gerar um título.")

    return (desc[:80] + "…") if len(desc) > 80 else desc


def resolve_parent_id(az: "AzDO", task: Dict[str, Any]) -> int:
    """Resolve o ID do PBI pai para uma Task.

    Objetivo:
        Determinar o PBI "pai" para vínculo hierárquico ao criar Tasks.

    Entradas (Args):
        az: Cliente AzDO (usado para lookup em modo `parent_key`).
        task: Dicionário da task. Suporta:
              1) `parent_id` (preferido)
              2) `parent_url` (extrai ID)
              3) `parent_key` (lookup por tag `ext:<key>` via WIQL) [opcional]

    Saídas (Returns):
        ID do PBI pai (int > 0).

    Erros (Exit):
        Finaliza com `die()` se não conseguir resolver o parent.
    """
    if task.get("parent_id") is not None:
        try:
            pid = int(task["parent_id"])
            if pid <= 0:
                die("parent_id inválido (deve ser > 0).")
            return pid
        except ValueError:
            die("parent_id deve ser um número inteiro.")

    if task.get("parent_url"):
        pid = extract_work_item_id_from_url(str(task["parent_url"]))
        if pid and pid > 0:
            return pid
        die("parent_url informado, mas não foi possível extrair o ID (tente usar parent_id).")

    if task.get("parent_key"):
        pid = az.find_pbi_id_by_ext_key(str(task["parent_key"]))
        if pid:
            return pid
        die(f"Não encontrei PBI com tag ext:{task['parent_key']} (parent_key).")

    die("Task precisa de parent_id OU parent_url (ou parent_key, se você usar tags ext:).")
    raise RuntimeError("unreachable")


# -----------------------------
# Azure DevOps REST Client
# -----------------------------
class AzDO:
    """Cliente mínimo para Azure DevOps Work Item Tracking.

    Objetivo:
        Encapsular chamadas REST necessárias para:
        - WIQL (consultas) para idempotência opcional
        - criação de work items via JSON Patch (PBI/Task)

    Observação:
        É um cliente intencionalmente pequeno, focado no necessário para a CLI.
    """

    def __init__(self, org_url: str, project: str, pat: str, dry_run: bool = False) -> None:
        """Inicializa o cliente AzDO.

        Entradas (Args):
            org_url: URL base da organização (ex.: https://dev.azure.com/Name).
            project: Nome do projeto no Azure DevOps.
            pat: Personal Access Token.
            dry_run: Se True, não cria itens (simula chamadas).

        Saídas (Returns):
            None.

        Efeitos colaterais:
            - Prepara headers HTTP.
            - Armazena credenciais em memória (não logar).
        """
        self.org_url = org_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.dry_run = dry_run

        self.headers_json = build_headers(pat, "application/json")
        self.headers_patch = build_headers(pat, "application/json-patch+json")

    def _url(self, path: str) -> str:
        """Concatena org + project + path para formar URL final.

        Entradas (Args):
            path: Caminho relativo (ex.: "/_apis/wit/wiql?...").

        Saídas (Returns):
            URL final (string).
        """
        return f"{self.org_url}/{self.project}{path}"

    def wiql(self, query: str) -> Dict[str, Any]:
        """Executa uma consulta WIQL.

        Objetivo:
            Suportar lookup (ex.: idempotência via tag ext:<key>).

        Entradas (Args):
            query: String WIQL.

        Saídas (Returns):
            JSON (dict) retornado pela API de WIQL.

        Erros (Raises):
            RuntimeError: Se status HTTP >= 300.
        """
        with loading("wiql"):
            url = self._url(f"/_apis/wit/wiql?api-version={API_VERSION}")

            if self.dry_run:
                return {"workItems": []}

            r = requests.post(url, headers=self.headers_json, json={"query": query}, timeout=30)
            if r.status_code >= 300:
                raise RuntimeError(f"WIQL falhou: {r.status_code}\n{r.text}")
            return r.json()

    def create_work_item(self, wi_type: str, patch_ops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Cria um work item (PBI/Task/etc.) via JSON Patch.

        Objetivo:
            Criar work items usando o endpoint oficial do Azure DevOps.

        Entradas (Args):
            wi_type: Tipo do Work Item (ex.: "Product Backlog Item", "Task").
            patch_ops: Lista de operações JSON Patch (dicts).

        Saídas (Returns):
            JSON (dict) retornado pela API ao criar o item.

        Erros (Raises):
            RuntimeError: Se status HTTP >= 300.
        """
        with loading(f"create_work_item({wi_type})"):
            url = self._url(f"/_apis/wit/workitems/${wi_type}?api-version={API_VERSION}")

            if self.dry_run:
                return {"id": -1, "url": url, "dry_run": True}

            r = requests.post(url, headers=self.headers_patch, json=patch_ops, timeout=30)
            if r.status_code >= 300:
                raise RuntimeError(f"Create {wi_type} falhou: {r.status_code}\n{r.text}")
            return r.json()

    def find_pbi_id_by_ext_key(self, ext_key: str) -> Optional[int]:
        """Encontra um PBI por tag `ext:<key>` usando WIQL.

        Objetivo:
            Implementar idempotência opcional: se o PBI com a chave já existir,
            a CLI pode pular criação para evitar duplicação.

        Entradas (Args):
            ext_key: Valor da chave (sem o prefixo `ext:`).

        Saídas (Returns):
            - ID do PBI (int) se encontrado
            - None se não existir

        Observação:
            Retorna o item mais recentemente alterado com a tag.
        """
        q = f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE
            [System.TeamProject] = '{self.project}'
            AND [System.WorkItemType] = 'Product Backlog Item'
            AND [System.Tags] CONTAINS 'ext:{ext_key}'
        ORDER BY [System.ChangedDate] DESC
        """
        data = self.wiql(q)
        items = data.get("workItems") or []
        if not items:
            return None
        return int(items[0]["id"])


# -----------------------------
# Builders de JSON Patch
# -----------------------------
def pbi_patch(pbi: Dict[str, Any], ext_key: Optional[str]) -> List[Dict[str, Any]]:
    """Constrói JSON Patch ops para criar um Product Backlog Item.

    Objetivo:
        Converter um objeto de PBI do JSON de entrada em operações JSON Patch
        aceitas pelo endpoint de Work Items.

    Entradas (Args):
        pbi: Dicionário do PBI (ex.: name/title, description, iteration, etc).
        ext_key: Se informado, adiciona a tag `ext:<ext_key>`.

    Saídas (Returns):
        Lista de operações JSON Patch (list[dict]) para criação do PBI.

    Erros (Exit):
        Finaliza com `die()` se não existir `name`/`title` no PBI.

    Observação:
        - `System.Description` é enviado como HTML básico.
        - Acceptance Criteria (se lista) é enviada como `<ul>`.
    """
    ops: List[Dict[str, Any]] = []

    title = pbi.get("name") or pbi.get("title")
    if not title:
        die("PBI sem 'name'/'title'.")

    ops.append({"op": "add", "path": "/fields/System.Title", "value": title})

    if pbi.get("description"):
        ops.append(
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": f"<p>{safe_html(pbi['description'])}</p>",
            }
        )

    if pbi.get("iteration"):
        ops.append({"op": "add", "path": "/fields/System.IterationPath", "value": pbi["iteration"]})

    if pbi.get("area_path"):
        ops.append({"op": "add", "path": "/fields/System.AreaPath", "value": pbi["area_path"]})

    if isinstance(pbi.get("priority"), int):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": pbi["priority"]})

    if isinstance(pbi.get("effort"), (int, float)):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.Effort", "value": pbi["effort"]})

    if pbi.get("value_area"):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.ValueArea", "value": pbi["value_area"]})

    ac = pbi.get("acceptance_criteria")
    if isinstance(ac, list) and ac:
        ops.append(
            {
                "op": "add",
                "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                "value": html_ul(ac),
            }
        )

    tags = list(pbi.get("tags") or [])
    if ext_key:
        tags.append(f"ext:{ext_key}")

    if tags:
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": "; ".join(tags)})

    if pbi.get("state"):
        ops.append({"op": "add", "path": "/fields/System.State", "value": pbi["state"]})

    return ops


def task_patch(task: Dict[str, Any], parent_id: int, org_url: str, project: str) -> List[Dict[str, Any]]:
    """Constrói JSON Patch ops para criar uma Task vinculada a um PBI (pai).

    Objetivo:
        Converter um objeto Task do JSON de entrada em operações JSON Patch,
        incluindo o relacionamento hierárquico com o PBI pai.

    Entradas (Args):
        task: Dicionário da task (title/description/etc.).
        parent_id: ID do PBI pai (int).
        org_url: URL base da organização (ex.: https://dev.azure.com/Name).
        project: Nome do projeto ADO.

    Saídas (Returns):
        Lista de operações JSON Patch para criação da Task.

    Observação:
        - `System.Description` é convertido via `text_to_html`.
        - O relacionamento pai-filho é criado via `relations` com
          `System.LinkTypes.Hierarchy-Reverse` apontando para a API URL do pai.
    """
    ops: List[Dict[str, Any]] = []

    title = normalize_title_from_task(task)
    ops.append({"op": "add", "path": "/fields/System.Title", "value": title})

    if task.get("description"):
        ops.append(
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": text_to_html(task["description"]),
            }
        )

    if task.get("iteration"):
        ops.append({"op": "add", "path": "/fields/System.IterationPath", "value": task["iteration"]})

    if task.get("area_path"):
        ops.append({"op": "add", "path": "/fields/System.AreaPath", "value": task["area_path"]})

    if task.get("state"):
        ops.append({"op": "add", "path": "/fields/System.State", "value": task["state"]})

    if isinstance(task.get("priority"), int):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": task["priority"]})

    if isinstance(task.get("remaining_work"), (int, float)):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.RemainingWork", "value": task["remaining_work"]})

    if task.get("activity"):
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Activity", "value": task["activity"]})

    if task.get("assigned_to"):
        v = str(task["assigned_to"]).strip()
        if v:
            ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": v})

    if task.get("tags"):
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": "; ".join(task["tags"])})

    parent_url = f"{org_url.rstrip('/')}/{project}/_apis/wit/workItems/{parent_id}"
    ops.append(
        {
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": parent_url,
                "attributes": {"comment": "Parent PBI"},
            },
        }
    )

    return ops


# -----------------------------
# Commands (ações da CLI)
# -----------------------------
def cmd_create_pbis(az: AzDO, json_path: str, allow_duplicates: bool) -> int:
    """Comando `pbis`: cria Product Backlog Items a partir de um JSON.

    Objetivo:
        Ler `pbi.json` e criar PBIs no Azure DevOps. Se o PBI tiver `key` e
        `allow_duplicates` estiver False, tenta evitar duplicação usando WIQL
        com tag `ext:<key>`.

    Entradas (Args):
        az: Cliente AzDO configurado (org/projeto/pat).
        json_path: Caminho do arquivo JSON contendo `{ "pbis": [ ... ] }`.
        allow_duplicates: Se True, não tenta idempotência (pode duplicar).

    Saídas (Returns):
        Código de saída (0 em sucesso).

    Efeitos colaterais:
        - Cria PBIs no Azure DevOps (exceto em `dry_run`).
        - Gera `created_pbis_output.json` com IDs criados (se houver).

    Erros (Exit/Raises):
        - Finaliza com `die()` se JSON não tiver estrutura esperada.
        - Pode propagar `RuntimeError` do client em falhas HTTP.
    """
    data = load_json(json_path)
    pbis = data.get("pbis")
    if not isinstance(pbis, list) or not pbis:
        die('pbi.json precisa conter { "pbis": [ ... ] } com ao menos 1 item.')

    created = []
    for pbi in pbis:
        ext_key = pbi.get("key")

        if ext_key and not allow_duplicates:
            existing = az.find_pbi_id_by_ext_key(str(ext_key))
            if existing:
                print(f"⏭️  PBI já existe (ext:{ext_key}) -> #{existing} (skip)")
                continue

        ops = pbi_patch(pbi, str(ext_key) if ext_key else None)
        created_item = az.create_work_item("Product Backlog Item", ops)

        print(f"✅ PBI criado -> #{created_item['id']}")
        created.append(
            {
                "key": ext_key,
                "id": created_item["id"],
                "title": pbi.get("name") or pbi.get("title"),
            }
        )

    if created:
        with open("created_pbis_output.json", "w", encoding="utf-8") as f:
            json.dump({"pbis": created}, f, ensure_ascii=False, indent=2)
        print("📌 created_pbis_output.json gerado (use os IDs para preencher parent_id nas tasks).")

    return 0


def cmd_create_tasks(az: AzDO, json_path: str) -> int:
    """Comando `tasks`: cria Tasks e as vincula a PBIs existentes.

    Objetivo:
        Ler `task.json` e criar Tasks no Azure DevOps, vinculando cada Task
        ao PBI pai via `parent_id` (preferido) ou `parent_url`.

    Entradas (Args):
        az: Cliente AzDO configurado (org/projeto/pat).
        json_path: Caminho do arquivo JSON contendo `{ "tasks": [ ... ] }`.

    Saídas (Returns):
        Código de saída (0 em sucesso).

    Efeitos colaterais:
        - Cria Tasks no Azure DevOps (exceto em `dry_run`).
        - Gera `created_tasks_output.json` com IDs criados (se houver).

    Erros (Exit/Raises):
        - Finaliza com `die()` se JSON não tiver estrutura esperada.
        - Pode propagar `RuntimeError` do client em falhas HTTP.
    """
    data = load_json(json_path)
    tasks = data.get("tasks")

    if not isinstance(tasks, list) or not tasks:
        die('task.json precisa conter { "tasks": [ ... ] } com ao menos 1 item.')

    created = []
    for task in tasks:
        parent_id = resolve_parent_id(az, task)

        ops = task_patch(task, parent_id, az.org_url, az.project)
        created_item = az.create_work_item("Task", ops)

        print(f"✅ Task criada -> #{created_item['id']} (parent #{parent_id})")
        created.append({"id": created_item["id"], "parent_id": parent_id, "title": normalize_title_from_task(task)})

    if created:
        with open("created_tasks_output.json", "w", encoding="utf-8") as f:
            json.dump({"tasks": created}, f, ensure_ascii=False, indent=2)
        print("📌 created_tasks_output.json gerado.")

    return 0


# -----------------------------
# CLI Parser
# -----------------------------
def build_parser() -> argparse.ArgumentParser:
    """Monta o parser da CLI.

    Objetivo:
        Definir flags globais e subcomandos com `argparse`.

    Entradas (Args):
        Nenhuma (usa apenas `argparse`).

    Saídas (Returns):
        Instância de `argparse.ArgumentParser` configurada.

    Subcomandos:
        - pbis  (criar PBIs)
        - tasks (criar Tasks)
        - help  (ajuda)
    """
    p = argparse.ArgumentParser(
        prog="azdo_cli",
        description="CLI para criar PBIs e Tasks no Azure DevOps (Boards) via REST API.",
        add_help=True,
    )

    p.add_argument("--org", help="Organização. Ex: Name ou https://dev.azure.com/Name")
    p.add_argument("--project", help="Nome do projeto no Azure DevOps")
    p.add_argument("--pat", default=os.getenv("AZDO_PAT"), help="PAT (ou defina env AZDO_PAT)")
    p.add_argument("--dry-run", action="store_true", help="Simula sem criar itens")

    sub = p.add_subparsers(dest="command")

    pbis = sub.add_parser("pbis", help="Criar Product Backlog Items a partir de um JSON")
    pbis.add_argument("--file", required=True, help="Caminho do pbi.json")
    pbis.add_argument("--allow-duplicates", action="store_true", help="Não tenta evitar duplicação (mesmo com key)")

    tasks = sub.add_parser("tasks", help="Criar Tasks e linkar ao PBI usando parent_id/parent_url")
    tasks.add_argument("--file", required=True, help="Caminho do task.json")

    help_cmd = sub.add_parser("help", help="Exibe ajuda geral ou de um comando específico")
    help_cmd.add_argument("topic", nargs="?", help="Comando específico: pbis ou tasks")

    return p


def main() -> None:
    """Entry point da CLI.

    Objetivo:
        Orquestrar o fluxo principal:
        - parse args
        - tratar `help`
        - validar pré-requisitos (PAT, org, project)
        - instanciar cliente AzDO
        - executar subcomando selecionado
        - normalizar erros para o usuário

    Entradas (Args):
        Nenhuma diretamente; usa `sys.argv` via `argparse`.

    Saídas (Returns):
        None (finaliza processo com `sys.exit` internamente).

    Efeitos colaterais:
        - Pode executar chamadas HTTP ao Azure DevOps.
        - Pode escrever arquivos `created_*_output.json`.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help":
        if args.topic:
            valid = {"pbis", "tasks"}
            if args.topic not in valid:
                die(f"Comando desconhecido para help: {args.topic}")

            subparser = parser._subparsers._group_actions[0].choices[args.topic]
            subparser.print_help()
            sys.exit(0)

        parser.print_help()
        sys.exit(0)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if not args.pat:
        die("PAT não informado. Use --pat ou defina AZDO_PAT no ambiente.")

    if not args.org or not args.project:
        die("Você deve informar --org e --project.")

    org_url = normalize_org_url(args.org)
    az = AzDO(org_url=org_url, project=args.project, pat=args.pat, dry_run=args.dry_run)

    try:
        if args.command == "pbis":
            code = cmd_create_pbis(az, args.file, allow_duplicates=args.allow_duplicates)
        elif args.command == "tasks":
            code = cmd_create_tasks(az, args.file)
        else:
            parser.print_help()
            sys.exit(0)

        sys.exit(code)

    except requests.RequestException as e:
        die(f"Falha de rede/HTTP: {e}")

    except RuntimeError as e:
        die(str(e))

    except Exception as e:
        die(f"Falha inesperada: {e}")


if __name__ == "__main__":
    main()
