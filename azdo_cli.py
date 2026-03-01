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

  python3 azdo_cli.py --org 4le --project Lab pbis  --file ./pbi.json
  python3 azdo_cli.py --org 4le --project Lab tasks --file ./task.json

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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

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
    """
    Carrega variáveis do arquivo .env para o os.environ.
    - Ignora linhas vazias e comentários (#)
    - Aceita 'export KEY=VALUE'
    - Remove aspas simples/duplas ao redor do valor
    - Por padrão, NÃO sobrescreve env já existente (override=False)
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

load_dotenv()  # carrega .env na inicialização (se existir)

# -----------------------------
# Utilitários
# -----------------------------
def die(msg: str, code: int = 1) -> None:
    """
    Encerra a execução do programa com erro e mensagem clara.

    Use para falhas que não valem stacktrace, como:
    - parâmetro ausente
    - JSON inválido
    - falta de PAT
    - pré-condições quebradas
    """
    print(f"Erro: {msg}", file=sys.stderr)
    sys.exit(code)


def load_json(path: str) -> Dict[str, Any]:
    """
    Lê um arquivo JSON do disco e retorna o objeto Python (dict).

    Falha de forma explícita se:
    - arquivo não existir
    - conteúdo não for JSON válido
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die(f"Arquivo não encontrado: {path}")
    except json.JSONDecodeError as e:
        die(f"JSON inválido em {path}: {e}")


def text_to_html(text: str) -> str:
    """
    Converte texto com quebras de linha e bullets '-' em HTML simples
    para o Azure DevOps renderizar corretamente.

    Regras:
    - Linhas em branco => separação de parágrafos
    - Blocos de linhas começando com '- ' => <ul><li>...</li></ul>
    - Escapa HTML para evitar injeção
    """
    lines = (text or "").splitlines()

    blocks: List[str] = []
    buf: List[str] = []

    def flush_paragraph():
        nonlocal buf
        if not buf:
            return
        # junta linhas do parágrafo com <br/>
        p = "<br/>".join([safe_html(x) for x in buf])
        blocks.append(f"<p>{p}</p>")
        buf = []

    def flush_list(items: List[str]):
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


def safe_html(text: str) -> str:
    """
    Faz escaping mínimo de HTML para evitar quebrar campos ricos do Azure DevOps
    (System.Description e Acceptance Criteria geralmente aceitam HTML).
    """
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def html_ul(items: List[str]) -> str:
    """
    Converte lista de strings em um <ul><li>..</li></ul> com escaping básico.

    Útil para Acceptance Criteria: o ADO exibe bem listas em HTML.
    """
    safe_items = [safe_html(x) for x in items]
    return "<ul>" + "".join([f"<li>{x}</li>" for x in safe_items]) + "</ul>"


def make_basic_auth(pat: str) -> str:
    """
    Cria header Authorization no formato Basic Auth esperado pelo Azure DevOps
    quando se usa PAT.

    Padrão: username vazio e PAT como 'senha' -> base64(':PAT')
    """
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"


def build_headers(pat: str, content_type: str) -> Dict[str, str]:
    """
    Monta headers padrão para requisições à API do Azure DevOps.

    content_type varia:
    - application/json para WIQL
    - application/json-patch+json para criar work items (JSON Patch)
    """
    return {
        "Authorization": make_basic_auth(pat),
        "Accept": "application/json",
        "Content-Type": content_type,
    }


def normalize_org_url(org: str) -> str:
    """
    Normaliza o argumento de organização.

    Aceita:
    - '4le'                       -> vira https://dev.azure.com/4le
    - 'https://dev.azure.com/4le' -> permanece

    Retorna sempre sem barra final.
    """
    if org.startswith("http://") or org.startswith("https://"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}".rstrip("/")


def extract_work_item_id_from_url(url: str) -> Optional[int]:
    """
    Extrai um work item ID de uma URL, suportando:
    - query param: ?workitem=123
    - padrão API: .../_apis/wit/workItems/123

    Retorna None se não conseguir extrair.
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        # Ex: ...?workitem=2
        if "workitem" in qs and qs["workitem"]:
            return int(qs["workitem"][0])

        # Ex: .../_apis/wit/workItems/123
        parts = [p for p in parsed.path.split("/") if p]
        if "workItems" in parts:
            idx = parts.index("workItems")
            if idx + 1 < len(parts):
                return int(parts[idx + 1])

    except Exception:
        return None

    return None


def normalize_title_from_task(task: Dict[str, Any]) -> str:
    """
    Define o título da Task de forma robusta.

    Ordem:
    - title
    - name
    - primeira parte do description (fallback)
    """
    title = task.get("title") or task.get("name")
    if title:
        return str(title).strip()

    desc = (task.get("description") or "").strip()
    if not desc:
        die("Task precisa ter 'title' (ou 'name') ou ao menos 'description' para gerar um título.")
    # Fallback simples: corta para não virar um título gigante
    return (desc[:80] + "…") if len(desc) > 80 else desc


def resolve_parent_id(az: "AzDO", task: Dict[str, Any]) -> int:
    """
    Resolve o ID do PBI pai para uma task.

    Suporta:
    1) parent_id (preferido)
    2) parent_url (extrai id)
    3) parent_key (lookup via WIQL por tag ext:<key>) [opcional]

    Se não conseguir resolver, falha.
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

    # Opcional: modo antigo por key/tag ext:
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
    """
    Cliente mínimo para Azure DevOps Work Item Tracking.

    Responsabilidades:
    - executar WIQL (opcional, para idempotência por key/tag)
    - criar work items via JSON Patch (PBI/Task)
    """

    def __init__(self, org_url: str, project: str, pat: str, dry_run: bool = False) -> None:
        """
        org_url: ex. https://dev.azure.com/4le
        project: nome do projeto (ex. Lab)
        pat: Personal Access Token
        dry_run: se True, não cria nada (simula)
        """
        self.org_url = org_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.dry_run = dry_run

        self.headers_json = build_headers(pat, "application/json")
        self.headers_patch = build_headers(pat, "application/json-patch+json")

    def _url(self, path: str) -> str:
        """
        Concatena org + project + path.
        """
        return f"{self.org_url}/{self.project}{path}"

    def wiql(self, query: str) -> Dict[str, Any]:
        """
        Executa uma consulta WIQL e retorna o JSON da API.

        Útil para:
        - evitar duplicar PBIs quando você usa "key" e grava ext:<key> em tags.
        """
        url = self._url(f"/_apis/wit/wiql?api-version={API_VERSION}")

        if self.dry_run:
            return {"workItems": []}

        r = requests.post(url, headers=self.headers_json, json={"query": query}, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"WIQL falhou: {r.status_code}\n{r.text}")
        return r.json()

    def create_work_item(self, wi_type: str, patch_ops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Cria um work item (PBI, Task, etc.) via endpoint de Work Items + JSON Patch.

        wi_type:
        - 'Product Backlog Item'
        - 'Task'
        """
        url = self._url(f"/_apis/wit/workitems/${wi_type}?api-version={API_VERSION}")

        if self.dry_run:
            return {"id": -1, "url": url, "dry_run": True}

        r = requests.post(url, headers=self.headers_patch, json=patch_ops, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Create {wi_type} falhou: {r.status_code}\n{r.text}")
        return r.json()

    def find_pbi_id_by_ext_key(self, ext_key: str) -> Optional[int]:
        """
        Encontra o ID de um PBI buscando por tag ext:<ext_key>.

        Retorna:
        - int (System.Id) do PBI mais recentemente alterado com essa tag
        - None se não encontrar
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
    """
    Constrói JSON Patch ops para criar um Product Backlog Item.

    Regras:
    - Title é obrigatório
    - Se ext_key existir, adiciona tag ext:<key> (útil para idempotência)
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

    # Tags (opcionais)
    tags = list(pbi.get("tags") or [])
    if ext_key:
        tags.append(f"ext:{ext_key}")

    if tags:
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": "; ".join(tags)})

    if pbi.get("state"):
        ops.append({"op": "add", "path": "/fields/System.State", "value": pbi["state"]})

    return ops


def task_patch(task: Dict[str, Any], parent_id: int, org_url: str, project: str) -> List[Dict[str, Any]]:
    """
    Constrói JSON Patch para criar Task vinculada a um PBI existente.
    Usa parent_id diretamente e ignora a URL de board (se existir).
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
        # Se for string vazia, não adiciona (ADO costuma rejeitar vazio)
        v = str(task["assigned_to"]).strip()
        if v:
            ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": v})

    if task.get("tags"):
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": "; ".join(task["tags"])})

    # Link pai-filho (API URL, não URL do board)
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
    """
    Comando: pbis

    Lê o arquivo pbi.json e cria PBIs.

    - key é opcional:
      - se existir, adiciona tag ext:<key> e pode evitar duplicar (via WIQL).
      - se não existir, não há como garantir idempotência (pode duplicar ao reexecutar).
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
    """
    Comando: tasks

    Lê o arquivo task.json e cria Tasks linkadas a PBIs existentes.
    Resolve parent por parent_id (preferido) ou parent_url.
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
    """
    Monta parser da CLI.

    - flags globais: --org, --project, --pat, --dry-run
    - subcomandos: pbis, tasks, help
    """
    p = argparse.ArgumentParser(
        prog="azdo_cli",
        description="CLI para criar PBIs e Tasks no Azure DevOps (Boards) via REST API.",
        add_help=True,
    )

    p.add_argument("--org", help="Organização. Ex: 4le ou https://dev.azure.com/4le")
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
    """
    Entry point:
    - lê args
    - executa help explícito se solicitado
    - valida PAT e parâmetros obrigatórios
    - cria cliente AzDO
    - executa o comando escolhido
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
