# =============================
# Makefile - OpenAudit Brasil
# Automação para:
# - criar e usar virtualenv
# - instalar dependências
# - rodar as CLIs azdo_cli.py e refine_cli.py
# - executar pipeline completo (gerar -> validar -> criar tasks no Azure DevOps)
# =============================

# Interpretador Python a ser usado para criar o virtualenv (pode sobrescrever via: make PYTHON=python3.11 ...)
PYTHON ?= python3

# Diretório do virtualenv
VENV_DIR ?= .venv

# Binários do virtualenv
BIN := $(VENV_DIR)/bin

# Declara targets que não representam arquivos (evita conflito com arquivos de mesmo nome)
.PHONY: help venv install run-pbis run-tasks run-help run-help-azdo run-help-refine clean run-refine run-refine-validate run-full

# ---------------------------------
# help
# Mostra resumo de comandos e variáveis exigidas.
# OBS: mantenha apenas UM target "help" (evita override).
# ---------------------------------
help:
	@echo "Targets:"
	@echo "  make venv                 - cria virtualenv em .venv (se não existir)"
	@echo "  make install              - instala dependências do requirements.txt"
	@echo "  make run-help             - mostra help do azdo_cli + refine_cli"
	@echo "  make run-help-azdo        - mostra help apenas do azdo_cli"
	@echo "  make run-help-refine      - mostra help apenas do refine_cli"
	@echo "  make run-pbis ORG=4le PROJECT=Lab FILE=./data/pbi.json"
	@echo "  make run-tasks ORG=4le PROJECT=Lab FILE=./data/task.json"
	@echo "  make run-refine INPUT=./data/refinement.txt PARENT_ID=123 ITERATION='Lab\\Sprint 1' AREA=Lab [OUT=./data/task.json]"
	@echo "  make run-refine-validate FILE=./data/task.json [PARENT_ID=123]"
	@echo "  make run-full ORG=4le PROJECT=Lab INPUT=./data/refinement.txt PARENT_ID=4 ITERATION='Lab\\Sprint 1' AREA=Lab [OUT=./data/task.json] [MODEL=gpt-4.1-nano]"
	@echo "  make clean                - remove .venv"
	@echo ""
	@echo "Variáveis de ambiente necessárias:"
	@echo "  AZDO_PAT                  - Personal Access Token do Azure DevOps (Work Items Read & write)"
	@echo "  OPENAI_API_KEY            - necessário para refine_cli.py generate (pode vir do .env)"
	@echo ""
	@echo "Opções:"
	@echo "  AZDO_PROGRESS=0           - desliga loading do azdo_cli.py"
	@echo "  REFINE_PROGRESS=0         - desliga loading do refine_cli.py"

# ---------------------------------
# venv
# Cria o virtualenv somente se o diretório não existir.
# ---------------------------------
venv:
	@test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)

# ---------------------------------
# install
# Instala/atualiza pip e instala dependências do requirements.txt dentro do venv.
# Depende de "venv".
# ---------------------------------
install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r requirements.txt

# ---------------------------------
# run-pbis
# Cria PBIs no Azure DevOps a partir de um arquivo JSON.
# Requer variáveis: ORG, PROJECT, FILE.
# Depende de "install" (garante venv e deps).
# ---------------------------------
run-pbis: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(FILE)" ]; then \
		echo "Uso: make run-pbis ORG=4le PROJECT=Lab FILE=./data/pbi.json"; \
		exit 1; \
	fi
	$(BIN)/python azdo_cli.py --org $(ORG) --project $(PROJECT) pbis --file $(FILE)

# ---------------------------------
# run-tasks
# Cria Tasks no Azure DevOps a partir de um arquivo JSON e faz link com o PBI pai.
# Requer variáveis: ORG, PROJECT, FILE.
# Depende de "install".
# ---------------------------------
run-tasks: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(FILE)" ]; then \
		echo "Uso: make run-tasks ORG=4le PROJECT=Lab FILE=./data/task.json"; \
		exit 1; \
	fi
	$(BIN)/python azdo_cli.py --org $(ORG) --project $(PROJECT) tasks --file $(FILE)

# ---------------------------------
# clean
# Remove o virtualenv (limpa ambiente local).
# ---------------------------------
clean:
	rm -rf $(VENV_DIR)

# ---------------------------------
# run-refine
# Gera task.json chamando a OpenAI (Structured Outputs).
# Requer variáveis: INPUT, PARENT_ID, ITERATION, AREA.
# Opcional: OUT (senão usa task.json).
# Depende de "install".
# ---------------------------------
run-refine: install
	@if [ -z "$(INPUT)" ] || [ -z "$(PARENT_ID)" ] || [ -z "$(ITERATION)" ] || [ -z "$(AREA)" ]; then \
		echo "Uso: make run-refine INPUT=./data/refinement.txt PARENT_ID=123 ITERATION='Lab\\Sprint 1' AREA=Lab OUT=./data/task.json"; \
		exit 1; \
	fi
	$(BIN)/python refine_cli.py generate --input $(INPUT) --parent-id $(PARENT_ID) --iteration "$(ITERATION)" --area-path "$(AREA)" --out $(or $(OUT),task.json)

# ---------------------------------
# run-refine-validate
# Valida um task.json localmente (sem chamada OpenAI).
# Requer variável: FILE.
# Opcional: PARENT_ID (se quiser validar uniformidade do parent_id).
# Depende de "install".
# ---------------------------------
run-refine-validate: install
	@if [ -z "$(FILE)" ]; then \
		echo "Uso: make run-refine-validate FILE=./data/task.json [PARENT_ID=123]"; \
		exit 1; \
	fi
	$(BIN)/python refine_cli.py validate --file $(FILE) $(if $(PARENT_ID),--parent-id $(PARENT_ID),)

# ---------------------------------
# run-full
# Pipeline completo:
# 1) refine_cli.py generate -> gera JSON (OpenAI)
# 2) refine_cli.py validate -> valida JSON gerado
# 3) azdo_cli.py tasks      -> cria Tasks no Azure DevOps
#
# Requer variáveis: ORG, PROJECT, INPUT, PARENT_ID, ITERATION, AREA.
# Opcionais: OUT (default: ./data/task.generated.json), MODEL (default: gpt-4.1-nano)
# Depende de "install".
# ---------------------------------
run-full: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(INPUT)" ] || [ -z "$(PARENT_ID)" ] || [ -z "$(ITERATION)" ] || [ -z "$(AREA)" ]; then \
		echo "Uso: make run-full ORG=4le PROJECT=Lab INPUT=./data/refinement.txt PARENT_ID=4 ITERATION='Lab\\Sprint 1' AREA=Lab [OUT=./data/task.json] [MODEL=gpt-4.1-nano]"; \
		exit 1; \
	fi
	@OUT_FILE="$(if $(OUT),$(OUT),./data/task.generated.json)"; \
	MODEL_NAME="$(if $(MODEL),$(MODEL),gpt-4.1-nano)"; \
	mkdir -p "$$(dirname "$$OUT_FILE")"; \
	echo "==> 1) Gerando tasks via OpenAI (modelo: $$MODEL_NAME)"; \
	$(BIN)/python refine_cli.py generate \
	  --input "$(INPUT)" \
	  --parent-id "$(PARENT_ID)" \
	  --iteration "$(ITERATION)" \
	  --area-path "$(AREA)" \
	  --out "$$OUT_FILE" \
	  --model "$$MODEL_NAME"; \
	echo "==> 2) Validando JSON gerado"; \
	$(BIN)/python refine_cli.py validate --file "$$OUT_FILE" --parent-id "$(PARENT_ID)"; \
	echo "==> 3) Criando tasks no Azure DevOps"; \
	$(BIN)/python azdo_cli.py --org "$(ORG)" --project "$(PROJECT)" tasks --file "$$OUT_FILE"; \
	echo "✅ Pipeline concluído. Arquivo final: $$OUT_FILE"

# ---------------------------------
# run-help
# Mostra o help das duas CLIs (azdo_cli e refine_cli).
# Depende de "install".
# ---------------------------------
run-help: install
	@echo "==> azdo_cli.py help"
	@$(BIN)/python azdo_cli.py help
	@echo ""
	@echo "==> refine_cli.py help"
	@$(BIN)/python refine_cli.py help

# ---------------------------------
# run-help-azdo
# Mostra apenas a ajuda do azdo_cli.py.
# Depende de "install".
# ---------------------------------
run-help-azdo: install
	$(BIN)/python azdo_cli.py help

# ---------------------------------
# run-help-refine
# Mostra apenas a ajuda do refine_cli.py.
# Depende de "install".
# ---------------------------------
run-help-refine: install
	$(BIN)/python refine_cli.py help