# Compose entry point. ENGINE=auto prefers Docker when both engines are ready.
ENGINE ?= auto
COMPOSE_FILE ?= compose.yaml
TEST_COMPOSE_FILE ?= compose.test.yaml
PROJECT_NAME ?= manga-translator
TEST_PROJECT_NAME ?= manga-translator-test

DOCKER_COMPOSE_READY := $(shell command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && printf yes)
PODMAN_COMPOSE_READY := $(shell command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1 && printf yes)
PODMAN_COMPOSE_STANDALONE_READY := $(shell command -v podman >/dev/null 2>&1 && command -v podman-compose >/dev/null 2>&1 && printf yes)

ifeq ($(ENGINE),auto)
  ifeq ($(DOCKER_COMPOSE_READY),yes)
    COMPOSE_CMD := docker compose
    SELECTED_ENGINE := docker
  else ifeq ($(PODMAN_COMPOSE_READY),yes)
    COMPOSE_CMD := podman compose
    SELECTED_ENGINE := podman
  else ifeq ($(PODMAN_COMPOSE_STANDALONE_READY),yes)
    COMPOSE_CMD := podman-compose
    SELECTED_ENGINE := podman
  endif
else ifeq ($(ENGINE),docker)
  ifeq ($(DOCKER_COMPOSE_READY),yes)
    COMPOSE_CMD := docker compose
    SELECTED_ENGINE := docker
  endif
else ifeq ($(ENGINE),podman)
  ifeq ($(PODMAN_COMPOSE_READY),yes)
    COMPOSE_CMD := podman compose
    SELECTED_ENGINE := podman
  else ifeq ($(PODMAN_COMPOSE_STANDALONE_READY),yes)
    COMPOSE_CMD := podman-compose
    SELECTED_ENGINE := podman
  endif
endif

# Apple-silicon VMs (Podman applehv on M4) expose SME without SVE; cryptography>=47 and onnxruntime>=1.31 wheels
# then crash with SIGILL. COMPAT_SME_VM=1 builds with backend/requirements-compat-sme-vm.txt (older pins that carry
# known advisories, local use only). It is never selected automatically: set it on the command line, in the
# environment, or in .env (only that one line of .env is read here; nothing else from .env is printed or used).
# Detection below only informs `make doctor` and makes a *default* build refuse with instructions instead of
# silently switching branches or producing an image that dies with SIGILL.
ifeq ($(origin COMPAT_SME_VM),undefined)
  COMPAT_SME_VM := $(strip $(shell grep -E '^COMPAT_SME_VM=' .env 2>/dev/null | tail -n 1 | cut -d= -f2-))
  COMPAT_SME_VM_SOURCE := .env
endif
ifeq ($(strip $(COMPAT_SME_VM)),)
  COMPAT_SME_VM := 0
  COMPAT_SME_VM_SOURCE := default
endif
COMPAT_SME_VM_SOURCE ?= explicit
export COMPAT_SME_VM
# Recursively expanded on purpose: the VM is probed only by targets that reference it (not by `make help`).
ifeq ($(SELECTED_ENGINE),podman)
  SME_VM_DETECTED = $(shell podman machine ssh "grep -qw sme /proc/cpuinfo && ! grep -qw sve /proc/cpuinfo" >/dev/null 2>&1 && printf 1 || printf 0)
else
  SME_VM_DETECTED = 0
endif

APP_COMPOSE = $(COMPOSE_CMD) -p $(PROJECT_NAME) -f $(COMPOSE_FILE)
# --env-file /dev/null: the test project must not load the real .env (interpolation defaults in compose.yaml are
# enough; COMPAT_SME_VM is exported from this Makefile). compose.test.yaml also resets env_file/secrets.
TEST_COMPOSE = $(COMPOSE_CMD) -p $(TEST_PROJECT_NAME) --env-file /dev/null -f $(COMPOSE_FILE) -f $(TEST_COMPOSE_FILE)

.DEFAULT_GOAL := help
.PHONY: help doctor config build up down ps logs test secrets env _check-engine _check-app-files _check-test-files _check-sme-vm

help:
	@printf '%s\n' \
	  'Manga Translator — Compose commands' \
	  '  make doctor             Detect Docker/Podman Compose' \
	  '  make config             Validate app Compose configuration' \
	  '  make build              Build app images' \
	  '  make up                 Build and start app containers' \
	  '  make down               Stop app containers (keep data)' \
	  '  make ps                 List app containers' \
	  '  make logs               Follow app logs' \
	  '  make test               Run tests in isolated Compose project' \
	  '  make lama-model         Download the LaMa inpainting model into the models volume' \
	  '  make secrets            Create secrets/master.key if missing (credential encryption)' \
	  '  make env                Create .env from .env.example if missing (edit MT_* values there)' \
	  'Optional: ENGINE=auto|docker|podman (default: auto; Docker first)' \
	  '          OCR_EXTRAS=full|none (default: full) to skip the OCR/ML stack in the app image' \
	  '          COMPAT_SME_VM=0|1 (default: 0; also read from .env) explicit opt-in to the SME-VM compat pins' \
	  '                             (backend/requirements-compat-sme-vm.txt, known advisories; local use only)'

doctor: _check-engine
	@printf 'Selected engine: %s\nCompose command: %s\n' '$(SELECTED_ENGINE)' '$(COMPOSE_CMD)'
	@if [ '$(SELECTED_ENGINE)' = 'podman' ]; then \
	  if [ '$(SME_VM_DETECTED)' = '1' ]; then \
	    printf 'Podman VM CPU: SME without SVE detected -> cryptography>=47 / onnxruntime>=1.31 would SIGILL in containers\n'; \
	  else \
	    printf 'Podman VM CPU: SME-without-SVE quirk not detected\n'; \
	  fi; \
	fi
	@printf 'COMPAT_SME_VM=%s (source: %s)\n' '$(COMPAT_SME_VM)' '$(COMPAT_SME_VM_SOURCE)'
	@if [ '$(COMPAT_SME_VM)' = '1' ]; then \
	  printf 'Recommendation: compat pins active (cryptography 46.0.7 / onnxruntime 1.30.0, known advisories). Local use only; never deploy this image.\n'; \
	elif [ '$(SME_VM_DETECTED)' = '1' ]; then \
	  printf 'Recommendation: `make build/up/test` will refuse until you choose explicitly: COMPAT_SME_VM=1 make <target> (or COMPAT_SME_VM=1 in .env),\n'; \
	  printf '                or fix the VM so no pins are needed (docs/OPERATIONS.md, "VM Apple Silicon").\n'; \
	else \
	  printf 'Recommendation: default dependency pins (no advisories) are fine here.\n'; \
	fi
	@if [ -f '$(COMPOSE_FILE)' ]; then \
	  printf 'App Compose file: %s\n' '$(COMPOSE_FILE)'; \
	else \
	  printf 'App Compose file pending: %s (planned for implementation phase 1)\n' '$(COMPOSE_FILE)'; \
	fi
	@if [ -f '$(TEST_COMPOSE_FILE)' ]; then \
	  printf 'Test Compose file: %s\n' '$(TEST_COMPOSE_FILE)'; \
	else \
	  printf 'Test Compose file pending: %s (planned for implementation phase 1)\n' '$(TEST_COMPOSE_FILE)'; \
	fi

_check-engine:
	@if [ -z '$(COMPOSE_CMD)' ]; then \
	  printf '%s\n' 'No usable Compose command found for ENGINE=$(ENGINE).' \
	    'Install Docker with the Compose plugin, or Podman with a Compose provider.' >&2; \
	  exit 1; \
	fi

_check-app-files: _check-engine
	@if [ ! -f '$(COMPOSE_FILE)' ]; then \
	  printf 'Missing %s. Container implementation is scheduled for phase 1 in PLAN.md.\n' '$(COMPOSE_FILE)' >&2; \
	  exit 1; \
	fi

_check-test-files: _check-app-files
	@if [ ! -f '$(TEST_COMPOSE_FILE)' ]; then \
	  printf 'Missing %s. Container tests are scheduled for phase 1 in PLAN.md.\n' '$(TEST_COMPOSE_FILE)' >&2; \
	  exit 1; \
	fi

# Guard for every target that builds images: the compat branch is opt-in, and a default build on an affected VM
# stops with instructions instead of silently switching pins (or producing an image that SIGILLs).
_check-sme-vm:
	@if [ '$(COMPAT_SME_VM)' != '0' ] && [ '$(COMPAT_SME_VM)' != '1' ]; then \
	  printf 'COMPAT_SME_VM must be 0 or 1 (got "%s" from %s); "auto" is no longer supported.\n' '$(COMPAT_SME_VM)' '$(COMPAT_SME_VM_SOURCE)' >&2; \
	  exit 1; \
	fi
	@if [ '$(COMPAT_SME_VM)' = '1' ]; then \
	  printf 'COMPAT_SME_VM=1 (%s): building with backend/requirements-compat-sme-vm.txt (pinned wheels with known advisories; local use only).\n' '$(COMPAT_SME_VM_SOURCE)'; \
	elif [ '$(SME_VM_DETECTED)' = '1' ]; then \
	  if [ '$(COMPAT_SME_VM_SOURCE)' = 'default' ]; then \
	    printf '%s\n' \
	      'Refusing to build: the Podman VM advertises SME without SVE, so cryptography>=47 and onnxruntime>=1.31' \
	      'from the default pins crash with SIGILL (exit 132) inside containers. The build no longer switches' \
	      'dependency branches silently. Choose explicitly:' \
	      '  COMPAT_SME_VM=1 make <target>   (or add COMPAT_SME_VM=1 to .env)' \
	      '      -> backend/requirements-compat-sme-vm.txt: cryptography 46.0.7 (4 known advisories, fixed only in' \
	      '         >=48.0.1/49/50) + onnxruntime 1.30.0. Local development only; never deploy that image.' \
	      '  COMPAT_SME_VM=0 make <target>   -> default pins; expect SIGILL on this VM.' \
	      '  Fix the VM instead (no pins needed): docs/OPERATIONS.md, section "VM Apple Silicon".' >&2; \
	    exit 1; \
	  else \
	    printf 'Warning: VM advertises SME without SVE but COMPAT_SME_VM=0 was set explicitly (%s); expect SIGILL from cryptography/onnxruntime.\n' '$(COMPAT_SME_VM_SOURCE)' >&2; \
	  fi; \
	fi

# Master key for encrypting provider credentials. Lives outside DB/image/data volume; git-ignored.
secrets:
	@if [ ! -s secrets/master.key ]; then \
	  mkdir -p secrets && umask 077 && head -c 48 /dev/urandom | base64 | tr -d '\n' > secrets/master.key \
	  && printf 'Created secrets/master.key\n'; \
	fi

# Local configuration read by Compose (git-ignored). Never overwrites an existing .env.
env:
	@if [ -f .env ]; then \
	  printf '.env already exists; edit it directly (template: .env.example)\n'; \
	else \
	  cp .env.example .env && printf 'Created .env from .env.example\n'; \
	fi

config: _check-app-files secrets
	@$(APP_COMPOSE) config

build: _check-app-files _check-sme-vm secrets
	@$(APP_COMPOSE) build

up: _check-app-files _check-sme-vm secrets
	@$(APP_COMPOSE) up --build -d

down: _check-app-files
	@$(APP_COMPOSE) down

ps: _check-app-files
	@$(APP_COMPOSE) ps

logs: _check-app-files
	@$(APP_COMPOSE) logs -f

# The test service in compose.test.yaml will run all tests; no host test runner is used.
# A separate Compose project keeps test containers/volumes away from app data. It does not depend on `secrets`
# (the overlay carries an inline throwaway key) nor on .env (see TEST_COMPOSE); volumes are removed afterwards.
# Fetch the LaMa inpainting model (~200 MB, Apache-2.0) into the models volume; the worker then uses
# it automatically (MT_RENDER_INPAINT_MODEL=auto). Needs the images built (`make up` once).
lama-model: _check-app-files _check-sme-vm secrets
	$(APP_COMPOSE) run --rm --no-deps worker python -m app.render.lama download

test: _check-test-files _check-sme-vm
	@set -eu; \
	  printf 'Test engine: %s (%s)\n' '$(SELECTED_ENGINE)' "$$($(COMPOSE_CMD) version 2>/dev/null | head -n 1)"; \
	  cleanup() { $(TEST_COMPOSE) down --volumes --remove-orphans; }; \
	  trap cleanup EXIT; \
	  $(TEST_COMPOSE) build; \
	  $(TEST_COMPOSE) run --rm --no-TTY tests
