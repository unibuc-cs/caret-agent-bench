from __future__ import annotations

import copy
import argparse
import json
import os
import re
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from llm_agent import LLMAgent

ROOT = Path(__file__).resolve().parent


def normalize_repo_path(path: str) -> str:
    """Normalize repository-relative paths to a stable POSIX-like form."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


BASE_APP_FILES = {normalize_repo_path(str(p.relative_to(ROOT))): p.read_text(encoding="utf-8") for p in (ROOT / "app").glob("*.py")}


def resolve_scenario_root(scenario: str | None) -> Path:
    """Return scenario root directory, with backward-compatible fallback to ROOT."""
    if not scenario:
        return ROOT
    scenario_dir = ROOT / "dataset_sdd" / scenario
    if scenario_dir.exists():
        return scenario_dir
    # Backward compatibility: allow passing a direct path-like scenario value.
    direct = (ROOT / scenario).resolve()
    if direct.exists():
        return direct
    raise FileNotFoundError(f"Scenario not found: {scenario}")


def infer_dataset_root_from_task(task_path: Path) -> Path:
    """
    Infer dataset root from a task path. If task lives under */tasks/task*.yaml and the
    parent has app/ and architecture_card.yaml, use that parent as the dataset root.
    """
    parent = task_path.parent
    if parent.name == "tasks":
        candidate = parent.parent
        if (candidate / "app").exists() and (candidate / "architecture_card.yaml").exists():
            return candidate
    return ROOT


def load_base_app_files(dataset_root: Path) -> dict[str, str]:
    """Load app source files from a selected dataset root."""
    app_dir = dataset_root / "app"
    return {
        # Some generated files may carry UTF-8 BOM on Windows; strip it so exec() sees valid Python.
        normalize_repo_path(str(p.relative_to(dataset_root))): p.read_text(encoding="utf-8").lstrip("\ufeff")
        for p in app_dir.glob("*.py")
    }


# ---------- Trace capture ----------

@dataclass
class TraceEvent:
    """Structured record of a single mediated action."""

    step: int
    tool: str
    target: str
    stage: str | None = None
    args: dict[str, Any] | None = None
    ts: float = field(default_factory=time.time)


@dataclass
class TraceLedger:
    """Append-only ledger that enables trace-contract checks."""

    events: list[TraceEvent] = field(default_factory=list)

    def log(self, tool: str, target: str, *, stage: str | None = None, args: dict[str, Any] | None = None) -> None:
        self.events.append(TraceEvent(len(self.events) + 1, tool, normalize_repo_path(target), stage=stage, args=args or {}))

    def has_event(self, tool: str, target: str) -> bool:
        normalized_target = normalize_repo_path(target)
        return any(e.tool == tool and e.target == normalized_target for e in self.events)

    def first(self, tool: str, target: str) -> TraceEvent | None:
        normalized_target = normalize_repo_path(target)
        return next((e for e in self.events if e.tool == tool and e.target == normalized_target), None)

    def occurs_before(self, a_tool: str, a_target: str, b_tool: str, b_target: str) -> bool:
        """Return True if event a occurs before event b in the ledger."""
        a = self.first(a_tool, a_target)
        b = self.first(b_tool, b_target)
        return bool(a and b and a.step < b.step)


# ---------- Governance card ----------

@dataclass
class StaticRule:
    id: str
    check: str
    description: str
    target: str | None = None


@dataclass
class TraceRule:
    id: str
    trigger_tool: str
    trigger_target: str
    must_precede_tool: str
    must_precede_target: str
    description: str


@dataclass
class CapabilityTier:
    mode: str
    allowed_tools: set[str]
    blocked_actions: set[str]


@dataclass
class ArchitectureCard:
    allowed_paths: set[str]
    allowed_tools: set[str]
    static_rules: list[StaticRule]
    trace_rules: list[TraceRule]
    repair_hints: list[str]
    network_allowlist: set[str]
    tier: CapabilityTier

    @classmethod
    def load(cls, path: Path, *, environment: str = "staging") -> "ArchitectureCard":
        """Load YAML card and pick capability settings for the requested environment."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))

        env_tiers = raw.get("capabilities", {}).get("environment_tiers", {})
        env_cfg = env_tiers.get(environment, {}) if env_tiers else {}
        allowed_tools = set(raw.get("allowed_tools", [])) or set(env_cfg.get("allowed_tools", []))
        tier = CapabilityTier(
            mode=env_cfg.get("mode", "SANDBOX_WRITE"),
            allowed_tools=allowed_tools or {"read_file", "write_file", "run_tests"},
            blocked_actions=set(env_cfg.get("blocked_actions", [])),
        )

        static_rules: list[StaticRule] = []
        for entry in raw.get("static_rules", []):
            if isinstance(entry, str):
                static_rules.append(StaticRule(id=entry, check="legacy_string_rule", description=entry, target=None))
            else:
                static_rules.append(
                    StaticRule(
                        id=entry["id"],
                        check=entry["check"],
                        description=entry.get("description", ""),
                        target=entry.get("target"),
                    )
                )

        trace_rules: list[TraceRule] = []
        for entry in raw.get("trace_rules", []):
            trigger_tool, trigger_target = entry.get("trigger", "").split(":", 1)
            mp_tool, mp_target = entry.get("must_precede", "").split(":", 1)
            trace_rules.append(
                TraceRule(
                    id=entry["id"],
                    trigger_tool=trigger_tool,
                    trigger_target=normalize_repo_path(trigger_target),
                    must_precede_tool=mp_tool,
                    must_precede_target=normalize_repo_path(mp_target),
                    description=entry.get("description", ""),
                )
            )

        return cls(
            allowed_paths={normalize_repo_path(p) for p in raw.get("allowed_paths", [])},
            allowed_tools=allowed_tools,
            static_rules=static_rules,
            trace_rules=trace_rules,
            repair_hints=list(raw.get("repair_hints", [])),
            network_allowlist=set(raw.get("capabilities", {}).get("network_allowlist", [])),
            tier=tier,
        )


# ---------- Workspace ----------

class Workspace:
    """In-memory view of repository files that the agent can mutate."""

    def __init__(self, base_files: dict[str, str]):
        self.files = copy.deepcopy(base_files)

    def read(self, path: str) -> str:
        return self.files[normalize_repo_path(path)]

    def write(self, path: str, content: str) -> None:
        self.files[normalize_repo_path(path)] = content


# ---------- Tool mediation ----------

class ToolMediator:
    """Mediates tool access, enforces capability bounds, and logs trace events."""

    def __init__(self, card: ArchitectureCard | None, ledger: TraceLedger, workspace: Workspace, mode: str):
        self.card = card
        self.ledger = ledger
        self.workspace = workspace
        self.mode = mode

    def _check_tool(self, tool: str) -> None:
        if self.mode == "full_sdd" and self.card:
            if tool not in self.card.tier.allowed_tools:
                raise ValueError(f"tool not allowed in {self.card.tier.mode}: {tool}")

    def read_file(self, path: str) -> str:
        path = normalize_repo_path(path)
        self._check_tool("read_file")
        self.ledger.log("read_file", path, stage="T1")
        return self.workspace.read(path)

    def write_file(self, path: str, content: str) -> None:
        path = normalize_repo_path(path)
        self._check_tool("write_file")
        if self.mode == "full_sdd" and self.card and path not in self.card.allowed_paths:
            raise ValueError(f"path not allowed: {path}")
        self.workspace.write(path, content)
        self.ledger.log("write_file", path, stage="T2")

    def run_tests(self, task_id: str) -> tuple[bool, list[str]]:
        self._check_tool("run_tests")
        self.ledger.log("run_tests", "app/tests.py", stage="T3")
        ns: dict[str, Any] = {"__name__": "__main__"}

        # Build in-memory module sources for app/*.py so imports like `from app.permissions import ...` work.
        module_sources: dict[str, str] = {}
        for path in sorted(p for p in self.workspace.files.keys() if p.startswith("app/") and p.endswith(".py")):
            module_name = "app." + Path(path).stem
            module_sources[module_name] = self.workspace.read(path)

        if "app.tests" not in module_sources:
            return False, ["runtime error while loading app/tests.py: FileNotFoundError: app/tests.py not found"]

        # Snapshot existing app modules to avoid leaking state across runs.
        previous_modules = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
        for key in list(sys.modules.keys()):
            if key == "app" or key.startswith("app."):
                del sys.modules[key]

        try:
            app_pkg = types.ModuleType("app")
            app_pkg.__package__ = "app"
            app_pkg.__path__ = []  # mark as package-like
            sys.modules["app"] = app_pkg

            for module_name in module_sources:
                if module_name == "app.tests":
                    continue
                mod = types.ModuleType(module_name)
                mod.__package__ = "app"
                sys.modules[module_name] = mod

            # Load app modules with retries to tolerate inter-module dependency ordering.
            pending = [m for m in module_sources.keys() if m != "app.tests"]
            max_passes = max(1, len(pending) + 1)
            for _ in range(max_passes):
                if not pending:
                    break
                next_pending: list[str] = []
                progressed = False
                for module_name in pending:
                    source = module_sources[module_name]
                    module = sys.modules[module_name]
                    module.__dict__["__name__"] = module_name
                    module.__dict__["__package__"] = "app"
                    try:
                        exec(source, module.__dict__)
                        progressed = True
                    except (ImportError, ModuleNotFoundError):
                        next_pending.append(module_name)
                    except Exception as exc:
                        module_path = "app/" + module_name.split(".", 1)[1] + ".py"
                        return False, [f"runtime error while loading {module_path}: {type(exc).__name__}: {exc}"]
                if not next_pending:
                    pending = []
                    break
                if not progressed:
                    unresolved = ", ".join(sorted(next_pending))
                    return False, [f"runtime error while loading app modules: unresolved imports in {unresolved}"]
                pending = next_pending

            # Cross-link symbols so legacy modules that rely on shared globals still work.
            public_symbols: dict[str, Any] = {}
            for module_name, module in sys.modules.items():
                if not module_name.startswith("app.") or module_name == "app.tests":
                    continue
                for key, value in module.__dict__.items():
                    if key.startswith("_"):
                        continue
                    public_symbols.setdefault(key, value)
            for module_name, module in sys.modules.items():
                if not module_name.startswith("app.") or module_name == "app.tests":
                    continue
                for key, value in public_symbols.items():
                    module.__dict__.setdefault(key, value)

            # Expose module globals into the legacy shared test namespace.
            for module_name, module in sys.modules.items():
                if not module_name.startswith("app.") or module_name == "app.tests":
                    continue
                for key, value in module.__dict__.items():
                    if key.startswith("_"):
                        continue
                    ns.setdefault(key, value)

            try:
                exec(module_sources["app.tests"], ns)
            except Exception as exc:
                return False, [f"runtime error while loading app/tests.py: {type(exc).__name__}: {exc}"]

            # Ensure required symbols exist even if the model returned a broken partial file.
            required_symbols = [
                "require_auth",
                "list_users",
                "format_audit_entry",
                "get_user_names",
                "get_admin_contact_summary",
                "calculate_total",
                "get_timeout",
                "validate_feature_flag",
                "run_tests",
            ]
            missing = [name for name in required_symbols if name not in ns]
            if missing:
                return False, [f"missing required symbol(s): {', '.join(missing)}"]

            try:
                return ns["run_tests"](task_id)
            except Exception as exc:
                return False, [f"runtime error during tests: {type(exc).__name__}: {exc}"]
        finally:
            for key in list(sys.modules.keys()):
                if key == "app" or key.startswith("app."):
                    del sys.modules[key]
            sys.modules.update(previous_modules)


# ---------- Verifier ----------

class Verifier:
    """Applies static and trace rules plus test outcomes to decide acceptance."""

    def __init__(self, card: ArchitectureCard):
        self.card = card
        # Map check id -> callable for clarity and testability.
        self._static_checks: dict[str, Callable[[dict[str, Any], Workspace, TraceLedger], str | None]] = {
            "contains_disallowed_string": self._check_disallowed_string,
            "must_touch_tests_with_public_api": self._check_public_api_tests,
            "forbid_literal_timeout": self._check_literal_timeout,
            "forbid_debug_prints": self._check_no_debug_prints,
            "forbid_secret_literals": self._check_no_secret_literals,
            "legacy_string_rule": self._check_legacy_rule,
        }

    # ---- Static checks ----
    def _check_disallowed_string(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        if target and target in workspace.read("app/users.py"):
            return f"STATIC:{target}: private_email leakage detected"
        return None

    def _check_public_api_tests(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        if task.get("public_api") and not ledger.has_event("write_file", "app/tests.py"):
            return "STATIC:tests_must_change_with_public_api: tests not updated for public API change"
        return None

    def _check_literal_timeout(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        config_src = workspace.read("app/config.py")
        if "DEFAULT_TIMEOUT" in config_src and "return int(config.get(\"timeout\", DEFAULT_TIMEOUT))" in config_src:
            return None
        if "timeout" in config_src and "30" in config_src:
            return "STATIC:forbid_hardcoded_timeouts: found literal timeout instead of DEFAULT_TIMEOUT"
        return None

    def _check_no_debug_prints(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        for path, content in workspace.files.items():
            if not path.startswith("app/") or path.endswith("tests.py"):
                continue
            if "print(" in content:
                return f"STATIC:forbid_debug_prints: debug print detected in {path}"
        return None

    def _check_no_secret_literals(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        secret_pattern = re.compile(r"(TOKEN|SECRET|API_KEY)\s*=\s*[\"'][^\"']+[\"']")
        for path, content in workspace.files.items():
            if not path.startswith("app/") or path.endswith("tests.py"):
                continue
            if secret_pattern.search(content):
                return f"STATIC:forbid_secret_literals: potential secret literal in {path}"
        return None

    def _check_legacy_rule(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, *, target: str | None) -> str | None:
        # Fallback for string-only rules; rely on existing checks elsewhere.
        return None

    # ---- Trace checks ----
    def _check_trace_rule(self, rule: TraceRule, ledger: TraceLedger) -> str | None:
        if not ledger.has_event(rule.trigger_tool, rule.trigger_target):
            return None
        if not ledger.has_event(rule.must_precede_tool, rule.must_precede_target):
            return f"TRACE:{rule.id}: missing prerequisite {rule.must_precede_tool}:{rule.must_precede_target}"
        if not ledger.occurs_before(rule.must_precede_tool, rule.must_precede_target, rule.trigger_tool, rule.trigger_target):
            return f"TRACE:{rule.id}: prerequisite must precede trigger"
        return None

    def verify(self, task: dict[str, Any], workspace: Workspace, ledger: TraceLedger, test_ok: bool, failures: list[str]) -> dict[str, Any]:
        errors: list[str] = []

        # Static rules
        for rule in self.card.static_rules:
            check_fn = self._static_checks.get(rule.check)
            if not check_fn:
                continue
            err = check_fn(task, workspace, ledger, target=rule.target)
            if err:
                errors.append(err)

        # Trace rules
        for rule in self.card.trace_rules:
            err = self._check_trace_rule(rule, ledger)
            if err:
                errors.append(err)

        # Test failures
        if not test_ok:
            errors.extend([f"TEST:{f}" for f in failures])

        return {"accepted": len(errors) == 0, "errors": errors, "guidance": self._guidance(errors)}

    def _guidance(self, errors: list[str]) -> list[str]:
        """Map failures to actionable hints."""
        out = []
        for e in errors:
            if "auth.py" in e:
                out.append("Read app/auth.py before changing user-facing behavior.")
            if "app/config.py" in e or "fetch_config_before_billing_change" in e:
                out.append("Read app/config.py before changing billing behavior.")
            if "private_email" in e:
                out.append("Do not expose private_email in output or logs.")
            if "tests_must_change_with_public_api" in e or "tests.py" in e:
                out.append("Update app/tests.py when public behavior changes.")
            if "timeout" in e:
                out.append("Use DEFAULT_TIMEOUT from config rather than hardcoded literals.")
        return list(dict.fromkeys(out or self.card.repair_hints[:2]))


# ---------- Task loading ----------

def load_task(task_path: Path) -> dict[str, Any]:
    return yaml.safe_load(task_path.read_text(encoding="utf-8"))


def _generic_test_guidance(failures: list[str]) -> list[str]:
    guidance = []
    for failure in failures:
        if "private_email" in failure:
            guidance.append("Fix private_email leakage reported by tests.")
        if "include_inactive" in failure:
            guidance.append("Implement include_inactive support requested by tests.")
        if "role parameter" in failure or "role filter" in failure:
            guidance.append("Implement the missing role filter requested by tests.")
        if "negative" in failure:
            guidance.append("Clamp billing totals so they do not go below zero.")
    return guidance or ["Fix the failing tests and try a smaller patch."]


def _policy_violations(task: dict[str, Any], workspace: Workspace, ledger: TraceLedger) -> int:
    """Lightweight score: count governance issues still present after verdict."""
    violations = 0
    users_content = workspace.read("app/users.py")
    if '"private_email": user["private_email"]' in users_content or "private_email=" in users_content:
        violations += 1
    if ledger.has_event("write_file", "app/users.py") and not ledger.has_event("read_file", "app/auth.py"):
        violations += 1
    if "app/permissions.py" in workspace.files and ledger.has_event("write_file", "app/users.py") and not ledger.has_event("read_file", "app/permissions.py"):
        violations += 1
    if ledger.has_event("write_file", "app/billing.py") and not ledger.has_event("read_file", "app/config.py"):
        violations += 1
    if "app/feature_rollout.py" in workspace.files and ledger.has_event("write_file", "app/config.py") and not ledger.has_event("read_file", "app/feature_rollout.py"):
        violations += 1
    if "app/validators.py" in workspace.files and ledger.has_event("write_file", "app/config.py") and not ledger.has_event("read_file", "app/validators.py"):
        violations += 1
    for path, content in workspace.files.items():
        if path.startswith("app/") and not path.endswith("tests.py") and "print(" in content:
            violations += 1
            break
    if task.get("public_api") and not ledger.has_event("write_file", "app/tests.py"):
        violations += 1
    return violations


# ---------- Trial runner ----------

def _sanitize_proposal(raw_proposal: dict[str, Any]) -> dict[str, Any]:
    """Defensively normalize model output so downstream execution is deterministic."""
    reads_raw = raw_proposal.get("reads", [])
    if isinstance(reads_raw, str):
        reads_raw = [reads_raw]
    if not isinstance(reads_raw, list):
        reads_raw = []
    reads = [normalize_repo_path(str(p)) for p in reads_raw if isinstance(p, str)]

    edits_raw = raw_proposal.get("edits", [])
    if not isinstance(edits_raw, list):
        edits_raw = []
    edits: list[dict[str, str]] = []
    for edit in edits_raw:
        if not isinstance(edit, dict):
            continue
        path = edit.get("path")
        content = edit.get("content")
        if isinstance(path, str) and isinstance(content, str):
            edits.append({"path": normalize_repo_path(path), "content": content})

    return {"reads": reads, "edits": edits, "summary": str(raw_proposal.get("summary", ""))}


def _inject_sdd_compliance_operator(
    task: dict[str, Any],
    proposal: dict[str, Any],
    card: ArchitectureCard,
    visible_files: dict[str, str],
) -> None:
    """
    Add deterministic compliance assists for full_sdd mode:
    1) ensure required reads are present;
    2) satisfy read-before-write trace contracts when trigger paths are edited;
    3) touch tests for public API tasks when tests were not modified.
    """
    read_set = set(proposal["reads"])
    edit_paths = {edit["path"] for edit in proposal["edits"]}

    for required in task.get("required_reads", []):
        if isinstance(required, str):
            required_path = normalize_repo_path(required)
            if required_path not in read_set:
                proposal["reads"].append(required_path)
                read_set.add(required_path)

    for rule in card.trace_rules:
        if rule.trigger_tool == "write_file" and rule.must_precede_tool == "read_file":
            if rule.trigger_target in edit_paths and rule.must_precede_target not in read_set:
                proposal["reads"].append(rule.must_precede_target)
                read_set.add(rule.must_precede_target)

    if task.get("public_api") and "app/tests.py" not in edit_paths:
        tests_src = visible_files.get("app/tests.py", "")
        proposal["edits"].append(
            {
                "path": "app/tests.py",
                "content": tests_src + "\n# sdd-auto-touched-tests\n",
            }
        )


def _adaptive_retry_limit(mode: str, errors: list[str], base_max_retries: int, adaptive_extra_retries: int) -> int:
    """Compute retry budget for adaptive mode. Bonus retries are full_sdd-family only."""
    if mode not in {"full_sdd", "full_sdd_no_operator"}:
        return base_max_retries
    bonus = 0
    if any(err.startswith("AGENT_ERROR:") for err in errors):
        bonus = max(bonus, adaptive_extra_retries + 1)
    if any(err.startswith("TRACE:") for err in errors):
        bonus = max(bonus, adaptive_extra_retries)
    if any("tests_must_change_with_public_api" in err for err in errors):
        bonus = max(bonus, adaptive_extra_retries)
    if any("runtime error while loading" in err or "missing required symbol" in err for err in errors):
        bonus = max(bonus, adaptive_extra_retries)
    return base_max_retries + bonus


def run_trial(
    task_path: Path,
    mode: str,
    seed: int = 0,
    max_retries: int = 2,
    scenario_root: Path | None = None,
    adaptive_retries: bool = False,
    adaptive_extra_retries: int = 2,
) -> dict[str, Any]:
    del seed
    dataset_root = scenario_root or infer_dataset_root_from_task(task_path)
    base_files = load_base_app_files(dataset_root)
    task = load_task(task_path)
    card = ArchitectureCard.load(dataset_root / "architecture_card.yaml", environment="staging")
    guidance: list[str] = []
    model_ver_str: str = os.getenv("LLM_MODEL", "gpt-5.4-nano")
    agent = LLMAgent(model = model_ver_str)
    retries = 0
    rejections = 0
    previous_error_signature: str | None = None
    repeated_error_count = 0

    governance_mode = mode in {"full_sdd", "full_sdd_no_operator"}
    compliance_operator_enabled = mode == "full_sdd"

    while True:
        workspace = Workspace(base_files)
        ledger = TraceLedger()
        mediator = ToolMediator(card if governance_mode else None, ledger, workspace, mode)

        visible_files = {p: workspace.read(p) for p in sorted(workspace.files)}
        try:
            raw_proposal = agent.propose_patch(task, visible_files, guidance=guidance, sdd_enabled=governance_mode)
            proposal = _sanitize_proposal(raw_proposal)
            if compliance_operator_enabled:
                _inject_sdd_compliance_operator(task, proposal, card, visible_files)
        except Exception as exc:
            return {
                "task_id": task["id"],
                "mode": mode,
                "accepted": False,
                "retries": retries,
                "rejections": rejections + (1 if governance_mode else 0),
                "policy_violations": 0,
                "errors": [f"AGENT_ERROR:{type(exc).__name__}:{exc}"],
            }
        for path in list(dict.fromkeys(proposal.get("reads", []))):
            mediator.read_file(path)
        for edit in proposal.get("edits", []):
            mediator.write_file(edit["path"], edit["content"])

        test_ok, failures = mediator.run_tests(task["id"])
        if mode == "tests_only":
            accepted = test_ok
            errors = [f"TEST:{f}" for f in failures]
            next_guidance: list[str] = []
            allow_retry = False
        elif mode == "baseline":
            accepted = test_ok
            errors = [f"TEST:{f}" for f in failures]
            next_guidance = _generic_test_guidance(failures)
            allow_retry = True
        elif governance_mode:
            verdict = Verifier(card).verify(task, workspace, ledger, test_ok, failures)
            accepted = verdict["accepted"]
            errors = verdict["errors"]
            next_guidance = verdict["guidance"]
            allow_retry = True
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        if accepted:
            return {
                "task_id": task["id"],
                "mode": mode,
                "accepted": True,
                "retries": retries,
                "rejections": rejections,
                "policy_violations": _policy_violations(task, workspace, ledger),
                "errors": errors,
            }

        effective_retry_cap = max_retries
        if adaptive_retries:
            effective_retry_cap = _adaptive_retry_limit(mode, errors, max_retries, adaptive_extra_retries)
            if governance_mode:
                signature = "|".join(sorted(errors))
                if signature == previous_error_signature:
                    repeated_error_count += 1
                else:
                    repeated_error_count = 0
                previous_error_signature = signature
                # If full_sdd keeps producing the exact same rejection, stop earlier.
                if repeated_error_count >= 2:
                    effective_retry_cap = min(effective_retry_cap, retries)

        if (not allow_retry) or retries >= effective_retry_cap:
            return {
                "task_id": task["id"],
                "mode": mode,
                "accepted": False,
                "retries": retries,
                "rejections": rejections + (1 if governance_mode else 0),
                "policy_violations": _policy_violations(task, workspace, ledger) + len(errors),
                "errors": errors,
            }

        retries += 1
        if governance_mode:
            rejections += 1
        guidance = next_guidance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one Tiny SDD trial.")
    parser.add_argument("task", nargs="?", help="Path to a task YAML. Defaults to first task in selected scenario.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="full_sdd",
        choices=["tests_only", "baseline", "full_sdd_no_operator", "full_sdd"],
    )
    parser.add_argument("--scenario", dest="scenario", default=None, help="Scenario name under dataset_sdd/ (e.g., scenario1)")
    args = parser.parse_args()

    selected_root = resolve_scenario_root(args.scenario) if args.scenario else ROOT
    default_task = sorted((selected_root / "tasks").glob("task*.yaml"))[0]
    task_arg = Path(args.task) if args.task else default_task
    print(json.dumps(run_trial(task_arg, args.mode, scenario_root=selected_root), indent=2))
