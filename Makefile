PYTHON ?= python3
VENV_DIR ?= .venv
BIN := $(VENV_DIR)/bin

.PHONY: help venv install run-pbis run-tasks run-help clean run-refine run-refine-validate run-full

help:
	@echo "Targets:"
	@echo "  make venv            - cria virtualenv em .venv (se não existir)"
	@echo "  make install         - instala dependências do requirements.txt"
	@echo "  make run-help        - mostra help do CLI"
	@echo "  make run-pbis ORG=4le PROJECT=Lab FILE=./pbi.json"
	@echo "  make run-tasks ORG=4le PROJECT=Lab FILE=./task.json"
	@echo "  make clean           - remove .venv"
	@echo ""
	@echo "Variáveis de ambiente necessárias:"
	@echo "  AZDO_PAT             - Personal Access Token do Azure DevOps (Work Items Read & write)"

# Cria venv apenas se não existir
venv:
	@test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r requirements.txt

run-help: install
	$(BIN)/python azdo_cli.py help

run-pbis: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(FILE)" ]; then \
		echo "Uso: make run-pbis ORG=4le PROJECT=Lab FILE=./pbi.json"; \
		exit 1; \
	fi
	$(BIN)/python azdo_cli.py --org $(ORG) --project $(PROJECT) pbis --file $(FILE)

run-tasks: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(FILE)" ]; then \
		echo "Uso: make run-tasks ORG=4le PROJECT=Lab FILE=./task.json"; \
		exit 1; \
	fi
	$(BIN)/python azdo_cli.py --org $(ORG) --project $(PROJECT) tasks --file $(FILE)

clean:
	rm -rf $(VENV_DIR)

run-refine: install
	@if [ -z "$(INPUT)" ] || [ -z "$(PARENT_ID)" ] || [ -z "$(ITERATION)" ] || [ -z "$(AREA)" ]; then \
		echo "Uso: make run-refine INPUT=./refinement.txt PARENT_ID=123 ITERATION='Lab\\Sprint 1' AREA=Lab OUT=./task.json"; \
		exit 1; \
	fi
	$(BIN)/python refine_cli.py generate --input $(INPUT) --parent-id $(PARENT_ID) --iteration "$(ITERATION)" --area-path "$(AREA)" --out $(or $(OUT),task.json)

run-refine-validate: install
	@if [ -z "$(FILE)" ]; then \
		echo "Uso: make run-refine-validate FILE=./task.json"; \
		exit 1; \
	fi
	$(BIN)/python refine_cli.py validate --file $(FILE)

run-full: install
	@if [ -z "$(ORG)" ] || [ -z "$(PROJECT)" ] || [ -z "$(INPUT)" ] || [ -z "$(PARENT_ID)" ] || [ -z "$(ITERATION)" ] || [ -z "$(AREA)" ]; then \
		echo "Uso: make run-full ORG=4le PROJECT=Lab INPUT=./refinement.txt PARENT_ID=4 ITERATION='Lab\\Sprint 1' AREA=Lab [OUT=./task.json] [MODEL=gpt-4.1-nano]"; \
		exit 1; \
	fi
	@OUT_FILE="$(if $(OUT),$(OUT),./task.generated.json)"; \
	MODEL_NAME="$(if $(MODEL),$(MODEL),gpt-4.1-nano)"; \
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
